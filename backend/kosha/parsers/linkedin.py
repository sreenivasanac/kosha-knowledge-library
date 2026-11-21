"""Parsers for LinkedIn captures: the saved-posts page read by the extension
(`saved_posts`) and the rows of LinkedIn's data export (`saves_csv`)."""
import hashlib
import re
from datetime import UTC, datetime
from typing import Any

from kosha.parsers.common import ParsedItem, ParseResult, order_content_types

SOURCE = "linkedin"
MEDIA_TYPES = {"image": "photo", "photo": "photo", "video": "video", "document": "document", "audio": "audio"}
CONTENT_TYPE_BY_MEDIA = {"photo": "image", "video": "video", "document": "document", "audio": "audio"}


def source_id_for(urn: str | None, url: str | None, text: str | None = None) -> str:
    """One id for a post however it reached us: `urn:li:<kind>:<digits>` when the
    URN or URL carries one (so the page capture and the export row match),
    else the URL, else a hash of the text."""
    for candidate in (urn, url):
        match = re.search(r"urn:li:(\w+):(\d+)", candidate or "") or re.search(r"(activity|ugcPost|share)[-:](\d+)", candidate or "")
        if match:
            return f"urn:li:{match.group(1)}:{match.group(2)}"
    return urn or url or "text:" + hashlib.sha256((text or "").encode()).hexdigest()[:24]


def posted_at_from_id(source_id: str) -> datetime | None:
    """LinkedIn activity ids carry their creation time in the bits above the
    low 22 (milliseconds since the Unix epoch)."""
    match = re.fullmatch(r"urn:li:(?:activity|ugcPost|share):(\d+)", source_id)
    if not match:
        return None
    try:
        when = datetime.fromtimestamp((int(match.group(1)) >> 22) / 1000, UTC)
    except (OverflowError, OSError, ValueError):
        return None
    return when if 2003 <= when.year <= datetime.now(UTC).year else None


def _parse_time(value: str | None) -> datetime | None:
    value = (value or "").strip()
    for attempt in (lambda: datetime.fromisoformat(value.replace("Z", "+00:00")),
                    lambda: datetime.strptime(value, "%Y-%m-%d %H:%M:%S %Z"),
                    lambda: datetime.strptime(value, "%m/%d/%y, %I:%M %p")):
        try:
            when = attempt()
            return when if when.tzinfo else when.replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def _parse_post(post: dict, out: dict[str, ParsedItem], is_saved: bool) -> ParsedItem:
    source_id = source_id_for(post.get("urn"), post.get("url"), post.get("text"))
    media = []
    for position, m in enumerate(post.get("media") or []):
        if not m.get("url") and not m.get("poster"):
            continue
        media.append({
            "source_key": m.get("url") or m["poster"],
            "type": MEDIA_TYPES.get(m.get("type"), "photo"),
            "position": position,
            "original_url": m.get("url"),
            "preview_url": m.get("poster"),
        })
    text = (post.get("text") or "").strip()
    types = {CONTENT_TYPE_BY_MEDIA[m["type"]] for m in media} | ({"text"} if text else set())
    author_url = (post.get("author_url") or "").split("?")[0].rstrip("/")
    item = ParsedItem(
        source=SOURCE,
        source_id=source_id,
        is_saved=is_saved,
        fields={
            "url": post.get("url"),
            "body": text or None,
            "author_name": post.get("author_name"),
            "author_handle": author_url.rsplit("/", 1)[-1] or None,
            "posted_at": posted_at_from_id(source_id),
        },
        content_types=order_content_types(types),
        raw_format="page",
        raw=post,
        media=media,
    )
    reshared = post.get("reshared")
    if reshared:  # a reshare with a comment is modelled exactly like an X quote
        inner = _parse_post(reshared, out, is_saved=False)
        item.relations.append({"relation": "quotes", "to_source_id": inner.source_id, "target_state": "held"})
    previous = out.get(source_id)
    if previous and previous.is_saved and not is_saved:
        return previous
    out[source_id] = item
    return item


def parse_saved_posts(payload: Any) -> ParseResult:
    out: dict[str, ParsedItem] = {}
    for post in payload.get("posts") or []:
        _parse_post(post, out, is_saved=True)
    return ParseResult(items=list(out.values()))


def parse_saves_csv(payload: Any) -> ParseResult:
    """Export rows carry only the URL and the real save time."""
    items = []
    for row in payload.get("rows") or []:
        if not row.get("url"):
            continue
        items.append(ParsedItem(
            source=SOURCE,
            source_id=source_id_for(None, row["url"]),
            fields={"url": row["url"]},
            saved_at=_parse_time(row.get("saved_at")),
            raw_format="export",
            raw=row,
        ))
    return ParseResult(items=items)
