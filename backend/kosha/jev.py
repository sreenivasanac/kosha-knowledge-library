"""TypeSafe Jev for Kosha: what an item's question reads, the answer cache, the
estimate before spending, the budgets and the ledger rows.

The client, the request packing and the price constants are Constella's
(`constella.jev`). Measured facts this module is built on
(constella/agentic_development_docs/measurements_2026-11-21_jev.md):

- A rule-built summary loses up to 21 of 33 true "yes" answers; the first
  2,000 characters of the text agree with the full text 99 to 100%. So Jev
  reads the head of the item's text, never the summary.
- The two request layouts agree only 92 to 97% at 0.8, and the cache key does
  not include the layout. Every call here uses the one layout `JEV_LAYOUT`.
"""
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from constella import jev as cj
from constella.jev import Answer, Question, Spend
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from kosha.db import lock_spend
from kosha.keys import get_key
from kosha.models import JevAnswer, JevQuestion, LabelRun, SpendLedger
from kosha.prefs import load_settings

# --- Tunables ---------------------------------------------------------------
JEV_TEXT_CHARS = 2000  # about 500 tokens; longer adds nothing (measured)
JEV_MIN_CHARS = 20  # less text than this cannot be judged: the item is skipped and counted
JEV_LAYOUT = "question"  # the question is the state, many items per request (about 2.5x cheaper than one request per item)
JEV_CONFIRM_ITEMS = 2000  # asking about more new items than this needs a confirm token first
DEFAULT_THRESHOLD = 0.8  # labels accepted at 0.8 were about 97% correct (Constella v4, measured)
PROVIDER = "jev"
INSERT_CHUNK = 5000  # rows per INSERT, to stay under the driver's parameter limit
# The four-level usefulness score (SEARCH_DESIGN.md D.4), low to high.
USEFULNESS_LEVELS = (
    "Unrelated.",
    "Same broad area, nothing usable.",
    "Contains a usable idea, technique or reference.",
    "Directly addresses the need: a how-to, a tool or a worked example.",
)

# What Jev reads: title, body, quoted post, link titles and media extracts.
TEXT_SQL = text("""
SELECT i.id, left(concat_ws(E'\\n',
    CASE WHEN i.body IS NULL OR position(i.title in i.body) <> 1 THEN i.title END,
    i.body,
    (SELECT string_agg(concat_ws(' ', q.title, left(q.body, :max)), E'\\n')
       FROM item_relations r JOIN items q ON q.id = r.to_item_id
      WHERE r.from_item_id = i.id AND r.relation = 'quotes'),
    (SELECT string_agg(l.title, E'\\n' ORDER BY l.position) FROM item_links l WHERE l.item_id = i.id),
    (SELECT string_agg(concat_ws(' ', m.alt_text, left(m.extract_text, :max)), E'\\n' ORDER BY m.position)
       FROM item_media m WHERE m.item_id = i.id)), :max) AS text
FROM items i
WHERE i.user_id = :user_id AND i.id = ANY(:ids)
""")


# Ask sets this around one pipeline call: the most that call's Jev stages may
# spend (what is left of the question's allowance). None means no such limit.
call_allowance: ContextVar[float | None] = ContextVar("jev_call_allowance", default=None)


class BudgetError(RuntimeError):
    """The call would pass the run's or the month's Jev budget. Nothing was spent."""


def get_client():
    """The Jev client. Tests replace this with a fake that has `.model` and `.ask(pairs, by)`."""
    return cj.Jev(api_key=get_key("typesafe"))


def item_texts(session: Session, user_id: int, ids: list[int]) -> dict[int, str]:
    """The exact text Jev reads for each item. Items with almost no text are left out."""
    rows = session.execute(TEXT_SQL, {"user_id": user_id, "ids": list(ids), "max": JEV_TEXT_CHARS})
    return {r.id: r.text.strip() for r in rows if r.text and len(r.text.strip()) >= JEV_MIN_CHARS}


class AnswerCache:
    """Constella's cache protocol on `jev_questions` / `jev_answers`: a key is
    (question hash, item id, text hash), so the same question about the same
    text is never paid for twice, and a reworded question or a changed text is
    asked again without anything being invalidated by hand."""

    def __init__(self, session: Session, user_id: int, questions: list[Question], model: str):
        self.session, self.user_id = session, user_id
        self.wording = {q.key(model): q.instructions for q in questions}

    def get_many(self, keys):
        if not keys:
            return {}
        rows = self.session.execute(
            select(JevQuestion.question_hash, JevAnswer.item_id, JevAnswer.summary_hash, JevAnswer.score, JevAnswer.answer)
            .join(JevAnswer, JevAnswer.question_id == JevQuestion.id)
            .where(JevQuestion.user_id == self.user_id, JevQuestion.question_hash.in_({k[0] for k in keys}),
                   JevAnswer.item_id == func.any(sorted({int(k[1]) for k in keys}))))
        wanted = set(keys)
        found = {}
        for q_hash, item_id, text_hash, score, detail in rows:
            key = (q_hash, str(item_id), text_hash)
            if key in wanted:
                found[key] = Answer(score, (detail or {}).get("confidence"))
        return found

    def put_many(self, answers):
        if not answers:
            return
        hashes = {k[0] for k in answers}
        self.session.execute(insert(JevQuestion).values(
            [{"user_id": self.user_id, "question": self.wording[h], "question_hash": h} for h in hashes]
        ).on_conflict_do_nothing())
        ids = dict(self.session.execute(select(JevQuestion.question_hash, JevQuestion.id).where(
            JevQuestion.user_id == self.user_id, JevQuestion.question_hash.in_(hashes))).all())
        rows = [{"question_id": ids[k[0]], "item_id": int(k[1]), "summary_hash": k[2], "score": a.value,
                 "answer": {"confidence": a.confidence} if a.confidence is not None else None}
                for k, a in answers.items()]
        for start in range(0, len(rows), INSERT_CHUNK):
            self.session.execute(insert(JevAnswer).values(rows[start:start + INSERT_CHUNK]).on_conflict_do_nothing())
        self.session.commit()  # paid answers survive whatever the rest of the call does


@dataclass
class Estimate:
    n: int  # pairs in the call
    to_ask: int  # pairs the cache has no answer for
    usd: float  # what asking those would cost
    uncached_usd: float  # what the whole call would cost with an empty cache


def _cache(session: Session, user_id: int, pairs, model: str) -> AnswerCache:
    return AnswerCache(session, user_id, list(dict.fromkeys(p[0] for p in pairs)), model)


def estimate(session: Session, user_id: int, pairs: list) -> Estimate:
    """What a call would cost, from character counts and the cache. No network."""
    model = get_client().model
    keys = [cj.cache_key(q, item_id, text, model) for q, item_id, text in pairs]
    known = _cache(session, user_id, pairs, model).get_many(list(dict.fromkeys(keys)))
    todo = list({k: p for k, p in zip(keys, pairs) if k not in known}.values())
    return Estimate(len(pairs), len(todo), cj.estimate(todo, JEV_LAYOUT).usd if todo else 0.0,
                    cj.estimate(pairs, JEV_LAYOUT).usd if pairs else 0.0)


def month_spend(session: Session, user_id: int) -> float:
    start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return float(session.scalar(select(func.coalesce(func.sum(SpendLedger.amount_usd), 0)).where(
        SpendLedger.user_id == user_id, SpendLedger.provider == PROVIDER, SpendLedger.created_at >= start)))


def run_spend(session: Session, user_id: int, run_id: int) -> float:
    """Everything a label run has paid for, any provider."""
    return float(session.scalar(select(func.coalesce(func.sum(SpendLedger.amount_usd), 0)).where(
        SpendLedger.user_id == user_id, SpendLedger.meta["run_id"].as_integer() == run_id)))


def run_budget(settings: dict, asked: float | None) -> float:
    """What a label run may spend in total: the budget it was started with
    (the per-call budget when none was given), never more than the owner's
    `jev_run_max_budget_usd`. The agent picks the first number, the owner the cap."""
    return min(asked or settings["jev_run_budget_usd"], settings["jev_run_max_budget_usd"])


def check_budget(session: Session, user_id: int, usd: float, run: LabelRun | None = None) -> None:
    """Refuse a call that would pass a budget: Ask's allowance, the month, the
    per-call budget `jev_run_budget_usd` (inside a label run as well), and the
    run's own budget (`run_budget`)."""
    allowance = call_allowance.get()
    if allowance is not None and usd > allowance:
        raise BudgetError(f"est ${usd:.4f} is over what this question may still spend on Jev (${allowance:.4f}); "
                          "narrow the set first (head N, or a tighter filter)")
    settings = load_settings(session, user_id)
    spent = month_spend(session, user_id)
    if spent + usd > settings["jev_monthly_budget_usd"]:
        raise BudgetError(f"est ${usd:.4f} would pass the monthly Jev budget "
                          f"(${spent:.4f} of ${settings['jev_monthly_budget_usd']:.2f} spent)")
    if usd > settings["jev_run_budget_usd"]:
        raise BudgetError(f"est ${usd:.4f} is over the per-call Jev budget of ${settings['jev_run_budget_usd']:.2f} "
                          "(setting jev_run_budget_usd); narrow the set first")
    if run is not None:
        budget = run_budget(settings, (run.params or {}).get("budget_usd"))
        used = run_spend(session, user_id, run.id)
        if used + usd > budget:
            raise BudgetError(f"est ${usd:.4f} would pass run {run.id}'s budget (${used:.4f} of ${budget:.2f} spent)")


def ask(session: Session, user_id: int, pairs: list, operation: str, run: LabelRun | None = None,
        estimate_usd: float | None = None) -> tuple[list[Answer], Spend]:
    """Answer every (Question, item id, text) pair, asking Jev only what the
    cache lacks, and write one ledger row. The row also records what the call
    would have cost with an empty cache, so two strategies can be compared
    fairly when the second one benefits from the first one's answers.

    Budgets: under the user's spend lock the estimate is checked
    (`check_budget`, raising BudgetError with nothing spent) and written as a
    provisional ledger row, committed before Jev is called, so calls running
    at the same time count each other. Afterwards the row becomes what was
    really paid; when Jev fails midway it becomes `<operation>_failed` with
    the part that was billed."""
    client = get_client()
    cache = _cache(session, user_id, pairs, client.model)
    uncached_usd = cj.estimate(pairs, JEV_LAYOUT).usd if pairs else 0.0
    if estimate_usd is None:
        estimate_usd = estimate(session, user_id, pairs).usd
    lock_spend(session, user_id)
    check_budget(session, user_id, estimate_usd, run)
    row = SpendLedger(user_id=user_id, provider=PROVIDER, operation=operation, units=0, amount_usd=Decimal(f"{estimate_usd:.6f}"),
                      meta={"model": client.model, "unit": "tokens", "provisional": True, **({"run_id": run.id} if run else {})})
    session.add(row)
    session.commit()  # ends the transaction, which releases the lock

    def settle(spend: Spend, name: str) -> None:
        failed = name != operation
        row.operation, row.units, row.amount_usd = name, spend.input_tokens, Decimal(f"{spend.usd:.6f}")
        row.meta = {"model": client.model, "unit": "tokens", "requests": spend.requests, "asked": spend.asked, "cached": spend.cached,
                    "uncached_usd": round(spend.usd if failed else uncached_usd, 6), **({"run_id": run.id} if run else {})}
        if failed and not spend.usd:
            session.delete(row)  # nothing was billed: the reservation goes
        session.commit()

    try:
        answers, spend = cj.ask_cached(client, pairs, cache, by=JEV_LAYOUT)
    except BaseException as exc:
        # Requests that got through before the failure were billed and their answers are cached.
        session.rollback()
        partial = getattr(exc, "partial", None)
        settle(partial[1] if partial else Spend(), f"{operation}_failed")
        raise
    settle(spend, operation)
    return answers, spend
