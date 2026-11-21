"""Parser for the official X API `GET /2/users/{id}/bookmarks` response.

Produces the same shape as the GraphQL parser so both routes merge into one
row per post. The API gives no save order or time, and does not return the
media of quoted posts. Field names are read in both spellings X has used
(`referenced_posts` / `referenced_tweets`, `note_post` / `note_tweet`,
`includes.posts` / `includes.tweets`).
"""
import html
from datetime import datetime
from typing import Any

from kosha.parsers.common import ParsedItem, ParseResult, dig, domain_of, order_content_types
from kosha.parsers.x_graphql import SOURCE, _status_url_matches, best_variant

RELATION_BY_REF = {"quoted": "quotes", "replied_to": "replies_to"}


def _parse_time(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def _media_row(m: dict, position: int) -> dict:
    variants = [
        {"bitrate": v.get("bit_rate"), "content_type": v.get("content_type"), "url": v.get("url")}
        for v in m.get("variants") or []
    ]
    best = best_variant(variants)
    is_moving = m.get("type") in ("video", "animated_gif")
    return {
        "source_key": m["media_key"],
        "type": m.get("type") or "photo",
        "position": position,
        "original_url": best["url"] if is_moving and best else m.get("url"),
        "preview_url": m.get("preview_image_url"),
        "variants": variants or None,
        "mime_type": best["content_type"] if is_moving and best else None,
        "duration_ms": m.get("duration_ms"),
        "width": m.get("width"),
        "height": m.get("height"),
        "alt_text": m.get("alt_text"),
    }


def _parse_post(post: dict, users: dict, media_by_key: dict, is_saved: bool) -> ParsedItem:
    post_id = post["id"]
    user = users.get(post.get("author_id")) or {}
    refs = post.get("referenced_posts") or post.get("referenced_tweets") or []
    quoted_id = next((r["id"] for r in refs if r.get("type") == "quoted"), None)

    note = dig(post, "note_post", "text") or dig(post, "note_tweet", "text")
    text = note or html.unescape(post.get("text") or "")  # `text` is HTML-escaped, note text is not
    links: list[dict] = []
    for u in dig(post, "entities", "urls") or []:
        tco, expanded = u.get("url") or "\0", u.get("expanded_url") or u.get("unwound_url")
        if u.get("media_key") or _status_url_matches(expanded, quoted_id):
            text = text.replace(tco, "")  # the post's own media, or the link that makes it a quote
            continue
        if not expanded:
            continue
        text = text.replace(tco, expanded)
        if expanded not in {l["url"] for l in links}:
            links.append({
                "url": expanded, "domain": domain_of(expanded), "position": len(links),
                "title": u.get("title"), "description": u.get("description"),
                "image_url": dig(u, "images", 0, "url"),
            })
    text = text.strip()

    keys = list(dig(post, "attachments", "media_keys") or [])
    article = post.get("article") or None
    title = None
    if article:
        title = article.get("title") or post.get("article_title")
        text = article.get("plain_text") or article.get("preview_text") or text
        if article.get("cover_media") and article["cover_media"] not in keys:
            keys.append(article["cover_media"])
    media = [_media_row(media_by_key[k], i) for i, k in enumerate(k for k in keys if k in media_by_key)]

    types = {"article"} if article else ({"text"} if text else set())
    types |= {"video" if m["type"] == "video" else "image" for m in media}
    if links:
        types.add("link")
    metrics = post.get("public_metrics") or {}
    durations = [m["duration_ms"] for m in media if m.get("duration_ms")]
    handle = user.get("username")
    item = ParsedItem(
        source=SOURCE,
        source_id=post_id,
        is_saved=is_saved,
        fields={
            "url": post.get("url") or f"https://x.com/{handle or 'i'}/status/{post_id}",
            "title": title,
            "body": text or None,
            "author_name": user.get("name"),
            "author_handle": handle,
            "author_avatar": user.get("profile_image_url"),
            "posted_at": _parse_time(post.get("created_at")),
            "lang": post.get("lang"),
            "likes": metrics.get("like_count"),
            "reposts": metrics.get("repost_count", metrics.get("retweet_count")),
            "replies": metrics.get("reply_count"),
            "views": metrics.get("impression_count"),
            "duration_s": round(max(durations) / 1000) if durations else None,
        },
        content_types=order_content_types(types),
        raw_format="api",
        raw=post,
        media=media,
        links=links,
    )
    for ref in refs:
        relation = RELATION_BY_REF.get(ref.get("type"))
        if relation:
            item.relations.append({"relation": relation, "to_source_id": ref["id"], "target_state": "missing"})
    root = post.get("conversation_id")
    # Self-thread: a reply to the author's own post inside their conversation.
    if root and root != post_id and post.get("in_reply_to_user_id") and post["in_reply_to_user_id"] == post.get("author_id"):
        item.relations.append({"relation": "thread_member", "to_source_id": root, "target_state": "missing"})
    return item


def parse_api_page(payload: Any) -> ParseResult:
    includes = payload.get("includes") or {}
    users = {u["id"]: u for u in includes.get("users") or []}
    media_by_key = {m["media_key"]: m for m in includes.get("media") or []}
    out: dict[str, ParsedItem] = {}
    # Referenced posts (quoted, replied-to) first, as context items; a post that
    # is also in `data` is then overwritten by its saved form.
    for post in includes.get("posts") or includes.get("tweets") or []:
        out[post["id"]] = _parse_post(post, users, media_by_key, is_saved=False)
    for post in payload.get("data") or []:
        out[post["id"]] = _parse_post(post, users, media_by_key, is_saved=True)
    return ParseResult(items=list(out.values()))
