"""The qualifier language (SEARCH_DESIGN.md section B).

`parse()` turns the search-bar text into one filter AST plus the query-level
parts (rank text, `similar:`, `sort:`, `include:`). `compile_filter()` turns the
AST into SQLAlchemy conditions on `Item`. Every error is a `QueryError` with a
character position, so the bar can underline the offending token.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, and_, cast, exists, func, literal, literal_column, not_, or_, select, true
from sqlalchemy.orm import Session, aliased

from kosha.config import DEFAULT_NAMESPACE
from kosha.items_query import STABLE_ORDER, STABLE_ORDER_ASC
from kosha.models import (
    Collection,
    Item,
    ItemCollection,
    ItemLink,
    ItemMedia,
    ItemRelation,
    ItemTag,
    List,
    ListItem,
    Ltree,
    ResultSet,
    Tag,
)

# --- Tunables ---------------------------------------------------------------
LONG_BODY_CHARS = 1000  # `is:long`
THIN_TEXT_CHARS = 40  # `is:thin`: less text than this and no media extract
TS_CONFIGS = ("english", "simple")  # a query term is tried stemmed and as written

# `in:` field -> (tsvector column, weight label); see kosha.search.index.
FIELDS = {
    "title": ("tsv", "a"), "body": ("tsv", "b"), "quote": ("tsv", "c"), "link": ("tsv", "c"),
    "summary": ("tsv", "d"), "note": ("tsv_extra", "a"), "transcript": ("tsv_extra", "b"),
    "image": ("tsv_extra", "c"),
}
CONTENT_TYPES = ("text", "image", "video", "audio", "article", "document", "link")
SORT_KEYS = ("relevance", "saved", "posted", "captured", "likes", "views", "reposts", "replies",
             "duration", "len", "conf", "random")
DEFAULT_DESC = True  # every sort key defaults to largest / newest first


@dataclass(frozen=True)
class Key:
    type: str  # str | enum | int | float | duration | date | tag | field | special
    hint: str
    example: str
    values: tuple[str, ...] = ()


KEYS: dict[str, Key] = {
    "from": Key("str", "author", "from:@simonw"),
    "quotes": Key("str", "quotes a post by this author, or this item id", "quotes:@karpathy"),
    "replies-to": Key("str", "replies to this author", "replies-to:@karpathy"),
    "thread": Key("int", "every held post of this item's thread", "thread:1234"),
    "source": Key("str", "where it was saved from", "source:x"),
    "folder": Key("str", "source folder or playlist", 'folder:"Coding with AI"'),
    "list": Key("str", "one of your lists", 'list:"To read"'),
    "id": Key("int", "these items", "id:18234,9912"),
    "type": Key("enum", "content type", "type:video", CONTENT_TYPES),
    "has": Key("enum", "a part is present", "has:link",
               ("image", "video", "audio", "article", "document", "link", "quote", "note", "summary",
                "transcript", "image-text", "media")),
    "lang": Key("str", "language code", "lang:en"),
    "len": Key("int", "characters of body", "len:>1000"),
    "duration": Key("duration", "longest video or audio", "duration:<5m"),
    "domain": Key("str", "a link points at this domain", "domain:github.com"),
    "site": Key("str", "same as domain:", "site:github.com"),
    "link": Key("str", "a link's URL or title contains this", "link:pgvector"),
    "posted": Key("date", "when it was posted", "posted:2026-03..2026-05"),
    "after": Key("date", "posted on or after", "after:30d"),
    "before": Key("date", "posted before", "before:2026-01"),
    "saved": Key("date", "when you saved it (approximate where estimated)", "saved:2026-03..2026-05"),
    "captured": Key("date", "when Kosha first stored it", "captured:1d.."),
    "recent": Key("int", "the N most recently saved", "recent:500"),
    "is": Key("enum", "state", "is:starred",
              ("starred", "read", "unread", "untagged", "archived", "thread", "reply", "thin", "long")),
    "likes": Key("int", "likes at the source", "likes:>1000"),
    "reposts": Key("int", "reposts at the source", "reposts:>100"),
    "replies": Key("int", "replies at the source", "replies:>50"),
    "views": Key("int", "views at the source", "views:>100000"),
    "tags": Key("int", "number of tags on the item", "tags:0"),
    "tagged-by": Key("enum", "who attached a tag", "tagged-by:ai", ("ai", "human", "import")),
    "conf": Key("float", "confidence of an AI-attached tag", "conf:<0.85"),
    "tag-status": Key("enum", "status of a tag on the item", "tag-status:proposed", ("proposed", "approved", "rejected")),
    "primary": Key("tag", "the item's primary tag", "primary:#ai/rag"),
    "secondary": Key("tag", "the item's secondary tag", "secondary:#ai/rag"),
    "title": Key("field", "words in the title", "title:hnsw"),
    "note": Key("field", "words in your note", "note:todo"),
    "in": Key("special", "search only these fields", "in:title,note", tuple(FIELDS)),
    "sort": Key("special", "order of results", "sort:likes", SORT_KEYS),
    "similar": Key("special", "rank by similarity to an item or @set", "similar:18234"),
    "include": Key("special", "also show archived or context items", "include:archived", ("archived", "context")),
}
# In the design but needing data Kosha does not hold yet.
NOT_YET = {"mentions", "kind", "linkkind", "stars", "bookmarks"}
TAG_MOD_KEYS = ("tagged-by", "conf", "tag-status")  # read together with the tag in the same group
DURATION_UNITS = {"s": 1, "m": 60, "h": 3600}
RELATIVE_UNITS = {"h": 3600, "d": 86400, "w": 7 * 86400, "y": 365 * 86400}


class QueryError(ValueError):
    def __init__(self, message: str, position: int):
        super().__init__(message)
        self.message, self.position = message, position

    def as_dict(self) -> dict:
        return {"message": self.message, "position": self.position}


# --- AST --------------------------------------------------------------------
# `raw` is the canonical source text of an atom (used to print the query back);
# `pos` is where it started. Neither takes part in equality.
@dataclass(frozen=True)
class And:
    children: tuple


@dataclass(frozen=True)
class Or:
    children: tuple


@dataclass(frozen=True)
class Not:
    child: Any


@dataclass(frozen=True)
class Pred:
    key: str
    op: str  # eq | gt | ge | lt | le | range (inclusive numbers; half-open [start, end) dates)
    value: Any
    raw: str = field(default="", compare=False)
    pos: int = field(default=0, compare=False)


@dataclass(frozen=True)
class TagRef:
    path: str
    mode: str = "subtree"  # subtree | exact | below
    role: str | None = None
    raw: str = field(default="", compare=False)
    pos: int = field(default=0, compare=False)


@dataclass(frozen=True)
class Text:
    kind: str  # word | phrase | prefix | fuzzy | regex
    value: str
    fields: tuple[str, ...] = ()
    raw: str = field(default="", compare=False)
    pos: int = field(default=0, compare=False)


@dataclass(frozen=True)
class SetRef:
    name: str
    raw: str = field(default="", compare=False)
    pos: int = field(default=0, compare=False)


@dataclass
class Query:
    filter: Any = None  # the whole tree, or None for "everything"
    # Bare words and phrases at the top level: in ranked modes they leave the
    # filter and feed the rankers instead (SEARCH_DESIGN C.1).
    rank_atoms: tuple[Text, ...] = ()
    rank_terms: tuple[str, ...] = ()  # every positive word and "phrase", for the rankers
    rank_text: str = ""  # the same, joined: what gets embedded
    similar: str | None = None  # an item id, or "@name"
    sort: tuple[str, bool] | None = None  # (key, descending)
    include: frozenset[str] = frozenset()
    chips: list[dict] = field(default_factory=list)  # one per token, for the UI


# --- Tokenizer --------------------------------------------------------------
@dataclass(frozen=True)
class Token:
    kind: str  # lparen | rparen | or | and | not | minus | term
    text: str
    start: int
    end: int


def tokenize(q: str) -> list[Token]:
    """Split on whitespace and parentheses, keeping "quoted text" and /regex/
    whole. A `-` at the start of a term is its own token."""
    tokens: list[Token] = []
    i, n = 0, len(q)
    while i < n:
        c = q[i]
        if c.isspace():
            i += 1
        elif c in "()":
            tokens.append(Token("lparen" if c == "(" else "rparen", c, i, i + 1))
            i += 1
        elif c == "-" and i + 1 < n and not q[i + 1].isspace() and q[i + 1] != ")":
            tokens.append(Token("minus", c, i, i + 1))
            i += 1
        else:
            start = i
            closing = _regex_end(q, i) if c == "/" else None
            if closing:
                i = closing
            else:
                while i < n and not q[i].isspace() and q[i] not in "()":
                    if q[i] == '"':
                        close = q.find('"', i + 1)
                        if close < 0:
                            raise QueryError('Missing closing quote "', i)
                        i = close
                    i += 1
            text = q[start:i]
            tokens.append(Token(text.lower() if text in ("OR", "AND", "NOT") else "term", text, start, i))
    return tokens


def _regex_end(q: str, start: int) -> int | None:
    """Index just past the closing slash of a /regex/ starting at `start`, or
    None when this is only a word that begins with a slash."""
    i = start + 1
    while i < len(q):
        if q[i] == "\\":
            i += 2
            continue
        if q[i] == "/":
            ends_term = i + 1 == len(q) or q[i + 1].isspace() or q[i + 1] == ")"
            return i + 1 if ends_term and i > start + 1 else None
        i += 1
    return None


# --- Value parsing ----------------------------------------------------------
def _unquote(value: str) -> str:
    return value[1:-1] if len(value) >= 2 and value[0] == value[-1] == '"' else value


def _split_commas(value: str) -> list[str]:
    """Split on commas that are outside quotes."""
    return [p for p in re.findall(r'(?:"[^"]*"|[^,"])+', value) if p]


def _int(value: str, pos: int) -> int:
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([kKmM]?)", value.replace("_", ""))
    if not match:
        raise QueryError(f'"{value}" is not a number', pos)
    return int(float(match.group(1)) * {"": 1, "k": 1_000, "m": 1_000_000}[match.group(2).lower()])


def _float(value: str, pos: int) -> float:
    try:
        return float(value)
    except ValueError:
        raise QueryError(f'"{value}" is not a number', pos) from None


def _duration(value: str, pos: int) -> int:
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([smh]?)", value)
    if not match:
        raise QueryError(f'"{value}" is not a duration; use 90s, 5m or 2h', pos)
    return int(float(match.group(1)) * DURATION_UNITS[match.group(2) or "s"])


def _period(value: str, pos: int, now: datetime) -> tuple[datetime, datetime]:
    """A date bound as the [start, end) span it covers: a day, month or year,
    or the single instant "N units ago" for a relative value such as 30d."""
    match = re.fullmatch(r"(\d+)([hdwy])", value)
    if match:
        instant = now - timedelta(seconds=int(match.group(1)) * RELATIVE_UNITS[match.group(2)])
        return instant, instant
    match = re.fullmatch(r"(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", value)
    try:
        if not match:
            raise ValueError
        year, month, day = (int(g) if g else None for g in match.groups())
        start = datetime(year, month or 1, day or 1, tzinfo=UTC)
        if day:
            end = start + timedelta(days=1)
        elif month:
            end = datetime(year + month // 12, month % 12 + 1, 1, tzinfo=UTC)
        else:
            end = datetime(year + 1, 1, 1, tzinfo=UTC)
        return start, end
    except ValueError:
        raise QueryError(f'"{value}" is not a date; use 2026-03-14, 2026-03, 2026 or 30d', pos) from None


def _date_pred(key: str, value: str, raw: str, pos: int, now: datetime) -> Pred:
    """Every date qualifier becomes a half-open range on its column."""
    relative = bool(re.fullmatch(r"\d+[hdwy]", value))
    column = "posted" if key in ("after", "before") else key
    if ".." in value:
        lo, hi = value.split("..", 1)
        span = (_period(lo, pos, now)[0] if lo else None, _period(hi, pos, now)[1] if hi else None)
    else:
        match = re.match(r"(>=|<=|>|<)?(.*)", value)
        op, bound = match.group(1), match.group(2)
        start, end = _period(bound, pos, now)
        if key == "after":
            op = op or ">="
        elif key == "before":
            op = op or "<"
        elif op is None and relative:
            op = ">="  # `saved:7d` means "in the last 7 days"
        span = {None: (start, end), ">=": (start, None), ">": (end, None), "<": (None, start), "<=": (None, end)}[op]
    return Pred(column, "range", span, raw, pos)


def _number_pred(key: str, value: str, raw: str, pos: int) -> Pred:
    convert = {"float": _float, "duration": _duration}.get(KEYS[key].type, _int)
    if ".." in value:
        lo, hi = value.split("..", 1)
        return Pred(key, "range", (convert(lo, pos) if lo else None, convert(hi, pos) if hi else None), raw, pos)
    match = re.match(r"(>=|<=|>|<)?(.*)", value)
    op = {None: "eq", ">": "gt", ">=": "ge", "<": "lt", "<=": "le"}[match.group(1)]
    return Pred(key, op, convert(match.group(2), pos), raw, pos)


def _tag_ref(text: str, role: str | None, raw: str, pos: int) -> TagRef:
    """`#a/b` (subtree), `#=a/b` (exact), `#a/b/*` (below only)."""
    body = text.removeprefix("#")
    mode = "subtree"
    if body.startswith("="):
        mode, body = "exact", body[1:]
    body = _unquote(body)
    if body.endswith("/*"):
        mode, body = "below", body[:-2]
    path = "/".join(part.strip() for part in body.split("/") if part.strip())
    if not path:
        raise QueryError("A tag name is missing after #", pos)
    return TagRef(path, mode, role, raw, pos)


def parse_sort(value: str, pos: int = 0) -> tuple[str, bool]:
    """`likes`, `likes-asc`, `posted-desc` -> (key, descending)."""
    match = re.fullmatch(r"([a-z]+)(?:-(asc|desc))?", value.lower())
    if not match or match.group(1) not in SORT_KEYS:
        raise QueryError(f'Unknown sort "{value}". Use one of: {", ".join(SORT_KEYS)}', pos)
    return match.group(1), (match.group(2) != "asc" if match.group(2) else DEFAULT_DESC)


def _nearest(word: str, options) -> str | None:
    match = difflib.get_close_matches(word, list(options), n=1, cutoff=0.5)
    return match[0] if match else None


def _quote(value: str) -> str:
    return f'"{value}"' if re.search(r'[\s(),]', value) or not value else value


# --- Parser -----------------------------------------------------------------
class _In:
    def __init__(self, fields: tuple[str, ...]):
        self.fields = fields


def _scope(node, fields: tuple[str, ...]):
    """Apply an `in:` field list to every text atom of a group that has none."""
    if isinstance(node, Text):
        # printed back as its own group so the scope survives a round trip
        return node if node.fields else Text(node.kind, node.value, fields, f"(in:{','.join(fields)} {node.raw})", node.pos)
    if isinstance(node, (And, Or)):
        return type(node)(tuple(_scope(c, fields) for c in node.children))
    if isinstance(node, Not):
        return Not(_scope(node.child, fields))
    return node


def _flat(kind, children: list) -> list:
    """(a b) c is the same tree as a b c."""
    return [g for c in children for g in (c.children if isinstance(c, kind) else (c,))]


class _Parser:
    def __init__(self, q: str, now: datetime):
        self.tokens, self.i, self.now, self.query = tokenize(q), 0, now, Query()

    def peek(self) -> Token | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def chip(self, token: Token, kind: str, negated: bool = False, **extra) -> None:
        self.query.chips.append({"start": token.start - (1 if negated else 0), "end": token.end,
                                 "text": ("-" if negated else "") + token.text, "kind": kind, "negated": negated, **extra})

    def parse_or(self):
        children = [self.parse_and()]
        while (token := self.peek()) and token.kind == "or":
            self.chip(token, "operator")
            self.i += 1
            after = self.peek()
            if children[-1] is None or after is None or after.kind in ("or", "rparen"):
                raise QueryError("OR needs a term on both sides", token.start)
            children.append(self.parse_and())
        children = _flat(Or, [c for c in children if c is not None])
        return children[0] if len(children) == 1 else Or(tuple(children)) if children else None

    def parse_and(self):
        children, fields = [], None
        while (token := self.peek()) and token.kind not in ("or", "rparen"):
            if token.kind == "and":
                self.chip(token, "operator")
                self.i += 1
                continue
            node = self.parse_unary()
            if isinstance(node, _In):
                fields = node.fields
            elif node is not None:
                children.append(node)
        if fields:
            children = [_scope(c, fields) for c in children]
        children = _flat(And, children)
        return children[0] if len(children) == 1 else And(tuple(children)) if children else None

    def parse_unary(self):
        token = self.peek()
        negated = token.kind in ("minus", "not")
        if negated:
            self.i += 1
            if token.kind == "not":
                self.chip(token, "operator")
            after = self.peek()
            if after is None or after.kind in ("or", "and", "rparen"):
                raise QueryError(f"{token.text} needs a term after it", token.start)
        node = self.parse_atom(negated and token.kind == "minus")
        if negated and node is not None:
            if isinstance(node, _In):
                raise QueryError("in: cannot be negated", token.start)
            return Not(node)
        return node

    def parse_atom(self, minus: bool):
        token = self.peek()
        self.i += 1
        if token.kind == "lparen":
            node = self.parse_or()
            closing = self.peek()
            if closing is None or closing.kind != "rparen":
                raise QueryError("Missing closing )", token.start)
            self.i += 1
            return node
        if token.kind == "rparen":
            raise QueryError("Unexpected )", token.start)
        return self.term(token, minus)

    def term(self, token: Token, minus: bool):
        text, pos = token.text, token.start
        if text.startswith("#") and len(text) > 1:
            node = _tag_ref(text, None, "", pos)
            self.chip(token, "tag", minus, value=node.path)
            return _with_raw(node)
        if text.startswith("@") and len(text) > 1:
            self.chip(token, "set", minus, value=text[1:])
            return SetRef(text[1:], text, pos)
        if text.startswith("~") and len(text) > 1:
            self.chip(token, "text", minus, value=text[1:])
            return Text("fuzzy", text[1:], (), text, pos)
        if len(text) > 2 and text[0] == "/" and text[-1] == "/" and _regex_end(text, 0):
            try:
                re.compile(text[1:-1])
            except re.error as exc:
                raise QueryError(f"Invalid regular expression: {exc}", pos) from None
            self.chip(token, "text", minus, value=text[1:-1])
            return Text("regex", text[1:-1], (), text, pos)
        if text.startswith('"'):
            return self.words(token, text, (), minus)
        match = re.fullmatch(r"([A-Za-z][A-Za-z-]*):(.*)", text, re.S)
        if match:
            return self.qualifier(token, match.group(1).lower(), match.group(2), minus)
        return self.words(token, text, (), minus)

    def words(self, token: Token, text: str, fields: tuple[str, ...], minus: bool, key: str = ""):
        """A bare word, "phrase" or prefix*."""
        prefix = f"{key}:" if key else ""
        if text.startswith('"'):
            value = _unquote(text).strip()
            kind = "phrase" if " " in value else "word"
            raw = prefix + f'"{value}"'
        elif text.endswith("*") and len(text) > 1:
            value, kind, raw = text[:-1], "prefix", prefix + text
        else:
            value, kind, raw = text, "word", prefix + text
        if not key:
            self.chip(token, "text", minus, value=value)
        return Text(kind, value, fields, raw, token.start) if value else None

    def qualifier(self, token: Token, key: str, value: str, minus: bool):
        pos = token.start
        if key not in KEYS:
            if key in NOT_YET:
                raise QueryError(f"{key}: is not available yet (Kosha does not hold that data)", pos)
            near = _nearest(key, KEYS)
            hint = f' Did you mean "{near}:"?' if near else ""
            raise QueryError(f'Unknown key "{key}:".{hint} To search for the text itself, put it in quotes.', pos)
        spec = KEYS[key]
        if not value:
            raise QueryError(f"{key}: needs a value, for example {spec.example}", token.end)
        value_pos = pos + len(key) + 1
        self.chip(token, "qualifier", minus, key=key, value=value)

        if key == "sort":
            self.query.sort = parse_sort(value, value_pos)
            return None
        if key == "similar":
            if not re.fullmatch(r"\d+|@[\w.-]+", value):
                raise QueryError("similar: takes an item id or an @set", value_pos)
            self.query.similar = value
            return None
        parts = _split_commas(value)
        if key in ("include", "in"):
            chosen = tuple(_unquote(p).lower() for p in parts)
            for name in chosen:
                if name not in spec.values:
                    raise QueryError(f'Unknown value "{name}" for {key}:. Use one of: {", ".join(spec.values)}', value_pos)
            if key == "in":
                return _In(chosen)
            self.query.include |= set(chosen)
            return None

        nodes = [self.value(key, spec, part, value_pos, token) for part in parts]
        return nodes[0] if len(nodes) == 1 else Or(tuple(nodes))

    def value(self, key: str, spec: Key, part: str, pos: int, token: Token):
        plain = _unquote(part)
        raw = f"{key}:{part}"
        if spec.type == "field":
            return self.words(token, part, (key,), False, key)
        if spec.type == "tag":
            return _tag_ref(plain, key, raw, pos)
        if spec.type == "date":
            return _date_pred(key, plain, raw, pos, self.now)
        if key in ("id", "recent", "thread"):
            return Pred(key, "eq", _int(plain, pos), raw, pos)
        if spec.type in ("int", "float", "duration"):
            return _number_pred(key, plain, raw, pos)
        if spec.type == "enum":
            plain = plain.lower()
            if plain not in spec.values:
                near = _nearest(plain, spec.values)
                hint = f' Did you mean "{near}"?' if near else f' Use one of: {", ".join(spec.values)}'
                raise QueryError(f'Unknown value "{plain}" for {key}:.{hint}', pos)
        return Pred("domain" if key == "site" else key, "eq", plain, raw, pos)


def _with_raw(node: TagRef) -> TagRef:
    prefix = {"subtree": "#", "exact": "#="}.get(node.mode, "#")
    path = _quote(node.path + ("/*" if node.mode == "below" else ""))
    return TagRef(node.path, node.mode, node.role, prefix + path, node.pos)


def _texts(node, positive: bool = True):
    """Every text atom with whether it is reached through an even number of NOTs."""
    if isinstance(node, Text):
        yield node, positive
    elif isinstance(node, (And, Or)):
        for child in node.children:
            yield from _texts(child, positive)
    elif isinstance(node, Not):
        yield from _texts(node.child, not positive)


def parse(q: str, now: datetime | None = None) -> Query:
    parser = _Parser(q or "", now or datetime.now(UTC))
    tree = parser.parse_or()
    leftover = parser.peek()
    if leftover is not None:  # only a stray ")" can stop the top-level loop
        raise QueryError("Unexpected )", leftover.start)
    query = parser.query
    query.filter = tree
    rankable = lambda n: isinstance(n, Text) and n.kind in ("word", "phrase")  # noqa: E731
    top = tree.children if isinstance(tree, And) else (tree,)
    query.rank_atoms = tuple(n for n in top if rankable(n))
    query.rank_terms = tuple(f'"{t.value}"' if t.kind == "phrase" else t.value
                             for t, positive in _texts(tree) if positive and rankable(t))
    query.rank_text = " ".join(query.rank_terms)
    return query


def to_string(query: Query) -> str:
    """The canonical text of a query; `parse(to_string(q))` gives the same tree.
    Saved views and recent searches store this."""
    def show(node, nested: bool = False) -> str:
        if isinstance(node, And):
            return " ".join(show(c, True) for c in node.children)
        if isinstance(node, Or):
            text = " OR ".join(show(c, True) for c in node.children)
            return f"({text})" if nested else text
        if isinstance(node, Not):
            inner = show(node.child, True)
            return "-" + (f"({inner})" if isinstance(node.child, And) else inner)
        return node.raw

    parts = [show(query.filter)] if query.filter is not None else []
    parts += [f"include:{name}" for name in sorted(query.include)]
    if query.similar:
        parts.append(f"similar:{query.similar}")
    if query.sort:
        parts.append(f"sort:{query.sort[0]}" + ("" if query.sort[1] == DEFAULT_DESC else "-asc"))
    return " ".join(parts)


# --- Compiler ---------------------------------------------------------------
def ts_config(config: str):
    return literal_column(f"'{config}'")


def text_condition(node: Text) -> ColumnElement[bool]:
    """One text atom as a condition on the search columns."""
    if node.kind == "fuzzy":  # pg_trgm word similarity, default threshold 0.6
        return literal(node.value).op("<%")(Item.search_text)
    if node.kind == "regex":
        return Item.search_text.op("~*")(node.value)
    if node.fields:
        labels: dict[str, set[str]] = {}
        for name in node.fields:
            column, label = FIELDS[name]
            labels.setdefault(column, set()).add(label)
        vectors = [func.ts_filter(getattr(Item, column), literal_column("'{%s}'::\"char\"[]" % ",".join(sorted(ls))))
                   for column, ls in labels.items()]
    else:
        vectors = [Item.tsv, Item.tsv_extra]
    if node.kind == "prefix":
        make, arg = func.to_tsquery, "'" + node.value.replace("'", "''").replace("\\", "") + "':*"
    else:
        make, arg = (func.phraseto_tsquery if node.kind == "phrase" else func.plainto_tsquery), node.value
    matches = [vector.op("@@")(make(ts_config(config), arg)) for config in TS_CONFIGS for vector in vectors]
    # A term made only of stop words ("the") has nothing to match; let it pass.
    return or_(func.numnode(make(ts_config("english"), arg)) == 0, *matches)


def _compare(column, op: str, value) -> ColumnElement[bool]:
    if op == "range":
        lo, hi = value
        return and_(column >= lo if lo is not None else true(), column <= hi if hi is not None else true())
    return {"eq": column == value, "gt": column > value, "ge": column >= value,
            "lt": column < value, "le": column <= value}[op]


def _has_tag(*where) -> ColumnElement[bool]:
    return exists().where(ItemTag.item_id == Item.id, ~ItemTag.removed_by_user, ItemTag.tag_id == Tag.id, *where)


def _relation(kind: str, *where) -> ColumnElement[bool]:
    return exists().where(ItemRelation.from_item_id == Item.id, ItemRelation.relation == kind, *where)


SAVED_TIME = func.coalesce(Item.saved_at, Item.saved_at_est)
BODY_LEN = func.char_length(func.coalesce(Item.body, ""))
TAG_COUNT = (select(func.count()).select_from(ItemTag)
             .where(ItemTag.item_id == Item.id, ~ItemTag.removed_by_user).correlate(Item).scalar_subquery())
PLAIN_COLUMNS = {"likes": Item.likes, "reposts": Item.reposts, "replies": Item.replies, "views": Item.views,
                 "duration": Item.duration_s, "len": BODY_LEN, "tags": TAG_COUNT}
DATE_COLUMNS = {"posted": Item.posted_at, "saved": SAVED_TIME, "captured": Item.captured_at}


class _Compiler:
    def __init__(self, session: Session, user_id: int, skip: tuple = ()):
        self.session, self.user_id, self.skip = session, user_id, {id(n) for n in skip}
        self._tags: list[tuple[Tag, str]] | None = None

    def node(self, n) -> ColumnElement[bool]:
        if isinstance(n, And):
            return self.group(n.children)
        if isinstance(n, Or):
            return or_(*(self.node(c) for c in n.children))
        if isinstance(n, Not):
            return not_(self.node(n.child))
        if isinstance(n, Text):
            return text_condition(n)
        if isinstance(n, TagRef):
            return self.tag(n, [])
        if isinstance(n, SetRef):
            return self.result_set(n)
        if n.key in TAG_MOD_KEYS:
            return _has_tag(self.tag_mod(n))
        return self.pred(n)

    def group(self, children) -> ColumnElement[bool]:
        """AND. `tagged-by:`, `conf:` and `tag-status:` describe the tag row, so
        inside one group they apply to the same tag as any `#tag` beside them."""
        children = [c for c in children if id(c) not in self.skip]
        mods = [self.tag_mod(c) for c in children if isinstance(c, Pred) and c.key in TAG_MOD_KEYS]
        tags = [c for c in children if isinstance(c, TagRef)]
        conds = [self.node(c) for c in children
                 if not isinstance(c, TagRef) and not (isinstance(c, Pred) and c.key in TAG_MOD_KEYS)]
        conds += [self.tag(t, mods) for t in tags]
        if mods and not tags:
            conds.append(_has_tag(*mods))
        return and_(true(), *conds)

    def tag_mod(self, p: Pred) -> ColumnElement[bool]:
        if p.key == "conf":
            return _compare(ItemTag.confidence, p.op, p.value)
        if p.key == "tag-status":
            return Tag.status == p.value
        return ItemTag.attached_by == ("agent" if p.value == "ai" else p.value)

    def tag(self, ref: TagRef, mods: list) -> ColumnElement[bool]:
        tag = self.resolve_tag(ref)
        where = [ItemTag.role == ref.role] if ref.role else []
        if ref.mode == "exact":
            where.append(Tag.id == tag.id)
        else:
            where.append(Tag.path.op("<@")(cast(str(tag.path), Ltree())))
            if ref.mode == "below":
                where.append(Tag.id != tag.id)
        return _has_tag(*where, *mods)

    def resolve_tag(self, ref: TagRef) -> Tag:
        """A full path from the root, or any unique tail of one (`#rag`)."""
        if self._tags is None:
            tags = {t.id: t for t in self.session.scalars(
                select(Tag).where(Tag.user_id == self.user_id, Tag.namespace == DEFAULT_NAMESPACE))}
            self._tags = [(t, "/".join(tags[int(i)].name.lower() for i in str(t.path).split(".") if int(i) in tags))
                          for t in tags.values()]
        wanted = ref.path.lower()
        found = [(t, path) for t, path in self._tags if path == wanted or path.endswith("/" + wanted)]
        exact = [f for f in found if f[1] == wanted]
        if len(exact) == 1 or len(found) == 1:
            return (exact or found)[0][0]
        if not found:
            raise QueryError(f"No tag named #{ref.path}", ref.pos)
        raise QueryError(f"#{ref.path} matches several tags: " + ", ".join("#" + path for _, path in found[:8]), ref.pos)

    def result_set(self, ref: SetRef) -> ColumnElement[bool]:
        ids = self.session.scalar(select(ResultSet.item_ids).where(
            ResultSet.user_id == self.user_id, ResultSet.name == ref.name,
            or_(ResultSet.expires_at.is_(None), ResultSet.expires_at > func.now())))
        if ids is None:
            raise QueryError(f"No result set named @{ref.name}. For an author, use from:@{ref.name}", ref.pos)
        return Item.id.in_(ids)

    def pred(self, p: Pred) -> ColumnElement[bool]:  # noqa: C901 (one branch per qualifier)
        key, v = p.key, p.value
        if key in PLAIN_COLUMNS:
            return _compare(PLAIN_COLUMNS[key], p.op, v)
        if key in DATE_COLUMNS:
            lo, hi = v
            column = DATE_COLUMNS[key]
            return and_(column >= lo if lo is not None else true(), column < hi if hi is not None else true())
        if key == "from":
            if v.startswith("@") or re.fullmatch(r"\w+", v):
                return func.lower(Item.author_handle) == v.lstrip("@").lower()
            return func.lower(Item.author_name) == v.lower()
        if key in ("quotes", "replies-to"):
            kind = "quotes" if key == "quotes" else "replies_to"
            if v.isdigit():
                return _relation(kind, or_(ItemRelation.to_item_id == int(v), ItemRelation.to_source_id == v))
            # Only targets Kosha holds have an author to compare with.
            target = aliased(Item)
            return _relation(kind, ItemRelation.to_item_id == target.id,
                             func.lower(target.author_handle) == v.lstrip("@").lower())
        if key == "thread":
            same = aliased(Item)
            root = func.coalesce(
                select(ItemRelation.to_source_id).where(
                    ItemRelation.from_item_id == v, ItemRelation.relation == "thread_member").limit(1).scalar_subquery(),
                select(same.source_id).where(same.id == v).scalar_subquery())
            return or_(Item.source_id == root, _relation("thread_member", ItemRelation.to_source_id == root))
        if key == "folder":
            return exists().where(ItemCollection.item_id == Item.id, ItemCollection.collection_id == Collection.id,
                                  Collection.user_id == self.user_id, func.lower(Collection.name) == v.lower())
        if key == "list":
            return exists().where(ListItem.item_id == Item.id, ListItem.list_id == List.id,
                                  List.user_id == self.user_id, func.lower(List.name) == v.lower())
        if key == "id":
            return Item.id == v
        if key == "source":
            return Item.source == v.lower()
        if key == "lang":
            return Item.lang == v.lower()
        if key == "type":
            return Item.content_types.contains([v])
        if key == "domain":
            domain = v.lower().removeprefix("www.")
            return exists().where(ItemLink.item_id == Item.id,
                                  or_(ItemLink.domain == domain, ItemLink.domain.endswith("." + domain, autoescape=True)))
        if key == "link":
            return exists().where(ItemLink.item_id == Item.id,
                                  or_(ItemLink.url.icontains(v, autoescape=True), ItemLink.title.icontains(v, autoescape=True)))
        if key == "recent":
            newest = select(Item.id).where(Item.user_id == self.user_id, Item.is_saved, Item.archived_at.is_(None))
            return Item.id.in_(newest.order_by(*STABLE_ORDER).limit(v))
        if key == "has":
            return self.has(v)
        if key == "is":
            return self.state(v)
        raise QueryError(f"{key}: cannot be used here", p.pos)

    def has(self, v: str) -> ColumnElement[bool]:
        media = lambda *where: exists().where(ItemMedia.item_id == Item.id, *where)  # noqa: E731
        if v in ("image", "video", "audio", "article", "document"):
            return Item.content_types.contains([v])
        return {
            "link": lambda: exists().where(ItemLink.item_id == Item.id),
            "quote": lambda: _relation("quotes"),
            "note": lambda: func.coalesce(Item.note, "") != "",
            "summary": lambda: func.coalesce(Item.summary, "") != "",
            "transcript": lambda: media(ItemMedia.extract_kind == "transcript", ItemMedia.extract_text.is_not(None)),
            "image-text": lambda: media(or_(ItemMedia.alt_text.is_not(None), and_(
                ItemMedia.extract_text.is_not(None), ItemMedia.extract_kind.is_distinct_from("transcript")))),
            "media": media,
        }[v]()

    def state(self, v: str) -> ColumnElement[bool]:
        other = aliased(ItemRelation)
        return {
            "starred": lambda: Item.starred,
            "read": lambda: Item.read_at.is_not(None),
            "unread": lambda: Item.read_at.is_(None),
            "untagged": lambda: TAG_COUNT == 0,
            "archived": lambda: Item.archived_at.is_not(None),
            # a member of a thread, or the root other held posts point at
            "thread": lambda: or_(_relation("thread_member"), exists().where(
                other.relation == "thread_member", other.to_source_id == Item.source_id)),
            "reply": lambda: _relation("replies_to"),
            "long": lambda: BODY_LEN > LONG_BODY_CHARS,
            "thin": lambda: and_(
                func.char_length(func.coalesce(Item.title, "") + func.coalesce(Item.body, "")) < THIN_TEXT_CHARS,
                ~exists().where(ItemMedia.item_id == Item.id, ItemMedia.extract_text.is_not(None))),
        }[v]()


def _mentions_archived(node) -> bool:
    if isinstance(node, Pred):
        return node.key == "is" and node.value == "archived"
    if isinstance(node, (And, Or)):
        return any(_mentions_archived(c) for c in node.children)
    return isinstance(node, Not) and _mentions_archived(node.child)


def compile_filter(session: Session, user_id: int, query: Query, lift_rank_atoms: bool = False) -> list[ColumnElement[bool]]:
    """The query as SQL conditions on `Item`, scoped to the user. By default a
    query sees saved, non-archived items. With `lift_rank_atoms` the top-level
    bare words are left out, for the ranked modes where they rank, not filter."""
    where: list[ColumnElement[bool]] = [Item.user_id == user_id]
    if "context" not in query.include:
        where.append(Item.is_saved)
    if "archived" not in query.include and not _mentions_archived(query.filter):
        where.append(Item.archived_at.is_(None))
    if query.filter is not None:
        compiler = _Compiler(session, user_id, query.rank_atoms if lift_rank_atoms else ())
        root = query.filter if isinstance(query.filter, And) else And((query.filter,))
        where.append(compiler.node(root))
    return where


def order_by(sort: tuple[str, bool]) -> list:
    """ORDER BY for a plain (unranked) sort. `saved` is the list's own stable order."""
    key, descending = sort
    if key == "saved":
        return list(STABLE_ORDER if descending else STABLE_ORDER_ASC)
    conf = (select(func.max(ItemTag.confidence)).where(ItemTag.item_id == Item.id, ~ItemTag.removed_by_user)
            .correlate(Item).scalar_subquery())
    column = {"posted": Item.posted_at, "captured": Item.captured_at, "likes": Item.likes, "views": Item.views,
              "reposts": Item.reposts, "replies": Item.replies, "duration": Item.duration_s, "len": BODY_LEN,
              "conf": conf}[key]
    return [(column.desc() if descending else column.asc()).nulls_last(), Item.seq.desc()]
