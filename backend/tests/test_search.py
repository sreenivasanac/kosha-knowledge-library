"""Filters compiled to SQL, ranking, snapshots, suggest and the search routes,
on a small seeded library."""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from kosha.config import EMBEDDING_DIM
from kosha.models import (
    Collection,
    Item,
    ItemCollection,
    ItemEmbedding,
    ItemLink,
    ItemMedia,
    ItemRelation,
    ItemTag,
    ResultSet,
    Tag,
)
from kosha.search import engine
from kosha.search.embeddings import DOC_MODEL
from kosha.search.engine import rrf, search
from kosha.search.index import reindex
from kosha.search.query import QueryError, compile_filter, parse
from kosha.search.suggest import suggest
from kosha.summaries import METHOD_OWN_TEXT, METHOD_RULE, summarize

NOW = datetime.now(UTC)


def axis(n: int) -> list[float]:
    """A unit vector along dimension n: two of them are orthogonal."""
    return [1.0 if i == n else 0.0 for i in range(EMBEDDING_DIM)]


def blend(*parts: tuple[int, float]) -> list[float]:
    vector = [0.0] * EMBEDDING_DIM
    for n, weight in parts:
        vector[n] = weight
    return vector


@pytest.fixture
def library(session, user):
    """Six saved items, one archived, one context item; returns {name: id}."""
    def item(source_id, body, handle="ada", **fields):
        defaults = dict(lang="en", content_types=["text"], saved_order=int(source_id),
                        saved_at_est=NOW - timedelta(days=100 - int(source_id)))
        row = Item(user_id=user.id, source="x", source_id=source_id, body=body, author_handle=handle,
                   author_name=handle.title(), **{**defaults, **fields})
        session.add(row)
        session.flush()
        return row

    pg = item("1", "Tuning pgvector HNSW indexes for retrieval, a thread", likes=5000, posted_at=NOW - timedelta(days=200))
    rag = item("2", "Hybrid search beats plain retrieval augmented generation", handle="bob", likes=10,
               posted_at=NOW - timedelta(days=5), starred=True, title="RAG notes")
    video = item("3", "Watch this demo", handle="bob", duration_s=120, content_types=["text", "video"],
                 note="todo: retrieving the transcript", read_at=NOW)
    repo = item("4", "A Postgres job queue", handle="cy", content_types=["text", "link"])
    quoting = item("5", "Agree with this", handle="cy")
    long_post = item("6", "Retrievers explained. " * 60, handle="dee", lang="de")
    archived = item("7", "Old retrieval post", archived_at=NOW)
    context = item("8", "The quoted original about embeddings", handle="eve", is_saved=False)

    session.add(ItemLink(item_id=repo.id, url="https://github.com/acme/pgqueue", domain="github.com", title="pgqueue: jobs in Postgres"))
    session.add(ItemLink(item_id=rag.id, url="https://blog.example.com/rag", domain="blog.example.com"))
    session.add(ItemMedia(item_id=video.id, source_key="v1", type="video", extract_text="we cache the prompt prefix",
                          extract_kind="transcript"))
    session.add(ItemRelation(from_item_id=quoting.id, relation="quotes", to_item_id=context.id, to_source_id="8", target_state="held"))
    session.add(ItemRelation(from_item_id=pg.id, relation="thread_member", to_source_id="99"))
    folder = Collection(user_id=user.id, source="x", source_id="f1", name="Coding with AI", kind="x_folder")
    session.add(folder)
    session.flush()
    session.add(ItemCollection(item_id=repo.id, collection_id=folder.id))
    ai = Tag(user_id=user.id, name="AI", slug="ai", path="1", id=1)
    session.add(ai)
    session.flush()
    rag_tag = Tag(user_id=user.id, name="RAG", slug="rag", path="1.2", id=2, parent_id=1)
    session.add(rag_tag)
    session.flush()
    session.add(ItemTag(item_id=rag.id, tag_id=2, role="primary", attached_by="agent", confidence=0.7))
    session.add(ItemTag(item_id=pg.id, tag_id=1, attached_by="human"))
    session.add(ItemTag(item_id=video.id, tag_id=2, removed_by_user=True))
    reindex(session)
    session.commit()
    return {"pg": pg.id, "rag": rag.id, "video": video.id, "repo": repo.id, "quoting": quoting.id,
            "long": long_post.id, "archived": archived.id, "context": context.id}


def found(session, user, q):
    names = {v: k for k, v in session.info["library"].items()}
    ids = session.scalars(select(Item.id).where(*compile_filter(session, user.id, parse(q))))
    return {names[i] for i in ids}


@pytest.mark.parametrize("q, expected", [
    ("", {"pg", "rag", "video", "repo", "quoting", "long"}),  # saved and not archived by default
    ("is:archived", {"archived"}),
    ("include:archived retrieval", {"pg", "rag", "video", "archived"}),
    ("include:context from:eve", {"context"}),
    # text: stemmed word, phrase, prefix, fuzzy, regex, negation, fields
    ("retrieval", {"pg", "rag", "video"}),  # stemmed: the video matches through "retrieving" in its note
    ("retrieve in:body", {"pg", "rag"}),
    ('"hybrid search"', {"rag"}),
    ('"search hybrid"', set()),
    ("retriev*", {"pg", "rag", "video", "long"}),  # prefix also reaches the German item's plain words
    ("~pgvectr", {"pg"}),
    (r"/hnsw\s+index/", {"pg"}),
    ("retrieval -hybrid", {"pg", "video"}),
    ("pgvector OR queue", {"pg", "repo"}),
    ("embeddings", {"quoting"}),  # the quoted post's text
    ("pgqueue", {"repo"}),  # a link title
    ("in:transcript (prompt)", {"video"}),
    ("in:body (prompt)", set()),
    ("note:todo", {"video"}),
    ("title:rag", {"rag"}),
    ("the", {"pg", "rag", "video", "repo", "quoting", "long"}),  # only stop words: no constraint
    # tags
    ("#ai", {"pg", "rag"}),
    ("#=ai", {"pg"}),
    ("#ai/*", {"rag"}),
    ("#rag", {"rag"}),  # a unique leaf name; the removed tag on the video does not count
    ("-#ai", {"video", "repo", "quoting", "long"}),
    ("primary:#rag", {"rag"}),
    ("secondary:#rag", set()),
    ("tags:0", {"video", "repo", "quoting", "long"}),
    ("is:untagged", {"video", "repo", "quoting", "long"}),
    ("tags:>0", {"pg", "rag"}),
    ("tagged-by:ai", {"rag"}),
    ("tagged-by:human", {"pg"}),
    ("tagged-by:ai #ai conf:<0.85", {"rag"}),
    ("tagged-by:human #rag", set()),  # the modifiers bind to the same tag row
    ("conf:>0.9", set()),
    # people and structure
    ("from:bob", {"rag", "video"}),
    ("from:@BOB,cy", {"rag", "video", "repo", "quoting"}),
    ('from:"Dee"', {"long"}),
    ("-from:bob -from:cy", {"pg", "long"}),
    ("quotes:@eve", {"quoting"}),
    ("has:quote", {"quoting"}),
    ("is:thread", {"pg"}),
    ('folder:"coding with ai"', {"repo"}),
    # content
    ("type:video", {"video"}),
    ("has:link", {"rag", "repo"}),
    ("has:note", {"video"}),
    ("has:transcript", {"video"}),
    ("type:video -has:transcript", set()),
    ("lang:de", {"long"}),
    ("len:>1000", {"long"}),
    ("is:long", {"long"}),
    ("duration:<5m", {"video"}),
    ("duration:>10m", set()),
    ("domain:github.com", {"repo"}),
    ("site:example.com", {"rag"}),  # subdomains included
    ("link:pgqueue", {"repo"}),
    ("source:x type:link", {"repo"}),
    # time, state, engagement
    ("after:30d", {"rag"}),
    ("before:30d", {"pg"}),
    ("saved:..97d", {"pg", "rag", "video"}),
    ("captured:1d..", {"pg", "rag", "video", "repo", "quoting", "long"}),
    ("recent:2", {"quoting", "long"}),
    ("is:starred", {"rag"}),
    ("is:read", {"video"}),
    ("is:unread is:starred", {"rag"}),
    ("likes:>1000", {"pg"}),
    ("likes:1..100", {"rag"}),
    ("(from:bob OR from:cy) -type:video", {"rag", "repo", "quoting"}),
])
def test_filters(session, user, library, q, expected):
    session.info["library"] = library
    assert found(session, user, q) == expected


def test_id_list_set_and_compile_errors(session, user, library, client):
    session.info["library"] = library
    assert found(session, user, f"id:{library['pg']},{library['repo']}") == {"pg", "repo"}
    assert found(session, user, f"thread:{library['pg']}") == {"pg"}
    session.add(ResultSet(user_id=user.id, name="h7", item_ids=[library["rag"], library["video"]]))
    session.commit()
    assert found(session, user, "@h7 -type:video") == {"rag"}
    made = client.post("/api/lists", json={"name": "To read"}).json()
    client.post(f"/api/lists/{made['id']}/items", json={"item_ids": [library["long"]]})
    assert found(session, user, 'list:"to read"') == {"long"}
    for q, position in (("x #nope", 2), ("@nope", 0)):
        with pytest.raises(QueryError) as caught:
            compile_filter(session, user.id, parse(q))
        assert caught.value.position == position


def test_rrf_fusion():
    fused = rrf([([1, 2, 3], 1.0), ([3, 4], 1.0)])
    assert [i for i, _ in fused] == [3, 1, 2, 4]  # in both lists wins; equal scores go to the lower id
    assert dict(fused)[3] == pytest.approx(1 / 63 + 1 / 61)
    assert dict(fused)[1] == pytest.approx(1 / 61) and dict(fused)[2] == dict(fused)[4] == pytest.approx(1 / 62)
    assert [i for i, _ in rrf([([1, 2], 1.0), ([2, 1], 3.0)])] == [2, 1]  # the heavier ranker decides
    assert rrf([]) == [] and rrf([([], 1.0)]) == []
    assert engine.fusion_weights("hnsw", "hybrid") == engine.WEIGHTS_IDENTIFIER
    assert engine.fusion_weights("what should I read before building search", "hybrid") == engine.WEIGHTS_LONG
    assert engine.fusion_weights("tuning vector indexes well", "hybrid") == engine.WEIGHTS_DEFAULT
    assert engine.fusion_weights("anything at all", "keyword") == (1.0, 0.0)


def test_modes_and_ranking(session, user, library, monkeypatch):
    # Meaning lives on axis 0: the repo is closest, the video next; the rest are unrelated.
    for name, vector in (("repo", axis(0)), ("video", blend((0, 0.9), (1, 0.44))), ("pg", axis(2)), ("rag", axis(3))):
        session.add(ItemEmbedding(item_id=library[name], model=DOC_MODEL, content_hash="h", vector=vector))
    session.commit()
    monkeypatch.setattr(engine, "query_vector", lambda session, user_id, text: axis(0))
    ids = lambda result: [i.id for i in result["items"]]  # noqa: E731

    keyword = search(session, user.id, "retrieval", mode="keyword")
    words = {library["pg"], library["rag"], library["video"]}  # the video through "retrieving" in its note
    assert keyword["mode"] == "keyword" and set(ids(keyword)) == words
    semantic = search(session, user.id, "retrieval", mode="semantic")
    assert ids(semantic) == [library["repo"], library["video"]]  # nearest first; unrelated ones are cut
    hybrid = search(session, user.id, "retrieval")
    assert hybrid["mode"] == "hybrid" and set(ids(hybrid)) == words | {library["repo"]}
    assert ids(hybrid)[0] == library["video"]  # the only item both rankers return
    assert all(hybrid["scores"][i] > 0 for i in ids(hybrid)) and "<mark>" in hybrid["snippets"][library["pg"]]
    assert "<mark>" not in hybrid["snippets"][library["repo"]]  # a semantic-only hit shows its opening

    # Qualifiers filter first: both rankers stay inside the set.
    assert ids(search(session, user.id, "retrieval from:bob")) == [library["video"], library["rag"]]
    related = search(session, user.id, "zebra from:cy")
    assert ids(related) == [library["repo"]] and related["notice"] == "no_exact_matches"
    similar = search(session, user.id, f"similar:{library['repo']}")
    assert similar["mode"] == "hybrid" and ids(similar) == [library["video"]]

    # No words to rank by, or an explicit sort: a plain filter in SQL order.
    plain = search(session, user.id, "from:bob")
    assert plain["mode"] == "filter" and plain["snapshot_id"] is None and ids(plain) == [library["video"], library["rag"]]
    by_likes = search(session, user.id, "retrieval sort:likes")
    assert by_likes["mode"] == "filter" and ids(by_likes) == [library["pg"], library["rag"], library["video"]]
    assert ids(search(session, user.id, "", sort="likes-asc"))[0] == library["rag"]
    assert ids(search(session, user.id, "retriev*")) == [library["long"], library["video"], library["rag"], library["pg"]]


def test_snapshot_paging_is_stable(session, user, client):
    for n in range(60):
        session.add(Item(user_id=user.id, source="x", source_id=str(n), body="retrieval " * (n % 7 + 1) + f"post {n}", lang="en"))
    reindex(session)
    session.commit()
    params = {"q": "retrieval", "mode": "keyword", "page_size": 25}
    first = client.get("/api/items", params=params).json()
    assert first["total"] == 60 and first["mode"] == "keyword" and first["snapshot_id"] and not first["refreshed"]
    snapshot = {"snapshot_id": first["snapshot_id"]}
    page2 = client.get("/api/items", params={**params, **snapshot, "page": 2}).json()

    # New matches and a changed row arrive while the reader is on page 2.
    session.add(Item(user_id=user.id, source="x", source_id="new", body="retrieval retrieval retrieval", title="retrieval", lang="en"))
    session.delete(session.get(Item, page2["items"][3]["id"]))
    reindex(session)
    session.commit()
    again = client.get("/api/items", params={**params, **snapshot, "page": 2}).json()
    assert again["snapshot_id"] == first["snapshot_id"] and again["total"] == 60
    kept = [i["id"] for i in page2["items"] if i["id"] != page2["items"][3]["id"]]
    assert [i["id"] for i in again["items"]] == kept  # same order; the deleted row is skipped
    page3 = client.get("/api/items", params={**params, **snapshot, "page": 3}).json()
    seen = [i["id"] for p in (first, page2, page3) for i in p["items"]]
    assert len(seen) == len(set(seen)) == 60
    assert all(i["score"] is not None and "<mark>" in i["snippet"] for i in first["items"])

    # A fresh search sees the new row; an expired or foreign snapshot runs again.
    assert client.get("/api/items", params=params).json()["total"] == 60  # 61 matches minus the deleted one
    session.get(ResultSet, first["snapshot_id"]).expires_at = NOW - timedelta(minutes=1)
    session.commit()
    rerun = client.get("/api/items", params={**params, **snapshot}).json()
    assert rerun["refreshed"] and rerun["snapshot_id"] != first["snapshot_id"]
    other = client.get("/api/items", params={**params, **snapshot, "q": "post"}).json()
    assert other["refreshed"] and other["total"] == 59


def test_items_route_errors_and_legacy_filters(client, library):
    bad = client.get("/api/items", params={"q": "from:bob frm:x"})
    assert bad.status_code == 422 and bad.json()["detail"]["position"] == 9 and "frm" in bad.json()["detail"]["message"]
    assert client.get("/api/items", params={"q": "x", "mode": "nope"}).status_code == 422
    assert client.get("/api/items", params={"q": "", "sort": "nope"}).status_code == 422
    both = client.get("/api/items", params={"q": "from:bob", "content_type": "video"}).json()
    assert [i["id"] for i in both["items"]] == [library["video"]] and both["mode"] == "filter"
    assert client.get("/api/items", params={"q": "is:archived"}).json()["total"] == 1
    assert client.get("/api/items").json()["mode"] is None  # no q: the plain list, as before
    assert client.get("/api/search/recent").json() == ["is:archived", "from:bob"]


def test_suggest(session, user, client, library):
    def rows(q, cursor=None):
        return suggest(session, user.id, q, cursor)

    keys = rows("fr")
    assert keys["replace_range"] == [0, 2] and keys["suggestions"][0]["insert"] == "from:" and keys["suggestions"][0]["kind"] == "key"
    authors = rows("from:")
    assert [(s["insert"], s["count"]) for s in authors["suggestions"]] == [("@bob", 2), ("@cy", 2), ("@ada", 1), ("@dee", 1)]
    # Counts are inside the current result, and only the value is replaced.
    scoped = rows("retrieval from:b")
    assert scoped["replace_range"] == [15, 16] and [(s["insert"], s["count"]) for s in scoped["suggestions"]] == [("@bob", 2)]
    second = rows("from:ada,c")
    assert second["replace_range"] == [9, 10] and second["suggestions"][0]["insert"] == "@cy"
    middle = rows("from:b is:starred", cursor=6)
    assert middle["replace_range"] == [5, 6] and [(s["insert"], s["count"]) for s in middle["suggestions"]] == [("@bob", 1)]

    tags = {s["insert"]: s["count"] for s in rows("#")["suggestions"]}
    assert tags == {"#AI": 2, "#AI/*": 1, "#AI/RAG": 1}
    assert [s["insert"] for s in rows("from:bob #ra")["suggestions"]] == ["#AI/RAG"]
    assert {s["insert"]: s["count"] for s in rows("domain:")["suggestions"]} == {"github.com": 1, "blog.example.com": 1}
    assert rows("folder:")["suggestions"][0] == {"insert": '"Coding with AI"', "label": "Coding with AI", "count": 1, "kind": "value"}
    states = {s["insert"]: s["count"] for s in rows("is:")["suggestions"]}
    assert states["starred"] == 1 and states["untagged"] == 4 and states["archived"] == 1
    assert {s["insert"]: s["count"] for s in rows("from:bob has:")["suggestions"]}["transcript"] == 1
    assert {s["insert"]: s["count"] for s in rows("type:v")["suggestions"]} == {"video": 1}
    assert {s["insert"]: s["count"] for s in rows("lang:")["suggestions"]} == {"en": 5, "de": 1}
    assert {s["insert"]: s["count"] for s in rows("-from:bob source:")["suggestions"]} == {"x": 4}
    assert rows("duration:")["suggestions"][1] == {"insert": "1m..5m", "label": "1m..5m", "count": 1, "kind": "value"}
    assert [s["insert"] for s in rows("frm:x")["suggestions"]][0] == "from:x"
    hints = rows("bo")["suggestions"]
    assert {"insert": "from:@bob", "label": "from:@bob", "count": 2, "kind": "author"} in hints

    # Empty bar: recent searches, then saved views.
    client.get("/api/items", params={"q": "from:bob"})
    client.post("/api/saved-views", json={"name": "Starred", "query": "is:starred"})
    session.expire_all()
    empty = client.get("/api/search/suggest", params={"q": ""}).json()
    assert [(s["kind"], s["insert"]) for s in empty["suggestions"]] == [("recent", "from:bob"), ("view", "is:starred")]


def test_parse_route_and_saved_views(client, library):
    parsed = client.post("/api/search/parse", json={"q": "pgvector from:@ada  -#ai"}).json()
    assert parsed["ok"] and parsed["canonical"] == "pgvector from:@ada -#ai" and parsed["has_text"]
    assert [(c["kind"], c["start"], c["end"]) for c in parsed["chips"]] == [("text", 0, 8), ("qualifier", 9, 18), ("tag", 20, 24)]
    broken = client.post("/api/search/parse", json={"q": "a #nope"}).json()
    assert not broken["ok"] and broken["error"]["position"] == 2

    assert client.post("/api/saved-views", json={"name": "Bad", "query": "frm:x"}).status_code == 422
    view = client.post("/api/saved-views", json={"name": "Bob", "query": "from:bob   is:starred", "params": {"mode": "keyword"}}).json()
    assert view["query"] == "from:bob is:starred"  # stored in canonical form
    patched = client.patch(f"/api/saved-views/{view['id']}", json={"name": "Bob's"}).json()
    assert patched["name"] == "Bob's" and patched["params"] == {"mode": "keyword"}
    assert [v["name"] for v in client.get("/api/saved-views").json()] == ["Bob's"]
    assert client.delete(f"/api/saved-views/{view['id']}").status_code == 204
    assert client.get("/api/saved-views").json() == [] and client.delete(f"/api/saved-views/{view['id']}").status_code == 404


def test_lists(client, library):
    made = client.post("/api/lists", json={"name": "To read", "description": "soon"}).json()
    url = f"/api/lists/{made['id']}"
    added = client.post(f"{url}/items", json={"item_ids": [library["pg"], library["rag"], library["repo"], 99999, library["pg"]]}).json()
    assert added["count"] == 3
    order = lambda: [i["id"] for i in client.get(url).json()["items"]]  # noqa: E731
    assert order() == [library["pg"], library["rag"], library["repo"]]
    client.put(f"{url}/order", json={"item_ids": [library["repo"], library["pg"]]})
    assert order() == [library["repo"], library["pg"], library["rag"]]
    removed = client.request("DELETE", f"{url}/items", json={"item_ids": [library["pg"]]}).json()
    assert removed["count"] == 2 and order() == [library["repo"], library["rag"]]
    assert client.get("/api/lists").json()[0]["name"] == "To read" and client.get("/api/lists/999").status_code == 404


def test_summaries(session, user, library):
    counts = summarize(session, user.id)
    assert counts["rule"] == 2 and counts["no_text"] == 0  # the long post, and the video with a transcript
    assert session.get(Item, library["video"]).summary.endswith("Transcript: we cache the prompt prefix")
    short, long_post = session.get(Item, library["repo"]), session.get(Item, library["long"])
    assert (short.summary, short.summary_method, short.summary_status) == ("A Postgres job queue", METHOD_OWN_TEXT, "done")
    assert long_post.summary_method == METHOD_RULE
    lead, meta = long_post.summary.split("\n")
    assert lead == "Retrievers explained. Retrievers explained. Retrievers explained."
    assert meta == "[Dee @dee · x · 1,319 chars · text]"
    short.summary, short.summary_method = "Written by hand", "human"
    reindex(session, [short.id])
    session.commit()
    assert summarize(session, user.id)["changed"] == 0 and session.get(Item, library["repo"]).summary == "Written by hand"
    # The summary is searchable.
    assert session.scalar(select(func.count()).select_from(Item).where(*compile_filter(session, user.id, parse("in:summary (hand)")))) == 1
