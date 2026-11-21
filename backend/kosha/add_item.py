"""Add one item by hand or by an agent: a URL or a piece of text, source `web`.

A URL's title and description are read from the page itself under the same
rules as media downloads (kosha/media.py): https only, redirects followed by
hand so every hop is checked, a size cap and a timeout. Any public host is
allowed here, so each hop must also resolve to a public address.
"""
import hashlib
import html
import ipaddress
import re
import socket
from urllib.parse import urljoin, urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from kosha.config import APP_NAME, MEDIA_MAX_REDIRECTS
from kosha.ingest import upsert_result
from kosha.models import Item
from kosha.parsers.common import ParsedItem, ParseResult, domain_of
from kosha.search.index import reindex
from kosha.summaries import summarize

PAGE_MAX_BYTES = 1024 * 1024  # the <head> is all that is read
PAGE_TIMEOUT_S = 15
SOURCE = "web"


def public_https(url: str) -> bool:
    """https, and a host that resolves only to public addresses (no loopback,
    private, link-local or reserved ranges)."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    # ponytail: checked at lookup time, and httpx resolves again when it connects; a
    # host that flips its DNS between the two gets through. Pin the address if this
    # ever serves untrusted callers.
    return all(ipaddress.ip_address(info[4][0]).is_global for info in infos)


def fetch_page_meta(url: str, client: httpx.Client | None = None) -> dict:
    """`{title, description}` from the page's head; empty when the page cannot
    be read safely. Never raises: the item is saved either way."""
    own = client is None
    client = client or httpx.Client(timeout=PAGE_TIMEOUT_S, headers={"User-Agent": f"{APP_NAME}/1.0"})
    try:
        for _ in range(MEDIA_MAX_REDIRECTS + 1):
            if not public_https(url):
                return {}
            with client.stream("GET", url, follow_redirects=False) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers.get("location", ""))
                    continue
                if response.status_code != 200 or "html" not in response.headers.get("content-type", ""):
                    return {}
                body = b""
                for chunk in response.iter_bytes():
                    body += chunk
                    if len(body) >= PAGE_MAX_BYTES:
                        break
            page = body[:PAGE_MAX_BYTES].decode(response.encoding or "utf-8", errors="replace")
            return {"title": _meta(page, "og:title") or _first(r"<title[^>]*>(.*?)</title>", page),
                    "description": _meta(page, "og:description") or _meta(page, "description")}
        return {}
    except (httpx.HTTPError, OSError):
        return {}
    finally:
        if own:
            client.close()


def _first(pattern: str, page: str) -> str | None:
    match = re.search(pattern, page, re.I | re.S)
    return " ".join(html.unescape(match.group(1)).split())[:500] or None if match else None


def _meta(page: str, name: str) -> str | None:
    """content of <meta property|name="…">, with the attributes in either order."""
    key = re.escape(name)
    return (_first(rf'<meta[^>]+(?:property|name)=["\']{key}["\'][^>]*content=["\'](.*?)["\']', page)
            or _first(rf'<meta[^>]+content=["\'](.*?)["\'][^>]*(?:property|name)=["\']{key}["\']', page))


def add_item(session: Session, user_id: int, url: str | None = None, text: str | None = None,
             title: str | None = None, note: str | None = None) -> tuple[Item, bool]:
    """Save a URL or a text as an item. The same URL (or the same text) is
    one item: adding it again returns the item already held. Returns (item,
    whether it is new). Commits."""
    url, text = (url or "").strip() or None, (text or "").strip() or None
    if not url and not text:
        raise ValueError("Give a url or some text")
    if url and urlparse(url).scheme not in ("http", "https"):
        raise ValueError("The url must start with http:// or https://")
    meta = fetch_page_meta(url) if url else {}
    title = title or meta.get("title")
    body = "\n\n".join(p for p in (text, meta.get("description") if not text else None) if p) or None
    source_id = hashlib.sha256((url or text).encode()).hexdigest()[:32]
    parsed = ParsedItem(
        source=SOURCE, source_id=source_id, fields={"url": url, "title": title, "body": body},
        content_types=["link"] if url else ["text"],
        links=[{"url": url, "domain": domain_of(url), "title": meta.get("title"), "description": meta.get("description"),
                "position": 0}] if url else [])
    counts = upsert_result(session, user_id, ParseResult(items=[parsed]))
    item = session.scalar(select(Item).where(Item.user_id == user_id, Item.source == SOURCE, Item.source_id == source_id))
    if item is None:
        raise ValueError("This item was deleted before; Kosha does not bring deleted items back")
    if note:
        item.note = note
        reindex(session, [item.id])  # the note is searchable
    session.commit()
    summarize(session, user_id)  # rule-built, free; the embedding comes with `jobs embed`
    return item, bool(counts.new)
