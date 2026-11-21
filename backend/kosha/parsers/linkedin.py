"""Parsers for LinkedIn captures: the saved-posts page read by the extension
(`saved_posts`) and the rows of LinkedIn's data export (`saves_csv`)."""
import hashlib
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from kosha.parsers.common import ParsedItem, ParseResult, domain_of, order_content_types

SOURCE = "linkedin"
MEDIA_TYPES = {"image": "photo", "photo": "photo", "video": "video", "document": "document", "audio": "audio"}
CONTENT_TYPE_BY_MEDIA = {"photo": "image", "video": "video", "document": "document", "audio": "audio"}
# --- The saved-posts card ---------------------------------------------------
HEADER_MAX_LINES = 10  # the time label sits within this many lines of the top
LABEL_RE = re.compile(r"(\d+)\s*(yr|mo|w|d|h|m)s?\s*•")
# The label alone, or after "Reposted from <name> •" or "Saved link •".
LABEL_LINE_RE = re.compile(r"(?:.{1,80} • )?\d+\s*(?:yr|mo|w|d|h|m)s?\s*•(?:\s*Edited\s*•?)?")
LABEL_UNIT_DAYS = {"m": 1 / 1440, "h": 1 / 24, "d": 1, "w": 7, "mo": 30.44, "yr": 365.25}
PRESENCE_RE = re.compile(r"Status is \w+")
DEGREE_RE = re.compile(r"•\s*(?:1st|2nd|3rd\+?)|Following|Promoted", re.IGNORECASE)
SEE_MORE = "…see more"
DOMAIN_RE = re.compile(r"(?:[a-z0-9-]+\.)+[a-z]{2,}", re.IGNORECASE)
URL_RE = re.compile(r"https?://[^\s<>\"]+")
GITHUB_TITLE_RE = re.compile(r"GitHub - ([\w.-]+/[\w.-]+)")
RAW_BODY_MIN_SHARE = 0.9
SAVED_ORDER_BATCH = 1000  # positions reserved per capture batch in `saved_order`
# What a poster address says the picture is (first match wins).
POSTER_KINDS = (
    ("company-logo", "avatar"), ("profile-displayphoto", "avatar"),
    ("articleshare", "link"), ("article-cover", "link"),
    ("document-cover", "document"),
    ("videocover", "video"), ("video-thumbnail", "video"), ("feedshare-thumbnail", "video"),
    ("feedshare-shrink", "photo"), ("image-shrink", "photo"),
)


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


def posted_at_from_label(label: str | None, captured_at: datetime | None) -> datetime | None:
    """Approximate post time from the page's relative label ("4d •", "3mo •",
    "Reposted from X • 1yr •"), counted back from when the page was captured.
    Only used when the post id carries no time."""
    match = LABEL_RE.search(label or "")
    if not match or captured_at is None:
        return None
    return captured_at - timedelta(days=int(match.group(1)) * LABEL_UNIT_DAYS[match.group(2)])


def split_raw_text(raw_text: str | None, embedded_text: str | None) -> tuple[list[str], str | None, str]:
    """The card's visible text as (header lines, time label, body). The card
    reads: [presence line], author name, connection degree or follower count,
    headline, the time label, the post text, "…see more", then the title and
    domain of an embedded link preview. Header and label are empty when the
    time label is not found near the top."""
    lines = [line.strip() for line in (raw_text or "").split("\n")]
    at = next((n for n, line in enumerate(lines[:HEADER_MAX_LINES]) if LABEL_LINE_RE.fullmatch(line)), None)
    if at is None:
        return [], None, ""
    header = [line for line in lines[:at] if line and not PRESENCE_RE.fullmatch(line)]
    body = lines[at + 1:]
    if SEE_MORE in body:
        body = body[:body.index(SEE_MORE)]
    text = "\n".join(body).strip()
    embedded = (embedded_text or "").strip()
    if embedded and text.endswith(embedded):  # a short post has no "…see more" before the preview
        text = text[:-len(embedded)].rstrip()
    return header, lines[at], re.sub(r"\n{3,}", "\n\n", text)


def headline_of(post: dict, header: list[str]) -> str | None:
    """The author's headline. The page's own field is often only the
    connection degree ("• 2nd"), so the header lines of the card are read
    instead: what follows the name, minus the degree. A company page has only
    its follower count, which is kept."""
    lines = [line for line in header[1:] if not DEGREE_RE.fullmatch(line)]
    field = (post.get("author_headline") or "").strip()
    junk = not field or DEGREE_RE.fullmatch(field) or LABEL_RE.search(field)  # a saved link's "headline" is its time label
    return (lines[0] if lines else None) or (None if junk else field)


def _media_and_link(post: dict) -> tuple[list[dict], list[dict], str | None]:
    """(media rows, link rows, author avatar). The page reports every picture
    as a "video" with only a poster, so the kind is read from the poster
    address: a photo, a video or document cover, the picture of a link
    preview (which becomes an `item_links` row with the preview's title and
    domain), or a company logo (the author's avatar)."""
    embedded = [line.strip() for line in (post.get("embedded_text") or "").split("\n") if line.strip()]
    domain = embedded[-1].lower() if len(embedded) > 1 and DOMAIN_RE.fullmatch(embedded[-1]) else None
    title = " ".join(embedded[:-1] if domain else embedded) or None
    media, link_image, avatar = [], None, None
    for m in post.get("media") or []:
        poster, url = m.get("poster"), m.get("url")
        if not poster and not url:
            continue
        kind = next((kind for marker, kind in POSTER_KINDS if marker in (poster or "")), None)
        if kind == "avatar":
            avatar = poster
        elif kind == "link":
            link_image = poster
        else:
            kind = kind or MEDIA_TYPES.get(m.get("type"), "photo")
            media.append({
                # Without the query string: its signature changes on every page load.
                "source_key": (url or poster).split("?")[0], "type": kind, "position": len(media),
                # A photo's "poster" is the photo itself; a video's or a document's is only its cover.
                "original_url": url or (poster if kind == "photo" else None), "preview_url": poster,
                # The preview's title belongs to the video or document when there is no link.
                **({"alt_text": title} if title and not domain and kind in ("video", "document") else {}),
            })
    links = []
    if domain or link_image:
        urls = URL_RE.findall(post.get("text") or post.get("raw_text") or "")
        # ponytail: the page does not give the preview's address. With exactly one
        # address in the text it is that one; otherwise the site's front page
        # stands in, so the title and domain are still searchable.
        repo = GITHUB_TITLE_RE.match(title or "") if domain == "github.com" else None  # the title names the repository
        url = (f"https://github.com/{repo.group(1)}" if repo else urls[0].rstrip(".,)") if len(urls) == 1
               else f"https://{domain}/" if domain else None)
        if url:
            links.append({"url": url, "domain": domain or domain_of(url), "title": title, "image_url": link_image, "position": 0})
    return media, links, avatar


def _parse_post(post: dict, out: dict[str, ParsedItem], is_saved: bool, captured_at: datetime | None = None) -> ParsedItem:
    source_id = source_id_for(post.get("urn"), post.get("url"), post.get("text"))
    header, label, raw_body = split_raw_text(post.get("raw_text"), post.get("embedded_text"))
    media, links, avatar = _media_and_link(post)
    # `text` has lost its line breaks and is sometimes missing; the card's raw
    # text keeps them. Take the raw body unless it is clearly the shorter one.
    text = (post.get("text") or "").strip()
    if raw_body and len(raw_body) >= RAW_BODY_MIN_SHARE * len(text):
        text = raw_body
    types = ({CONTENT_TYPE_BY_MEDIA[m["type"]] for m in media} | ({"text"} if text else set()) | ({"link"} if links else set()))
    author_url = (post.get("author_url") or "").split("?")[0].rstrip("/")
    headline = headline_of(post, header)
    item = ParsedItem(
        source=SOURCE,
        source_id=source_id,
        is_saved=is_saved,
        fields={
            "url": post.get("url"),
            # A saved link has no text of its own: the preview's title is the item's title.
            "title": links[0]["title"] if links and not text else None,
            "body": text or None,
            "author_name": post.get("author_name"),
            "author_handle": author_url.rsplit("/", 1)[-1] or None,
            "author_avatar": avatar,
            "posted_at": posted_at_from_id(source_id) or posted_at_from_label(label or post.get("posted_label"), captured_at),
        },
        content_types=order_content_types(types),
        raw_format="page",
        # The headline the web app shows is read from here (serialize.py), so the page's junk value is replaced.
        raw={**post, "author_headline": headline},
        media=media,
        links=links,
    )
    reshared = post.get("reshared")
    if reshared:  # a reshare with a comment is modelled exactly like an X quote
        inner = _parse_post(reshared, out, is_saved=False, captured_at=captured_at)
        item.relations.append({"relation": "quotes", "to_source_id": inner.source_id, "target_state": "held"})
    previous = out.get(source_id)
    if previous and previous.is_saved and not is_saved:
        return previous
    out[source_id] = item
    return item


def parse_saved_posts(payload: Any, meta: dict | None = None) -> ParseResult:
    """The saved-posts page lists the newest save first and gives no save
    time. Two things keep that order:
    - `saved_order`: the position in the capture, larger for a newer save.
    - `saved_at_est`: a lower bound of the save time. A post was saved after it
      was posted and after everything below it in the list was saved, so the
      estimate is the latest post time at or below its position in this
      batch. Lists and `saved:` filters then place LinkedIn saves among the
      others by time instead of all at the moment of capture.
    """
    captured_at = _parse_time((meta or {}).get("captured_at"))
    out: dict[str, ParsedItem] = {}
    saved: list[ParsedItem] = []
    for post in payload.get("posts") or []:
        saved.append(_parse_post(post, out, is_saved=True, captured_at=captured_at))
    base = int(captured_at.timestamp() * 1000) if captured_at else 0
    floor = None
    for position in range(len(saved) - 1, -1, -1):  # from the oldest save up
        item = saved[position]
        posted = item.fields.get("posted_at")
        floor = max(floor, posted) if floor and posted else (posted or floor)
        item.saved_at_est = floor
        # A later batch of the same scroll holds older saves, so it must sort lower.
        item.saved_order = -(base * SAVED_ORDER_BATCH + position)
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
