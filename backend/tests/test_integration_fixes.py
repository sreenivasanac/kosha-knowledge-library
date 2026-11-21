"""Review fixes and small additions: bulk undo scope, sync isolation and resume,
the fair media-text worker, automatic media text, the display name, new CLI commands."""
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from typer.testing import CliRunner

from kosha import media_text
from kosha.app import app
from kosha.cli import app as cli
from kosha.config import get_settings
from kosha.models import Item, ItemMedia, Setting, SyncState, User
from kosha.sync import scheduler
from kosha.sync.x_api import RESUME_KEY, sync_bookmarks
from tests.conftest import API_TOKEN
from tests.test_accounts import browser
from tests.test_api import seed
from tests.test_sync_media import api_page, enable, mock_api

LOGIN = {"email": "admin@example.test", "password": "test-password"}


def test_undo_restores_only_what_the_bulk_action_changed(client):
    seed(client, 2)
    first, second = (i["id"] for i in client.get("/api/items").json()["items"])
    tag = client.post("/api/tags", json={"name": "AI"}).json()["id"]
    client.put(f"/api/items/{first}/tags/{tag}")  # already tagged before the bulk action
    batch = client.post("/api/items/bulk", json={"ids": [first, second], "starred": True, "add_tag_ids": [tag]}).json()["undo_batch_id"]
    client.patch(f"/api/items/{first}", json={"note": "written later", "read": True})
    client.delete(f"/api/items/{first}/tags/{tag}")  # a later removal of a tag the bulk action did not attach
    assert client.post(f"/api/undo/{batch}").status_code == 204
    rows = {i["id"]: i for i in client.get("/api/items").json()["items"]}
    assert rows[first]["starred"] is False and rows[first]["note"] == "written later" and rows[first]["read"] is True
    assert rows[first]["tags"] == [] and rows[second]["tags"] == [] and rows[second]["starred"] is False


def test_scheduler_goes_on_after_one_users_sync_fails(session, user, monkeypatch):
    other = User(email="b@example.test", password_hash="x", role="user")
    session.add(other)
    session.flush()
    for user_id in (user.id, other.id):
        session.add(Setting(user_id=user_id, key="x_api_sync_enabled", value=True))
    session.commit()

    def sync(session, user_id):
        if user_id == user.id:
            raise httpx.ConnectError("X is down")
        return {"status": "end"}

    monkeypatch.setattr(scheduler, "sync_bookmarks", sync)
    assert scheduler.run_due(session) == [{"status": "end"}]


def test_sync_resumes_where_the_cap_or_a_failure_stopped_it(session, user):
    enable(session, user, cap=0.101)  # room for one page
    client, calls = mock_api([api_page(["5", "4"], "t1")])
    assert sync_bookmarks(session, user.id, client)["status"] == "cap_reached"
    assert session.get(SyncState, (user.id, "x_api")).state[RESUME_KEY] == ["t1"]

    # Next run: the top of the list ends at a held bookmark, then the walk goes on from the saved page; it fails there.
    enable(session, user, cap=3.0)

    def failing(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("pagination_token") == "t1":
            return httpx.Response(503)
        return httpx.Response(200, json=api_page(["6", "5"], "t9"))

    with pytest.raises(httpx.HTTPStatusError):
        sync_bookmarks(session, user.id, httpx.Client(transport=httpx.MockTransport(failing)))
    session.expire_all()
    assert session.get(SyncState, (user.id, "x_api")).state[RESUME_KEY] == ["t1"]
    assert session.scalar(select(Item.id).where(Item.source_id == "6")) is not None  # the page that was paid for is kept

    client, calls = mock_api([api_page(["6"], "t9"), api_page(["3", "2"], "t2"), api_page(["1"])])
    done = sync_bookmarks(session, user.id, client)
    assert [c.url.params.get("pagination_token") for c in calls] == [None, "t1", "t2"]
    assert (done["status"], done["items_new"]) == ("end", 3)
    session.expire_all()
    assert session.get(SyncState, (user.id, "x_api")).state[RESUME_KEY] == []


def _queued_media(session, user_id, n, source_id):
    item = Item(user_id=user_id, source="x", source_id=source_id, body="post")
    session.add(item)
    session.flush()
    rows = [ItemMedia(item_id=item.id, source_key=f"{source_id}-{i}", type="photo", original_url="https://pbs.twimg.com/a.jpg",
                      extract_status="queued") for i in range(n)]
    session.add_all(rows)
    session.commit()
    return item, rows


def test_media_text_worker_takes_users_in_turn(session, user, monkeypatch):
    other = User(email="b@example.test", password_hash="x", role="user")
    session.add(other)
    session.flush()
    _queued_media(session, user.id, 3, "1")
    _queued_media(session, other.id, 1, "2")
    order = []

    def extract(session, user_id, media):
        order.append(user_id)
        media.extract_status = "done"
        session.commit()
        return 0.0, None

    monkeypatch.setattr(media_text, "extract_one", extract)
    media_text.run_all(session)
    assert order == [user.id, other.id, user.id, user.id]  # the second user did not wait for the first one's whole queue
    for user_id, done in ((user.id, 3), (other.id, 1)):
        state = session.get(SyncState, (user_id, media_text.STATE_SOURCE)).state
        assert (state["done"], state["running"]) == (done, False)


def test_media_text_auto_queues_new_items_within_the_cap(client, session, user, monkeypatch):
    assert client.get("/api/settings").json()["media_text_auto"] is False
    item, _ = _queued_media(session, user.id, 1, "1")
    session.execute(ItemMedia.__table__.update().values(extract_status="none"))
    session.commit()
    started = []
    monkeypatch.setattr(media_text, "start", lambda session, user_id, rows: started.append([m.id for m in rows]) or {"queued": len(rows)})
    assert media_text.auto_queue(session, user.id, [item.id]) == 0 and not started  # off by default
    assert client.patch("/api/settings", json={"media_text_auto": True}).json()["media_text_auto"] is True
    assert client.patch("/api/settings", json={"media_text_auto": "yes"}).status_code == 422
    assert media_text.auto_queue(session, user.id, [item.id]) == 1 and len(started) == 1
    client.patch("/api/settings", json={"llm_monthly_cap_usd": 0})  # nothing left this month: nothing is queued
    assert media_text.auto_queue(session, user.id, [item.id]) == 0 and len(started) == 1
    monkeypatch.setattr(get_settings(), "kosha_demo", True)
    client.patch("/api/settings", json={"llm_monthly_cap_usd": 5})
    assert media_text.auto_queue(session, user.id, [item.id]) == 0  # never in a demo
    monkeypatch.undo()

    # A capture hands its new saved items over.
    seen = []
    monkeypatch.setattr(media_text, "auto_queue", lambda session, user_id, ids: seen.append((user_id, len(ids))))
    seed(client, 2)
    seed(client, 2)  # the same posts again: nothing new
    assert seen == [(user.id, 2)]


def test_change_display_name(user):
    signed_in = browser(LOGIN)
    changed = signed_in.patch("/api/auth/me", json={"name": "  Ada L.  "})
    assert changed.status_code == 200 and changed.json()["name"] == "Ada L." and signed_in.get("/api/auth/me").json()["name"] == "Ada L."
    assert signed_in.patch("/api/auth/me", json={"name": "   "}).status_code == 422
    assert signed_in.patch("/api/auth/me", json={}).status_code == 422
    token = TestClient(app, headers={"Authorization": f"Bearer {API_TOKEN}"})
    assert token.patch("/api/auth/me", json={"name": "Bot"}).status_code in (401, 403)  # cookie sessions only


def test_storage_and_import_commands(user, session, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "kosha_media_dir", tmp_path / "media")
    tested = CliRunner().invoke(cli, ["storage-test"])
    assert tested.exit_code == 0 and "all work" in tested.output
    assert CliRunner().invoke(cli, ["storage-migrate", "--to", "local"]).exit_code == 1  # already the active backend
    file = tmp_path / "bookmarks.csv"
    file.write_text("url,title\nhttps://example.com/a,A page\nnot a url,x\n")
    dry = CliRunner().invoke(cli, ["import", str(file), "--dry-run"])
    assert dry.exit_code == 0 and "created=1" in dry.output and "skipped=1" in dry.output
    assert session.scalar(select(Item.id)) is None
    assert "created=1" in CliRunner().invoke(cli, ["import", str(file), "--user", "admin@example.test"]).output
    assert session.scalar(select(Item.url)) == "https://example.com/a"
    assert CliRunner().invoke(cli, ["import", str(tmp_path / "missing.csv")]).exit_code == 1
