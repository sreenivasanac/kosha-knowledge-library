"""Runs a pipeline (SEARCH_DESIGN.md section E).

    where from:a,b | grep ~retriev | semantic "retrieval augmented generation" | head 200
      | jev "about RAG / retrieval-augmented generation?" >= 0.9 | tag "AI/RAG"

Three rules: a stage only sees the set that came in; the response holds counts
and only what `show` asks for; a write is the last stage and follows the
preview rules in `writes.py`. All work happens here, on the server.
"""
import difflib
import math
import random
import re
import statistics
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import numpy as np
from constella import Label, Question, assign, group, shortlist, tree_hints
from constella.groups import choose_k
from constella.loop import label_question
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from kosha import jev
from kosha.config import DEFAULT_NAMESPACE, PIPELINE_MAX_CHARS
from kosha.items_query import STABLE_ORDER
from kosha.models import Item, ItemEmbedding, LabelRun, ResultSet, SavedView
from kosha.pipeline import writes
from kosha.pipeline.parser import Stage, Tok, number, split
from kosha.prefs import load_settings
from kosha.search import engine
from kosha.search.embeddings import DOC_MODEL, QUERY_MODEL, EmbeddingError, embed_texts, query_vector, record_spend
from kosha.search import rerank as reranking
from kosha.search.query import KEYS, SORT_KEYS, And, Not, Or, QueryError, Text, compile_filter, order_by, parse, to_string
from kosha.summaries import item_text

# --- Tunables ---------------------------------------------------------------
RESPONSE_MAX_TOKENS = 1500  # hard cap on one response
CHARS_PER_TOKEN = 4  # the estimate used for the cap and reported in the docs
SET_TTL = timedelta(days=7)  # a named set lives this long after its last use
SAMPLE_SEED = 0  # `sample` without a seed repeats, so a confirmed write sees the set it previewed
DEFAULT_ROWS = 5  # rows shown when the pipeline has no `show`
DEFAULT_ROW_FIELDS = ("id", "author", "date", "text:60")
SHOW_MAX_ROWS = 300  # rows fetched for `show`; the token cap cuts sooner
SHOW_DEFAULT_CHARS = {"summary": 100, "body": 100, "text": 60, "title": 60, "url": 80, "tags": 60, "folder": 40, "domain": 40}
PREVIEW_SAMPLES = 5
COUNT_DEFAULT_TOP = 20
TERMS_DEFAULT_TOP = 15
TERMS_MIN_ITEMS = 3  # a stem in fewer items of the set than this is noise
TERMS_WEIGHTS = "bc"  # body and quoted text/links: author names (A) would only repeat `count by author`
TERMS_LIBRARY_TTL_S = 600
GROUPS_DEFAULT_K = 12  # about 80 tokens a group, so 12 fit the response cap
GROUPS_MAX_K = 40
GROUP_SAMPLE_CHARS = 60
SHORTLIST_LABELS = 8  # Jev's best label was among the 8 nearest for 96.6% of items (Constella v4)
ANSWER_BANDS = (0.2, 0.5, 0.8, 0.9)
COUNT_DIMS = ("author", "domain", "tag", "folder", "month", "year", "source", "type", "lang")
STAT_FIELDS = {"likes": "likes", "views": "views", "reposts": "reposts", "replies": "replies",
               "duration": "duration_s", "len": "char_length(coalesce(body, ''))"}
SHOW_FIELDS = ("id", "author", "date", "source", "summary", "body", "text", "title", "tags", "folder", "domain", "url", "score")
WRITE_STAGES = ("tag", "untag", "roles", "list", "save", *writes.STATE_WRITES)
NAME_RE = r"[A-Za-z_][\w.-]*"

GRAMMAR = """\
PIPELINE: stages joined by " | ". Start with `where <filter>`, `@name` (a saved set) or `all`.
A stage sees only the set that came in. Item text never comes back unless `show` asks for it.

FILTER (in `where`): from:a,b  #tag/path  is:untagged|starred|unread|thin  type:video  has:link
  domain:github.com  folder:"Name"  saved:2026-03..  posted:30d..  likes:>100  lang:en  tags:0
  word "a phrase" prefix* ~fuzzy /regex/  in:title,body word   -negated  (a OR b)  @set
  after a jev stage: p:>=0.9  p:0.5..0.8  (any answer name)

NARROW/RANK  where <filter> | grep [-v] word ~fuzzy /re/ [in:title,body] | semantic "text" [top N]
  hybrid "text" [top N] (keyword+meaning, best 400 at most) | similar <id|@set> [top N] (50 nearest unless top N, or head N follows)
  rerank [voyage|jev] ["text"] [top N]  (paid, about $0.0005 with voyage: a second reader reorders the first 50 of the set
  by how well each item answers the text; without "text", the text of the hybrid/semantic stage before it. Use after
  `hybrid ... | head 50` for long or project-style questions. voyage is the default; jev reads literally, so not for misspelled text)
  sort <saved|posted|likes|views|len|score|p> [asc] | head N | tail N | skip N | sample N [seed]
JUDGE (paid, cached; Jev reads the first 2,000 characters of each item, literally: ask a full question
  that says what counts. A fragment scores near 0.5 for everything. 0.8 is the tested cut-off.)
  jev "yes/no question, spelled out" [>= p] [as name]      attaches p (0..1); >= keeps only those
  jev score "question" ["level0;level1;level2;level3"] [>= p] [as name]   4-level usefulness, 0..1
  score_labels [#subtree] [>= p] [apply]   each item against its 8 nearest tags; apply writes them
COMBINE      union @a | intersect @a | except @a
SUMMARISE    count | count by author|domain|tag|folder|month|year|source|type|lang [top N]
  stats [likes|views|len|duration|p] | terms [N] (stems distinctive to this set)
  groups [k] [as prefix] (embedding groups saved as @prefix1.. with size, cohesion, 3 samples)
SHOW         show id author date summary:80 body:200 title tags folder domain url score p
SAVE         as @name   (7 days from last use)
WRITE (last stage; over 25 items returns a preview and confirm token: call again with it)
  tag "A/B" [role primary|secondary|other|auto] | untag "A/B" | roles auto | star | unstar | read
  unread | archive | unarchive | list "name" | save view "name" (an existing name: preview, then confirm)

EXAMPLES
  all | count by author top 30
  where from:a,b,c | grep ~retriev | semantic "retrieval augmented generation" | head 200 | jev "about RAG / retrieval-augmented generation?" >= 0.9 | tag "AI/RAG"
  where is:untagged | groups 12 as u            then: @u3 | groups 6 as u3.
  @u3 | terms 12                                then: all | grep retriev* | except @u3 | sample 8 | show id text:60
  all | hybrid "which posts explain how to evaluate a retrieval pipeline" | head 50 | rerank voyage top 10 | show id summary:120 score
  all | semantic "vector databases" top 300 | jev "Is this about vector databases or embedding search?" as vdb | as @vdb
  @vdb | where vdb:0.5..0.8 | sample 12 | show id summary:90 vdb
  @vdb | where vdb:>=0.8 | tag "AI/Vector search"      (preview) then the same call with confirm="c_..."
  where id:1234 | show author date body:2000 url tags
"""


@dataclass
class Ctx:
    session: Session
    user_id: int
    text: str
    confirm: str | None = None
    run: LabelRun | None = None
    namespace: str = DEFAULT_NAMESPACE
    ids: list[int] = field(default_factory=list)
    scores: dict[int, float] = field(default_factory=dict)  # the last ranking score
    answers: dict[str, dict[int, float]] = field(default_factory=dict)  # Jev answers by name
    last_alias: str | None = None
    rank_text: str | None = None  # what the last `hybrid` or `semantic` stage ranked by: `rerank`'s default text
    out: list[str] = field(default_factory=list)  # body lines, in stage order
    rows: list[dict] = field(default_factory=list)  # what `show` produced, for the web app
    row_lines: tuple[int, int, int] | None = None  # (first line in `out`, rows shown, rows in set)
    spent: float = 0.0
    usd: float = 0.0  # of the stage running now
    confirm_out: str | None = None
    confirm_used: bool = False
    batch_id: int | None = None
    saved: str | None = None
    halted: bool = False
    source_query: str | None = None  # canonical filter of a `where` source, for `save view`
    stages: list[Stage] = field(default_factory=list)
    set_prefix: str = ""  # put before the name of every set this pipeline saves (Ask: "ask.")


def _in_set(ids: list[int]):
    return Item.id == func.any(ids)


def _filter(arg: str, offset: int):
    """Parse filter text, reporting errors at their place in the pipeline."""
    try:
        return parse(arg)
    except QueryError as exc:
        raise QueryError(exc.message, exc.position + offset) from None


def _conditions(ctx: Ctx, query, offset: int) -> list:
    try:
        return compile_filter(ctx.session, ctx.user_id, query, namespace=ctx.namespace)
    except QueryError as exc:
        raise QueryError(exc.message, exc.position + offset) from None


def _keep(ctx: Ctx, keep: set[int]) -> None:
    ctx.ids = [i for i in ctx.ids if i in keep]


def _quoted_text(ctx: Ctx, stage: Stage, toks: list[Tok], what: str) -> str:
    if not toks or not toks[0].text.strip():
        raise QueryError(f'{stage.name} needs {what} in quotes: {stage.name} "..."', stage.arg_pos if stage.arg else stage.pos)
    return toks[0].text


def _options(stage: Stage, toks: list[Tok], known: dict) -> dict:
    """`top 5`, `as name`, `>= 0.9` after a stage's main argument. `known`
    maps a keyword to the converter of its value."""
    out, i = {}, 0
    while i < len(toks):
        tok = toks[i]
        word = tok.text.lower()
        attached = re.fullmatch(r">=(.+)", tok.text)  # ">=0.9" written without a space
        if attached and ">=" in known and not tok.quoted:
            out[">="] = known[">="](Tok(attached.group(1), tok.pos + 2))
            i += 1
        elif word in known and not tok.quoted:
            if i + 1 >= len(toks):
                raise QueryError(f"{tok.text} needs a value", tok.pos)
            out[word] = known[word](toks[i + 1])
            i += 2
        else:
            raise QueryError(f'Unexpected "{tok.text}" in {stage.name}', tok.pos)
    return out


def _top(tok: Tok) -> int:
    return number(tok, int, 1)


def _threshold(tok: Tok) -> float:
    value = number(tok, float)
    if value > 1:
        raise QueryError("A threshold is between 0 and 1", tok.pos)
    return value


def _name(tok: Tok) -> str:
    name = tok.text.removeprefix("@")
    if not re.fullmatch(NAME_RE, name) or name.startswith(engine.SNAPSHOT_PREFIX):
        raise QueryError(f'"{tok.text}" is not a usable name (letters, digits, _ . -)', tok.pos)
    return name


def _alias(tok: Tok) -> str:
    name = _name(tok)
    if name.lower() in KEYS or name in ("score",) or not re.fullmatch(r"[A-Za-z_]\w*", name):
        raise QueryError(f'"{name}" cannot name an answer (it is a filter key or has punctuation)', tok.pos)
    return name


# --- Sets -------------------------------------------------------------------
def _scoped(ctx: Ctx, name: str) -> str:
    """The name a set is saved under: with the caller's prefix, once."""
    return name if name.startswith(ctx.set_prefix) else ctx.set_prefix + name


def _load_set(ctx: Ctx, name: str, pos: int) -> ResultSet:
    """A live set by name. A caller with a prefix reads its own `@name` first, then the user's."""
    row = None
    for wanted in dict.fromkeys((_scoped(ctx, name), name)):
        row = row or ctx.session.scalar(select(ResultSet).where(
            ResultSet.user_id == ctx.user_id, ResultSet.name == wanted,
            (ResultSet.expires_at.is_(None)) | (ResultSet.expires_at > func.now())))
    if row is None:
        raise QueryError(f"No result set named @{name} (sets expire {SET_TTL.days} days after last use)", pos)
    row.expires_at = datetime.now(UTC) + SET_TTL
    return row


def _live(ctx: Ctx, ids: list[int]) -> list[int]:
    """Drop ids whose item was deleted after the set was saved; keep the order."""
    alive = set(ctx.session.scalars(select(Item.id).where(Item.user_id == ctx.user_id, _in_set(ids))))
    return [i for i in ids if i in alive]


def save_set(ctx: Ctx, name: str, ids: list[int]) -> None:
    now = datetime.now(UTC)
    ctx.session.execute(delete(ResultSet).where(ResultSet.user_id == ctx.user_id, ResultSet.expires_at < now))
    row = ctx.session.scalar(select(ResultSet).where(ResultSet.user_id == ctx.user_id, ResultSet.name == name))
    if row is None:
        row = ResultSet(user_id=ctx.user_id, name=name)
        ctx.session.add(row)
    inside = set(ids)
    row.query, row.item_ids, row.expires_at = ctx.text, list(ids), now + SET_TTL
    row.scores = [ctx.scores.get(i, 0.0) for i in ids] if ctx.scores else None
    row.answers = {alias: {str(i): v for i, v in values.items() if i in inside} for alias, values in ctx.answers.items()} or None


def _adopt(ctx: Ctx, row: ResultSet) -> None:
    """Take a saved set's scores and answers along with its ids."""
    if row.scores:
        ctx.scores.update(zip(row.item_ids, row.scores))
    for alias, values in (row.answers or {}).items():
        ctx.answers.setdefault(alias, {}).update({int(i): v for i, v in values.items()})
        ctx.last_alias = alias


# --- Sources and narrowing --------------------------------------------------
def _answer_tests(ctx: Ctx, stage: Stage) -> tuple[str, list]:
    """Pull `p:>=0.9`, `rag:0.5..0.9` and `score:>0.4` out of a filter: they
    test values attached to the set, which the SQL filter knows nothing about.
    Returns the filter with those blanked (positions kept) and the tests."""
    tests = []

    def take(m: re.Match) -> str:
        name = m.group(2)
        values = ctx.scores if name == "score" else ctx.answers.get(name)
        if values is None or name.lower() in KEYS:
            return m.group(0)
        if m.group(5) is not None or m.group(6) is not None:
            lo, hi = float(m.group(5) or 0), float(m.group(6) or 1)
            test = lambda v: lo <= v <= hi  # noqa: E731
        else:
            op, x = m.group(3) or "=", float(m.group(4))
            test = {">=": lambda v: v >= x, ">": lambda v: v > x, "<=": lambda v: v <= x,
                    "<": lambda v: v < x, "=": lambda v: v == x}[op]
        tests.append((values, test, bool(m.group(1))))
        return " " * len(m.group(0))

    number_ = r"(?:\d+(?:\.\d+)?|\.\d+)"  # each digit can match in one place only, so a long digit string costs linear time
    rest = re.sub(rf"(?<!\S)(-?)(\w+):(?:(>=|<=|>|<)?({number_})(?!\.\.)|({number_})?\.\.({number_})?)(?!\S)", take, stage.arg)
    return rest, tests


def s_where(ctx: Ctx, stage: Stage, source: bool = False):
    rest, tests = _answer_tests(ctx, stage)
    query = _filter(rest, stage.arg_pos)
    if query.similar:
        raise QueryError("similar: is a stage here: ... | similar <id|@set>", stage.arg_pos)
    where = _conditions(ctx, query, stage.arg_pos)
    if source:
        sort = query.sort if query.sort and query.sort[0] not in ("relevance", "random") else None
        ctx.ids = list(ctx.session.scalars(select(Item.id).where(*where).order_by(*(order_by(sort) if sort else STABLE_ORDER))))
        ctx.source_query = to_string(query)
    elif rest.strip():
        _keep(ctx, set(ctx.session.scalars(select(Item.id).where(*where, _in_set(ctx.ids)))))
    for values, test, negated in tests:
        ctx.ids = [i for i in ctx.ids if (i in values and test(values[i])) != negated]


def s_all(ctx: Ctx, stage: Stage):
    if stage.arg:
        raise QueryError('"all" takes nothing; filter with: all | where ...', stage.arg_pos)
    s_where(ctx, Stage("where", "", stage.pos, stage.pos), source=True)


def s_set(ctx: Ctx, stage: Stage):
    row = _load_set(ctx, _name(Tok(stage.arg.strip(), stage.arg_pos)), stage.pos)
    ctx.ids = _live(ctx, list(row.item_ids))
    _adopt(ctx, row)


def _only_text(node, offset: int) -> None:
    if node is None or isinstance(node, Text):
        return
    if isinstance(node, (And, Or)):
        for child in node.children:
            _only_text(child, offset)
    elif isinstance(node, Not):
        _only_text(node.child, offset)
    else:
        raise QueryError("grep takes words, ~fuzzy, /regex/ and in:fields only; use where for qualifiers",
                         offset + getattr(node, "pos", 0))


def s_grep(ctx: Ctx, stage: Stage):
    invert = bool(re.match(r"-v(\s|$)", stage.arg))
    arg = (" " * 2 + stage.arg[2:]) if invert else stage.arg  # blanked, so error positions stay right
    if not arg.strip():
        raise QueryError("grep needs a word, ~fuzzy or /regex/", stage.pos)
    query = _filter(arg, stage.arg_pos)
    _only_text(query.filter, stage.arg_pos)
    query.include = frozenset({"archived", "context"})  # the set decides what is in scope, not grep
    found = set(ctx.session.scalars(select(Item.id).where(*_conditions(ctx, query, stage.arg_pos), _in_set(ctx.ids))))
    _keep(ctx, set(ctx.ids) - found if invert else found)


def _rank_by_vector(ctx: Ctx, vector, top: int | None, exclude: int | None = None) -> str | None:
    """Order the set by cosine similarity to a vector (exact, no index). Items
    with no embedding yet drop out and are counted in the stage note."""
    distance = ItemEmbedding.vector.cosine_distance(vector)
    stmt = (select(ItemEmbedding.item_id, distance).where(ItemEmbedding.model == DOC_MODEL, ItemEmbedding.item_id == func.any(ctx.ids))
            .order_by(distance, ItemEmbedding.item_id))
    rows = [(i, d) for i, d in ctx.session.execute(stmt) if i != exclude]
    missing = len(ctx.ids) - len(rows) - (1 if exclude in ctx.ids else 0)
    rows = rows[:top] if top else rows
    ctx.ids = [i for i, _ in rows]
    ctx.scores = {i: round(1 - d, 4) for i, d in rows}
    return f"no_vector={missing}" if missing > 0 else None


def s_semantic(ctx: Ctx, stage: Stage):
    toks = stage.toks()
    wanted = _quoted_text(ctx, stage, toks, "a description")
    top = _options(stage, toks[1:], {"top": _top}).get("top")
    ctx.rank_text = wanted
    try:
        vector = query_vector(ctx.session, ctx.user_id, wanted)
    except EmbeddingError as exc:
        raise QueryError(f"Semantic search is unavailable: {exc}", stage.pos) from None
    return _rank_by_vector(ctx, vector, top)


def s_hybrid(ctx: Ctx, stage: Stage):
    """The search bar's ranking inside the set: keyword and semantic rankers
    fused. ponytail: each ranker contributes engine.RANKER_TOP_N hits, so the
    result is at most twice that; for wider recall use `semantic "..." top N`
    and `union` it with a `grep`."""
    toks = stage.toks()
    wanted = _quoted_text(ctx, stage, toks, "search words")
    top = _options(stage, toks[1:], {"top": _top}).get("top")
    query = _filter(re.sub(r"[:()#@~/]", " ", wanted), toks[0].pos + 1)  # plain words, not filter syntax
    if not query.rank_text:
        raise QueryError("hybrid needs words to rank by", toks[0].pos)
    query.include = frozenset({"archived", "context"})
    ctx.rank_text = wanted
    ids, scores, notice = engine.rank(ctx.session, ctx.user_id, query, "hybrid", [_in_set(ctx.ids)])
    ctx.ids = ids[:top] if top else ids
    ctx.scores = dict(zip(ids, (round(s, 4) for s in scores)))
    return notice


def s_similar(ctx: Ctx, stage: Stage):
    toks = stage.toks()
    if not toks or not re.fullmatch(rf"\d+|@{NAME_RE}", toks[0].text):
        raise QueryError("similar needs an item id or a @set", stage.arg_pos if stage.arg else stage.pos)
    top = _options(stage, toks[1:], {"top": _top}).get("top")
    if top is None:  # the 50 nearest, unless the next stage is a `head N` that asks for more
        after = next((later for this, later in zip(ctx.stages, ctx.stages[1:]) if this is stage), None)
        top = max(engine.SIMILAR_TOP_N, _one_number(after) if after is not None and after.name == "head" else 0)
    target = toks[0].text
    if target.startswith("@"):
        target = "@" + _load_set(ctx, target[1:], toks[0].pos).name
    try:
        vector = engine._similar_vector(ctx.session, ctx.user_id, target)
    except QueryError as exc:
        raise QueryError(exc.message, toks[0].pos) from None
    return _rank_by_vector(ctx, vector, top, exclude=int(target) if target.isdigit() else None)


def s_rerank(ctx: Ctx, stage: Stage):
    """A paid second stage on the head of the set (kosha.search.rerank): the
    first RERANK_TOP_N items are reordered by how well each answers the text,
    the rest keep their place behind them. The scores replace `score`."""
    toks = stage.toks()
    method = "voyage"
    if toks and not toks[0].quoted and toks[0].text.lower() in ("voyage", "jev"):
        method, toks = toks[0].text.lower(), toks[1:]
    wanted = ctx.rank_text
    if toks and toks[0].quoted:
        wanted, toks = toks[0].text, toks[1:]
    top = _options(stage, toks, {"top": _top}).get("top")
    if not (wanted or "").strip():
        raise QueryError('rerank needs a text: rerank [voyage|jev] "what you are looking for" [top N] '
                         "(or put it after a hybrid or semantic stage, whose text it then uses)", stage.arg_pos if stage.arg else stage.pos)
    # Ask's allowance for the question. Jev checks its own estimate against it (jev.check_budget).
    # ponytail: a Voyage rerank of 50 items is about $0.0005 and its price is only known after the
    # call, so it is refused only once the allowance is used up; estimate from the texts if the head grows.
    allowance = jev.call_allowance.get()
    if method == "voyage" and allowance is not None and allowance <= 0:
        raise QueryError("rerank: this question has used up what it may spend; answer from the order you have", stage.pos)
    try:
        ids, scores, usd = reranking.rerank_with_cost(ctx.session, ctx.user_id, wanted, ctx.ids, method)
    except reranking.RerankError as exc:
        raise QueryError(f"rerank {method} is unavailable: {exc}", stage.pos) from None
    ctx.usd += usd
    ctx.ids = ids[:top] if top else ids
    ctx.scores = {i: round(s, 4) for i, s in scores.items()}
    return f"{method} judged={len(scores)}"


def s_sort(ctx: Ctx, stage: Stage):
    toks = stage.toks()
    if not toks or len(toks) > 2 or (len(toks) == 2 and toks[1].text.lower() not in ("asc", "desc")):
        raise QueryError("sort <key> [asc|desc]", stage.arg_pos if stage.arg else stage.pos)
    key, descending = toks[0].text, not (len(toks) == 2 and toks[1].text.lower() == "asc")
    values = ctx.scores if key in ("score", "relevance") else ctx.answers.get(key)
    if values is not None:  # in memory; items without a value go last
        have = sorted((i for i in ctx.ids if i in values), key=lambda i: values[i], reverse=descending)
        ctx.ids = have + [i for i in ctx.ids if i not in values]
    elif key == "random":
        random.Random(SAMPLE_SEED).shuffle(ctx.ids)
    elif key in SORT_KEYS:
        ctx.ids = list(ctx.session.scalars(select(Item.id).where(_in_set(ctx.ids)).order_by(*order_by((key, descending)))))
    else:
        options = [k for k in SORT_KEYS if k != "relevance"] + ["score", *ctx.answers]
        raise QueryError(f'Unknown sort "{key}". Use one of: {", ".join(options)}', toks[0].pos)


def _one_number(stage: Stage) -> int:
    toks = stage.toks()
    if len(toks) != 1:
        raise QueryError(f"{stage.name} needs one number: {stage.name} 50", stage.arg_pos if stage.arg else stage.pos)
    return number(toks[0], int)


def s_head(ctx: Ctx, stage: Stage):
    ctx.ids = ctx.ids[:_one_number(stage)]


def s_tail(ctx: Ctx, stage: Stage):
    n = _one_number(stage)
    ctx.ids = ctx.ids[-n:] if n else []


def s_skip(ctx: Ctx, stage: Stage):
    ctx.ids = ctx.ids[_one_number(stage):]


def s_sample(ctx: Ctx, stage: Stage):
    toks = [t for t in stage.toks() if t.text.lower() != "seed"]
    if not 1 <= len(toks) <= 2:
        raise QueryError("sample N [seed]", stage.arg_pos if stage.arg else stage.pos)
    n, seed = number(toks[0], int), number(toks[1], int) if len(toks) == 2 else SAMPLE_SEED
    picked = set(random.Random(seed).sample(ctx.ids, min(n, len(ctx.ids))))
    _keep(ctx, picked)


def _other_set(ctx: Ctx, stage: Stage) -> ResultSet:
    toks = stage.toks()
    if len(toks) != 1 or not toks[0].text.startswith("@"):
        raise QueryError(f"{stage.name} needs a saved set: {stage.name} @name", stage.arg_pos if stage.arg else stage.pos)
    return _load_set(ctx, toks[0].text[1:], toks[0].pos)


def s_union(ctx: Ctx, stage: Stage):
    row = _other_set(ctx, stage)
    have = set(ctx.ids)
    ctx.ids += [i for i in _live(ctx, list(row.item_ids)) if i not in have]
    _adopt(ctx, row)


def s_intersect(ctx: Ctx, stage: Stage):
    _keep(ctx, set(_other_set(ctx, stage).item_ids))


def s_except(ctx: Ctx, stage: Stage):
    _keep(ctx, set(ctx.ids) - set(_other_set(ctx, stage).item_ids))


# --- Judging ----------------------------------------------------------------
def _ask(ctx: Ctx, stage: Stage, pairs: list, operation: str) -> list | None:
    """Estimate, check the budgets, then ask. Returns the answers, or None
    when the call stopped to ask for a confirm token (nothing spent)."""
    est = jev.estimate(ctx.session, ctx.user_id, pairs)
    if est.to_ask > jev.JEV_CONFIRM_ITEMS:
        # Bound to the pipeline, to how many questions are new and to the estimate shown:
        # if the set or the cache changed since the preview, the token no longer fits.
        digest = writes.digest_of("spend", ctx.text, est.to_ask, f"{est.usd:.4f}")
        if not writes.confirm_ok(ctx.user_id, digest, ctx.confirm):
            ctx.confirm_out, ctx.halted = writes.confirm_token(ctx.user_id, digest), True
            ctx.out.append(f"needs_confirm: {stage.name} would ask Jev {est.to_ask} new questions, est=${est.usd:.4f}. "
                           f'Nothing spent. Call again with confirm="{ctx.confirm_out}".')
            return None
        ctx.confirm_used = True
    try:
        answers, spend = jev.ask(ctx.session, ctx.user_id, pairs, operation, ctx.run, estimate_usd=est.usd)
    except jev.cj.JevError as exc:
        # Requests that got through before the failure were billed and their
        # answers are cached; `jev.ask` left that money in the ledger.
        paid = exc.partial[1] if exc.partial else None
        if paid is not None and paid.usd:
            raise QueryError(f"{stage.name}: {exc} (${paid.usd:.4f} was spent on {paid.asked} answers that are now cached; "
                             "send the same call again to ask only the rest)", stage.pos) from None
        raise QueryError(f"{stage.name}: {exc}", stage.pos) from None
    except jev.BudgetError as exc:
        raise QueryError(f"{stage.name}: {exc}", stage.pos) from None
    ctx.usd += spend.usd
    return answers, f"est=${est.usd:.4f} asked={spend.asked} cached={spend.cached}"


def _bands(values) -> str:
    edges = (0.0, *ANSWER_BANDS, 1.0001)
    counts = [sum(lo <= v < hi for v in values) for lo, hi in zip(edges, edges[1:])]
    labels = [f"<{ANSWER_BANDS[0]}", *(f"{lo}-{hi}" for lo, hi in zip(ANSWER_BANDS, ANSWER_BANDS[1:])), f">={ANSWER_BANDS[-1]}"]
    return "  ".join(f"{label}:{n}" for label, n in zip(labels, counts))


def s_jev(ctx: Ctx, stage: Stage):
    toks = stage.toks()
    is_score = bool(toks) and not toks[0].quoted and toks[0].text.lower() == "score"
    toks = toks[1:] if is_score else toks
    if not toks or not toks[0].quoted or not toks[0].text.strip():
        raise QueryError('jev needs the question in quotes: jev "Is this about ...?" [>= 0.8] [as name]',
                         toks[0].pos if toks else (stage.arg_pos if stage.arg else stage.pos))
    levels, rest = jev.USEFULNESS_LEVELS, toks[1:]
    if is_score and rest and rest[0].quoted:  # custom level descriptions, low to high
        levels, rest = tuple(part.strip() for part in rest[0].text.split(";") if part.strip()), rest[1:]
        if not 2 <= len(levels) <= 10:
            raise QueryError("A score takes 2 to 10 levels, low to high, separated by ;", toks[1].pos)
    opts = _options(stage, rest, {">=": _threshold, "as": _alias})
    alias = opts.get("as", "p")
    question = Question(toks[0].text.strip(), levels=levels if is_score else ())
    texts = jev.item_texts(ctx.session, ctx.user_id, ctx.ids)
    skipped = len(ctx.ids) - len(texts)  # too little text to judge; they carry no answer
    asked = _ask(ctx, stage, [(question, i, texts[i]) for i in ctx.ids if i in texts], "jev")
    if asked is None:
        return None
    answers, note = asked
    values = {i: round(a.value, 4) for i, a in zip((i for i in ctx.ids if i in texts), answers)}
    ctx.answers[alias], ctx.last_alias = values, alias
    ctx.out.append(f"{alias}: {_bands(values.values())}")
    if ">=" in opts:
        ctx.ids = [i for i in ctx.ids if values.get(i, -1) >= opts[">="]]
    return note + (f" skipped_thin={skipped}" if skipped else "")


def _vectors(ctx: Ctx, ids: list[int]) -> tuple[list[int], np.ndarray]:
    """The embeddings of the set as a float32 matrix, in set order."""
    rows = dict(ctx.session.execute(text(
        "SELECT item_id, vector::real[] FROM item_embeddings WHERE model = :model AND item_id = ANY(:ids)"),
        {"model": DOC_MODEL, "ids": ids}).all())
    have = [i for i in ids if i in rows]
    return have, np.array([rows[i] for i in have], dtype=np.float32).reshape(len(have), -1)


def s_score_labels(ctx: Ctx, stage: Stage):
    """Constella's label round over the tags of this namespace: each item is
    asked about its nearest labels, and the assignment rule picks primary,
    secondary and others. Uses Kosha's one request layout and answer cache,
    so it shares answers with `jev` stages instead of Constella's own loop."""
    toks = stage.toks()
    subtree = toks.pop(0) if toks and toks[0].text.startswith("#") else None
    apply = bool(toks) and toks[-1].text.lower() == "apply" and not toks[-1].quoted
    accept = _options(stage, toks[:-1] if apply else toks, {">=": _threshold}).get(">=", jev.DEFAULT_THRESHOLD)
    paths = writes.tag_paths(ctx.session, ctx.user_id, ctx.namespace)
    root = None
    if subtree:
        root = writes.find_tag(ctx.session, ctx.user_id, ctx.namespace, subtree.text)
        if root is None:
            raise QueryError(f"No tag at {subtree.text} in namespace {ctx.namespace}", subtree.pos)
    # A parent without a definition is a container for its children, not a label
    # to ask about: its bare name ("AI") would pass for most of the library.
    containers = {tag.parent_id for tag, _ in paths.values() if tag.parent_id is not None}
    labels = [Label(str(tag.id), path, tag.description or tag.name) for tag, path in paths.values()
              if tag.status in ("approved", "proposed") and (tag.description or tag.id not in containers)
              and (root is None or str(tag.path) == str(root.path) or str(tag.path).startswith(f"{root.path}."))]
    if not labels:
        raise QueryError(f"No approved or proposed tags to score against in namespace {ctx.namespace}", stage.pos)
    ids, vectors = _vectors(ctx, ctx.ids)
    texts = jev.item_texts(ctx.session, ctx.user_id, ids)
    keep = [n for n, i in enumerate(ids) if i in texts]
    ids, vectors = [ids[n] for n in keep], vectors[keep]
    if not ids:
        ctx.out.append("score_labels: no item in the set has both an embedding and text")
        return None
    try:
        label_vectors, tokens = embed_texts([f"{label.path}: {label.definition}" for label in labels], QUERY_MODEL, "query")
    except EmbeddingError as exc:
        raise QueryError(f"score_labels cannot embed the tag definitions: {exc}", stage.pos) from None
    record_spend(ctx.session, ctx.user_id, QUERY_MODEL, "embed_labels", tokens, labels=len(labels))
    questions = [label_question(label) for label in labels]
    nearest = shortlist(vectors, np.array(label_vectors, dtype=np.float32), SHORTLIST_LABELS)
    pairs = [(questions[j], item_id, texts[item_id]) for row, item_id in enumerate(ids) for j in nearest[row]]
    cells = [(item_id, labels[j].id) for row, item_id in enumerate(ids) for j in nearest[row]]
    asked = _ask(ctx, stage, pairs, "score_labels")
    if asked is None:
        return None
    answers, note = asked
    path_of = {label.id: label.path for label in labels}
    scores: dict = {i: {} for i in ids}
    for (item_id, label_id), answer in zip(cells, answers):
        scores[item_id][label_id] = answer.value
    # A label and its own ancestor never both count: where both pass, the deeper one wins.
    for by_label in scores.values():
        passed = [lid for lid, p in by_label.items() if p >= accept]
        for lid in passed:
            if any(path_of[other].startswith(path_of[lid] + "/") for other in passed):
                del by_label[lid]
    assignments = assign(scores, accept)
    placed = [a for a in assignments.values() if not a.pending]
    hints = tree_hints(assignments, labels)
    sizes = sorted(hints["sizes"].items(), key=lambda kv: -kv[1])
    ctx.out.append(f"score_labels >= {accept}: placed {len(placed)} of {len(ids)}, "
                   f"with a second label {sum(1 for a in placed if a.secondary)}, pending {len(ids) - len(placed)}, "
                   f"labels {len(labels)} (empty {len(hints['empty'])})")
    ctx.out += [f"  {path_of[lid]}  {n}" for lid, n in sizes[:10] if n]
    ctx.out += [f"  too broad: {path_of[lid]} holds {share:.0%}" for lid, share in hints["broad"]]
    ctx.out += [f"  overlap: {path_of[a]} / {path_of[b]} share {shared}" for a, b, shared, _ in hints["overlap"][:5]]
    ctx.answers["p"], ctx.last_alias = {i: round(a.primary[1], 4) for i, a in assignments.items() if a.primary}, "p"
    if apply:
        plan = [(a.item_id, int(label_id), role, round(p, 4)) for a in placed
                for role, (label_id, p) in (("primary", a.primary), *((("secondary", a.secondary),) if a.secondary else ()),
                                            *(("other", o) for o in a.others))]
        target = sorted({p[0] for p in plan})
        if _gate(ctx, stage, target, f"{len(plan)} tag rows on {len(target)} items"):
            counts, ctx.batch_id = writes.apply_tags(ctx.session, ctx.user_id, ctx.namespace, plan)
            ctx.out.append(_done("score_labels apply", counts, ctx.batch_id))
    return note


# --- Summaries --------------------------------------------------------------
_COUNT_SQL = {
    "author": "SELECT coalesce('@' || i.author_handle, i.author_name, '(none)') AS v, count(*) AS n FROM items i WHERE i.id = ANY(:ids) GROUP BY 1",
    "source": "SELECT i.source AS v, count(*) AS n FROM items i WHERE i.id = ANY(:ids) GROUP BY 1",
    "lang": "SELECT coalesce(i.lang, '(none)') AS v, count(*) AS n FROM items i WHERE i.id = ANY(:ids) GROUP BY 1",
    "type": "SELECT t AS v, count(*) AS n FROM items i, unnest(i.content_types) t WHERE i.id = ANY(:ids) GROUP BY 1",
    "month": "SELECT to_char(coalesce(i.saved_at, i.saved_at_est, i.captured_at), 'YYYY-MM') AS v, count(*) AS n FROM items i WHERE i.id = ANY(:ids) GROUP BY 1",
    "year": "SELECT to_char(coalesce(i.saved_at, i.saved_at_est, i.captured_at), 'YYYY') AS v, count(*) AS n FROM items i WHERE i.id = ANY(:ids) GROUP BY 1",
    "domain": "SELECT l.domain AS v, count(DISTINCT l.item_id) AS n FROM item_links l WHERE l.item_id = ANY(:ids) AND l.domain IS NOT NULL GROUP BY 1",
    "folder": "SELECT c.name AS v, count(*) AS n FROM item_collections ic JOIN collections c ON c.id = ic.collection_id WHERE ic.item_id = ANY(:ids) GROUP BY 1",
    "tag": "SELECT it.tag_id::text AS v, count(*) AS n FROM item_tags it WHERE it.item_id = ANY(:ids) AND NOT it.removed_by_user AND it.namespace = :ns GROUP BY 1",
}


def s_count(ctx: Ctx, stage: Stage):
    toks = stage.toks()
    if not toks:
        ctx.out.append(f"count: {len(ctx.ids)}")
        return None
    if toks[0].text.lower() != "by" or len(toks) < 2 or toks[1].text.lower() not in COUNT_DIMS:
        raise QueryError(f"count by one of: {', '.join(COUNT_DIMS)}", (toks[1] if len(toks) > 1 else toks[0]).pos)
    dim = toks[1].text.lower()
    top = _options(stage, toks[2:], {"top": _top}).get("top", COUNT_DEFAULT_TOP)
    order = "v DESC" if dim in ("month", "year") else "n DESC, v"
    rows = ctx.session.execute(text(f"SELECT v, n, count(*) OVER () AS total FROM ({_COUNT_SQL[dim]}) x ORDER BY {order} LIMIT :top"),
                               {"ids": ctx.ids, "ns": ctx.namespace, "top": top}).all()
    names = {str(tag.id): path for tag, path in writes.tag_paths(ctx.session, ctx.user_id, ctx.namespace).values()} if dim == "tag" else {}
    ctx.out.append(f"count by {dim}:")
    ctx.out += [f"  {names.get(v, v)}  {n}" for v, n, _ in rows]
    if rows and rows[0].total > len(rows):
        ctx.out.append(f"  ... {rows[0].total - len(rows)} more values (top {rows[0].total})")
    return None


def _summary(values: list[float]) -> str:
    if not values:
        return "n=0"
    fmt = lambda v: f"{v:.2f}" if isinstance(v, float) and not float(v).is_integer() else f"{int(v)}"  # noqa: E731
    return f"n={len(values)} min={fmt(min(values))} median={fmt(statistics.median(values))} max={fmt(max(values))} sum={fmt(sum(values))}"


def s_stats(ctx: Ctx, stage: Stage):
    toks = stage.toks()
    if len(toks) > 1:
        raise QueryError("stats takes one field at most", toks[1].pos)
    if toks:
        name = toks[0].text
        values = ctx.scores if name == "score" else ctx.answers.get(name)
        if values is not None:
            ctx.out.append(f"stats {name}: {_summary([values[i] for i in ctx.ids if i in values])}")
            ctx.out.append(f"  {_bands([values[i] for i in ctx.ids if i in values])}")
        elif name in STAT_FIELDS:
            found = ctx.session.scalars(text(f"SELECT {STAT_FIELDS[name]} FROM items WHERE id = ANY(:ids)"), {"ids": ctx.ids})
            ctx.out.append(f"stats {name}: {_summary([v for v in found if v is not None])}")
        else:
            raise QueryError(f"stats of one of: {', '.join([*STAT_FIELDS, 'score', *ctx.answers])}", toks[0].pos)
        return None
    row = ctx.session.execute(text("""
        SELECT count(*) AS n, count(DISTINCT i.author_handle) AS authors,
          min(coalesce(i.saved_at, i.saved_at_est, i.captured_at))::date AS first,
          max(coalesce(i.saved_at, i.saved_at_est, i.captured_at))::date AS last,
          coalesce(percentile_cont(0.5) WITHIN GROUP (ORDER BY char_length(coalesce(i.body, ''))), 0)::int AS median_len,
          count(*) FILTER (WHERE EXISTS (SELECT 1 FROM item_tags t WHERE t.item_id = i.id AND t.namespace = :ns
                                           AND t.role = 'primary' AND NOT t.removed_by_user)) AS with_primary,
          count(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM item_tags t WHERE t.item_id = i.id AND t.namespace = :ns
                                               AND NOT t.removed_by_user)) AS untagged,
          count(*) FILTER (WHERE EXISTS (SELECT 1 FROM item_embeddings e WHERE e.item_id = i.id)) AS embedded,
          count(*) FILTER (WHERE i.starred) AS starred, count(*) FILTER (WHERE i.read_at IS NULL) AS unread
        FROM items i WHERE i.id = ANY(:ids)"""), {"ids": ctx.ids, "ns": ctx.namespace}).one()
    share = f"{row.with_primary / row.n:.0%}" if row.n else "0%"
    ctx.out.append(f"stats: n={row.n} authors={row.authors} saved={row.first}..{row.last} median_len={row.median_len} "
                   f"with_primary={row.with_primary} ({share}) untagged={row.untagged} embedded={row.embedded} "
                   f"starred={row.starred} unread={row.unread}")
    for alias, values in ctx.answers.items():
        ctx.out.append(f"  {alias}: {_summary([values[i] for i in ctx.ids if i in values])}")
    return None


_library_terms: dict[int, tuple[float, int, dict[str, int]]] = {}  # user -> (time, items, stem -> items containing it)


def _stem_counts(ctx: Ctx, where_sql: str) -> dict[str, int]:
    """For each stem, how many of the selected items contain it (Postgres `ts_stat`)."""
    inner = f"SELECT tsv FROM items WHERE {where_sql}".replace("'", "''")
    return dict(ctx.session.execute(text(f"SELECT word, ndoc FROM ts_stat('{inner}', '{TERMS_WEIGHTS}')")).all())


def s_terms(ctx: Ctx, stage: Stage):
    """Stems distinctive to this set against the library, ranked by a
    smoothed log-odds z-score, so a stem must be both over-represented and
    frequent enough to trust. Feed one back as `grep stem*`."""
    toks = [t for t in stage.toks() if t.text.lower() != "top"]
    if len(toks) > 1:
        raise QueryError("terms [N]", toks[1].pos)
    top = number(toks[0], int, 1) if toks else TERMS_DEFAULT_TOP
    cached = _library_terms.get(ctx.user_id)
    if cached is None or time.monotonic() - cached[0] > TERMS_LIBRARY_TTL_S:
        scope = f"user_id = {int(ctx.user_id)} AND is_saved AND archived_at IS NULL"
        total = ctx.session.scalar(text(f"SELECT count(*) FROM items WHERE {scope}"))
        cached = _library_terms[ctx.user_id] = (time.monotonic(), total, _stem_counts(ctx, scope))
    _, lib_n, lib = cached
    set_n, rest_n = len(ctx.ids), lib_n - len(ctx.ids)
    if not ctx.ids or rest_n <= 0:
        ctx.out.append("terms: the set is empty or is the whole library; terms compares a subset with the rest")
        return None
    mine = _stem_counts(ctx, "id = ANY('{%s}'::bigint[])" % ",".join(str(int(i)) for i in ctx.ids))
    ranked = []
    for stem, a in mine.items():
        if a < TERMS_MIN_ITEMS or len(stem) < 3 or stem.isdigit() or "/" in stem or stem.startswith("http"):
            continue
        b = max(0, lib.get(stem, a) - a)  # items outside the set that contain the stem
        odds = math.log((a + 0.5) / (set_n - a + 0.5)) - math.log((b + 0.5) / (max(rest_n - b, 0) + 0.5))
        variance = 1 / (a + 0.5) + 1 / (set_n - a + 0.5) + 1 / (b + 0.5) + 1 / (max(rest_n - b, 0) + 0.5)
        ranked.append((odds / math.sqrt(variance), stem, a, a + b))
    ranked.sort(key=lambda r: (-r[0], r[1]))
    ctx.out.append("terms (stem  z  in_set  in_library):")
    ctx.out += [f"  {stem}  {z:.1f}  {a}  {n}" for z, stem, a, n in ranked[:top]]
    return None


def s_groups(ctx: Ctx, stage: Stage):
    """Embedding groups (Constella `group`), each saved as a set. A group is a
    way to select items, never a label: name it, then check its items with jev."""
    toks = stage.toks()
    k = None
    if toks and toks[0].text.isdigit():
        k = number(toks.pop(0), int, 1)
        if k > GROUPS_MAX_K:
            raise QueryError(f"At most {GROUPS_MAX_K} groups", stage.arg_pos)
    prefix = _scoped(ctx, _options(stage, toks, {"as": _name}).get("as", "g"))
    ids, vectors = _vectors(ctx, ctx.ids)
    if not ids:
        ctx.out.append("groups: no item in the set has an embedding yet (jobs: embed)")
        return None
    groups = group(vectors, ids, k=k or min(GROUPS_DEFAULT_K, choose_k(len(ids))))
    samples = _texts(ctx, [i for g in groups for i in g.central], GROUP_SAMPLE_CHARS)
    ctx.out.append(f"groups of {len(ids)} items ({len(ctx.ids) - len(ids)} without an embedding left out):")
    for g in groups:
        name = f"{prefix}{g.id + 1}"
        save_set(ctx, name, g.ids)
        ctx.out.append(f"@{name} n={g.size} cohesion={g.cohesion:.2f}")
        ctx.out += [f"  - {samples.get(i, '')}" for i in g.central]
    return f"saved @{prefix}1..@{prefix}{len(groups)}"


# --- Rows -------------------------------------------------------------------
def _cut(value, chars: int | None) -> str:
    value = " ".join(str(value if value is not None else "").split())
    return value if not chars or len(value) <= chars else value[:chars].rstrip() + "…"


def _texts(ctx: Ctx, ids: list[int], chars: int) -> dict[int, str]:
    rows = ctx.session.execute(select(Item.id, Item.title, Item.body).where(_in_set(ids)))
    return {i: _cut(item_text(title, body), chars) for i, title, body in rows}


def _rows(ctx: Ctx, ids: list[int], fields: list[tuple[str, int | None]]) -> list[dict]:
    """The asked-for fields of each item, cut to the asked-for length."""
    items = {i.id: i for i in ctx.session.scalars(select(Item).where(Item.user_id == ctx.user_id, _in_set(ids)))}
    names = {name for name, _ in fields}
    extra: dict[str, dict[int, list[str]]] = {}
    if "tags" in names:
        paths = writes.tag_paths(ctx.session, ctx.user_id, ctx.namespace)
        extra["tags"] = {}
        for item_id, tag_id, role in ctx.session.execute(text(
                "SELECT item_id, tag_id, role FROM item_tags WHERE item_id = ANY(:ids) AND namespace = :ns AND NOT removed_by_user "
                "ORDER BY array_position(ARRAY['primary','secondary','other'], role)"), {"ids": ids, "ns": ctx.namespace}):
            if tag_id in paths:
                extra["tags"].setdefault(item_id, []).append(paths[tag_id][1] + ("*" if role == "primary" else ""))
    for name, sql in (("folder", "SELECT ic.item_id, c.name FROM item_collections ic JOIN collections c ON c.id = ic.collection_id WHERE ic.item_id = ANY(:ids)"),
                      ("domain", "SELECT DISTINCT item_id, domain FROM item_links WHERE item_id = ANY(:ids) AND domain IS NOT NULL")):
        if name in names:
            extra[name] = {}
            for item_id, value in ctx.session.execute(text(sql), {"ids": ids}):
                extra[name].setdefault(item_id, []).append(value)

    def value(item: Item, name: str):
        if name in extra:
            return ", ".join(extra[name].get(item.id, []))
        if name == "author":
            return f"@{item.author_handle}" if item.author_handle else (item.author_name or "")
        if name == "date":
            when = item.saved_at or item.saved_at_est or item.posted_at or item.captured_at
            return when.date().isoformat() if when else ""
        if name == "text":
            return item_text(item.title, item.body)
        if name == "score":
            return ctx.scores.get(item.id, "")
        if name in ctx.answers:
            return ctx.answers[name].get(item.id, "")
        return getattr(item, name)

    return [{name: (value(items[i], name) if name in ("id", "score") or name in ctx.answers
                    else _cut(value(items[i], name), chars or SHOW_DEFAULT_CHARS.get(name)))
             for name, chars in fields} for i in ids if i in items]


def _lines(rows: list[dict]) -> list[str]:
    return [" | ".join(str(v) for v in row.values()) for row in rows]


def _fields(ctx: Ctx, specs: list[Tok]) -> list[tuple[str, int | None]]:
    fields = []
    for tok in specs:
        for part in filter(None, tok.text.split(",")):
            name, _, chars = part.partition(":")
            if name not in SHOW_FIELDS and name not in ctx.answers:
                close = difflib.get_close_matches(name, [*SHOW_FIELDS, *ctx.answers], 1)
                raise QueryError(f'Unknown field "{name}"' + (f'; did you mean "{close[0]}"?' if close else
                                 f". Fields: {', '.join([*SHOW_FIELDS, *ctx.answers])}"), tok.pos)
            if chars and not chars.isdigit():
                raise QueryError(f'"{chars}" is not a length: {name}:80', tok.pos)
            fields.append((name, int(chars) if chars else None))
    return fields


def s_show(ctx: Ctx, stage: Stage):
    toks = stage.toks()
    if not toks:
        raise QueryError(f"show needs fields: show id author summary:80 (fields: {', '.join(SHOW_FIELDS)})", stage.pos)
    rows = _rows(ctx, ctx.ids[:SHOW_MAX_ROWS], _fields(ctx, toks))
    ctx.rows = rows
    ctx.row_lines = (len(ctx.out), len(rows), len(ctx.ids))
    ctx.out += _lines(rows)
    return None


def s_as(ctx: Ctx, stage: Stage):
    toks = stage.toks()
    if len(toks) != 1 or not toks[0].text.startswith("@"):
        raise QueryError("as @name", stage.arg_pos if stage.arg else stage.pos)
    ctx.saved = _scoped(ctx, _name(toks[0]))
    save_set(ctx, ctx.saved, ctx.ids)
    return f"saved @{ctx.saved}"


# --- Writes -----------------------------------------------------------------
def _gate(ctx: Ctx, stage: Stage, ids: list[int], what: str) -> bool:
    """True when the write may be applied now. Otherwise the response becomes
    a preview with a confirm token bound to this stage and this exact set."""
    if len(ids) <= writes.WRITE_DIRECT_MAX:
        return True
    digest = writes.digest_of(stage.raw, ctx.namespace, ",".join(map(str, sorted(ids))))
    if writes.confirm_ok(ctx.user_id, digest, ctx.confirm):
        return True
    stale = ctx.confirm and not ctx.confirm_used
    ctx.confirm_out, ctx.halted = writes.confirm_token(ctx.user_id, digest), True
    ctx.out.append(f"PREVIEW {stage.raw} -> {what}. Nothing written.")
    if stale:
        ctx.out.append("The confirm token sent does not match this write (it expired, or the set changed since the preview).")
    ctx.out += [f"  {line}" for line in _lines(_rows(ctx, ids[:PREVIEW_SAMPLES], _fields(ctx, [Tok(f, 0) for f in DEFAULT_ROW_FIELDS])))]
    ctx.out.append(f'To apply, call again with confirm="{ctx.confirm_out}" (valid {writes.CONFIRM_TTL_S // 60} min).')
    return False


def _done(what: str, counts: dict, batch_id: int) -> str:
    return f"{what}: " + " ".join(f"{k}={v}" for k, v in counts.items()) + f" batch={batch_id} (undo: runs undo {batch_id})"


def s_tag(ctx: Ctx, stage: Stage):
    toks = stage.toks()
    if not toks:
        raise QueryError('tag needs a path: tag "AI/RAG" [role primary|secondary|other|auto]', stage.pos)
    role = _options(stage, toks[1:], {"role": lambda t: t.text.lower()}).get("role")
    if role not in (None, "auto", *writes.ROLES):
        raise QueryError("role is one of: primary, secondary, other, auto", toks[2].pos)
    path = toks[0].text.strip("#/ ")
    tag = writes.find_tag(ctx.session, ctx.user_id, ctx.namespace, path)
    counts = writes.classify(ctx.session, ctx.ids, tag)
    new_tag = " The tag is new: it is created as proposed, for you to approve." if tag is None else ""
    proposed = (" Over %d items: the tag goes to proposed until approved." % writes.WRITE_PROPOSE_ABOVE
                if tag is not None and tag.origin == writes.AGENT and len(ctx.ids) > writes.WRITE_PROPOSE_ABOVE else "")
    summary = f"{len(ctx.ids)} items: " + " ".join(f"{k}={v}" for k, v in counts.items()) + new_tag + proposed
    if not ctx.ids or not _gate(ctx, stage, ctx.ids, summary):
        return None
    if tag is None:
        tag = writes.create_tag_path(ctx.session, ctx.user_id, ctx.namespace, path)
    elif proposed:
        tag.status = "proposed"
    confidence = ctx.answers.get(ctx.last_alias, {})
    plan = [(i, tag.id, None if role == "auto" else role, confidence.get(i)) for i in ctx.ids]
    counts, ctx.batch_id = writes.apply_tags(ctx.session, ctx.user_id, ctx.namespace, plan, auto_roles=role == "auto")
    ctx.out.append(_done(f'tag "{path}" ({tag.status})', counts, ctx.batch_id))
    return None


def s_untag(ctx: Ctx, stage: Stage):
    toks = stage.toks()
    if len(toks) != 1:
        raise QueryError('untag needs one path: untag "AI/RAG"', stage.arg_pos if stage.arg else stage.pos)
    tag = writes.find_tag(ctx.session, ctx.user_id, ctx.namespace, toks[0].text)
    if tag is None:
        raise QueryError(f'No tag at "{toks[0].text}" in namespace {ctx.namespace}', toks[0].pos)
    if ctx.ids and _gate(ctx, stage, ctx.ids, f"{len(ctx.ids)} items (only rows an agent attached are removed)"):
        counts, ctx.batch_id = writes.remove_tags(ctx.session, ctx.user_id, ctx.ids, tag)
        ctx.out.append(_done(stage.raw, counts, ctx.batch_id))


def s_roles(ctx: Ctx, stage: Stage):
    if stage.arg.strip().lower() != "auto":
        raise QueryError("roles auto", stage.arg_pos if stage.arg else stage.pos)
    if ctx.ids and _gate(ctx, stage, ctx.ids, f"{len(ctx.ids)} items: primary/secondary/other from confidence, agent-attached tags only"):
        counts, ctx.batch_id = writes.auto_roles(ctx.session, ctx.user_id, ctx.ids, ctx.namespace)
        ctx.out.append(_done("roles auto", counts, ctx.batch_id))


def s_state(ctx: Ctx, stage: Stage):
    if stage.arg:
        raise QueryError(f"{stage.name} takes nothing", stage.arg_pos)
    if ctx.ids and _gate(ctx, stage, ctx.ids, f"{len(ctx.ids)} items"):
        counts, ctx.batch_id = writes.set_state(ctx.session, ctx.user_id, ctx.ids, stage.name)
        ctx.out.append(_done(stage.name, counts, ctx.batch_id))


def s_list(ctx: Ctx, stage: Stage):
    toks = stage.toks()
    if len(toks) != 1 or not toks[0].text.strip():
        raise QueryError('list needs a name: list "To read"', stage.arg_pos if stage.arg else stage.pos)
    if ctx.ids and _gate(ctx, stage, ctx.ids, f"{len(ctx.ids)} items appended to the list"):
        counts, ctx.batch_id = writes.add_to_list(ctx.session, ctx.user_id, ctx.ids, toks[0].text.strip())
        ctx.out.append(_done(stage.raw, counts, ctx.batch_id))


def s_save(ctx: Ctx, stage: Stage):
    """`save view "name"`: keeps the pipeline's filter (and sort) as a saved
    view. A view is a live filter, so only `where ... [| sort ...]` can be one."""
    toks = stage.toks()
    if len(toks) != 2 or toks[0].text.lower() != "view" or not toks[1].text.strip():
        raise QueryError('save view "name"', stage.arg_pos if stage.arg else stage.pos)
    middle = ctx.stages[1:-1]
    if ctx.source_query is None or len(middle) > 1 or (middle and (middle[0].name != "sort" or middle[0].toks()[0].text not in SORT_KEYS)):
        raise QueryError('A saved view is a filter and a sort only: where <filter> [| sort <key>] | save view "name". '
                         "To keep a set of items, use: list \"name\"", stage.pos)
    query = ctx.source_query
    if middle:
        sort = middle[0].toks()
        query = f"{query} sort:{sort[0].text}" + ("-asc" if len(sort) == 2 and sort[1].text.lower() == "asc" else "")
    name = toks[1].text.strip()
    view = ctx.session.scalar(select(SavedView).where(SavedView.user_id == ctx.user_id, SavedView.name == name))
    if view is None:
        view = SavedView(user_id=ctx.user_id, name=name)
        ctx.session.add(view)
    elif view.query != query.strip():
        # Replacing someone's saved view needs a confirm token bound to the old and the new filter.
        digest = writes.digest_of(stage.raw, view.query, query.strip())
        if not writes.confirm_ok(ctx.user_id, digest, ctx.confirm):
            ctx.confirm_out, ctx.halted = writes.confirm_token(ctx.user_id, digest), True
            ctx.out.append(f'PREVIEW {stage.raw}: a view with this name exists ({view.query or "(everything)"}) and would be '
                           f'replaced by: {query.strip() or "(everything)"}. Nothing written.')
            ctx.out.append(f'To replace it, call again with confirm="{ctx.confirm_out}" (valid {writes.CONFIRM_TTL_S // 60} min), or pick another name.')
            return
    view.query = query.strip()
    ctx.out.append(f'saved view "{name}": {view.query or "(everything)"}')


STAGES = {
    "where": s_where, "grep": s_grep, "semantic": s_semantic, "hybrid": s_hybrid, "similar": s_similar, "rerank": s_rerank, "sort": s_sort,
    "head": s_head, "tail": s_tail, "skip": s_skip, "sample": s_sample, "jev": s_jev, "score_labels": s_score_labels,
    "union": s_union, "intersect": s_intersect, "except": s_except, "count": s_count, "stats": s_stats, "terms": s_terms,
    "groups": s_groups, "show": s_show, "as": s_as, "tag": s_tag, "untag": s_untag, "roles": s_roles, "list": s_list,
    "save": s_save, **{name: s_state for name in writes.STATE_WRITES},
}


def _is_write(stage: Stage) -> bool:
    return stage.name in WRITE_STAGES or (stage.name == "score_labels" and stage.arg.rstrip().lower().endswith("apply"))


def _render(ctx: Ctx, trace: list[dict]) -> str:
    """Header, one trace line, then the body, cut at whole lines to stay under
    the response cap; a cut inside `show` rows says how to get the rest."""
    head = f"n={len(ctx.ids)} spent=${ctx.spent:.4f}"
    if ctx.run is not None:
        budget = jev.run_budget(load_settings(ctx.session, ctx.user_id), (ctx.run.params or {}).get("budget_usd"))
        head += f" run={ctx.run.id} ${jev.run_spend(ctx.session, ctx.user_id, ctx.run.id):.4f}/${budget:.2f}"
    if ctx.saved:
        head += f" saved=@{ctx.saved} ({SET_TTL.days}d)"
    steps = " | ".join(f"{t['stage']} {t['in']}→{t['out']}" + (f" ${t['usd']:.4f}" if t["usd"] else "")
                       + (f" ({t['note']})" if t["note"] else "") for t in trace)
    lines = [head, steps]
    budget = RESPONSE_MAX_TOKENS * CHARS_PER_TOKEN - len(head) - len(steps) - 120  # room for the closing hint
    for n, line in enumerate(ctx.out):
        budget -= len(line) + 1
        if budget < 0:
            if ctx.row_lines and ctx.row_lines[0] <= n:
                shown = n - ctx.row_lines[0]
                ctx.rows = ctx.rows[:shown]
                lines.append(f'showing {shown} of {ctx.row_lines[2]}; more: add "| skip {shown}" before show')
            else:
                lines.append("(cut at the response cap; narrow with head / top N, or ask for fewer fields)")
            return "\n".join(lines)
        lines.append(line)
    if ctx.row_lines and ctx.row_lines[1] < ctx.row_lines[2]:
        lines.append(f'showing {ctx.row_lines[1]} of {ctx.row_lines[2]}; more: add "| skip {ctx.row_lines[1]}" before show')
    return "\n".join(lines)


def run_query(session: Session, user_id: int, pipeline: str, confirm: str | None = None, run_id: int | None = None,
              set_prefix: str = "") -> dict:
    """Run one pipeline and commit. Raises `QueryError` (message, position)
    for anything wrong with the text. Returns `text` (what an agent reads)
    plus the same facts as fields for the web app. With `set_prefix` (Ask),
    every set the pipeline saves gets that prefix, so it cannot replace a set
    saved elsewhere."""
    pipeline = (pipeline or "").strip()
    if pipeline.lower() in ("help", "?"):
        return {"text": GRAMMAR, "n": 0, "stages": [], "spent_usd": 0.0}
    if len(pipeline) > PIPELINE_MAX_CHARS:
        raise QueryError(f"A pipeline is at most {PIPELINE_MAX_CHARS} characters; save a long id list as a set first (as @name)", PIPELINE_MAX_CHARS)
    ctx = Ctx(session, user_id, pipeline, confirm, set_prefix=set_prefix)
    if run_id is not None:
        ctx.run = session.get(LabelRun, run_id)
        if ctx.run is None or ctx.run.user_id != user_id or ctx.run.status != "running":
            raise QueryError(f"No running label run {run_id}", 0)
        ctx.namespace = ctx.run.namespace
    stages = ctx.stages = split(pipeline)
    first = stages[0]
    if first.name not in ("where", "@", "all"):
        raise QueryError('A pipeline starts with "where <filter>", "@name" or "all"', first.pos)
    for stage in stages[1:]:
        if stage.name not in STAGES:
            close = difflib.get_close_matches(stage.name, STAGES, 1)
            raise QueryError(f'Unknown stage "{stage.name}"' + (f'; did you mean "{close[0]}"?' if close else '. Send "help" for the grammar.'), stage.pos)
    for stage in stages[:-1]:
        if _is_write(stage):
            raise QueryError(f"{stage.name} writes, so it must be the last stage", stage.pos)
    trace = []
    for n, stage in enumerate(stages):
        entered, started, ctx.usd = len(ctx.ids), time.perf_counter(), 0.0
        if n and not entered and stage.name in WRITE_STAGES and stage.name != "save":
            ctx.out.append(f"{stage.name}: nothing to write, the set is empty")
        if n == 0:
            note = {"where": lambda: s_where(ctx, stage, source=True), "@": lambda: s_set(ctx, stage), "all": lambda: s_all(ctx, stage)}[stage.name]()
        else:
            note = STAGES[stage.name](ctx, stage)
        ctx.spent += ctx.usd
        trace.append({"stage": "@" + stage.arg.strip() if stage.name == "@" else stage.name, "in": entered, "out": len(ctx.ids),
                      "usd": round(ctx.usd, 6), "ms": round((time.perf_counter() - started) * 1000), "note": note})
        if ctx.halted:
            break
    if not ctx.out and not ctx.halted:  # no stage asked for anything: a few compact rows
        ctx.out = _lines(_rows(ctx, ctx.ids[:DEFAULT_ROWS], _fields(ctx, [Tok(f, 0) for f in DEFAULT_ROW_FIELDS])))
    session.commit()
    return {"text": _render(ctx, trace), "n": len(ctx.ids), "stages": trace, "spent_usd": round(ctx.spent, 6),
            "rows": ctx.rows, "set": ctx.saved, "confirm": ctx.confirm_out, "batch_id": ctx.batch_id}
