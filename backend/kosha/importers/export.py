"""Export the saved items a query matches, as a stream: Raindrop-compatible
CSV, Netscape bookmarks HTML, or JSON (everything the API shows of an item;
no raw payloads, no embeddings)."""
import csv
import html
import io
import json
from collections.abc import Iterator
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from kosha.models import Item
from kosha.search.query import compile_filter, parse
from kosha.serialize import serialize_items

EXPORT_BATCH = 500  # items read and written at a time
# Raindrop's own export header, so the file imports there (and here) as it is.
CSV_COLUMNS = ("id", "title", "note", "excerpt", "url", "folder", "tags", "created", "cover", "highlights", "favorite")
MEDIA_TYPES = {"csv": "text/csv; charset=utf-8", "html": "text/html; charset=utf-8", "json": "application/json"}
NETSCAPE_HEAD = ('<!DOCTYPE NETSCAPE-Bookmark-file-1>\n<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">\n'
                 "<TITLE>Bookmarks</TITLE>\n<H1>Bookmarks</H1>\n<DL><p>\n")


def _batches(session: Session, user_id: int, q: str) -> Iterator[list[dict]]:
    """The matching items, oldest id first, as the API serialises them."""
    where, last = compile_filter(session, user_id, parse(q)), 0
    while True:
        items = session.scalars(select(Item).where(*where, Item.id > last).order_by(Item.id).limit(EXPORT_BATCH)).all()
        if not items:
            return
        last = items[-1].id
        yield serialize_items(session, user_id, list(items), nest_quoted=False, detail=True)
        session.expunge_all()  # keep memory flat over a large library


def _flat(item: dict) -> dict:
    """The fields the bookmark formats share."""
    saved = (item["saved_at"] or item["captured_at"]).astimezone(UTC)
    cover = next((m["remote_url"] for m in item["media"] if m["type"] == "photo" and m["remote_url"]), None)
    return {"id": item["id"], "title": item["title"] or (item["body"] or "")[:80] or item["url"] or "", "note": item["note"] or "",
            "excerpt": item["body"] or "", "url": item["url"] or "", "folder": item["collections"][0]["name"] if item["collections"] else "",
            "tags": ["/".join(t["path"]) for t in item["tags"]], "created": saved, "cover": cover or "", "favorite": item["starred"]}


def _csv(batches: Iterator[list[dict]]) -> Iterator[str]:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_COLUMNS)
    for batch in batches:
        for row in map(_flat, batch):
            writer.writerow([row["id"], row["title"], row["note"], row["excerpt"], row["url"], row["folder"], ", ".join(row["tags"]),
                             row["created"].isoformat() if row["created"] else "", row["cover"], "", "true" if row["favorite"] else "false"])
        yield buffer.getvalue()
        buffer.seek(0)
        buffer.truncate()
    yield buffer.getvalue()  # the header alone, when nothing matched


def _html(batches: Iterator[list[dict]]) -> Iterator[str]:
    """Items without a folder first, then one <H3> per folder (a nested
    folder keeps its whole path as the name). Tags carry their full path
    ("AI/RAG") in TAGS. The lines are gathered per folder before anything
    is sent, since the format needs a folder's items together."""
    folders: dict[str, list[str]] = {"": []}
    for batch in batches:
        for row in map(_flat, batch):
            if not row["url"]:
                continue  # a note without an address is not a bookmark
            date = f' ADD_DATE="{int(row["created"].timestamp())}"' if row["created"] else ""
            tags = f' TAGS="{html.escape(",".join(row["tags"]), quote=True)}"' if row["tags"] else ""
            line = f'<DT><A HREF="{html.escape(row["url"], quote=True)}"{date}{tags}>{html.escape(row["title"])}</A>\n'
            if row["note"] or row["excerpt"]:
                line += f'<DD>{html.escape(" ".join((row["note"] or row["excerpt"]).split()))}\n'
            folders.setdefault(row["folder"], []).append(line)
    yield NETSCAPE_HEAD + "".join(folders.pop(""))
    for name, lines in sorted(folders.items()):
        yield f"<DT><H3>{html.escape(name)}</H3>\n<DL><p>\n" + "".join(lines) + "</DL><p>\n"
    yield "</DL><p>\n"


def _json(batches: Iterator[list[dict]]) -> Iterator[str]:
    def default(value):
        return value.isoformat() if isinstance(value, datetime) else str(value)

    first = True
    yield '{"items": ['
    for batch in batches:
        for item in batch:
            yield ("" if first else ",") + "\n" + json.dumps(item, ensure_ascii=False, default=default)
            first = False
    yield "\n]}\n"


def stream(session: Session, user_id: int, format: str, q: str = "") -> Iterator[str]:
    """The export as text chunks. `q` is the search bar's qualifier language
    (saved, non-archived items by default); raises QueryError when it does not parse."""
    return {"csv": _csv, "html": _html, "json": _json}[format](_batches(session, user_id, q))
