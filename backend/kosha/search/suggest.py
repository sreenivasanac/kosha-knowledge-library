"""Autocomplete for the search bar (SEARCH_DESIGN.md B.5).

Looks at the token under the cursor and offers keys, values, tags or result
sets for it. Counts are computed with the rest of the bar as the filter, so
each one means "if you add this, you get N".
"""
import difflib
import re
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from kosha.config import DEFAULT_NAMESPACE
from kosha.models import Collection, Item, ItemCollection, ItemLink, List, ResultSet, SavedView, Tag
from kosha.search.engine import SNAPSHOT_PREFIX, recent_searches
from kosha.search.query import DATE_WORDS, KEYS, QueryError, compile_filter, parse

# --- Tunables ---------------------------------------------------------------
MAX_SUGGESTIONS = 10
MAX_HINT_ROWS = 3  # tag and author hints under a bare word
# Offered for keys that take a number or a date; each is shown with its count.
PRESETS = {
    "date": ("today", "7d", "30d", "90d", "thismonth", "lastmonth", "{year}", "{last_year}"),
    "duration": ("<1m", "1m..5m", "5m..20m", ">20m"),
    "likes": (">100", ">1000", ">10000"), "views": (">10000", ">100000", ">1000000"),
    "reposts": (">100", ">1000"), "replies": (">50", ">500"),
    "len": ("<280", "280..1000", ">1000"), "conf": ("<0.5", "0.5..0.85", ">0.85"),
    "tags": ("0", "1", ">1"), "recent": ("100", "500"),
}
COUNTED_ENUMS = ("is", "has", "type", "tagged-by", "tag-status")
# A handle this long is a source's internal id (LinkedIn), not something to
# show or type: `from:` then inserts the author's name instead.
OPAQUE_HANDLE_CHARS = 30
UNCOUNTED_VALUES = ("has:code",)  # a regex over every text (about 40 ms): offered without a count


def _quote(value: str) -> str:
    return f'"{value}"' if re.search(r"[\s(),]", value) else value


def _token_at(q: str, cursor: int) -> tuple[int, int]:
    """Start and end of the term the cursor is in or just after. Quotes keep a
    term together, as in the tokenizer."""
    spans, i = [], 0
    while i < len(q):
        if q[i].isspace() or q[i] in "()":
            i += 1
            continue
        start = i
        while i < len(q) and not q[i].isspace() and q[i] not in "()":
            if q[i] == '"':
                close = q.find('"', i + 1)
                i = close if close >= 0 else len(q) - 1
            i += 1
        spans.append((start, i))
    return next(((s, e) for s, e in spans if s <= cursor <= e), (cursor, cursor))


class _Context:
    def __init__(self, session: Session, user_id: int, rest: str, negated: bool):
        self.session, self.user_id, self.rest, self.negated = session, user_id, rest, negated
        try:
            self.where = compile_filter(session, user_id, parse(rest))
        except QueryError:  # the rest of the bar is not valid yet: count in the whole library
            self.rest = ""
            self.where = compile_filter(session, user_id, parse(""))

    def count(self, atom: str) -> int | None:
        """Results if `atom` were added to the rest of the bar."""
        try:
            where = compile_filter(self.session, self.user_id, parse(f"{self.rest} {'-' if self.negated else ''}{atom}"))
        except QueryError:
            return None
        return self.session.scalar(select(func.count()).select_from(Item).where(*where))

    def grouped(self, column, typed: str, *joins, name=None) -> list[tuple]:
        """(value, name, count) by `column` inside the current result: values
        starting with what was typed first, then values containing it."""
        like = column.ilike(f"%{typed}%", escape="\\") if name is None else or_(
            column.ilike(f"%{typed}%", escape="\\"), name.ilike(f"%{typed}%", escape="\\"))
        stmt = select(column, func.max(name if name is not None else column), func.count(func.distinct(Item.id)))
        for join in joins:
            stmt = stmt.join(*join)
        stmt = (stmt.where(*self.where, column.is_not(None), like).group_by(column)
                .order_by(func.bool_or(column.ilike(f"{typed}%", escape="\\")).desc(), func.count(func.distinct(Item.id)).desc())
                .limit(MAX_SUGGESTIONS))
        return [tuple(row) for row in self.session.execute(stmt)]


def _row(insert: str, label: str, kind: str, count: int | None = None) -> dict:
    return {"insert": insert, "label": label, "count": count, "kind": kind}


def _keys(prefix: str) -> list[dict]:
    return [_row(f"{key}:", f"{spec.hint} — e.g. {spec.example}", "key")
            for key, spec in KEYS.items() if key.startswith(prefix)]


def _tags(ctx: _Context, typed: str) -> list[dict]:
    tags = {t.id: t for t in ctx.session.scalars(
        select(Tag).where(Tag.user_id == ctx.user_id, Tag.namespace == DEFAULT_NAMESPACE))}
    parents = {t.parent_id for t in tags.values()}
    out = []
    for tag in tags.values():
        path = "/".join(tags[int(i)].name for i in str(tag.path).split(".") if int(i) in tags)
        if typed.lower() in path.lower():
            out.append((path, tag.id in parents))
    out.sort(key=lambda pair: (not pair[0].lower().startswith(typed.lower()), len(pair[0]), pair[0].lower()))
    rows = []
    for path, has_children in out[:MAX_SUGGESTIONS]:
        rows.append(_row("#" + _quote(path), path.replace("/", " › "), "tag", ctx.count("#" + _quote(path))))
        if has_children:
            below = "#" + _quote(path + "/*")
            rows.append(_row(below, path.replace("/", " › ") + " › (only what is beneath)", "tag", ctx.count(below)))
    return rows


def _values(ctx: _Context, key: str, typed: str) -> list[dict]:
    spec, session = KEYS[key], ctx.session
    escaped = typed.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    if key == "from":
        rows = ctx.grouped(Item.author_handle, escaped.lstrip("@"), name=Item.author_name)
        return [_row(_quote(name), name, "author", n) if name and len(handle) > OPAQUE_HANDLE_CHARS
                else _row(f"@{handle}", f"{name} (@{handle})" if name else f"@{handle}", "author", n) for handle, name, n in rows]
    if key in ("domain", "site"):
        rows = ctx.grouped(ItemLink.domain, escaped, (ItemLink, ItemLink.item_id == Item.id))
        return [_row(domain, domain, "value", n) for domain, _, n in rows]
    if key == "folder":
        rows = ctx.grouped(Collection.name, escaped, (ItemCollection, ItemCollection.item_id == Item.id),
                           (Collection, Collection.id == ItemCollection.collection_id))
        return [_row(_quote(name), name, "value", n) for name, _, n in rows]
    if key == "list":
        names = session.scalars(select(List.name).where(List.user_id == ctx.user_id, List.name.ilike(f"%{escaped}%", escape="\\"))
                                .order_by(List.name).limit(MAX_SUGGESTIONS))
        return [_row(_quote(name), name, "value", ctx.count(f"list:{_quote(name)}")) for name in names]
    if key in ("source", "lang"):
        rows = ctx.grouped(getattr(Item, key), escaped)
        return [_row(value, value, "value", n) for value, _, n in rows]
    if key == "similar":
        return [_row("", "Paste an item id, or use Find similar on a card", "hint")]
    if spec.values:  # fixed values; the countable ones show how many each would give
        return [_row(v, v, "value", ctx.count(f"{key}:{v}") if key in COUNTED_ENUMS and f"{key}:{v}" not in UNCOUNTED_VALUES else None)
                for v in spec.values if v.startswith(typed.lower())]
    year = datetime.now(UTC).year
    presets = PRESETS.get("date" if spec.type == "date" else key, ())
    values = [p.format(year=year, last_year=year - 1) for p in presets]
    if spec.type == "date" and typed:  # a word being typed: offer the rest of the date words too
        values += [w for w in DATE_WORDS if w not in values]
    return [_row(v, v, "value", ctx.count(f"{key}:{v}")) for v in values if v.startswith(typed.lower())]


def suggest(session: Session, user_id: int, q: str, cursor: int | None = None) -> dict:
    cursor = len(q) if cursor is None else max(0, min(cursor, len(q)))
    if not q.strip():
        views = session.scalars(select(SavedView).where(SavedView.user_id == user_id).order_by(SavedView.name))
        rows = [_row(text, text, "recent") for text in recent_searches(session, user_id)]
        rows += [_row(view.query, view.name, "view") for view in views]
        return {"replace_range": [0, len(q)], "suggestions": rows}

    start, end = _token_at(q, cursor)
    token = q[start:end]
    negated = token.startswith("-") and len(token) > 1
    if negated:
        start, token = start + 1, token[1:]
    ctx = _Context(session, user_id, f"{q[:start - (1 if negated else 0)]} {q[end:]}".strip(), negated)

    if token.startswith("#"):
        typed = token[1:].lstrip("=").strip('"')
        return {"replace_range": [start, end], "suggestions": _tags(ctx, typed)}
    if token.startswith("@"):
        names = session.scalars(select(ResultSet.name).where(
            ResultSet.user_id == user_id, ~ResultSet.name.startswith(SNAPSHOT_PREFIX), ResultSet.name.startswith(token[1:]),
            or_(ResultSet.expires_at.is_(None), ResultSet.expires_at > func.now()))
            .order_by(ResultSet.created_at.desc()).limit(MAX_SUGGESTIONS))
        return {"replace_range": [start, end], "suggestions": [_row(f"@{n}", f"@{n}", "set", ctx.count(f"@{n}")) for n in names]}

    match = re.fullmatch(r"([A-Za-z][A-Za-z-]*):(.*)", token, re.S)
    if match:
        key, value = match.group(1).lower(), match.group(2)
        if key not in KEYS:
            near = difflib.get_close_matches(key, list(KEYS), n=3, cutoff=0.5)
            return {"replace_range": [start, end],
                    "suggestions": [_row(f"{k}:{value}", f"Did you mean {k}: ? ({KEYS[k].hint})", "key") for k in near]}
        # Only the part after the last comma is being typed.
        typed = value.rsplit(",", 1)[-1]
        rows = _values(ctx, key, typed.strip('"'))
        return {"replace_range": [end - len(typed), end], "suggestions": rows[:MAX_SUGGESTIONS]}

    # A bare word: keys it starts, then the tag or author it may be naming.
    rows = _keys(token.lower())
    if token:
        rows += _tags(ctx, token)[:MAX_HINT_ROWS]
        escaped = token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        authors = ctx.session.execute(
            select(Item.author_handle, func.count()).where(*ctx.where, Item.author_handle.ilike(f"{escaped}%", escape="\\"))
            .group_by(Item.author_handle).order_by(func.count().desc()).limit(MAX_HINT_ROWS))
        rows += [_row(f"from:@{handle}", f"from:@{handle}", "author", n) for handle, n in authors]
    return {"replace_range": [start, end], "suggestions": rows}
