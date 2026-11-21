"""Health, removals, list order, and the request/login/CSRF guards."""
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from kosha import auth
from kosha.app import app, create_app
from kosha.config import CSRF_HEADER, CSRF_HEADER_VALUE, LOGIN_MAX_FAILURES, MAX_BODY_BYTES, get_settings
from kosha.media import _download, allowed_url
from kosha.models import DeletedItem, Item, ItemMedia, ItemTag, Setting, Tag, User
from kosha.parsers.x_graphql import parse_timeline
from kosha.sync.scheduler import run_due
from tests.xfix import NOV_10, entry, envelope, sort_index, timeline, tweet

LOGIN = {"email": "admin@example.test", "password": "test-password"}


def test_health(user, client):
    assert client.get("/api/health").json() == {"status": "ok"}
    assert TestClient(app).get("/api/health").status_code == 401
    assert TestClient(app, headers={"Authorization": "Bearer wrong"}).get("/api/health").status_code == 401


def test_removals(client, session, user, tmp_path):
    client.post("/api/captures", json=envelope("bookmarks_page", timeline([
        entry(str(n), tweet(str(n), f"Post {n}"), sort_index(NOV_10 + n)) for n in (1, 2, 3)])))
    ids = {i["source_id"]: i["id"] for i in client.get("/api/items").json()["items"]}
    assert client.get("/api/removals/pending").json() == []
    client.post("/api/items/bulk", json={"ids": [ids["1"], ids["2"]], "archived": True})
    pending = client.get("/api/removals/pending").json()
    assert [(p["source"], p["source_id"]) for p in pending] == [("x", "1"), ("x", "2")] and pending[0]["item_id"] == ids["1"]

    # A stored file goes with the item, wherever the server was started from.
    media_dir = get_settings().kosha_media_dir
    (media_dir / "9").mkdir(parents=True, exist_ok=True)
    (media_dir / "9" / "a.jpg").write_bytes(b"x")
    session.add(ItemMedia(item_id=ids["1"], source_key="k", type="photo", local_path="9/a.jpg"))
    session.commit()

    assert client.post("/api/removals/result", json={"item_id": ids["2"], "status": "failed", "detail": "x"}).json()["deleted"] is False
    assert client.post("/api/removals/result", json={"item_id": ids["1"], "status": "removed"}).json()["deleted"] is True
    assert client.post("/api/removals/result", json={"item_id": ids["1"], "status": "removed"}).status_code == 404
    assert not (media_dir / "9" / "a.jpg").exists()
    assert [p["source_id"] for p in client.get("/api/removals/pending").json()] == ["2"]
    assert session.scalars(select(DeletedItem.source_id)).all() == ["1"]
    assert client.post("/api/removals/result", json={"item_id": ids["2"], "status": "already_gone"}).json()["deleted"] is True
    assert client.post("/api/removals/result", json={"item_id": ids["3"], "status": "nope"}).status_code == 422


def test_items_without_a_save_order_sort_by_capture_time(client, session, user):
    now = datetime.now(UTC)
    rows = [("old", 100, now - timedelta(days=30), None), ("new", 200, now - timedelta(days=1), None),
            ("api", None, None, now - timedelta(days=10))]  # no order from the source: placed by captured_at
    for source_id, order, estimate, captured in rows:
        session.add(Item(user_id=user.id, source="x", source_id=source_id, saved_order=order, saved_at_est=estimate,
                         **({"captured_at": captured} if captured else {})))
    session.commit()
    assert [i["source_id"] for i in client.get("/api/items").json()["items"]] == ["new", "api", "old"]


def test_oversized_body_is_refused(client):
    big = client.post("/api/captures", content=b"x", headers={"Content-Length": str(MAX_BODY_BYTES + 1), "Content-Type": "application/json"})
    assert big.status_code == 413

    def chunks():  # no Content-Length: counted while it streams
        for _ in range(MAX_BODY_BYTES // (1024 * 1024) + 2):
            yield b" " * 1024 * 1024

    assert client.post("/api/captures", content=chunks(), headers={"Content-Type": "application/json"}).status_code == 413
    assert client.post("/api/search/parse", json={"q": "a"}).status_code == 200


def test_login_rate_limit(user):
    auth._login_failures.clear()
    anonymous = TestClient(app)
    for _ in range(LOGIN_MAX_FAILURES):
        assert anonymous.post("/api/auth/login", json={**LOGIN, "password": "nope"}).status_code == 401
    assert anonymous.post("/api/auth/login", json=LOGIN).status_code == 429  # even the right password waits
    auth._login_failures.clear()
    assert anonymous.post("/api/auth/login", json=LOGIN).status_code == 200


def test_cookie_requests_need_the_csrf_header(user):
    auth._login_failures.clear()
    browser = TestClient(app)
    assert browser.post("/api/auth/login", json=LOGIN).status_code == 200
    assert browser.get("/api/items").status_code == 200  # reads are not affected
    assert browser.post("/api/lists", json={"name": "x"}).status_code == 403
    assert browser.post("/api/lists", json={"name": "x"}, headers={CSRF_HEADER: CSRF_HEADER_VALUE}).status_code == 201
    token = TestClient(app, headers={"Authorization": "Bearer test-api-token"})
    assert token.post("/api/lists", json={"name": "y"}).status_code == 201  # bearer tokens are exempt


def test_docs_need_sign_in_outside_dev(user, monkeypatch):
    monkeypatch.setenv("KOSHA_DEV", "false")
    get_settings.cache_clear()
    try:
        private = create_app()
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
    assert TestClient(private).get("/api/openapi.json").status_code == 401
    assert TestClient(private).get("/api/docs").status_code == 401
    signed_in = TestClient(private, headers={"Authorization": "Bearer test-api-token"})
    assert "/api/items" in signed_in.get("/api/openapi.json").json()["paths"]
    assert signed_in.get("/api/docs").status_code == 200


def test_create_admin_updates_the_only_user(session, user, monkeypatch):
    monkeypatch.setenv("KOSHA_ADMIN_EMAIL", "renamed@example.test")
    get_settings.cache_clear()
    try:
        assert auth.create_admin(session).id == user.id
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
    session.commit()
    assert session.scalars(select(User.email)).all() == ["renamed@example.test"]


def test_folder_page_without_folder_id_keeps_main_order():
    page = timeline([entry("1", tweet("1", "Post"), sort_index(NOV_10))], key="bookmark_collection_timeline")
    item = parse_timeline(page, {}, in_folder=True).items[0]
    assert item.saved_order is None and item.saved_at_est is None and item.collections == []
    assert parse_timeline(page, {}, in_folder=False).items[0].saved_order is not None


def test_undo_survives_deleted_tags_and_taken_roles(client, session, user):
    client.post("/api/captures", json=envelope("bookmarks_page", timeline([entry("1", tweet("1", "Post"), sort_index(NOV_10))])))
    item_id = client.get("/api/items").json()["items"][0]["id"]
    first, second, gone = (client.post("/api/tags", json={"name": n}).json()["id"] for n in ("first", "second", "gone"))
    client.put(f"/api/items/{item_id}/tags/{first}", json={"role": "primary"})
    client.put(f"/api/items/{item_id}/tags/{gone}", json={"role": "other"})
    batch = client.post("/api/items/bulk", json={"ids": [item_id], "remove_tag_ids": [first, gone]}).json()["undo_batch_id"]
    client.put(f"/api/items/{item_id}/tags/{second}", json={"role": "primary"})  # the role is taken meanwhile
    client.delete(f"/api/tags/{gone}")
    assert client.post(f"/api/undo/{batch}").status_code == 204
    roles = {session.get(Tag, t.tag_id).name: t.role for t in session.scalars(select(ItemTag).where(ItemTag.item_id == item_id))}
    assert roles == {"first": "primary", "second": "other"}


def test_media_downloads_stay_on_allowed_hosts(tmp_path):
    assert allowed_url("https://pbs.twimg.com/media/a.jpg") and allowed_url("https://a.video.twimg.com/x.mp4")
    assert not allowed_url("http://pbs.twimg.com/media/a.jpg") and not allowed_url("https://evil.example/pbs.twimg.com")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/hop":
            return httpx.Response(302, headers={"location": "https://internal.example/secret"})
        if request.url.path == "/ok-hop":
            return httpx.Response(302, headers={"location": "/file"})
        return httpx.Response(200, content=b"x" * 100)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert _download(client, "https://pbs.twimg.com/ok-hop", tmp_path / "a", max_bytes=1000) == 100
    with pytest.raises(OSError, match="allowed"):
        _download(client, "https://pbs.twimg.com/hop", tmp_path / "b", max_bytes=1000)
    with pytest.raises(OSError, match="Larger"):
        _download(client, "https://pbs.twimg.com/file", tmp_path / "c", max_bytes=10)
    assert not (tmp_path / "b").exists() and not list(tmp_path.glob("*.part"))


def test_scheduler_only_runs_when_the_setting_is_on(session, user, monkeypatch):
    calls = []
    monkeypatch.setattr("kosha.sync.scheduler.sync_bookmarks", lambda session, user_id: calls.append(user_id) or {"status": "end"})
    assert run_due(session) == [] and not calls  # off by default: nothing is called
    session.merge(Setting(user_id=user.id, key="x_api_sync_enabled", value=True))
    session.commit()
    assert run_due(session) == [{"status": "end"}] and calls == [user.id]


def test_built_web_app_is_served_with_spa_fallback(user, tmp_path, monkeypatch):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>kosha</html>")
    (tmp_path / "assets" / "app.js").write_text("console.log(1)")
    monkeypatch.setattr("kosha.app.WEB_DIST_DIR", tmp_path)
    site = TestClient(create_app())
    assert site.get("/").text == "<html>kosha</html>"
    assert site.get("/tags/12").text == "<html>kosha</html>"  # a client-side route
    assert site.get("/assets/app.js").text == "console.log(1)"
    assert site.get("/../secret").text == "<html>kosha</html>"
    assert site.get("/api/nope").status_code == 404 and site.get("/media/nope").status_code == 401
    assert site.get("/api/health").status_code == 401  # the API is untouched
    assert "/tags/12" not in TestClient(app).get("/api/openapi.json").text


def test_removal_result_only_deletes_archived_items(client, session, user):
    item = Item(user_id=user.id, source="x", source_id="77", body="still wanted")
    session.add(item)
    session.commit()
    assert client.post("/api/removals/result", json={"item_id": item.id, "status": "removed"}).status_code == 409
    assert client.get(f"/api/items/{item.id}").status_code == 200
    client.post("/api/items/bulk", json={"ids": [item.id], "archived": True})
    assert client.post("/api/removals/result", json={"item_id": item.id, "status": "removed"}).json()["deleted"] is True


def test_request_sessions_have_a_statement_timeout(user):
    from sqlalchemy import text

    from kosha.config import STATEMENT_TIMEOUT_MS
    from kosha.db import RequestSession, SessionLocal

    with RequestSession() as request, SessionLocal() as batch:
        assert request.execute(text("SHOW statement_timeout")).scalar() == f"{STATEMENT_TIMEOUT_MS // 1000}s"
        assert batch.execute(text("SHOW statement_timeout")).scalar() == "0"  # CLI and background jobs are not cut off


def test_serve_trusts_forwarding_headers_from_this_machine_only(monkeypatch):
    import uvicorn

    from kosha import cli
    from kosha.sync import scheduler

    seen = {}
    monkeypatch.setattr(scheduler, "start_scheduler", lambda: None)
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: seen.update(kwargs))
    cli.serve(host="127.0.0.1", port=8000, reload=False)
    assert seen["proxy_headers"] is True and seen["forwarded_allow_ips"] == "127.0.0.1"
