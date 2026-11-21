"""Item summaries, rule-built for now (DESIGN.md section 6).

Short items are their own summary. Longer ones get the title, the first few
sentences and one metadata line. `llm_summary` is the slot for the LLM recipe
that will be compared against these later.
"""
import re

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from kosha.models import Item, ItemLink
from kosha.search.index import reindex

OWN_TEXT_MAX_CHARS = 400  # at or under this, the text is the summary
RULE_SENTENCES = 3
RULE_LEAD_MAX_CHARS = 450  # cap on the sentence part, for run-on text
METHOD_OWN_TEXT, METHOD_RULE, METHOD_LLM = "own_text", "rule", "llm"
RULE_METHODS = (METHOD_OWN_TEXT, METHOD_RULE)  # summaries this module may overwrite


def item_text(title: str | None, body: str | None) -> str:
    title, body = (title or "").strip(), (body or "").strip()
    return body if not title or body.startswith(title) else f"{title}\n{body}".strip()


def rule_summary(item: Item, domains: list[str]) -> tuple[str | None, str | None]:
    """(summary, method) for one item; (None, None) when it has no text."""
    text = item_text(item.title, item.body)
    if not text:
        return None, None
    if len(text) <= OWN_TEXT_MAX_CHARS:
        return text, METHOD_OWN_TEXT
    body = (item.body or "").strip()
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", body) if s.strip()]
    lead = " ".join(sentences[:RULE_SENTENCES])[:RULE_LEAD_MAX_CHARS].rstrip()
    author = " ".join(p for p in (item.author_name, f"@{item.author_handle}" if item.author_handle else None) if p)
    meta = " · ".join(p for p in (author, item.source, f"{len(body):,} chars",
                                  ", ".join(item.content_types or []), ", ".join(domains[:4])) if p)
    title = (item.title or "").strip()
    return "\n".join(p for p in (title if title and not lead.startswith(title) else "", lead, f"[{meta}]") if p), METHOD_RULE


def llm_summary(item: Item) -> str:
    """Slot for the LLM recipe (SEARCH_DESIGN.md D.8). Not built: the recipes
    are to be compared on real bookmarks first."""
    raise NotImplementedError


def summarize(session: Session, user_id: int) -> dict:
    """Write rule-built summaries for every item that has none or whose text
    changed. Summaries written by an LLM, a person or an agent are left alone."""
    counts = {"own_text": 0, "rule": 0, "no_text": 0, "changed": 0}
    domains: dict[int, list[str]] = {}
    for item_id, domain in session.execute(
            select(ItemLink.item_id, ItemLink.domain).join(Item, Item.id == ItemLink.item_id)
            .where(Item.user_id == user_id, ItemLink.domain.is_not(None)).order_by(ItemLink.position)):
        if domain not in domains.setdefault(item_id, []):
            domains[item_id].append(domain)
    changed: list[int] = []
    for item in session.scalars(select(Item).where(
            Item.user_id == user_id, or_(Item.summary_method.is_(None), Item.summary_method.in_(RULE_METHODS)))):
        summary, method = rule_summary(item, domains.get(item.id, []))
        counts[method or "no_text"] += 1
        if (summary, method) != (item.summary, item.summary_method):
            item.summary, item.summary_method = summary, method
            item.summary_status = "done" if summary else "none"
            changed.append(item.id)
    counts["changed"] = len(changed)
    reindex(session, changed)  # the summary is part of the text-search vector
    session.commit()
    return counts
