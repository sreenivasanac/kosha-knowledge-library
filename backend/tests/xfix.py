"""Hand-made minimal X GraphQL bookmark payloads (no real data)."""

USER_A = {"rest_id": "100", "core": {"name": "Ada", "screen_name": "ada"}, "avatar": {"image_url": "https://pbs.twimg.com/profile_images/a_normal.jpg"}}
# The older layout, with everything under `legacy`.
USER_B = {"rest_id": "200", "legacy": {"name": "Bob", "screen_name": "bob", "profile_image_url_https": "https://pbs.twimg.com/profile_images/b_normal.jpg"}}

def sort_index(ms: int) -> str:
    """A bookmark sortIndex for the given Unix ms (see x_graphql: `>> 20`)."""
    return str(ms << 20)


def tweet(tid, text, user=USER_A, created="Mon Nov 02 10:00:00 +0000 2026", **extra):
    legacy = {
        "id_str": tid, "full_text": text, "created_at": created, "lang": "en",
        "user_id_str": user["rest_id"], "conversation_id_str": tid,
        "favorite_count": 5, "retweet_count": 2, "reply_count": 1,
        "entities": {"urls": []}, "display_text_range": [0, len(text)],
    }
    legacy.update(extra.pop("legacy", {}))
    return {"__typename": "Tweet", "rest_id": tid, "core": {"user_results": {"result": user}},
            "views": {"count": "1234"}, "legacy": legacy, **extra}


def entry(tid, result, index):
    return {
        "entryId": f"tweet-{tid}", "sortIndex": index,
        "content": {"entryType": "TimelineTimelineItem", "__typename": "TimelineTimelineItem",
                    "itemContent": {"itemType": "TimelineTweet", "__typename": "TimelineTweet",
                                    "tweet_results": {"result": result} if result is not None else {}}},
    }


def timeline(entries, key="bookmark_timeline_v2"):
    cursor = {"entryId": "cursor-bottom-1", "sortIndex": "1", "content": {"entryType": "TimelineTimelineCursor", "value": "c"}}
    return {"data": {key: {"timeline": {"instructions": [{"type": "TimelineAddEntries", "entries": [*entries, cursor]}]}}}}


NOV_10 = 1794304800000  # 2026-11-10T10:00:00Z in ms

VIDEO_MEDIA = {
    "media_key": "7_1", "type": "video", "url": "https://t.co/vid1",
    "media_url_https": "https://pbs.twimg.com/ext_tw_video_thumb/1/pu/img/poster.jpg",
    "original_info": {"width": 1280, "height": 720},
    "video_info": {"duration_millis": 61500, "variants": [
        {"content_type": "application/x-mpegURL", "url": "https://video.twimg.com/1/pl/x.m3u8"},
        {"bitrate": 832000, "content_type": "video/mp4", "url": "https://video.twimg.com/1/vid/480.mp4"},
        {"bitrate": 2176000, "content_type": "video/mp4", "url": "https://video.twimg.com/1/vid/720.mp4"},
    ]},
}
PHOTO_MEDIA = {"media_key": "3_2", "type": "photo", "url": "https://t.co/pic2",
               "media_url_https": "https://pbs.twimg.com/media/abc.jpg",
               "original_info": {"width": 800, "height": 600}, "ext_alt_text": "a chart"}


def bookmarks_page():
    """Six bookmarks: quote (whose quoted post quotes a third), long post with
    a link card, article, video reply in a self-thread, tombstone, and a
    visibility-wrapped photo post."""
    quoted = tweet("900", "Quoted &amp; original https://t.co/pic2", user=USER_B,
                   legacy={"extended_entities": {"media": [PHOTO_MEDIA]}, "quoted_status_id_str": "800", "is_quote_status": True})
    quote = tweet("1001", "My take on this", legacy={"quoted_status_id_str": "900", "is_quote_status": True,
                  "quoted_status_permalink": {"url": "https://t.co/q900", "expanded": "https://twitter.com/bob/status/900"}},
                  quoted_status_result={"result": quoted})
    long_post = tweet(
        "1002", "Short version… https://t.co/long",
        note_tweet={"note_tweet_results": {"result": {
            "text": "A long post. " * 40 + "Read https://t.co/link1",
            "entity_set": {"urls": [{"url": "https://t.co/link1", "expanded_url": "https://www.example.com/paper"}]}}}},
        card={"legacy": {"binding_values": [
            {"key": "title", "value": {"string_value": "The Paper"}},
            {"key": "description", "value": {"string_value": "About things"}},
            {"key": "card_url", "value": {"string_value": "https://t.co/link1"}},
            {"key": "thumbnail_image_original", "value": {"image_value": {"url": "https://pbs.twimg.com/card_img/1/x.jpg"}}},
        ]}},
    )
    article = tweet("1003", "https://t.co/art", article={"article_results": {"result": {
        "rest_id": "55", "title": "On Treasuries", "preview_text": "First lines",
        "content_state": {"blocks": [{"text": "Paragraph one."}, {"text": "Paragraph two."}]},
        "cover_media": {"media_info": {"original_img_url": "https://pbs.twimg.com/media/cover.jpg"}}}}})
    video_reply = tweet("1004", "@ada part two https://t.co/vid1", legacy={
        "display_text_range": [5, 13], "extended_entities": {"media": [VIDEO_MEDIA]},
        "in_reply_to_status_id_str": "700", "in_reply_to_user_id_str": "100", "in_reply_to_screen_name": "ada",
        "conversation_id_str": "700"})
    tombstone = {"__typename": "TweetTombstone", "tombstone": {"text": {"text": "This Post is from a suspended account."}}}
    wrapped = {"__typename": "TweetWithVisibilityResults", "tweet": tweet(
        "1006", "Limited https://t.co/pic2", legacy={"extended_entities": {"media": [PHOTO_MEDIA]}})}
    results = [("1001", quote), ("1002", long_post), ("1003", article), ("1004", video_reply), ("1005", tombstone), ("1006", wrapped)]
    # Newest first; deliberately shuffled, as X sometimes returns them.
    entries = [entry(tid, result, sort_index(NOV_10 - i * 60_000)) for i, (tid, result) in enumerate(results)]
    return timeline(entries[::-1])


def envelope(kind, payload, **meta):
    return {"source": "x", "kind": kind, "meta": meta, "payload": payload}
