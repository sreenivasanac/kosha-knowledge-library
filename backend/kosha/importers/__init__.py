"""Import bookmarks from a file, and export the library (export.py).

Three file formats, told apart by their content (`detect`):
- `netscape`: the bookmarks HTML that browsers, Raindrop and Pocket export.
  Folders (`<H3>` + nested `<DL>`) become collections, `TAGS="a,b"` tags.
- `raindrop_csv`: columns `url, folder, title, note, tags, created`
  (and `excerpt`, `cover`, `favorite`).
- `csv`: any CSV with at least a `url` column; the same optional columns.

Every row becomes an item of source `web`, or `x` / `linkedin` when the URL
is a post there (so it is the same item a capture of that post makes). Rows
go through `kosha.ingest.upsert_result`, like captures and `add_item`, so the
content hash, search columns and summary are built. Nothing is fetched.
"""
import csv
import hashlib
import io
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import urlparse

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from kosha.api.tags import attach_tag
from kosha.config import DEFAULT_NAMESPACE
from kosha.ingest import upsert_result
from kosha.models import DeletedItem, Item, Tag
from kosha.parsers.common import ParsedItem, ParseResult, domain_of
from kosha.parsers.linkedin import source_id_for as linkedin_source_id
from kosha.pipeline.writes import create_tag_path
from kosha.search.index import reindex
from kosha.summaries import summarize

# --- Tunables ---------------------------------------------------------------
IMPORT_MAX_BYTES = 20 * 1024 * 1024
FORMATS = ("netscape", "raindrop_csv", "csv")
PREVIEW_ROWS = 10  # parsed rows shown by a dry run
ERRORS_KEPT = 20
COLLECTION_SOURCE, COLLECTION_KIND = "import", "browser_folder"
TAG_ORIGIN = "import"
LOOKUP_CHUNK = 1000  # source ids per IN (...) when looking for items already held
X_HOSTS = ("x.com", "twitter.com", "mobile.twitter.com", "mobile.x.com")
TRUE_WORDS = ("true", "1", "yes", "y")


class ImportError_(ValueError):
    """The file cannot be read as any supported format; the message is shown to the user."""


@dataclass
class Row:
    url: str
    title: str | None = None
    note: str | None = None
    excerpt: str | None = None
    tags: list[str] = field(default_factory=list)
    folder: str | None = None  # "Parent/Child"
    created: datetime | None = None
    cover: str | None = None
    favorite: bool = False


def _when(value: str | None) -> datetime | None:
    """A date as the exports write it: Unix seconds (or milliseconds), or ISO 8601."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        if value.isdigit():
            seconds = int(value)
            return datetime.fromtimestamp(seconds / 1000 if seconds > 10**11 else seconds, UTC)
        when = datetime.fromisoformat(value)
        return when if when.tzinfo else when.replace(tzinfo=UTC)
    except (ValueError, OverflowError, OSError):
        return None


def _tags(value: str | None) -> list[str]:
    return list(dict.fromkeys(t.strip().strip("#") for t in (value or "").split(",") if t.strip().strip("#")))


# --- Netscape bookmarks HTML --------------------------------------------------
class _Netscape(HTMLParser):
    """The format has no closing tags to rely on: a folder is an <H3> followed
    by a <DL>, a bookmark an <A>, and a <DD> after it is its description."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: list[Row] = []
        self.folders: list[str] = []  # the open <DL>s; "" for one without a heading
        self.pending_folder: str | None = None
        self.capture: str | None = None  # which text is being collected: h3, a or dd
        self.text: list[str] = []

    def _close(self) -> None:
        text, kind = " ".join("".join(self.text).split()), self.capture
        self.capture, self.text = None, []
        if kind == "h3":
            self.pending_folder = text
        elif kind == "a" and self.rows:
            self.rows[-1].title = text or None
        elif kind == "dd" and self.rows and text:
            self.rows[-1].excerpt = text

    def handle_starttag(self, tag, attrs):
        self._close()
        attrs = dict(attrs)
        if tag == "h3":
            self.capture = "h3"
        elif tag == "dl":
            self.folders.append(self.pending_folder or "")
            self.pending_folder = None
        elif tag == "a" and attrs.get("href"):
            self.rows.append(Row(url=attrs["href"].strip(), tags=_tags(attrs.get("tags")),
                                 folder="/".join(f for f in self.folders if f) or None,
                                 created=_when(attrs.get("add_date") or attrs.get("time_added"))))
            self.capture = "a"
        elif tag == "dd" and self.rows:
            self.capture = "dd"

    def handle_endtag(self, tag):
        self._close()
        if tag == "dl" and self.folders:
            self.folders.pop()

    def handle_data(self, data):
        if self.capture:
            self.text.append(data)


def parse_netscape(text: str) -> list[Row]:
    parser = _Netscape()
    parser.feed(text)
    parser.close()
    parser._close()
    return parser.rows


# --- CSV ----------------------------------------------------------------------
def parse_csv(text: str) -> list[Row]:
    reader = csv.DictReader(io.StringIO(text))
    columns = {(name or "").strip().lower(): name for name in reader.fieldnames or []}
    if "url" not in columns:
        raise ImportError_("The CSV has no `url` column")

    def cell(line: dict, *names: str) -> str | None:
        for name in names:
            value = (line.get(columns.get(name, "")) or "").strip()
            if value:
                return value
        return None

    return [Row(url=cell(line, "url") or "", title=cell(line, "title", "name"), note=cell(line, "note", "notes"),
                excerpt=cell(line, "excerpt", "description"), tags=_tags(cell(line, "tags")), folder=cell(line, "folder"),
                created=_when(cell(line, "created", "date", "add_date")), cover=cell(line, "cover"),
                favorite=(cell(line, "favorite") or "").lower() in TRUE_WORDS)
            for line in reader]


def detect(text: str) -> str:
    head = text[:4096].lower()
    if "<!doctype netscape-bookmark-file" in head or "<dt>" in head or "<a " in head:
        return "netscape"
    columns = {c.strip().strip('"').lower() for c in head.splitlines()[0].split(",")} if head.strip() else set()
    if "url" not in columns:
        raise ImportError_("Not a bookmarks HTML file, and not a CSV with a `url` column")
    return "raindrop_csv" if {"folder", "created"} <= columns else "csv"


def parse(data: bytes, format: str | None = None) -> tuple[str, list[Row]]:
    """(format, rows). `format` forces one of FORMATS; without it the content decides."""
    text = data.decode("utf-8-sig", errors="replace")
    format = format or detect(text)
    if format not in FORMATS:
        raise ImportError_(f"Unknown format {format!r}; one of: {', '.join(FORMATS)}")
    return format, parse_netscape(text) if format == "netscape" else parse_csv(text)


# --- Rows to items --------------------------------------------------------------
def classify(url: str) -> tuple[str, str]:
    """(source, source_id) of a URL: an X or LinkedIn post keeps that source's
    own id, so the import and a capture of the post are one item; any other
    URL is a `web` item with the id `add_item` gives it."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if host in X_HOSTS and (post := re.search(r"/status(?:es)?/(\d+)", parsed.path)):
        return "x", post.group(1)
    if host.endswith("linkedin.com") and (urn := linkedin_source_id(None, url)).startswith("urn:li:"):
        return "linkedin", urn
    return "web", hashlib.sha256(url.encode()).hexdigest()[:32]


def run_import(session: Session, user_id: int, rows: list[Row], dry_run: bool = False) -> dict:
    """Create an item for every row not held yet: `{created, duplicates,
    skipped, errors}`. A duplicate is a URL already in the library (by source
    id) or earlier in the file; it is left as it is. Skipped rows (no usable
    URL, or an item the user deleted for good) are explained in `errors`, the
    first ERRORS_KEPT of them. With `dry_run` the same counts, nothing written. Commits."""
    out = {"created": 0, "duplicates": 0, "skipped": 0, "errors": []}

    def skip(n: int, reason: str) -> None:
        out["skipped"] += 1
        if len(out["errors"]) < ERRORS_KEPT:
            out["errors"].append(f"row {n}: {reason}")

    wanted: dict[tuple[str, str], tuple[int, Row]] = {}
    for n, row in enumerate(rows, 1):
        if urlparse(row.url).scheme not in ("http", "https") or not urlparse(row.url).hostname:
            skip(n, f"not a web address: {row.url[:80]!r}")
        elif (key := classify(row.url)) in wanted:
            out["duplicates"] += 1
        else:
            wanted[key] = (n, row)

    by_source: dict[str, list[str]] = {}
    for source, source_id in wanted:
        by_source.setdefault(source, []).append(source_id)
    for source, ids in by_source.items():
        for start in range(0, len(ids), LOOKUP_CHUNK):
            chunk = ids[start:start + LOOKUP_CHUNK]
            for source_id in session.scalars(select(Item.source_id).where(
                    Item.user_id == user_id, Item.source == source, Item.source_id.in_(chunk))):
                del wanted[(source, source_id)]
                out["duplicates"] += 1
            for source_id in session.scalars(select(DeletedItem.source_id).where(
                    DeletedItem.user_id == user_id, DeletedItem.source == source, DeletedItem.source_id.in_(chunk))):
                if (source, source_id) in wanted:
                    skip(wanted.pop((source, source_id))[0], "deleted from the library before; Kosha does not bring deleted items back")
    out["created"] = len(wanted)
    if dry_run or not wanted:
        return out

    # ponytail: one transaction and one in-memory batch for the whole file (20 MB
    # is some tens of thousands of bookmarks); write in chunks if imports get larger.
    folders = sorted({row.folder for _, row in wanted.values() if row.folder})
    result = ParseResult(collections=[{"source": COLLECTION_SOURCE, "source_id": f, "name": f, "kind": COLLECTION_KIND} for f in folders])
    for (source, source_id), (_, row) in wanted.items():
        web = source == "web"
        result.items.append(ParsedItem(
            source=source, source_id=source_id, fields={"url": row.url, "title": row.title, "body": row.excerpt},
            content_types=["link"] if web else [], saved_at=row.created, raw_format="import", raw=_plain(row),
            links=[{"url": row.url, "domain": domain_of(row.url), "title": row.title, "position": 0}] if web else [],
            media=[{"source_key": row.cover, "type": "photo", "position": 0, "original_url": row.cover}] if row.cover else [],
            collections=[{"source_id": row.folder}] if row.folder else []))
    result.items.reverse()  # upsert_result writes last to first; keep the file's order as the order of arrival
    upsert_result(session, user_id, result)

    held = {}
    for source, ids in by_source.items():
        for start in range(0, len(ids), LOOKUP_CHUNK):
            held.update({(source, i.source_id): i for i in session.scalars(select(Item).where(
                Item.user_id == user_id, Item.source == source, Item.source_id.in_(ids[start:start + LOOKUP_CHUNK])))})
    known_tags = set(session.scalars(select(Tag.id).where(Tag.user_id == user_id, Tag.namespace == DEFAULT_NAMESPACE)))
    tags: dict[str, Tag] = {}
    for key, (_, row) in wanted.items():
        item = held[key]
        item.note, item.starred = row.note, row.favorite
        for path in row.tags:
            if path.lower() not in tags:
                tags[path.lower()] = create_tag_path(session, user_id, DEFAULT_NAMESPACE, path)
            attach_tag(session, item.id, tags[path.lower()])
    # `create_tag_path` makes tags as an agent's proposals; the ones this import made are the owner's own.
    session.execute(update(Tag).where(Tag.user_id == user_id, Tag.namespace == DEFAULT_NAMESPACE, Tag.id.not_in(known_tags))
                    .values(status="approved", origin=TAG_ORIGIN))
    reindex(session, [item.id for item in held.values()])  # notes are searchable
    session.commit()
    summarize(session, user_id)  # rule-built, free
    return out


def _plain(row: Row) -> dict:
    """The row as JSON-ready values (kept under items.raw["import"], shown by a dry run)."""
    return {**asdict(row), "created": row.created.isoformat() if row.created else None}


def preview(rows: list[Row]) -> list[dict]:
    return [_plain(row) for row in rows[:PREVIEW_ROWS]]
