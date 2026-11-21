from tests.test_api import seed


def make(client, name, parent=None):
    return client.post("/api/tags", json={"name": name, "parent_id": parent}).json()


def tree(client):
    def flat(nodes, prefix=()):
        for n in nodes:
            yield "/".join((*prefix, n["name"])), n
            yield from flat(n["children"], (*prefix, n["name"]))
    return dict(flat(client.get("/api/tags").json()))


def test_tree_move_rename_keep_paths_and_counts(client):
    seed(client, 3)
    a, b, c = (i["id"] for i in client.get("/api/items").json()["items"])
    ai, cooking = make(client, "AI"), make(client, "Cooking")
    rag = make(client, "RAG", ai["id"])
    rerank = make(client, "Rerankers", rag["id"])  # no depth limit
    assert rerank["path_ids"] == f"{ai['id']}.{rag['id']}.{rerank['id']}"
    assert client.post("/api/tags", json={"name": "rag", "parent_id": ai["id"]}).status_code == 409  # sibling clash

    client.put(f"/api/items/{a}/tags/{ai['id']}")
    client.put(f"/api/items/{b}/tags/{rag['id']}")
    client.put(f"/api/items/{c}/tags/{rerank['id']}")
    t = tree(client)
    assert (t["AI"]["count"], t["AI"]["own_count"], t["AI/RAG"]["count"], t["AI/RAG/Rerankers"]["count"]) == (3, 1, 2, 1)
    assert client.get("/api/items", params={"tag_id": rag["id"]}).json()["total"] == 2  # subtree filter
    assert client.get("/api/items", params={"untagged": "true"}).json()["total"] == 0

    # Rename changes no path; move rewrites the subtree's paths.
    renamed = client.patch(f"/api/tags/{rag['id']}", json={"name": "Retrieval", "description": "RAG and search"}).json()
    assert renamed["path_ids"] == rag["path_ids"] and renamed["slug"] == "retrieval"
    moved = client.post(f"/api/tags/{rag['id']}/move", json={"parent_id": cooking["id"]}).json()
    assert moved["path_ids"] == f"{cooking['id']}.{rag['id']}"
    t = tree(client)
    assert t["Cooking/Retrieval/Rerankers"]["path_ids"] == f"{cooking['id']}.{rag['id']}.{rerank['id']}"
    assert (t["AI"]["count"], t["Cooking"]["count"]) == (1, 2)
    item = client.get(f"/api/items/{c}").json()
    assert item["tags"][0]["path"] == ["Cooking", "Retrieval", "Rerankers"]
    assert client.post(f"/api/tags/{cooking['id']}/move", json={"parent_id": rerank["id"]}).status_code == 400  # under itself
    assert client.post(f"/api/tags/{rag['id']}/move", json={"parent_id": None}).json()["path_ids"] == str(rag["id"])

    # Merge: items and children move to the target; delete needs recursive for a subtree.
    client.put(f"/api/items/{a}/tags/{rag['id']}")
    merged = client.post(f"/api/tags/{rag['id']}/merge", json={"into_id": ai["id"]})
    assert merged.status_code == 200
    t = tree(client)
    assert set(t) == {"AI", "AI/Rerankers", "Cooking"} and (t["AI"]["count"], t["AI"]["own_count"]) == (3, 2)
    assert client.delete(f"/api/tags/{ai['id']}").status_code == 409
    assert client.delete(f"/api/tags/{ai['id']}", params={"recursive": "true"}).status_code == 204
    assert set(tree(client)) == {"Cooking"}
    assert client.get("/api/items", params={"untagged": "true"}).json()["total"] == 3


def test_roles_and_status(client):
    seed(client, 1)
    item_id = client.get("/api/items").json()["items"][0]["id"]
    x, y, z = make(client, "X"), make(client, "Y"), make(client, "Z")

    def roles():
        return {t["name"]: t["role"] for t in client.get(f"/api/items/{item_id}").json()["tags"]}

    client.put(f"/api/items/{item_id}/tags/{x['id']}", json={"role": "primary"})
    client.put(f"/api/items/{item_id}/tags/{y['id']}", json={"role": "secondary"})
    client.put(f"/api/items/{item_id}/tags/{z['id']}")
    assert roles() == {"X": "primary", "Y": "secondary", "Z": "other"}
    # A new primary demotes the old one to "other"; so does a new secondary.
    client.put(f"/api/items/{item_id}/tags/{z['id']}", json={"role": "primary"})
    assert roles() == {"Z": "primary", "Y": "secondary", "X": "other"}
    client.put(f"/api/items/{item_id}/tags/{x['id']}", json={"role": "secondary"})
    assert roles() == {"Z": "primary", "X": "secondary", "Y": "other"}
    assert client.delete(f"/api/items/{item_id}/tags/{z['id']}").status_code == 204
    assert roles() == {"X": "secondary", "Y": "other"}

    proposed = client.post("/api/tags", json={"name": "Maybe", "status": "proposed", "origin": "agent"}).json()
    assert [t["name"] for t in client.get("/api/tags", params={"status": "proposed"}).json()] == ["Maybe"]
    assert client.post(f"/api/tags/{proposed['id']}/reject").json()["status"] == "rejected"
    assert client.post(f"/api/tags/{proposed['id']}/approve").json()["status"] == "approved"
