import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from kosha.parsers import parse_capture
from kosha.parsers.linkedin import source_id_for
from kosha.parsers.x_graphql import epoch_report, estimate_saved_at
from tests.xfix import NOV_10, bookmarks_page, sort_index, timeline, entry, tweet

FIXTURES = Path(__file__).parent / "fixtures"
REAL_API_SAMPLE = Path("/private/tmp/claude-501/-Users-sreenivasanac-SoftwareProjects-portfolio-website/4c587d4d-a1c9-4eb8-b867-4ecc2a524206/scratchpad/r.json")


def by_id(result):
    return {i.source_id: i for i in result.items}


def test_graphql_bookmarks_page():
    items = by_id(parse_capture("x", "bookmarks_page", bookmarks_page()))
    assert set(items) == {"1001", "900", "1002", "1003", "1004", "1005", "1006"}

    quote, quoted = items["1001"], items["900"]
    assert quote.is_saved and not quoted.is_saved
    assert quote.fields["body"] == "My take on this"
    assert quote.relations == [{"relation": "quotes", "to_source_id": "900", "target_state": "held"}]
    # The quoted post arrives whole, with its media; its own quote is an id only.
    assert quoted.fields["body"] == "Quoted & original"  # unescaped, media link stripped
    assert quoted.fields["author_handle"] == "bob" and quoted.media[0]["alt_text"] == "a chart"
    assert quoted.relations == [{"relation": "quotes", "to_source_id": "800", "target_state": "missing"}]
    assert quoted.saved_order is None

    long_post = items["1002"]
    assert long_post.fields["body"].startswith("A long post.") and len(long_post.fields["body"]) > 280
    assert long_post.fields["body"].endswith("Read https://www.example.com/paper")
    assert long_post.links == [{"url": "https://www.example.com/paper", "domain": "example.com", "position": 0,
                                "title": "The Paper", "description": "About things",
                                "image_url": "https://pbs.twimg.com/card_img/1/x.jpg"}]
    assert long_post.content_types == ["text", "link"]

    article = items["1003"]
    assert article.fields["title"] == "On Treasuries"
    assert article.fields["body"] == "Paragraph one.\n\nParagraph two."
    assert article.content_types == ["article", "image"] and article.media[0]["source_key"] == "article_cover"

    video = items["1004"]
    assert video.fields["body"] == "part two"  # reply mention and media link removed
    assert video.content_types == ["text", "video"] and video.fields["duration_s"] == 62
    media = video.media[0]
    assert media["original_url"].endswith("720.mp4") and media["preview_url"].endswith("poster.jpg")
    assert len(media["variants"]) == 3 and media["duration_ms"] == 61500
    assert {(r["relation"], r["to_source_id"]) for r in video.relations} == {("replies_to", "700"), ("thread_member", "700")}

    tombstone = items["1005"]  # kept, not dropped
    assert tombstone.is_saved and "suspended" in tombstone.fields["unavailable_reason"] and tombstone.raw

    assert items["1006"].fields["body"] == "Limited" and items["1006"].content_types == ["text", "image"]

    # sortIndex is kept raw, orders the list, and decodes to the save time.
    assert quote.saved_order == int(sort_index(NOV_10)) > long_post.saved_order
    assert quote.saved_at_est == datetime.fromtimestamp(NOV_10 / 1000, UTC)
    assert quote.fields["likes"] == 5 and quote.fields["views"] == 1234


def test_folder_page_and_folder_list():
    payload = timeline([entry("1001", tweet("1001", "In a folder"), "50")], key="bookmark_collection_timeline")
    result = parse_capture("x", "folder_page", payload, {"folder_id": "f1", "folder_name": "Coding with AI"})
    assert result.collections == [{"source": "x", "source_id": "f1", "kind": "x_folder", "name": "Coding with AI"}]
    item = result.items[0]
    assert item.collections == [{"source_id": "f1", "position": 50}] and item.saved_order is None

    folders = {"data": {"viewer": {"user_results": {"result": {"bookmark_collections_slice": {
        "items": [{"id": "f1", "name": "Coding with AI"}, {"id": "f2", "name": "Recipes"}]}}}}}}
    assert {c["name"] for c in parse_capture("x", "folder_list", folders).collections} == {"Coding with AI", "Recipes"}


def test_save_time_epoch_check():
    posted = datetime(2026, 11, 1, tzinfo=UTC)
    now = datetime(2026, 11, 21, tzinfo=UTC)
    index = int(sort_index(NOV_10))
    assert estimate_saved_at(index, posted, now) == datetime.fromtimestamp(NOV_10 / 1000, UTC)
    # No estimate rather than an impossible one: before the post, or in the future.
    assert estimate_saved_at(index, datetime(2026, 11, 15, tzinfo=UTC), now) is None
    assert estimate_saved_at(index, posted, datetime(2026, 11, 5, tzinfo=UTC)) is None
    # A pair shaped like real ones (saved hours after posting): the snowflake
    # readings put the save before the post; `>> 20` as Unix ms does not.
    pair = (1795000000000 << 20, datetime(2026, 11, 18, tzinfo=UTC))
    assert epoch_report([pair], now) == {"total": 1, "unix_ms_shift20": 1, "twitter_snowflake": 0, "bookmark_debt": 0}


def test_api_page_fixture():
    payload = json.loads((FIXTURES / "x_api_page.json").read_text())
    result = parse_capture("x", "api_page", payload)
    saved = [i for i in result.items if i.is_saved]
    assert len(saved) == 3 and len(result.items) == 4
    quote, long_post, article = (by_id(result)[p["id"]] for p in payload["data"])
    assert quote.relations[0]["relation"] == "quotes" and by_id(result)[quote.relations[0]["to_source_id"]].is_saved is False
    assert long_post.fields["body"] == payload["data"][1]["note_post"]["text"]  # the full text, not the cut one
    assert long_post.media and long_post.fields["author_handle"]
    assert article.fields["title"] and "article" in article.content_types
    assert article.fields["body"] == payload["data"][2]["article"]["plain_text"]


@pytest.mark.skipif(not REAL_API_SAMPLE.exists(), reason="the real API sample is gone")
def test_api_page_real_sample_counts():
    """The owner's real 100-bookmark response (never committed); counts only."""
    result = parse_capture("x", "api_page", json.loads(REAL_API_SAMPLE.read_text()))
    saved = [i for i in result.items if i.is_saved]
    assert len(saved) == 100
    assert sum("article" in i.content_types for i in saved) == 4
    assert sum(any(r["relation"] == "quotes" for r in i.relations) for i in saved) == 11
    assert sum(any(r["relation"] == "replies_to" for r in i.relations) for i in saved) == 5
    assert all(i.fields["author_handle"] and i.fields["posted_at"] for i in saved)


def test_linkedin():
    urn = "urn:li:activity:7123456789012345678"
    result = parse_capture("linkedin", "saved_posts", {"posts": [{
        "urn": urn, "url": f"https://www.linkedin.com/feed/update/{urn}/",
        "author_name": "Cy", "author_url": "https://www.linkedin.com/in/cy-example/?miniProfile=1",
        "text": "My comment", "media": [{"type": "document", "url": "https://media.licdn.com/doc.pdf", "poster": None}],
        "reshared": {"urn": "urn:li:activity:7000000000000000000", "author_name": "Di", "text": "Original",
                     "media": [{"type": "image", "url": "https://media.licdn.com/img.jpg"}]},
    }]})
    post, inner = by_id(result)[urn], by_id(result)["urn:li:activity:7000000000000000000"]
    assert post.fields["author_handle"] == "cy-example" and post.content_types == ["text", "document"]
    assert post.fields["posted_at"].year == 2023  # decoded from the activity id
    assert post.relations == [{"relation": "quotes", "to_source_id": inner.source_id, "target_state": "held"}]
    assert not inner.is_saved and inner.media[0]["type"] == "photo"

    rows = parse_capture("linkedin", "saves_csv", {"rows": [
        {"url": "https://www.linkedin.com/feed/update/urn%3Ali%3Aactivity%3A7123456789012345678", "saved_at": "2026-05-01 08:30:00 UTC"}]})
    # The export row and the page capture agree on the id, so they merge.
    assert source_id_for(None, "https://www.linkedin.com/posts/cy_activity-7123456789012345678-AbCd") == urn
    assert rows.items[0].saved_at == datetime(2026, 5, 1, 8, 30, tzinfo=UTC)
