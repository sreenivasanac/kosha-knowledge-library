"""Accounts, setup, sign-up modes, admin, onboarding, instance keys, per-user
isolation, caps and demo mode. No paid API is called: every one is a fake."""
import threading
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from typer.testing import CliRunner

from kosha import auth, demo, keys
from kosha.app import app
from kosha.cli import app as cli
from kosha.config import CSRF_HEADER, CSRF_HEADER_VALUE, get_settings
from kosha.models import (
    ApiToken,
    DemoQueryVector,
    InstanceSetting,
    Item,
    ItemEmbedding,
    ItemMedia,
    ItemTag,
    ResultSet,
    Setting,
    SpendLedger,
    Tag,
    User,
)
from kosha.pipeline import run_query
from kosha.prefs import load_settings
from kosha.search import embeddings
from kosha.search.embeddings import DOC_MODEL, QUERY_MODEL, EmbeddingError
from kosha.sync.x_api import sync_bookmarks
from tests.conftest import API_TOKEN
from tests.xfix import bookmarks_page, envelope

CSRF = {CSRF_HEADER: CSRF_HEADER_VALUE}
ADMIN_LOGIN = {"email": "admin@example.test", "password": "test-password"}
PASSWORD = "a-long-password"
B_TOKEN = "token-of-user-b"


def browser(login: dict | None = None) -> TestClient:
    """A web-app client: session cookie and the CSRF header."""
    auth._login_failures.clear()
    client = TestClient(app, headers=CSRF)
    if login:
        assert client.post("/api/auth/login", json=login).status_code == 200
    return client


def vec(axis: int) -> list[float]:
    return [1.0 if i == axis else 0.0 for i in range(1024)]


@pytest.fixture
def user_b(session, user):
    """A second, normal user with a bearer token of their own."""
    other = auth.new_user(session, "b@example.test", PASSWORD, "B")
    session.add(ApiToken(user_id=other.id, name="b", token_hash=auth.hash_token(B_TOKEN)))
    session.commit()
    return other


@pytest.fixture
def client_b(user_b):
    return TestClient(app, headers={"Authorization": f"Bearer {B_TOKEN}"})


@pytest.fixture
def demo_mode(monkeypatch):
    monkeypatch.setenv("KOSHA_DEMO", "true")
    monkeypatch.setenv("KOSHA_DEMO_EMAIL", "demo@example.test")
    monkeypatch.setenv("KOSHA_DEMO_PASSWORD", "demo-password")
    get_settings.cache_clear()
    embeddings._query_cache.clear()
    yield
    monkeypatch.undo()
    get_settings.cache_clear()
    embeddings._query_cache.clear()


# --- Setup, sign-up, passwords -----------------------------------------------
def test_setup_creates_the_admin_once():
    anonymous = browser()
    config = anonymous.get("/api/public-config").json()
    assert set(config) == {"app_name", "version", "has_users", "signups", "landing", "demo", "demo_url", "links"}
    assert (config["has_users"], config["signups"], config["landing"], config["demo"], config["demo_url"]) == (False, "closed", False, None, None)
    assert set(config["links"]) == {"github", "docs"}
    assert anonymous.post("/api/auth/signup", json={"email": "x@example.test", "password": PASSWORD}).status_code == 403  # setup first
    assert anonymous.post("/api/setup", json={"email": "Owner@Example.test", "password": "short", "name": "O"}).status_code == 422
    made = anonymous.post("/api/setup", json={"email": "Owner@Example.test", "password": PASSWORD, "name": "Owner"})
    assert made.status_code == 201
    assert made.json() == {"id": made.json()["id"], "email": "owner@example.test", "role": "admin", "name": "Owner",
                           "demo": False, "onboarding_completed": False}
    assert anonymous.get("/api/auth/me").json() == made.json()  # signed in
    assert anonymous.get("/api/public-config").json()["has_users"] is True
    assert browser().post("/api/setup", json={"email": "second@example.test", "password": PASSWORD}).status_code == 409


def test_two_setups_at_once_make_one_admin(session):
    codes = []
    post = lambda n: codes.append(TestClient(app).post("/api/setup", json={"email": f"u{n}@example.test", "password": PASSWORD}).status_code)  # noqa: E731
    threads = [threading.Thread(target=post, args=(n,)) for n in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(codes) == [201, 409, 409, 409]
    assert session.scalar(select(func.count()).select_from(User)) == 1


def test_signup_modes_and_invites(user, session):
    admin, new = browser(ADMIN_LOGIN), {"email": "new@example.test", "password": PASSWORD, "name": "New"}
    assert browser().post("/api/auth/signup", json=new).status_code == 403  # closed by default
    invite = admin.post("/api/admin/invites", json={"expires_days": 3})
    assert invite.status_code == 201 and invite.json()["url"].endswith("/signup?invite=" + invite.json()["token"])
    assert admin.get("/api/admin/instance").json()["signups"] == "invite"  # an invite opens the invite mode
    assert browser().get("/api/public-config").json()["signups"] == "invite"
    assert browser().post("/api/auth/signup", json=new).status_code == 403  # no token
    assert browser().post("/api/auth/signup", json={**new, "invite": "wrong"}).status_code == 403
    assert browser().post("/api/auth/signup", json={**new, "invite": invite.json()["token"], "password": "too-short"}).status_code == 422
    joined = browser()
    made = joined.post("/api/auth/signup", json={**new, "invite": invite.json()["token"]})
    assert made.status_code == 201 and made.json()["role"] == "user" and joined.get("/api/auth/me").json()["email"] == "new@example.test"
    again = browser().post("/api/auth/signup", json={"email": "other@example.test", "password": PASSWORD, "invite": invite.json()["token"]})
    assert again.status_code == 403  # single use
    listed = admin.get("/api/admin/invites").json()
    assert [(i["state"], i["used_by"]) for i in listed] == [("used", "new@example.test")] and "token" not in listed[0]

    assert admin.patch("/api/admin/instance", json={"signups": "open"}).json()["signups"] == "open"
    assert browser().post("/api/auth/signup", json=new).status_code == 409  # the email is taken
    assert browser().post("/api/auth/signup", json={"email": "open@example.test", "password": PASSWORD}).status_code == 201
    assert admin.patch("/api/admin/instance", json={"signups": "nonsense"}).status_code == 422
    admin.patch("/api/admin/instance", json={"signups": "closed"})
    assert browser().post("/api/auth/signup", json={"email": "late@example.test", "password": PASSWORD}).status_code == 403
    # A second invite can be revoked before it is used.
    spare = admin.post("/api/admin/invites", json={}).json()
    assert admin.delete(f"/api/admin/invites/{spare['id']}").status_code == 204
    assert browser().post("/api/auth/signup", json={"email": "late@example.test", "password": PASSWORD, "invite": spare["token"]}).status_code == 403


def test_password_change_and_admin_reset_link(user, user_b):
    b, b_elsewhere = browser({"email": "b@example.test", "password": PASSWORD}), browser({"email": "B@example.test", "password": PASSWORD})
    assert b.post("/api/auth/password", json={"current": "wrong-password", "new": "another-long-one"}).status_code == 403
    assert b.post("/api/auth/password", json={"current": PASSWORD, "new": "short"}).status_code == 422
    assert b.post("/api/auth/password", json={"current": PASSWORD, "new": "another-long-one"}).status_code == 204
    assert b.get("/api/auth/me").status_code == 200  # this session goes on
    assert b_elsewhere.get("/api/auth/me").status_code == 401  # every other session ended
    bearer = TestClient(app, headers={"Authorization": f"Bearer {B_TOKEN}"})
    assert bearer.post("/api/auth/password", json={"current": "another-long-one", "new": "x" * 12}).status_code == 403  # never by token

    admin = browser(ADMIN_LOGIN)
    link = admin.post(f"/api/admin/users/{user_b.id}/reset-link").json()
    assert set(link) == {"url", "expires_at"} and "/reset?token=" in link["url"]
    token = link["url"].split("token=")[1]
    assert browser().post("/api/auth/reset", json={"token": token, "password": "tiny"}).status_code == 422  # and the link is not spent
    assert browser().post("/api/auth/reset", json={"token": token, "password": "reset-password-1"}).status_code == 204
    assert browser().post("/api/auth/reset", json={"token": token, "password": "reset-password-2"}).status_code == 403  # once
    assert b.get("/api/auth/me").status_code == 401
    assert browser().post("/api/auth/login", json={"email": "b@example.test", "password": "reset-password-1"}).status_code == 200


# --- Admin --------------------------------------------------------------------
def test_admin_routes_need_an_admin_session(client, client_b, user_b):
    for path in ("/api/admin/users", "/api/admin/invites", "/api/admin/instance", "/api/admin/keys"):
        assert client.get(path).status_code == 403  # the admin's own bearer token: never
        assert client_b.get(path).status_code == 403
        assert browser({"email": "b@example.test", "password": PASSWORD}).get(path).status_code == 403  # a normal user's session
        assert TestClient(app).get(path).status_code == 403
        assert browser(ADMIN_LOGIN).get(path).status_code == 200


def test_admin_manages_users(client, client_b, user, user_b, session, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "kosha_media_dir", tmp_path)
    client_b.post("/api/captures", json=envelope("bookmarks_page", bookmarks_page()))
    media = session.scalar(select(ItemMedia).join(Item, Item.id == ItemMedia.item_id).where(Item.user_id == user_b.id))
    media.local_path, media.bytes = "b/file.jpg", 1234
    session.commit()
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "file.jpg").write_bytes(b"x")

    admin = browser(ADMIN_LOGIN)
    rows = {u["email"]: u for u in admin.get("/api/admin/users").json()}
    assert set(rows["b@example.test"]) == {"id", "email", "name", "role", "disabled", "created_at", "last_login_at", "item_count", "storage_bytes"}
    assert (rows["b@example.test"]["item_count"], rows["b@example.test"]["storage_bytes"], rows["admin@example.test"]["item_count"]) == (6, 1234, 0)
    assert rows["admin@example.test"]["role"] == "admin" and rows["admin@example.test"]["last_login_at"]

    made = admin.post("/api/admin/users", json={"email": "c@example.test", "password": PASSWORD, "name": "C"})
    assert made.status_code == 201 and made.json()["role"] == "user"
    assert admin.post("/api/admin/users", json={"email": "c@example.test", "password": PASSWORD}).status_code == 409
    assert admin.post("/api/admin/users", json={"email": "d@example.test", "password": "short"}).status_code == 422

    # Disabled: neither the password nor the token works; enabled again they do.
    assert admin.patch(f"/api/admin/users/{user_b.id}", json={"disabled": True, "name": "Bee"}).json()["disabled"] is True
    assert client_b.get("/api/items").status_code == 401
    assert browser().post("/api/auth/login", json={"email": "b@example.test", "password": PASSWORD}).status_code == 403
    assert admin.patch(f"/api/admin/users/{user_b.id}", json={"disabled": False}).json()["name"] == "Bee"
    assert client_b.get("/api/items").json()["total"] == 6

    # The last admin cannot be demoted, disabled or deleted; nobody deletes themselves.
    assert admin.patch(f"/api/admin/users/{user.id}", json={"role": "user"}).status_code == 409
    assert admin.patch(f"/api/admin/users/{user.id}", json={"disabled": True}).status_code == 409
    assert admin.delete(f"/api/admin/users/{user.id}").status_code == 409
    assert admin.patch(f"/api/admin/users/{user_b.id}", json={"role": "admin"}).json()["role"] == "admin"
    assert admin.patch(f"/api/admin/users/{user_b.id}", json={"role": "user"}).status_code == 200

    assert admin.delete(f"/api/admin/users/{user_b.id}").status_code == 204
    assert session.scalar(select(func.count()).select_from(Item).where(Item.user_id == user_b.id)) == 0
    assert not (tmp_path / "b" / "file.jpg").exists() and client_b.get("/api/items").status_code == 401
    assert admin.delete(f"/api/admin/users/{user_b.id}").status_code == 404


def test_instance_keys_are_encrypted_and_never_returned(user, session, monkeypatch):
    env = get_settings()
    monkeypatch.setattr(env, "voyage_api_key", "env-voyage-key-1111")
    monkeypatch.setattr(env, "openai_api_key", "  ")  # empty counts as not set
    monkeypatch.setattr(env, "typesafe_api_key", "")
    monkeypatch.setattr(env, "kosha_transcription_api_key", "")
    keys.invalidate()
    admin = browser(ADMIN_LOGIN)
    listed = {k["name"]: k for k in admin.get("/api/admin/keys").json()}
    assert listed["voyage"] == {"name": "voyage", "set": True, "source": "env", "last4": "1111"}
    assert listed["openai"] == {"name": "openai", "set": False, "source": None, "last4": None} and keys.get_key("openai") is None
    assert set(listed["transcription"]) == {"name", "set", "source", "last4", "provider", "model"}

    saved = admin.put("/api/admin/keys", json={"name": "VOYAGE", "value": "db-voyage-key-2222"})
    assert saved.json() == {"name": "voyage", "set": True, "source": "db", "last4": "2222"}
    assert keys.get_key("voyage") == "db-voyage-key-2222"  # the saved key wins over the environment
    stored = session.get(InstanceSetting, "key:voyage").value
    assert "db-voyage-key-2222" not in str(stored) and "db-voyage-key-2222" not in admin.get("/api/admin/keys").text
    assert admin.put("/api/admin/keys", json={"name": "nope", "value": "x"}).status_code == 422
    assert admin.put("/api/admin/keys", json={"name": "openai"}).status_code == 422
    speech = admin.put("/api/admin/keys", json={"name": "transcription", "value": "stt-key-3333", "provider": "deepinfra", "model": "whisper"}).json()
    assert (speech["last4"], speech["provider"], speech["model"]) == ("3333", "deepinfra", "whisper")
    assert keys.key_options("transcription") == {"provider": "deepinfra", "model": "whisper"}

    calls = []
    monkeypatch.setattr(httpx, "post", lambda url, **kw: calls.append((url, kw["headers"])) or httpx.Response(200, json={}))
    monkeypatch.setattr(httpx, "get", lambda url, **kw: httpx.Response(401, json={}))
    assert admin.post("/api/admin/keys/test", json={"name": "voyage"}).json() == {"ok": True, "message": "The key works"}
    assert calls[0][1] == {"Authorization": "Bearer db-voyage-key-2222"}
    assert admin.post("/api/admin/keys/test", json={"name": "openai"}).json() == {"ok": False, "message": "No key is set"}
    admin.put("/api/admin/keys", json={"name": "openai", "value": "sk-bad"})
    refused = admin.post("/api/admin/keys/test", json={"name": "openai"}).json()
    assert refused["ok"] is False and "401" in refused["message"]

    assert admin.delete("/api/admin/keys", params={"name": "voyage"}).json()["source"] == "env"
    assert admin.delete("/api/admin/keys/openai").json()["set"] is False
    assert keys.get_key("voyage") == "env-voyage-key-1111"


def test_onboarding_is_per_user(user, user_b):
    admin, b = browser(ADMIN_LOGIN), browser({"email": "b@example.test", "password": PASSWORD})
    steps = {"account": "done", "sources": "todo", "ai": "todo", "media": "todo", "look": "todo"}
    assert admin.get("/api/onboarding").json() == {"completed": False, "steps": steps}
    assert b.get("/api/onboarding").json()["steps"] == {**steps, "ai": "not_applicable", "media": "not_applicable"}
    assert admin.patch("/api/onboarding", json={"step": "ai", "state": "skipped"}).json()["steps"]["ai"] == "skipped"
    assert b.patch("/api/onboarding", json={"step": "ai", "state": "done"}).status_code == 403
    assert b.patch("/api/onboarding", json={"step": "sources", "state": "done"}).json()["steps"]["sources"] == "done"
    assert b.patch("/api/onboarding", json={"step": "nope", "state": "done"}).status_code == 422
    assert b.post("/api/onboarding/complete").json()["completed"] is True and b.get("/api/auth/me").json()["onboarding_completed"] is True
    assert admin.get("/api/onboarding").json() == {"completed": False, "steps": {**steps, "ai": "skipped"}}


# --- Isolation ----------------------------------------------------------------
@pytest.fixture
def library_a(client, session, user):
    """User A (the admin): six bookmarks with media, a tag on one, a list, a view, a named set, embeddings."""
    client.post("/api/captures", json=envelope("bookmarks_page", bookmarks_page()))
    items = client.get("/api/items").json()["items"]
    tag = client.post("/api/tags", json={"name": "Secret"}).json()
    client.put(f"/api/items/{items[0]['id']}/tags/{tag['id']}")
    client.patch(f"/api/items/{items[0]['id']}", json={"note": "private note"})
    shelf = client.post("/api/lists", json={"name": "Shelf"}).json()
    client.post(f"/api/lists/{shelf['id']}/items", json={"item_ids": [items[0]["id"]]})
    view = client.post("/api/saved-views", json={"name": "Mine", "query": "is:starred"}).json()
    batch = client.post("/api/items/bulk", json={"ids": [items[1]["id"]], "starred": True}).json()["undo_batch_id"]
    run_query(session, user.id, "all | as @mine")
    media = session.scalar(select(ItemMedia).join(Item, Item.id == ItemMedia.item_id).where(Item.user_id == user.id))
    session.add_all(ItemEmbedding(item_id=i["id"], model=DOC_MODEL, content_hash="h", vector=vec(0)) for i in items)
    session.commit()
    return {"items": items, "ids": [i["id"] for i in items], "tag": tag, "list": shelf, "view": view, "batch": batch, "media": media.id}


def test_user_b_cannot_reach_user_a(client, client_b, user, user_b, session, library_a, monkeypatch):
    a, first = library_a, library_a["ids"][0]
    assert client.get("/api/items").json()["total"] == 6 and client.get(f"/media/{a['media']}", follow_redirects=False).status_code in (200, 307)
    # Read, list, search, facets, stats.
    assert client_b.get("/api/items").json()["total"] == 0
    assert client_b.get("/api/items", params={"is_saved": "false"}).json()["total"] == 0
    assert client_b.get(f"/api/items/{first}").status_code == 404
    for q in ("take", "treasuries", f"id:{first}", "#Secret", "in:@mine", "list:Shelf", f"thread:{first}", f"quotes:{first}"):
        found = client_b.get("/api/items", params={"q": q, "mode": "keyword"})
        assert found.status_code in (200, 422) and found.json().get("total", 0) == 0, q
    assert client_b.get("/api/items", params={"q": f"similar:{first}"}).status_code == 422  # not "has an embedding": unknown to B
    assert client_b.get("/api/tags").json() == [] and client_b.get("/api/lists").json() == [] and client_b.get("/api/saved-views").json() == []
    assert client_b.get("/api/facets").json() == {"sources": [], "years": []} and client_b.get("/api/stats").json()["items"] == 0
    client.get("/api/items", params={"q": "aonly", "mode": "keyword"})
    assert client_b.get("/api/collections").json() == [] and "aonly" not in client_b.get("/api/search/recent").json()
    assert client_b.get("/api/search/suggest", params={"q": "#Sec"}).json()["suggestions"] == []
    assert client_b.get("/api/removals/pending").json() == []
    assert client_b.get(f"/media/{a['media']}").status_code == 404
    assert client_b.get(f"/api/lists/{a['list']['id']}").status_code == 404
    # Change, tag, undo, delete.
    assert client_b.patch(f"/api/items/{first}", json={"starred": True}).status_code == 404
    assert client_b.delete(f"/api/items/{first}").status_code == 404
    assert client_b.post("/api/removals/result", json={"item_id": first, "status": "removed"}).status_code == 404
    assert client_b.post("/api/items/bulk", json={"ids": a["ids"], "archived": True}).json()["updated"] == 0
    own_tag = client_b.post("/api/tags", json={"name": "Mine"}).json()
    assert client_b.put(f"/api/items/{first}/tags/{own_tag['id']}").status_code == 404  # A's item, B's tag
    assert client_b.post("/api/items/bulk", json={"ids": a["ids"], "add_tag_ids": [a["tag"]["id"]]}).status_code == 404  # A's tag
    for call in (client_b.patch(f"/api/tags/{a['tag']['id']}", json={"name": "x"}), client_b.delete(f"/api/tags/{a['tag']['id']}"),
                 client_b.post(f"/api/tags/{own_tag['id']}/merge", json={"into_id": a["tag"]["id"]}),
                 client_b.post(f"/api/tags/{own_tag['id']}/move", json={"parent_id": a["tag"]["id"]}),
                 client_b.post("/api/tags", json={"name": "child", "parent_id": a["tag"]["id"]}),
                 client_b.post(f"/api/lists/{a['list']['id']}/items", json={"item_ids": [first]}),
                 client_b.delete(f"/api/lists/{a['list']['id']}"), client_b.patch(f"/api/saved-views/{a['view']['id']}", json={"name": "x"}),
                 client_b.post(f"/api/undo/{a['batch']}")):
        assert call.status_code == 404
    own_list = client_b.post("/api/lists", json={"name": "B list"}).json()
    assert client_b.post(f"/api/lists/{own_list['id']}/items", json={"item_ids": a["ids"]}).json()["count"] == 0  # A's items are not added
    # Pipelines, sets, the Jev check and media text run over B's own (empty) library only.
    assert client_b.post("/api/query", json={"q": "all | count"}).json()["n"] == 0
    assert client_b.post("/api/query", json={"q": f"where id:{first} | star"}).json()["n"] == 0
    assert client_b.post("/api/query", json={"q": "@mine | count"}).status_code == 422  # A's named set does not exist for B
    assert client_b.post("/api/query", json={"q": f"all | similar {first}"}).status_code == 422
    assert client_b.post("/api/jev/check", json={"q": "", "question": "Is this about anything?", "dry_run": True}).json()["n"] == 0
    assert client_b.post("/api/media-text/estimate", json={"item_ids": a["ids"]}).json()["n_media"] == 0
    # Account data: tokens, spend, settings, keybindings, sync state.
    session.add(SpendLedger(user_id=user.id, provider="jev", operation="jev", amount_usd=Decimal("1")))
    session.commit()
    client.patch("/api/settings", json={"page_size": 100})
    client.put("/api/keybindings", json={"star": "x"})
    assert client_b.get("/api/spend").json()["total_usd"] == 0 and client_b.get("/api/settings").json()["page_size"] == 50
    assert client_b.get("/api/keybindings").json() == {} and client_b.get("/api/sync").json() == []
    b_web = browser({"email": "b@example.test", "password": PASSWORD})
    assert [t["name"] for t in b_web.get("/api/tokens").json()] == ["b"]
    a_token = session.scalar(select(ApiToken.id).where(ApiToken.user_id == user.id))
    assert b_web.delete(f"/api/tokens/{a_token}").status_code == 404
    # Nothing of A changed, and each bearer token is its own user.
    after = {i["id"]: i for i in client.get("/api/items").json()["items"]}
    assert after[first]["note"] == "private note" and [t["name"] for t in after[first]["tags"]] == ["Secret"]
    assert client.get("/api/health").status_code == 200 and client_b.get("/api/auth/me").json()["email"] == "b@example.test"
    assert client.get("/api/auth/me").json()["email"] == "admin@example.test"


def test_mcp_tools_work_on_the_token_owners_library(user, user_b, session, library_a):
    """The MCP endpoint takes the user from the bearer token (`auth.bearer_user`) and every tool works as that user."""
    import anyio

    from kosha import mcp_server

    def call(token: str, tool, **arguments) -> str:
        mcp_server._user_id.set(auth.bearer_user(session, f"Bearer {token}").id)
        return anyio.run(lambda: tool(**arguments))

    assert auth.bearer_user(session, "Bearer wrong") is None
    assert call(B_TOKEN, mcp_server.query, pipeline="all | count").startswith("n=0 ")
    assert call(B_TOKEN, mcp_server.edit_item, id=library_a["ids"][0], starred=True).startswith("ERROR: No item")
    assert "No tags" in call(B_TOKEN, mcp_server.tags)
    assert call(B_TOKEN, mcp_server.runs, action="undo", batch=library_a["batch"]).startswith("ERROR")
    assert "items: saved=0" in call(B_TOKEN, mcp_server.jobs)
    assert call(API_TOKEN, mcp_server.query, pipeline="all | count").startswith("n=6 ")


def test_caps_are_per_user_and_the_admin_sets_the_maximum(client, client_b, user, user_b, session, monkeypatch):
    admin = browser(ADMIN_LOGIN)
    caps = admin.patch("/api/admin/instance", json={"caps": {"jev_monthly_budget_usd": 1.0, "voyage_monthly_cap_usd": 0.5}}).json()["caps"]
    assert caps["jev_monthly_budget_usd"] == 1.0 and caps["llm_monthly_cap_usd"] == 5.0
    assert admin.patch("/api/admin/instance", json={"caps": {"page_size": 1}}).status_code == 422
    # A normal user cannot raise their cap past it; the admin's own cap is theirs to set.
    assert client_b.patch("/api/settings", json={"jev_monthly_budget_usd": 50}).json()["jev_monthly_budget_usd"] == 1.0
    assert client.patch("/api/settings", json={"jev_monthly_budget_usd": 50}).json()["jev_monthly_budget_usd"] == 50
    assert load_settings(session, user_b.id)["voyage_monthly_cap_usd"] == 0.5
    # B's Voyage spend stops B, not A.
    session.add(SpendLedger(user_id=user_b.id, provider="voyage", operation="embed_query", amount_usd=Decimal("0.5")))
    session.commit()
    monkeypatch.setattr(embeddings, "embed_texts", lambda texts, model, kind, client=None: ([vec(0)], 3))
    embeddings._query_cache.clear()
    with pytest.raises(EmbeddingError, match="monthly Voyage cap"):
        embeddings.query_vector(session, user_b.id, "anything")
    assert embeddings.query_vector(session, user.id, "anything") == vec(0)
    embeddings._query_cache.clear()
    spent = {c["name"]: c for c in client_b.get("/api/spend").json()["caps"]}
    assert (spent["voyage"]["cap_usd"], spent["voyage"]["spent_usd"]) == (0.5, 0.5)
    assert {c["name"]: c["spent_usd"] for c in client.get("/api/spend").json()["caps"]}["voyage"] == 0


def test_the_servers_x_account_syncs_for_the_admin_only(client_b, user_b, session):
    session.merge(Setting(user_id=user_b.id, key="x_api_sync_enabled", value=True))
    session.commit()
    assert sync_bookmarks(session, user_b.id)["status"] == "not_configured"  # no request is made
    assert client_b.post("/api/sync", json={"source": "x_api"}).json()["status"] == "not_configured"


# --- The three no-key bugs ----------------------------------------------------
def test_without_keys_the_answers_are_clean(client, session, user, monkeypatch, tmp_path):
    env = get_settings()
    for name in ("typesafe_api_key", "voyage_api_key", "openai_api_key"):
        monkeypatch.setattr(env, name, "")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    keys.invalidate()
    client.post("/api/captures", json=envelope("bookmarks_page", bookmarks_page()))
    answer = client.post("/api/query", json={"q": 'all | jev "Is this about treasuries?"'})
    assert answer.status_code == 503 and "Jev is not available" in answer.json()["detail"]["message"]
    result = CliRunner().invoke(cli, ["embed"])
    assert result.exit_code == 1 and result.output.strip() == "Cannot embed: VOYAGE_API_KEY is not set"
    assert keys.get_key("openai") is None
    # `OPENAI_API_KEY=` (set but empty) in the environment is "not set": it does not hide the key in .env.
    env_file = tmp_path / ".env"
    env_file.write_text("KOSHA_SECRET_KEY=s\nOPENAI_API_KEY=from-the-file\n")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    assert type(env)(_env_file=env_file).openai_api_key == "from-the-file"


# --- Demo mode ----------------------------------------------------------------
REFUSED = [("PATCH", "/api/auth/me"), ("POST", "/api/setup"), ("POST", "/api/auth/signup"), ("POST", "/api/auth/password"), ("POST", "/api/auth/reset"),
           ("GET", "/api/admin/users"), ("PATCH", "/api/admin/instance"), ("PUT", "/api/admin/keys"), ("POST", "/api/tokens"),
           ("DELETE", "/api/tokens/1"), ("POST", "/api/captures"), ("POST", "/api/import"), ("POST", "/api/items"),
           ("POST", "/api/media-text/start"), ("POST", "/api/ask"), ("POST", "/api/ask/stream"), ("POST", "/api/jev/check"),
           ("DELETE", "/api/items/1"), ("GET", "/api/removals/pending"), ("POST", "/api/removals/result"), ("POST", "/api/sync"),
           ("POST", "/mcp"), ("GET", "/mcp"), ("POST", "/api/some-route-added-later")]


def test_demo_refuses_by_allow_list(user, session, demo_mode):
    session.add(User(email="demo@example.test", password_hash=auth.hash_password("demo-password"), role="user"))
    session.commit()
    config = TestClient(app).get("/api/public-config").json()
    assert config["demo"]["email"] == "demo@example.test" and config["demo"]["password"] == "demo-password" and config["demo"]["notice"]
    assert config["signups"] == "closed"
    assert config["demo"]["searches"] == []  # the suggested searches come with the bundle; none is loaded here
    visitor = browser({"email": "demo@example.test", "password": "demo-password"})
    assert visitor.get("/api/auth/me").json()["demo"] is True
    for method, path in REFUSED:
        answer = visitor.request(method, path, json={})
        assert (answer.status_code, answer.json()) == (403, {"detail": {"message": "Not available in the demo", "code": "demo"}}), path
    # Bearer tokens do not sign in, whoever they belong to.
    assert TestClient(app, headers={"Authorization": f"Bearer {API_TOKEN}"}).get("/api/items").json()["detail"]["code"] == "demo"
    assert visitor.get("/api/items", params={"q": "robots", "rerank": "voyage"}).status_code == 403
    # Paid pipeline stages, wherever they stand in the pipeline.
    for pipeline in ('all | jev "Is this about robots?"', "all | head 5 | score_labels", 'all | rerank voyage "robots"'):
        assert visitor.post("/api/query", json={"q": pipeline}).json() == {"detail": {"message": "Not available in the demo", "code": "demo"}}
    # Everything else works.
    assert visitor.get("/api/tokens").status_code == 200 and visitor.get("/api/items").status_code == 200
    tag = visitor.post("/api/tags", json={"name": "Robots"})
    assert tag.status_code == 201 and visitor.post("/api/lists", json={"name": "L"}).status_code == 201
    assert visitor.post("/api/saved-views", json={"name": "V", "query": "is:starred"}).status_code == 201
    assert visitor.put("/api/keybindings", json={"star": "x"}).status_code == 200
    assert visitor.post("/api/query", json={"q": "all | count"}).status_code == 200
    assert visitor.patch("/api/onboarding", json={"step": "look", "state": "done"}).status_code == 200
    assert visitor.post("/api/search/parse", json={"q": "is:starred"}).status_code == 200
    assert visitor.delete(f"/api/tags/{tag.json()['id']}").status_code == 204


def test_demo_limits_writes_per_address(user, demo_mode, monkeypatch):
    monkeypatch.setattr(demo, "DEMO_WRITES_PER_MINUTE", 3)
    demo._writes.clear()
    visitor = TestClient(app)
    codes = [visitor.post("/api/search/parse", json={"q": "x"}).status_code for _ in range(5)]
    assert codes[:3] == [401, 401, 401] and codes[3:] == [429, 429]
    assert visitor.get("/api/public-config").status_code == 200  # reads are not limited


def test_demo_bundle_export_load_reset(client, user, session, tmp_path, monkeypatch):
    """Export from a real library, load into the (emptied) database as the demo, search it, change it, reset it."""
    env = get_settings()
    media_dir = tmp_path / "media"
    monkeypatch.setattr(env, "kosha_media_dir", media_dir)
    client.post("/api/captures", json=envelope("bookmarks_page", bookmarks_page()))
    items = {i["source_id"]: i for i in client.get("/api/items").json()["items"]}
    quote, long_post, article = items["1001"], items["1002"], items["1003"]
    public = client.post("/api/tags", json={"name": "Public"}).json()
    child = client.post("/api/tags", json={"name": "Papers", "parent_id": public["id"]}).json()
    private = client.post("/api/tags", json={"name": "Private"}).json()
    client.put(f"/api/items/{quote['id']}/tags/{public['id']}")
    client.put(f"/api/items/{long_post['id']}/tags/{child['id']}")
    client.put(f"/api/items/{long_post['id']}/tags/{private['id']}")
    client.put(f"/api/items/{article['id']}/tags/{private['id']}")
    client.patch(f"/api/items/{quote['id']}", json={"note": "my private note", "starred": True, "read": True})
    session.add(ItemEmbedding(item_id=quote["id"], model=DOC_MODEL, content_hash="h", vector=vec(1)))
    media = session.scalar(select(ItemMedia).where(ItemMedia.item_id == quote["quoted"]["id"]))
    media.local_preview_path = f"{media.item_id}/{media.id}-preview.jpg"
    session.commit()
    (media_dir / str(media.item_id)).mkdir(parents=True)
    (media_dir / media.local_preview_path).write_bytes(b"jpeg")
    queries = tmp_path / "queries.txt"
    queries.write_text("Robot arms\n\nvector databases\n")
    real_embed_texts = embeddings.embed_texts
    monkeypatch.setattr(embeddings, "embed_texts", lambda texts, model, kind, client=None: ([vec(1), vec(2)], 7))

    out = tmp_path / "bundle"
    result = CliRunner().invoke(cli, ["demo-export", "--from-user", "admin@example.test", "--tags", "Public", "--limit", "10",
                                      "--out", str(out), "--queries", str(queries)])
    assert result.exit_code == 0, result.output
    assert "items=2 (limit 10) context_items=1" in result.output and "Media files: 1 (4 bytes" in result.output and "query_vectors=2" in result.output
    assert "2  text" in result.output and "admin@example.test" not in result.output  # content types; no account name in the summary
    assert "1  Public\n" in result.output and "1  Public/Papers\n" in result.output and "2  @ada" in result.output
    text = (out / "bundle.json").read_text()
    assert "my private note" not in text and "Private" not in text and "On Treasuries" not in text and '"raw"' not in text
    assert (out / "media" / media.local_preview_path).read_bytes() == b"jpeg" and (out / "SUMMARY.txt").exists()
    assert CliRunner().invoke(cli, ["demo-export", "--from-user", "admin@example.test", "--tags", "Nope", "--out", str(out)]).exit_code == 1
    # An excluded tag bars the item whatever else it carries; a source filter; items taken from the tags in turn.
    narrow = lambda *more: CliRunner().invoke(cli, ["demo-export", "--from-user", "admin@example.test", "--tags", "Public,Public/Papers",  # noqa: E731
                                                    "--out", str(tmp_path / "narrow"), *more], catch_exceptions=False).output
    assert "items=1 (limit 300, spread across the tags)" in narrow("--exclude-tags", "Private", "--spread", "--source", "x")
    assert "1002" not in (tmp_path / "narrow" / "bundle.json").read_text()
    assert "items=2 (limit 300, spread" in narrow("--spread") and "items=0 " in narrow("--source", "linkedin")
    assert 'No tag at "Nope"' in narrow("--exclude-tags", "Nope")

    # Loading and resetting are for a demo instance only, and loading needs an empty database.
    assert "KOSHA_DEMO" in CliRunner().invoke(cli, ["demo-reset", str(out)]).output
    assert session.scalar(select(func.count()).select_from(Item)) == 7  # nothing was emptied
    for name, value in (("kosha_demo", True), ("kosha_demo_email", "demo@example.test"), ("kosha_demo_password", "demo-password")):
        monkeypatch.setattr(env, name, value)
    refused = CliRunner().invoke(cli, ["demo-load", str(out)])
    assert refused.exit_code == 1 and "already has users" in refused.output
    session.close()
    done = CliRunner().invoke(cli, ["demo-reset", str(out)])
    assert done.exit_code == 0 and "items=3" in done.output, done.output
    embeddings._query_cache.clear()

    assert session.scalars(select(User.email)).all() == ["demo@example.test"]
    visitor = browser({"email": "demo@example.test", "password": "demo-password"})
    rows = {i["source_id"]: i for i in visitor.get("/api/items").json()["items"]}
    assert set(rows) == {"1001", "1002"} and rows["1001"]["note"] is None and rows["1001"]["starred"] is False and rows["1001"]["read"] is False
    assert rows["1001"]["quoted"]["source_id"] == "900" and [t["name"] for t in rows["1002"]["tags"]] == ["Papers"]
    assert [(t["name"], [c["name"] for c in t["children"]]) for t in visitor.get("/api/tags").json()] == [("Public", ["Papers"])]
    preview = visitor.get(rows["1001"]["quoted"]["media"][0]["preview_url"])
    assert preview.status_code == 200 and preview.content == b"jpeg"
    assert visitor.get("/api/items", params={"q": "paper", "mode": "keyword"}).json()["total"] == 1  # the search columns were rebuilt
    # By meaning: a suggested search uses its stored vector; any other text falls back to keywords with a notice.
    monkeypatch.setattr(embeddings.httpx, "post", lambda *a, **kw: pytest.fail("the demo called the embedding API"))
    known = visitor.get("/api/items", params={"q": "robot arms", "mode": "semantic", "weak": "true"}).json()
    assert (known["mode"], known["notice"], [i["source_id"] for i in known["items"]]) == ("semantic", None, ["1001"])
    other = visitor.get("/api/items", params={"q": "take", "mode": "semantic"}).json()
    assert (other["mode"], other["notice"], [i["source_id"] for i in other["items"]]) == ("keyword", "demo_keyword_only", ["1001"])
    assert visitor.get("/api/items", params={"q": "take"}).json()["notice"] == "demo_keyword_only"  # hybrid
    with pytest.raises(EmbeddingError):
        real_embed_texts(["anything"], QUERY_MODEL, "query")
    # A visitor's changes are gone after the nightly reset, and their session goes on.
    made = visitor.post("/api/tags", json={"name": "Graffiti"}).json()  # ids go on after the loaded ones
    assert made["id"] > max(public["id"], child["id"])
    visitor.patch(f"/api/items/{rows['1001']['id']}", json={"note": "was here", "archived": True})
    session.close()  # an open transaction would make the reset's TRUNCATE wait
    assert CliRunner().invoke(cli, ["demo-reset", str(out)]).exit_code == 0
    again = {i["source_id"]: i for i in visitor.get("/api/items").json()["items"]}
    assert set(again) == {"1001", "1002"} and again["1001"]["note"] is None
    assert [t["name"] for t in visitor.get("/api/tags").json()] == ["Public"]
    assert session.scalar(select(func.count()).select_from(DemoQueryVector)) == 2
    assert session.scalar(select(func.count()).select_from(ResultSet)) == 0 and session.scalar(select(func.count()).select_from(ItemTag)) == 2
    assert session.scalar(select(func.count()).select_from(Tag)) == 2
