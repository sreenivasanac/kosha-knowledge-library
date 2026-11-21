from decimal import Decimal

import httpx
from sqlalchemy import func, select

from kosha.config import get_settings
from kosha.ingest import ingest_envelope
from kosha.media import fetch_media
from kosha.models import Item, ItemMedia, Setting, SpendLedger, SyncState
from kosha.sync.x_api import sync_bookmarks
from tests.xfix import bookmarks_page, envelope


def api_page(ids, next_token=None):
    return {"data": [{"id": i, "author_id": "100", "text": f"Post {i}", "created_at": "2026-11-02T10:00:00.000Z"} for i in ids],
            "includes": {"users": [{"id": "100", "name": "Ada", "username": "ada"}]},
            "meta": {"result_count": len(ids), **({"next_token": next_token} if next_token else {})}}


def mock_api(pages):
    """A client that serves the given pages in order and records the requests."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=pages[len(calls) - 1])

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def enable(session, user, cap=3.0):
    session.merge(Setting(user_id=user.id, key="x_api_sync_enabled", value=True))
    session.merge(Setting(user_id=user.id, key="x_api_monthly_cap_usd", value=cap))
    session.commit()


def test_sync_off_by_default(session, user):
    client, calls = mock_api([])
    assert sync_bookmarks(session, user.id, client)["status"] == "disabled" and not calls


def test_sync_pages_until_known_and_records_spend(session, user):
    enable(session, user)
    client, calls = mock_api([api_page(["5", "4"], "t1"), api_page(["3", "2"], "t2"), api_page(["1"])])
    first = sync_bookmarks(session, user.id, client)
    assert first == {"status": "end", "pages": 3, "items_new": 5, "spent_usd": 0.005}
    assert calls[0].headers["authorization"] == "Bearer test-x-token" and "/users/42/bookmarks" in calls[0].url.path
    assert calls[1].url.params["pagination_token"] == "t1"
    assert session.scalar(select(func.sum(SpendLedger.amount_usd))) == Decimal("0.005")
    # Newest first within the stable order, though the API gives no order key.
    assert list(session.scalars(select(Item.source_id).order_by(Item.seq.desc()).limit(2))) == ["5", "4"]

    # Next day: two new bookmarks, then a known one. Stops there, one page only.
    client, calls = mock_api([api_page(["7", "6", "5"], "t1"), api_page(["4", "3"], "t2")])
    second = sync_bookmarks(session, user.id, client)
    assert (second["status"], second["pages"], second["items_new"], len(calls)) == ("reached_known", 1, 2, 1)
    assert session.get(SyncState, (user.id, "x_api")).state["status"] == "reached_known"


def test_sync_stops_at_monthly_cap_and_after_stale_pages(session, user):
    enable(session, user, cap=0.101)  # room for one full page (100 x $0.001), not two
    client, calls = mock_api([api_page(["2", "1"], "t1"), api_page(["0"])])
    assert sync_bookmarks(session, user.id, client)["status"] == "cap_reached" and len(calls) == 1

    session.delete(session.get(SyncState, (user.id, "x_api")))  # a separate case: no walk to resume
    enable(session, user, cap=3.0)
    empty = [api_page([], f"t{i}") for i in range(5)]
    client, calls = mock_api(empty)
    assert sync_bookmarks(session, user.id, client)["status"] == "stale" and len(calls) == 3


def test_media_fetch_honours_settings_and_resumes(client, session, user):
    ingest_envelope(session, user.id, envelope("bookmarks_page", bookmarks_page()))
    session.commit()
    requested = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(404) if "cover" in str(request.url) else httpx.Response(200, content=b"bytes!")

    remote = httpx.Client(transport=httpx.MockTransport(handler))
    counts = fetch_media(session, user.id, client=remote)
    # Photos (full size) and the video's preview; not the video itself.
    assert counts == {"downloaded": 3, "failed": 1, "bytes": 18}
    assert "https://pbs.twimg.com/media/abc?format=jpg&name=orig" in requested
    assert not any(url.endswith(".mp4") for url in requested)
    video = session.scalar(select(ItemMedia).where(ItemMedia.type == "video"))
    assert video.local_path is None and video.local_preview_path
    assert (get_settings().kosha_media_dir / video.local_preview_path).read_bytes() == b"bytes!"

    photo = session.scalar(select(ItemMedia).where(ItemMedia.source_key == "3_2", ItemMedia.local_path.is_not(None)))
    served = client.get(f"/media/{photo.id}")
    assert served.status_code == 200 and served.content == b"bytes!"
    assert client.get(f"/media/{video.id}", params={"preview": 1}).content == b"bytes!"

    requested.clear()
    assert fetch_media(session, user.id, client=remote)["downloaded"] == 0  # only the failed one is retried
    assert len(requested) == 1
    session.merge(Setting(user_id=user.id, key="store_videos", value=True))
    session.commit()
    assert fetch_media(session, user.id, client=remote)["downloaded"] == 1 and any(u.endswith("720.mp4") for u in requested)
