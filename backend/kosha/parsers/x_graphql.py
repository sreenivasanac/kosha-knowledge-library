"""Parser for X's web GraphQL bookmark responses (`Bookmarks`,
`BookmarkFolderTimeline`, and the bookmark-folder list).

The tweet extraction logic (visibility wrappers, tombstones, long-text
`note_tweet`, quoted posts, media variants, sortIndex ordering) is ported from
twitter-web-exporter by prinsss, MIT licence:
https://github.com/prinsss/twitter-web-exporter (src/utils/api.ts,
src/types/tweet.ts, src/modules/bookmarks/api.ts). See backend/NOTICE.
Articles, link cards, folders and kept-not-dropped unavailable posts are new.
"""
import html
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from kosha.parsers.common import ParsedItem, ParseResult, dig, domain_of, order_content_types, to_int

SOURCE = "x"

# --- Save-time estimate from sortIndex ---------------------------------------
# X gives no save time, but a bookmark entry's sortIndex encodes one. Three
# decodings were candidates:
#   unix_ms_shift20    (sortIndex >> 20) is Unix milliseconds
#   twitter_snowflake  (sortIndex >> 22) + 1288834974657, the standard snowflake epoch
#   bookmark_debt      (sortIndex >> 22) + 1345287750720, the bookmark-debt project's constant
# A save time is plausible only if it is not before the post's own created_at
# and not after the capture. Measured on the owner's first real capture
# (21 Nov 2026, 1,358 bookmarks; `epoch_report`, printed by `kosha ingest-dir`):
#   unix_ms_shift20    1,358 plausible; median 9 hours after the post, newest
#                      2.7 hours before the capture
#   twitter_snowflake  0 plausible (every save ~22 months before its post)
#   bookmark_debt      the newest ~150 land up to 13 days before their post, and
#                      the whole list is squeezed into a quarter of its real span
# So the snowflake reading is wrong for bookmark timelines and unix_ms_shift20
# is used. If X changes the encoding, the report will show it and estimates
# become empty rather than wrong.
SORT_INDEX_DECODERS: dict[str, tuple[int, int]] = {  # name -> (shift, epoch ms)
    "unix_ms_shift20": (20, 0),
    "twitter_snowflake": (22, 1288834974657),
    "bookmark_debt": (22, 1345287750720),
}
SAVED_AT_DECODER = "unix_ms_shift20"


def sort_index_time(sort_index: int, decoder: str = SAVED_AT_DECODER) -> datetime | None:
    shift, epoch_ms = SORT_INDEX_DECODERS[decoder]
    try:
        return datetime.fromtimestamp(((sort_index >> shift) + epoch_ms) / 1000, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _plausible(saved: datetime | None, posted_at: datetime | None, now: datetime) -> bool:
    """A save time cannot precede the post, nor lie in the future."""
    if saved is None or saved > now + timedelta(days=1):
        return False
    return posted_at is None or saved >= posted_at


def estimate_saved_at(sort_index: int | None, posted_at: datetime | None, now: datetime | None = None) -> datetime | None:
    """Estimated save time for one bookmark, or None when the decoded time is
    impossible for this post (better no estimate than a wrong one)."""
    if sort_index is None:
        return None
    saved = sort_index_time(sort_index)
    return saved if _plausible(saved, posted_at, now or datetime.now(UTC)) else None


def epoch_report(pairs: list[tuple[int, datetime | None]], now: datetime | None = None) -> dict[str, int]:
    """The check behind the choice above: for (sortIndex, posted_at) pairs, how
    many each decoding turns into a plausible save time."""
    now = now or datetime.now(UTC)
    report = {"total": len(pairs)}
    for name in SORT_INDEX_DECODERS:
        report[name] = sum(_plausible(sort_index_time(si, name), posted_at, now) for si, posted_at in pairs)
    return report


# --- Timeline walking --------------------------------------------------------
def find_instructions(payload: Any) -> list[dict]:
    """Find the timeline `instructions` list wherever X has put it this month
    (`data.bookmark_timeline_v2.timeline`, the folder timeline, ...)."""
    stack = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            inst = node.get("instructions")
            if isinstance(inst, list) and all(isinstance(i, dict) and "type" in i for i in inst):
                return inst
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return []


def _timeline_tweets(payload: Any) -> list[tuple[str, int | None, dict]]:
    """(entry id, sortIndex, itemContent) for every tweet entry, newest first."""
    found = []
    for inst in find_instructions(payload):
        if inst.get("type") != "TimelineAddEntries":
            continue
        for entry in inst.get("entries") or []:
            content = entry.get("content") or {}
            sort_index = to_int(entry.get("sortIndex"))
            if "itemContent" in content:
                contents = [(entry.get("entryId", ""), content["itemContent"])]
            else:  # a module groups several items under one entry
                contents = [(i.get("entryId", ""), dig(i, "item", "itemContent")) for i in content.get("items") or []]
            for entry_id, item_content in contents:
                if isinstance(item_content, dict) and "tweet_results" in item_content:
                    found.append((entry_id, sort_index, item_content))
    # X may return entries in any order; sortIndex descending is timeline order.
    found.sort(key=lambda f: f[1] or 0, reverse=True)
    return found


def _unwrap(result: dict | None) -> tuple[dict | None, str | None]:
    """Return (tweet, None) or (None, reason) for a `tweet_results.result`."""
    if not result:
        return None, "unavailable"
    kind = result.get("__typename")
    if kind == "TweetWithVisibilityResults":
        result, kind = result.get("tweet") or {}, "Tweet"
    if kind == "Tweet":
        return (result, None) if result.get("legacy") else (None, "empty")
    if kind == "TweetTombstone":
        return None, dig(result, "tombstone", "text", "text") or "tombstone"
    if kind == "TweetUnavailable":
        return None, result.get("reason") or "unavailable"
    return None, f"unknown result type {kind}"


# --- One tweet ---------------------------------------------------------------
def _parse_time(value: str | None) -> datetime | None:
    try:
        return datetime.strptime(value or "", "%a %b %d %H:%M:%S %z %Y")
    except ValueError:
        return None


def best_variant(variants: list[dict]) -> dict | None:
    """Highest-bitrate mp4 (HLS playlists carry no bitrate)."""
    mp4 = [v for v in variants if v.get("content_type") == "video/mp4"]
    return max(mp4, key=lambda v: v.get("bitrate") or 0) if mp4 else (variants[0] if variants else None)


def _media_rows(legacy: dict) -> list[dict]:
    # Prefer `extended_entities`: `entities.media` lists only the first file.
    media = dig(legacy, "extended_entities", "media") or dig(legacy, "entities", "media") or []
    rows = []
    for position, m in enumerate(media):
        variants = [
            {"bitrate": v.get("bitrate"), "content_type": v.get("content_type"), "url": v.get("url")}
            for v in dig(m, "video_info", "variants") or []
        ]
        best = best_variant(variants)
        is_moving = m.get("type") in ("video", "animated_gif")
        rows.append({
            "source_key": m.get("media_key") or m.get("id_str") or m.get("media_url_https"),
            "type": m.get("type") or "photo",
            "position": position,
            "original_url": best["url"] if is_moving and best else m.get("media_url_https"),
            "preview_url": m.get("media_url_https") if is_moving else None,
            "variants": variants or None,
            "mime_type": best["content_type"] if is_moving and best else None,
            "duration_ms": dig(m, "video_info", "duration_millis"),
            "width": dig(m, "original_info", "width"),
            "height": dig(m, "original_info", "height"),
            "alt_text": m.get("ext_alt_text"),
        })
    return rows


def _card_link(tweet: dict) -> dict | None:
    """Title, description and image of the link card, when the post has one."""
    values = {v.get("key"): v.get("value") or {} for v in dig(tweet, "card", "legacy", "binding_values") or []}
    title = dig(values, "title", "string_value")
    if not title:
        return None
    image = next(
        (dig(values, k, "image_value", "url") for k in (
            "thumbnail_image_original", "summary_photo_image_original",
            "photo_image_full_size_original", "player_image_original",
        ) if dig(values, k, "image_value", "url")),
        None,
    )
    return {
        "tco": dig(values, "card_url", "string_value"),
        "title": title,
        "description": dig(values, "description", "string_value"),
        "image_url": image,
    }


def _article(tweet: dict) -> dict | None:
    art = dig(tweet, "article", "article_results", "result")
    if not art:
        return None
    blocks = dig(art, "content_state", "blocks") or []
    body = art.get("plain_text") or "\n\n".join(b.get("text", "") for b in blocks) or art.get("preview_text")
    return {"title": art.get("title"), "body": body, "cover": dig(art, "cover_media", "media_info", "original_img_url")}


def _status_url_matches(url: str | None, tweet_id: str | None) -> bool:
    return bool(tweet_id and url and re.search(rf"(?:twitter|x)\.com/[^/]+/status/{tweet_id}\b", url))


def parse_tweet(tweet: dict, out: dict[str, ParsedItem], is_saved: bool) -> ParsedItem:
    """Turn one unwrapped Tweet object into a ParsedItem (and, through `out`,
    its quoted post as a context item)."""
    legacy = tweet["legacy"]
    tweet_id = tweet.get("rest_id") or legacy.get("id_str")
    user = dig(tweet, "core", "user_results", "result") or {}
    # X moved name/handle/avatar out of `legacy`; support both layouts.
    handle = dig(user, "core", "screen_name") or dig(user, "legacy", "screen_name")
    note = dig(tweet, "note_tweet", "note_tweet_results", "result")
    media = _media_rows(legacy)
    quoted_id = legacy.get("quoted_status_id_str") or dig(tweet, "quotedRefResult", "result", "rest_id")

    # --- text: long-post text when present, else full_text without the leading
    # reply mentions; media t.co links removed, other t.co links expanded.
    if note:
        text, urls = note.get("text") or "", dig(note, "entity_set", "urls") or []
    else:
        text = legacy.get("full_text") or ""
        start = dig(legacy, "display_text_range", 0) or 0
        text, urls = text[start:], dig(legacy, "entities", "urls") or []
    for m in dig(legacy, "extended_entities", "media") or dig(legacy, "entities", "media") or []:
        text = text.replace(m.get("url") or "\0", "")
    text = text.replace(dig(legacy, "quoted_status_permalink", "url") or "\0", "")
    links: list[dict] = []
    by_tco: dict[str, dict] = {}
    for u in urls:
        expanded = u.get("expanded_url") or u.get("url")
        if not expanded:
            continue
        if _status_url_matches(expanded, quoted_id):  # the link that makes it a quote
            text = text.replace(u.get("url") or "\0", "")
            continue
        text = text.replace(u.get("url") or "\0", expanded)
        if expanded not in {l["url"] for l in links}:
            links.append({"url": expanded, "domain": domain_of(expanded), "position": len(links)})
            by_tco[u.get("url")] = links[-1]
    if not note:
        text = html.unescape(text)  # full_text is HTML-escaped, note text is not
    text = text.strip()

    card = _card_link(tweet)
    if card:
        target = by_tco.get(card["tco"]) or (links[0] if len(links) == 1 else None)
        if target:
            target.update({k: card[k] for k in ("title", "description", "image_url")})

    article = _article(tweet)
    title = None
    if article:
        title = article["title"]
        text = article["body"] or text
        if article["cover"]:
            media.append({"source_key": "article_cover", "type": "photo", "position": len(media),
                          "original_url": article["cover"]})

    types = set()
    if article:
        types.add("article")
    elif text:
        types.add("text")
    for m in media:
        types.add("video" if m["type"] == "video" else "image")
    if links:
        types.add("link")

    durations = [m["duration_ms"] for m in media if m.get("duration_ms")]
    item = ParsedItem(
        source=SOURCE,
        source_id=tweet_id,
        is_saved=is_saved,
        fields={
            "url": f"https://x.com/{handle or 'i'}/status/{tweet_id}",
            "title": title,
            "body": text or None,
            "author_name": dig(user, "core", "name") or dig(user, "legacy", "name"),
            "author_handle": handle,
            "author_avatar": dig(user, "avatar", "image_url") or dig(user, "legacy", "profile_image_url_https"),
            "posted_at": _parse_time(legacy.get("created_at")),
            "lang": legacy.get("lang"),
            "likes": legacy.get("favorite_count"),
            "reposts": legacy.get("retweet_count"),
            "replies": legacy.get("reply_count"),
            "views": to_int(dig(tweet, "views", "count")),
            "duration_s": round(max(durations) / 1000) if durations else None,
        },
        content_types=order_content_types(types),
        raw_format="graphql",
        raw=tweet,
        media=media,
        links=links,
    )

    # --- relations
    quoted_result = dig(tweet, "quoted_status_result", "result")
    if quoted_result:
        quoted, _reason = _unwrap(quoted_result)
        if quoted:
            parse_tweet(quoted, out, is_saved=False)
            quoted_id = quoted.get("rest_id") or quoted_id
        item.relations.append({
            "relation": "quotes",
            "to_source_id": quoted_id or f"unknown-quote-of-{tweet_id}",
            "target_state": "held" if quoted else "unavailable",
        })
    elif quoted_id:
        # A quote nested inside a quoted post arrives as an id only.
        item.relations.append({"relation": "quotes", "to_source_id": quoted_id, "target_state": "missing"})
    reply_to = legacy.get("in_reply_to_status_id_str")
    if reply_to:
        item.relations.append({"relation": "replies_to", "to_source_id": reply_to, "target_state": "missing"})
        root = legacy.get("conversation_id_str")
        # Self-thread: the author replying to themselves inside their own
        # conversation. Position is not known from one post; readers order a
        # thread by posted_at.
        if root and root != tweet_id and legacy.get("in_reply_to_user_id_str") == legacy.get("user_id_str"):
            item.relations.append({"relation": "thread_member", "to_source_id": root, "target_state": "missing"})

    # A post seen both as a bookmark and as a quote in one page stays saved.
    previous = out.get(tweet_id)
    if previous and previous.is_saved and not is_saved:
        return previous
    if previous:
        item.saved_order, item.saved_at_est, item.collections = previous.saved_order, previous.saved_at_est, previous.collections
        item.is_saved = item.is_saved or previous.is_saved
    out[tweet_id] = item
    return item


# --- Entry points ------------------------------------------------------------
def parse_timeline(payload: Any, meta: dict | None, in_folder: bool) -> ParseResult:
    """`bookmarks_page` and `folder_page`. In a folder page the sortIndex orders
    the folder, not the main bookmark list, so it is kept on the membership."""
    meta = meta or {}
    result = ParseResult()
    folder_id = str(meta["folder_id"]) if in_folder and meta.get("folder_id") else None
    if folder_id:
        result.collections.append({
            "source": SOURCE, "source_id": folder_id, "kind": "x_folder",
            "name": meta.get("folder_name") or folder_id,
        })
    out: dict[str, ParsedItem] = {}
    for entry_id, sort_index, item_content in _timeline_tweets(payload):
        tweet, reason = _unwrap(dig(item_content, "tweet_results", "result"))
        if tweet:
            item = parse_tweet(tweet, out, is_saved=True)
        else:
            # Deleted, private or withheld: keep the bookmark as a row with the
            # raw entry rather than dropping it.
            match = re.search(r"(\d+)$", entry_id)
            if not match:
                continue
            item = out.setdefault(match.group(1), ParsedItem(
                source=SOURCE, source_id=match.group(1), raw_format="graphql_unavailable",
                raw=item_content, fields={"unavailable_reason": reason},
            ))
            item.is_saved = True
        if folder_id:
            item.collections.append({"source_id": folder_id, "position": sort_index})
        elif not in_folder and sort_index is not None:
            # (a folder page that arrived without its folder id still orders
            # the folder, so it must not overwrite the main list's order)
            item.saved_order = sort_index
            item.saved_at_est = estimate_saved_at(sort_index, item.fields.get("posted_at"))
    result.items = list(out.values())
    return result


def parse_folder_list(payload: Any) -> ParseResult:
    """Bookmark folders: every {id, name} object under
    `bookmark_collections_slice` (or anywhere, if X renames that key)."""
    result = ParseResult()
    stack = [payload]
    while stack:  # narrow to the slice when it exists
        node = stack.pop()
        if isinstance(node, dict):
            if "bookmark_collections_slice" in node:
                payload = node["bookmark_collections_slice"]
                break
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    stack, seen = [payload], set()
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            folder_id, name = node.get("id") or node.get("rest_id"), node.get("name")
            if isinstance(folder_id, str) and isinstance(name, str) and folder_id not in seen:
                seen.add(folder_id)
                result.collections.append({"source": SOURCE, "source_id": folder_id, "kind": "x_folder", "name": name})
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return result
