from fastapi.testclient import TestClient

from kosha.app import app
from tests.xfix import NOV_10, bookmarks_page, entry, envelope, sort_index, timeline, tweet


def seed(client, n):
    entries = [entry(str(1000 + i), tweet(str(1000 + i), f"Post {i}"), sort_index(NOV_10 + i * 1000)) for i in range(n)]
    client.post("/api/captures", json=envelope("bookmarks_page", timeline(entries)))


def test_auth(user):
    anonymous = TestClient(app)
    assert anonymous.get("/api/items").status_code == 401
    assert anonymous.get("/media/1").status_code == 401
    assert anonymous.get("/api/items", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert anonymous.get("/api/items", headers={"Authorization": "Bearer test-api-token"}).status_code == 200

    assert anonymous.post("/api/auth/login", json={"email": "admin@example.test", "password": "nope"}).status_code == 401
    assert anonymous.post("/api/auth/login", json={"email": "admin@example.test", "password": "test-password"}).status_code == 200
    assert anonymous.get("/api/auth/me").json()["email"] == "admin@example.test"  # the session cookie
    assert anonymous.post("/api/auth/logout").status_code == 204
    assert anonymous.get("/api/auth/me").status_code == 401
    assert anonymous.get("/api/openapi.json").status_code == 200


def test_pagination_is_stable(client):
    seed(client, 60)
    pages = [client.get("/api/items", params={"page": p, "page_size": 25}).json() for p in (1, 2, 3)]
    assert [len(p["items"]) for p in pages] == [25, 25, 10]
    assert pages[0]["total"] == 60 and pages[0]["pages"] == 3
    ids = [i["source_id"] for p in pages for i in p["items"]]
    assert ids == [str(1000 + i) for i in reversed(range(60))]  # newest saved first, no repeats, no gaps
    # Changing an item's state does not move it.
    client.patch(f"/api/items/{pages[1]['items'][0]['id']}", json={"starred": True, "read": True})
    assert client.get("/api/items", params={"page": 2, "page_size": 25}).json()["items"][0]["starred"] is True
    assert client.get("/api/items", params={"page_size": 30}).status_code == 422


def test_item_rows_and_filters(client):
    client.post("/api/captures", json=envelope("bookmarks_page", bookmarks_page()))
    rows = {i["source_id"]: i for i in client.get("/api/items").json()["items"]}
    assert set(rows) == {"1001", "1002", "1003", "1004", "1005", "1006"}  # context items are not listed

    quote = rows["1001"]
    assert quote["quote"]["target_state"] == "held" and quote["saved_at_approximate"] is True
    nested = quote["quoted"]  # nested one level, with its own media and a pointer to what it quotes
    assert nested["author"]["handle"] == "bob" and nested["media"][0]["url"].startswith("/media/")
    assert nested["quote"] == {"source_id": "800", "item_id": None, "target_state": "missing", "author_handle": None}
    assert nested["quoted"] is None
    assert rows["1004"]["reply_to"]["source_id"] == "700" and rows["1004"]["thread"]["root_source_id"] == "700"
    assert rows["1002"]["links"][0]["domain"] == "example.com"

    def listed(**params):
        return {i["source_id"] for i in client.get("/api/items", params=params).json()["items"]}

    assert listed(content_type="video") == {"1004"}
    assert listed(content_type="article") == {"1003"}
    assert listed(is_saved="false") == {"900"}
    assert listed(author="@ADA") == set(rows) - {"1005"}
    assert listed(source="linkedin") == set()
    assert listed(year=2026) == set(rows)

    facets = client.get("/api/facets").json()
    assert facets["sources"] == [{"value": "x", "count": 6}] and facets["years"] == [{"value": "2026", "count": 6}]
    stats = client.get("/api/stats").json()
    assert (stats["items"], stats["context_items"], stats["unavailable"], stats["untagged"]) == (6, 1, 1, 6)

    detail = client.get(f"/api/items/{rows['1001']['id']}").json()
    assert detail["quoted"]["body"] == "Quoted & original" and detail["thread_items"] == []
    media_id = rows["1006"]["media"][0]["id"]
    redirect = client.get(f"/media/{media_id}", follow_redirects=False)  # not stored yet: go to the source
    assert redirect.status_code == 307 and redirect.headers["location"] == "https://pbs.twimg.com/media/abc.jpg"


def test_patch_bulk_undo_archive_delete(client):
    seed(client, 3)
    items = client.get("/api/items").json()["items"]
    ids = [i["id"] for i in items]
    tag = client.post("/api/tags", json={"name": "AI"}).json()
    client.patch(f"/api/items/{ids[0]}", json={"note": "keep", "starred": True})

    done = client.post("/api/items/bulk", json={"ids": ids, "archived": True, "starred": False, "add_tag_ids": [tag["id"]]}).json()
    assert done["updated"] == 3
    assert client.get("/api/items").json()["total"] == 0
    archived = client.get("/api/items", params={"archived": "true"}).json()
    assert archived["total"] == 3 and archived["items"][0]["tags"][0]["name"] == "AI"

    assert client.post(f"/api/undo/{done['undo_batch_id']}").status_code == 204
    assert client.post(f"/api/undo/{done['undo_batch_id']}").status_code == 409
    back = {i["id"]: i for i in client.get("/api/items").json()["items"]}
    assert len(back) == 3 and back[ids[0]]["starred"] is True and back[ids[0]]["note"] == "keep"
    assert all(i["tags"] == [] for i in back.values())

    assert client.delete(f"/api/items/{ids[0]}").status_code == 204
    assert client.get(f"/api/items/{ids[0]}").status_code == 404
    assert client.get("/api/items").json()["total"] == 2


def test_settings_keybindings_collections(client):
    assert client.get("/api/settings").json()["store_images"] is True
    assert client.patch("/api/settings", json={"store_videos": True, "x_api_monthly_cap_usd": 5}).json()["store_videos"] is True
    assert client.patch("/api/settings", json={"nope": 1}).status_code == 422
    assert client.patch("/api/settings", json={"store_images": "yes"}).status_code == 422
    assert client.put("/api/keybindings", json={"star": "s", "archive": "e"}).status_code == 200
    assert client.get("/api/keybindings").json() == {"star": "s", "archive": "e"}

    page = timeline([entry("1", tweet("1", "In a folder"), "50")])
    client.post("/api/captures", json=envelope("folder_page", page, folder_id="f1", folder_name="Coding with AI"))
    client.post("/api/captures", json=envelope("folder_list", {"data": {"bookmark_collections_slice": {"items": [{"id": "f2", "name": "Empty"}]}}}))
    folders = {c["name"]: c for c in client.get("/api/collections").json()}
    assert folders["Coding with AI"]["count"] == 1 and folders["Empty"]["count"] == 0
    assert client.get("/api/items", params={"collection_id": folders["Coding with AI"]["id"]}).json()["total"] == 1
