from sqlalchemy import func, select

from kosha.ingest import ingest_envelope
from kosha.models import Capture, Item, ItemCollection, ItemMedia, ItemRelation
from tests.xfix import NOV_10, USER_B, bookmarks_page, entry, envelope, sort_index, timeline, tweet


def item(session, source_id):
    return session.scalar(select(Item).where(Item.source_id == source_id))


def test_capture_endpoint_is_idempotent(client, session):
    body = envelope("bookmarks_page", bookmarks_page(), operation="Bookmarks")
    first = client.post("/api/captures", json=body).json()
    assert first["items_new"] == 6 and first["items_updated"] == 0  # the quoted post is context, not counted
    hashes = dict(session.execute(select(Item.source_id, Item.content_hash)).all())

    again = client.post("/api/captures", json=body).json()
    assert again == {"capture_id": first["capture_id"], "items_new": 0, "items_updated": 0, "items_unchanged": 6}
    assert session.scalar(select(func.count()).select_from(Item)) == 7
    assert session.scalar(select(func.count()).select_from(Capture)) == 1
    assert dict(session.execute(select(Item.source_id, Item.content_hash)).all()) == hashes

    # The quoted post is its own item, linked and held; its nested quote is missing.
    quote, quoted = item(session, "1001"), item(session, "900")
    rel = session.scalar(select(ItemRelation).where(ItemRelation.from_item_id == quote.id))
    assert (rel.relation, rel.to_item_id, rel.target_state) == ("quotes", quoted.id, "held")
    nested = session.scalar(select(ItemRelation).where(ItemRelation.from_item_id == quoted.id))
    assert (nested.to_item_id, nested.to_source_id, nested.target_state) == (None, "800", "missing")
    assert item(session, "1005").unavailable_reason and item(session, "1005").raw

    assert client.post("/api/captures", json={"source": "x", "kind": "nope", "payload": {}}).status_code == 422


def test_context_item_becomes_saved_without_duplicate(session, user):
    ingest_envelope(session, user.id, envelope("bookmarks_page", bookmarks_page()))
    session.commit()
    assert item(session, "900").is_saved is False
    # Later the quoted post is bookmarked too, and the post it quotes arrives.
    later = timeline([
        entry("900", tweet("900", "Quoted &amp; original", user=USER_B), sort_index(NOV_10 + 60_000)),
        entry("800", tweet("800", "The root"), sort_index(NOV_10 + 120_000)),
    ])
    capture = ingest_envelope(session, user.id, envelope("bookmarks_page", later))
    session.commit()
    assert (capture.items_new, capture.items_updated) == (1, 1)
    quoted = item(session, "900")
    assert quoted.is_saved and quoted.saved_order == int(sort_index(NOV_10 + 60_000))
    assert session.scalar(select(func.count()).select_from(Item).where(Item.source_id == "900")) == 1
    # Media from the first, richer capture is kept; the waiting relation is now held.
    assert session.scalar(select(func.count()).select_from(ItemMedia).where(ItemMedia.item_id == quoted.id)) == 1
    nested = session.scalar(select(ItemRelation).where(ItemRelation.from_item_id == quoted.id))
    assert (nested.to_item_id, nested.target_state) == (item(session, "800").id, "held")


def test_richer_payload_wins_field_by_field(session, user):
    long_text = "The whole long post. " * 30
    graphql = timeline([entry("1", tweet("1", "cut…", note_tweet={"note_tweet_results": {"result": {"text": long_text}}}), sort_index(NOV_10))])
    ingest_envelope(session, user.id, envelope("bookmarks_page", graphql))
    api = {"data": [{"id": "1", "author_id": "100", "text": "cut…", "lang": "en", "created_at": "2026-11-02T10:00:00.000Z",
                     "public_metrics": {"like_count": 99, "repost_count": 3, "reply_count": 1, "impression_count": 5000}}],
           "includes": {"users": [{"id": "100", "name": "Ada", "username": "ada"}]}}
    capture = ingest_envelope(session, user.id, envelope("api_page", api))
    session.commit()
    row = item(session, "1")
    assert capture.items_updated == 1
    assert row.body == long_text.strip()  # the shorter API text does not replace the long one
    assert row.likes == 99 and row.views == 5000  # fresher counts do
    assert row.saved_order == int(sort_index(NOV_10))  # the API has no order; it is not erased
    assert set(row.raw) == {"graphql", "api"}


def test_folder_page_adds_collection(session, user):
    payload = timeline([entry("1001", tweet("1001", "In a folder"), "50")])
    for _ in range(2):
        ingest_envelope(session, user.id, envelope("folder_page", payload, folder_id="f1", folder_name="Coding with AI"))
    session.commit()
    assert session.scalar(select(func.count()).select_from(ItemCollection)) == 1


def test_deleted_item_does_not_come_back(client, session):
    body = envelope("bookmarks_page", timeline([entry("1", tweet("1", "Hello"), sort_index(NOV_10))]))
    client.post("/api/captures", json=body)
    assert client.delete(f"/api/items/{item(session, '1').id}").status_code == 204
    body["meta"] = {"again": True}
    assert client.post("/api/captures", json=body).json()["items_new"] == 0
    assert item(session, "1") is None
