"""The one target shape every parser produces, and small shared helpers."""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

# Fixed display order for `items.content_types`.
CONTENT_TYPE_ORDER = ("article", "text", "image", "video", "audio", "document", "link")


@dataclass
class ParsedItem:
    source: str
    source_id: str
    is_saved: bool = True
    # Column values for `items` (url, title, body, author_*, posted_at, lang,
    # likes, reposts, replies, views, duration_s, unavailable_reason).
    fields: dict[str, Any] = field(default_factory=dict)
    content_types: list[str] = field(default_factory=list)
    saved_order: int | None = None
    saved_at: datetime | None = None
    saved_at_est: datetime | None = None
    raw_format: str = "raw"  # key under items.raw
    raw: Any = None
    media: list[dict] = field(default_factory=list)  # item_media columns, keyed by source_key
    links: list[dict] = field(default_factory=list)  # item_links columns, keyed by url
    # {relation, to_source_id, target_state, position?}
    relations: list[dict] = field(default_factory=list)
    # Folder membership: {source_id, position?}
    collections: list[dict] = field(default_factory=list)


@dataclass
class ParseResult:
    items: list[ParsedItem] = field(default_factory=list)
    # {source, source_id, name, kind}
    collections: list[dict] = field(default_factory=list)


def dig(obj: Any, *path: str | int) -> Any:
    """Nested lookup that returns None instead of raising on a missing step."""
    for key in path:
        if isinstance(obj, dict):
            obj = obj.get(key)
        elif isinstance(obj, list) and isinstance(key, int) and -len(obj) <= key < len(obj):
            obj = obj[key]
        else:
            return None
    return obj


def domain_of(url: str | None) -> str | None:
    # ponytail: host minus "www.", not the registrable domain (blog.x.co.uk stays
    # as is). Switch to tldextract if `domain:` needs public-suffix accuracy.
    host = (urlparse(url or "").hostname or "").lower()
    return host.removeprefix("www.") or None


def order_content_types(types: set[str]) -> list[str]:
    return [t for t in CONTENT_TYPE_ORDER if t in types]


def to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
