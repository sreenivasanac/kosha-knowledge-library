"""The pipeline: parser, every stage, Jev (a fake), write safety, label runs,
the routes and the MCP endpoint, on a small seeded library."""
from datetime import UTC, datetime, timedelta

import pytest
from constella.jev import Answer, Spend
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from kosha import add_item as add_item_module
from kosha import jev, runs
from kosha.app import app
from kosha.config import DEFAULT_SETTINGS, EMBEDDING_DIM
from kosha.models import (
    Collection,
    Item,
    ItemCollection,
    ItemEmbedding,
    ItemLink,
    ItemTag,
    JevAnswer,
    List,
    ListItem,
    ResultSet,
    SavedView,
    SpendLedger,
    Tag,
)
from kosha.pipeline import executor, run_query, writes
from kosha.pipeline.parser import split
from kosha.search import engine
from kosha.search.embeddings import DOC_MODEL
from kosha.search.index import reindex
from kosha.search.query import QueryError
from tests.conftest import API_TOKEN

NOW = datetime.now(UTC)
N_RAG, N_ROBOT, N_COOK = 30, 6, 4


def vec(axis: int, tilt: float = 0.0) -> list[float]:
    """Mostly along one dimension, a little along the next, so items of a topic sit close but apart."""
    v = [0.0] * EMBEDDING_DIM
    v[axis], v[axis + 10] = 1.0, tilt
    return v


class FakeJev:
    """Says yes (0.95) when the text is about retrieval, else 0.1; counts what it is asked."""
    model = "fake-jev"

    def __init__(self):
        self.asked = 0

    def ask(self, pairs, by):
        assert by == jev.JEV_LAYOUT  # one layout everywhere
        self.asked += len(pairs)
        answers = []
        for question, _, text in pairs:
            topic = "robot" if "robot" in question.instructions.lower() else "cook" if "cook" in question.instructions.lower() else "retriev"
            p = 0.95 if topic in text.lower() else 0.1
            answers.append(Answer(p, 0.9 if question.levels else None))
        return answers, Spend(requests=1, input_tokens=100 * len(pairs), usd=0.001 * len(pairs), asked=len(pairs))


@pytest.fixture
def fake(monkeypatch):
    client = FakeJev()
    monkeypatch.setattr(jev, "get_client", lambda: client)
    topic_axis = lambda text: 1 if "robot" in text.lower() else 2 if "cook" in text.lower() else 0  # noqa: E731
    monkeypatch.setattr(executor, "query_vector", lambda session, user_id, text: vec(topic_axis(text)))
    monkeypatch.setattr(engine, "query_vector", lambda session, user_id, text: vec(topic_axis(text)))
    monkeypatch.setattr(executor, "embed_texts", lambda texts, model, kind: ([vec(topic_axis(t)) for t in texts], 10))
    executor._library_terms.clear()
    return client


@pytest.fixture
def lib(session, user, fake):
    """30 retrieval posts by ada, 6 robotics by bob, 4 cooking by cy (one too thin to judge)."""
    made = {"rag": [], "robot": [], "cook": []}

    def item(n, topic, body, handle, axis, **fields):
        row = Item(user_id=user.id, source="x", source_id=str(n), body=body, author_handle=handle, author_name=handle.title(),
                   lang="en", saved_order=n, saved_at_est=NOW - timedelta(days=n), likes=n, **{"content_types": ["text"], **fields})
        session.add(row)
        session.flush()
        session.add(ItemEmbedding(item_id=row.id, model=DOC_MODEL, content_hash="h", vector=vec(axis, n / 1000)))
        made[topic].append(row.id)
        return row

    n = 0
    for _ in range(N_RAG):
        n += 1
        item(n, "rag", f"Retrieval augmented generation note {n}: chunking and rerankers for grounded answers", "ada", 0)
    for _ in range(N_ROBOT):
        n += 1
        item(n, "robot", f"Robot arm build log {n}: servo torque and inverse kinematics", "bob", 1, content_types=["text", "video"])
    for _ in range(N_COOK - 1):
        n += 1
        item(n, "cook", f"Cooking pasta at home, attempt {n}: salt the water", "cy", 2)
    thin = item(n + 1, "cook", "hi", "cy", 2)
    session.add(ItemLink(item_id=made["robot"][0], url="https://github.com/acme/arm", domain="github.com", title="arm"))
    folder = Collection(user_id=user.id, source="x", source_id="f1", name="Hardware", kind="x_folder")
    session.add(folder)
    session.flush()
    session.add(ItemCollection(item_id=made["robot"][0], collection_id=folder.id))
    reindex(session)
    session.commit()
    return {**made, "thin": thin.id, "all": N_RAG + N_ROBOT + N_COOK}


def q(session, user, text, **kw):
    return run_query(session, user.id, text, **kw)


def err(session, user, text, **kw):
    with pytest.raises(QueryError) as caught:
        run_query(session, user.id, text, **kw)
    session.rollback()
    return caught.value


def tag(session, user, name, tag_id, parent=None, namespace="main", **fields):
    row = Tag(id=tag_id, user_id=user.id, name=name, slug=name.lower(), parent_id=parent.id if parent else None,
              path=f"{parent.path}.{tag_id}" if parent else str(tag_id), namespace=namespace, **fields)
    session.add(row)
    session.flush()
    return row


def rows_of(session, tag_id):
    return {r.item_id: r for r in session.scalars(select(ItemTag).where(ItemTag.tag_id == tag_id))}


# --- Parser -----------------------------------------------------------------
def test_split_keeps_quotes_and_regex_whole():
    text = 'where from:a "x | y" | grep /rag|retriev/ | jev "a | b?" >= 0.9 | head 5'
    stages = split(text)
    assert [s.name for s in stages] == ["where", "grep", "jev", "head"]
    assert stages[1].arg == "/rag|retriev/" and text[stages[1].pos:].startswith("grep")
    assert [t.text for t in stages[2].toks()] == ["a | b?", ">=", "0.9"] and stages[2].toks()[0].quoted
    assert text[stages[3].arg_pos] == "5"
    assert split("@mine | count")[0].name == "@" and split("@mine | count")[0].arg == "mine"


@pytest.mark.parametrize("text,message,position", [
    ("all | bogus 3", 'Unknown stage "bogus"', 6),
    ("all | haed 3", 'did you mean "head"', 6),
    ("grep x", "A pipeline starts with", 0),
    ("all | | head 3", "Empty stage", 6),
    ('all | grep "open', "Missing closing quote", 11),
    ("all | head many", "is not a number", 11),
    ("all | tag \"A\" | head 3", "must be the last stage", 6),
    ("where frm:ada | head 3", "Unknown", 6),
    ("all | grep from:ada", "grep takes words", 16),
    ("all | sort shoe", 'Unknown sort "shoe"', 11),
    ("all | show id colour", 'Unknown field "colour"', 14),
    ("all | count by shoe", "count by one of", 15),
    ("all | jev about rag", "question in quotes", 10),
    ('all | jev "q?" >= 3', "between 0 and 1", 18),
    ("all | union nothing", "needs a saved set", 12),
    ("@nope | count", "No result set named @nope", 0),
    ('all | semantic "x" top', "top needs a value", 19),
])
def test_errors_have_positions(session, user, lib, text, message, position):
    error = err(session, user, text)
    assert message in error.message and error.position == position


# --- Stages -----------------------------------------------------------------
def test_narrowing_and_slicing(session, user, lib):
    assert q(session, user, "all")["n"] == lib["all"]
    assert q(session, user, "where from:bob,cy")["n"] == N_ROBOT + N_COOK
    assert q(session, user, "all | grep ~retrievl")["n"] == N_RAG  # fuzzy
    assert q(session, user, "all | grep /robot|pasta/")["n"] == N_ROBOT + N_COOK - 1
    assert q(session, user, "all | grep -v retrieval")["n"] == N_ROBOT + N_COOK
    assert q(session, user, "all | grep retriev* | where from:bob")["n"] == 0
    assert q(session, user, "all | where type:video | where from:bob")["n"] == N_ROBOT
    result = q(session, user, "all | sort likes | head 3 | show id")
    assert [r["id"] for r in result["rows"]] == [lib["thin"], *reversed(lib["cook"][-3:-1])]  # most likes = highest n
    assert q(session, user, "all | sort likes asc | skip 2 | tail 5")["n"] == 5
    first = [r["id"] for r in q(session, user, "all | sample 7 | show id")["rows"]]
    assert len(first) == 7 and first == [r["id"] for r in q(session, user, "all | sample 7 | show id")["rows"]]
    assert first != [r["id"] for r in q(session, user, "all | sample 7 3 | show id")["rows"]]
    # every stage reports what entered and what left
    stages = q(session, user, "where from:ada | grep retrieval | head 4")["stages"]
    assert [(s["stage"], s["in"], s["out"]) for s in stages] == [("where", 0, N_RAG), ("grep", N_RAG, N_RAG), ("head", N_RAG, 4)]


def test_ranking_stages(session, user, lib):
    result = q(session, user, 'all | semantic "robot arms" top 4 | show id score')
    assert {r["id"] for r in result["rows"]} <= set(lib["robot"]) and result["rows"][0]["score"] > 0.9
    assert q(session, user, 'where from:ada | semantic "robot arms" | head 3')["n"] == 3  # ranks the incoming set only
    hybrid = q(session, user, 'all | hybrid "robot servo torque" top 5 | show id')
    assert {r["id"] for r in hybrid["rows"]} <= set(lib["robot"]) and hybrid["n"] == 5
    similar = q(session, user, f"all | similar {lib['cook'][0]} top 2 | show id")
    assert {r["id"] for r in similar["rows"]} <= set(lib["cook"]) and lib["cook"][0] not in {r["id"] for r in similar["rows"]}
    q(session, user, "where from:bob | as @bots")
    assert {r["id"] for r in q(session, user, "all | similar @bots top 6 | show id")["rows"]} == set(lib["robot"])


def test_sets_and_algebra(session, user, lib):
    saved = q(session, user, "where from:bob | as @bots")
    assert saved["set"] == "bots" and "saved=@bots" in saved["text"]
    assert q(session, user, "@bots | count")["n"] == N_ROBOT
    assert q(session, user, "where from:cy | union @bots")["n"] == N_ROBOT + N_COOK
    assert q(session, user, "all | intersect @bots")["n"] == N_ROBOT
    assert q(session, user, "all | except @bots")["n"] == lib["all"] - N_ROBOT
    assert q(session, user, "where @bots type:video")["n"] == N_ROBOT  # a set is also a filter atom
    row = session.scalar(select(ResultSet).where(ResultSet.name == "bots"))
    row.expires_at = NOW - timedelta(minutes=1)
    session.commit()
    assert "No result set named @bots" in err(session, user, "@bots | count").message


def test_summaries(session, user, lib):
    text = q(session, user, "all | count by author")["text"]
    assert f"@ada  {N_RAG}" in text and f"@bob  {N_ROBOT}" in text and text.index("@ada") < text.index("@bob")
    assert "... 2 more values" in q(session, user, "all | count by author top 1")["text"]
    for dim, expected in (("source", f"x  {lib['all']}"), ("type", f"video  {N_ROBOT}"), ("lang", f"en  {lib['all']}"),
                          ("domain", "github.com  1"), ("folder", "Hardware  1"), ("year", str(NOW.year)), ("month", f"{NOW:%Y}")):
        assert expected in q(session, user, f"all | count by {dim}")["text"], dim
    stats = q(session, user, "where from:ada | stats")["text"]
    assert f"n={N_RAG} authors=1" in stats and "untagged=30" in stats and "embedded=30" in stats
    assert "stats likes: n=6 min=31 median=33.50 max=36" in q(session, user, "where from:bob | stats likes")["text"]
    terms = q(session, user, "where from:bob | terms 10")["text"]
    assert "robot" in terms and "servo" in terms and "retriev" not in terms
    assert "whole library" in q(session, user, "all | terms")["text"]


def test_groups_and_drill_down(session, user, lib):
    result = q(session, user, "all | groups 3 as t")
    assert "@t1 n=30 cohesion=" in result["text"] and "@t2 n=6" in result["text"] and "@t3 n=4" in result["text"]
    assert result["text"].count("  - ") == 9  # three samples a group
    assert set(session.scalar(select(ResultSet.item_ids).where(ResultSet.name == "t2"))) == set(lib["robot"])
    second = q(session, user, "@t1 | groups 2 as t1.")
    assert "@t1.1 n=" in second["text"] and q(session, user, "@t1.1 | count")["n"] + q(session, user, "@t1.2 | count")["n"] == N_RAG


def test_show_fields_and_token_cap(session, user, lib):
    session.add(ItemTag(item_id=lib["robot"][0], tag_id=tag(session, user, "Robots", 50).id, role="primary"))
    session.commit()
    row = q(session, user, f"where id:{lib['robot'][0]} | show id author date body:20 title tags folder domain url summary:10")["rows"][0]
    assert row["author"] == "@bob" and row["body"] == "Robot arm build log…" and row["tags"] == "Robots*"
    assert row["folder"] == "Hardware" and row["domain"] == "github.com" and len(row["date"]) == 10
    default = q(session, user, "all")
    assert default["text"].count("\n") == 1 + executor.DEFAULT_ROWS and default["rows"] == []  # counts plus five compact rows
    # 40 rows of 400 characters would be about 4,000 tokens: cut at whole rows with a hint
    session.execute(Item.__table__.update().values(body=func.repeat("retrieval words ", 40)))
    session.commit()
    capped = q(session, user, "all | show id body:400")
    assert len(capped["text"]) <= executor.RESPONSE_MAX_TOKENS * executor.CHARS_PER_TOKEN
    shown = len(capped["rows"])
    assert 5 < shown < lib["all"] and f'showing {shown} of {lib["all"]}; more: add "| skip {shown}" before show' in capped["text"]
    assert q(session, user, "help")["text"] == executor.GRAMMAR


# --- Jev --------------------------------------------------------------------
def test_jev_threshold_cache_and_ledger(session, user, lib, fake):
    first = q(session, user, 'all | jev "Is this about retrieval-augmented generation?" >= 0.9 | as @rag')
    assert first["n"] == N_RAG and fake.asked == lib["all"] - 1  # the thin item is never sent
    assert first["spent_usd"] == pytest.approx(0.001 * (lib["all"] - 1)) and "skipped_thin=1" in first["text"]
    assert "p: <0.2:9  0.2-0.5:0  0.5-0.8:0  0.8-0.9:0  >=0.9:30" in first["text"]
    assert session.scalar(select(func.count()).select_from(JevAnswer)) == lib["all"] - 1
    # the same question again costs nothing: no call, no dollars, and the ledger says what it would have cost
    again = q(session, user, 'all | jev "Is this about retrieval-augmented generation?" >= 0.9')
    assert again["n"] == N_RAG and fake.asked == lib["all"] - 1 and again["spent_usd"] == 0
    assert "asked=0 cached=39" in again["text"]
    ledger = session.scalars(select(SpendLedger).where(SpendLedger.provider == "jev").order_by(SpendLedger.id)).all()
    assert [float(r.amount_usd) for r in ledger] == [pytest.approx(0.039), 0.0]
    assert ledger[1].meta["cached"] == 39 and ledger[1].meta["uncached_usd"] > 0
    # a reworded question is a new question; a changed text is asked again
    q(session, user, 'where from:bob | jev "Is this about robots?" as bot')
    assert fake.asked == lib["all"] - 1 + N_ROBOT
    session.get(Item, lib["robot"][0]).body = "Robot arm build log, rewritten with new servo notes"
    session.commit()
    q(session, user, 'where from:bob | jev "Is this about robots?" as bot')
    assert fake.asked == lib["all"] + N_ROBOT
    # answers travel with a saved set and can be filtered, sorted and shown
    assert q(session, user, "@rag | where p:>=0.9")["n"] == N_RAG and q(session, user, "@rag | where p:0.2..0.5")["n"] == 0
    mixed = q(session, user, 'all | jev "Is this about retrieval-augmented generation?" | sort p asc | head 1 | show id p')
    assert mixed["rows"][0]["p"] == 0.1
    assert q(session, user, 'all | jev "Is this about retrieval-augmented generation?" | where -p:>=0.9')["n"] == N_ROBOT + N_COOK


def test_jev_score_budget_and_confirm(session, user, lib, fake, monkeypatch):
    scored = q(session, user, 'where from:ada | head 3 | jev score "How useful is this for building a RAG system?" as use | show id use')
    assert [r["use"] for r in scored["rows"]] == [0.95] * 3
    assert session.scalar(select(JevAnswer.answer).limit(1)) == {"confidence": 0.9}
    assert "2 to 10 levels" in err(session, user, 'all | jev score "q?" "only one"').message
    # over the per-call budget: refused before anything is sent
    asked = fake.asked
    monkeypatch.setitem(DEFAULT_SETTINGS, "jev_run_budget_usd", 0.000001)
    assert "per-call Jev budget" in err(session, user, 'all | jev "Is this about cooking?"').message and fake.asked == asked
    monkeypatch.setitem(DEFAULT_SETTINGS, "jev_run_budget_usd", 0.5)
    monkeypatch.setitem(DEFAULT_SETTINGS, "jev_monthly_budget_usd", 0.000001)
    assert "monthly Jev budget" in err(session, user, 'all | jev "Is this about cooking?"').message and fake.asked == asked
    monkeypatch.setitem(DEFAULT_SETTINGS, "jev_monthly_budget_usd", 5.0)
    # many new questions: an estimate and a confirm token first
    monkeypatch.setattr(jev, "JEV_CONFIRM_ITEMS", 10)
    held = q(session, user, 'all | jev "Is this about cooking?" >= 0.9')
    assert "needs_confirm" in held["text"] and "est=$" in held["text"] and held["confirm"] and fake.asked == asked
    done = q(session, user, 'all | jev "Is this about cooking?" >= 0.9', confirm=held["confirm"])
    assert done["n"] == N_COOK - 1 and fake.asked == asked + lib["all"] - 1


def test_score_labels(session, user, lib, fake):
    ai = tag(session, user, "AI", 60, description="Artificial intelligence")
    tag(session, user, "RAG", 61, ai, description="Retrieval augmented generation: retrieving documents to ground answers")
    tag(session, user, "Robots", 62, description="Robot hardware builds", status="proposed")
    tag(session, user, "Old", 63, description="A rejected robot label", status="rejected")
    session.commit()
    dry = q(session, user, "all | score_labels")
    assert f"placed {N_RAG + N_ROBOT} of {lib['all'] - 1}" in dry["text"] and "AI/RAG  30" in dry["text"] and "Robots  6" in dry["text"]
    assert session.scalar(select(func.count()).select_from(ItemTag)) == 0  # nothing written without apply
    assert q(session, user, "all | score_labels | where p:>=0.8")["n"] == N_RAG + N_ROBOT
    assert "placed 0 of" in q(session, user, "where from:bob | score_labels #AI")["text"]
    asked = fake.asked
    preview = q(session, user, "all | score_labels apply")
    assert "PREVIEW" in preview["text"] and preview["confirm"] and fake.asked == asked  # answers came from the cache
    applied = q(session, user, "all | score_labels apply", confirm=preview["confirm"])
    assert applied["batch_id"] and "new=36" in applied["text"]
    rag_rows = rows_of(session, 61)
    assert len(rag_rows) == N_RAG and all(r.role == "primary" and r.attached_by == "agent" and r.confidence == 0.95 for r in rag_rows.values())


# --- Writes -----------------------------------------------------------------
def test_small_write_applies_directly_with_jev_confidence(session, user, lib):
    ai = tag(session, user, "AI", 70)
    rag = tag(session, user, "RAG", 71, ai)
    hand, removed, mine = lib["rag"][0], lib["rag"][1], lib["rag"][2]
    session.add(ItemTag(item_id=hand, tag_id=rag.id, role="primary", attached_by="human"))
    session.add(ItemTag(item_id=removed, tag_id=rag.id, attached_by="agent", removed_by_user=True))
    session.add(ItemTag(item_id=mine, tag_id=rag.id, attached_by="agent", confidence=0.5))
    session.commit()
    result = q(session, user, 'where from:ada | sort likes asc | head 10 | jev "about retrieval augmented generation?" >= 0.9 | tag "AI/RAG"')
    assert result["confirm"] is None and result["batch_id"] and "new=7 already=1 by_hand=1 removed_by_you=1" in result["text"]
    rows = rows_of(session, rag.id)
    assert rows[hand].attached_by == "human" and rows[hand].role == "primary" and rows[hand].confidence is None  # never touched
    assert rows[removed].removed_by_user  # never put back
    assert rows[mine].confidence == 0.95
    fresh = rows[lib["rag"][5]]
    assert (fresh.attached_by, fresh.confidence, fresh.role, fresh.namespace) == ("agent", 0.95, "other", "main")
    assert session.get(Tag, rag.id).status == "approved"
    # one undo batch puts everything back
    assert TestClient(app, headers={"Authorization": f"Bearer {API_TOKEN}"}).post(f"/api/undo/{result['batch_id']}").status_code == 204
    session.expire_all()
    rows = rows_of(session, rag.id)
    assert set(rows) == {hand, removed, mine} and rows[mine].confidence == 0.5


def test_large_write_previews_then_needs_the_token(session, user, lib, monkeypatch):
    preview = q(session, user, 'where from:ada | tag "AI/RAG"')
    assert "PREVIEW" in preview["text"] and "30 items: new=30" in preview["text"] and "created as proposed" in preview["text"]
    assert preview["text"].count("@ada") == executor.PREVIEW_SAMPLES and preview["confirm"].startswith("c_")
    assert session.scalar(select(func.count()).select_from(Tag)) == 0 and session.scalar(select(func.count()).select_from(ItemTag)) == 0
    # a token for another write, a forged one, or the same one after the set changed: a new preview, nothing written
    other = q(session, user, 'where from:ada | tag "AI/Other"')["confirm"]
    for bad in (other, "c_ffffffffff_deadbeef", "nonsense"):
        again = q(session, user, 'where from:ada | tag "AI/RAG"', confirm=bad)
        assert "does not match" in again["text"] and again["batch_id"] is None
    changed = q(session, user, 'where from:ada | head 29 | tag "AI/RAG"', confirm=preview["confirm"])
    assert "PREVIEW" in changed["text"] and session.scalar(select(func.count()).select_from(ItemTag)) == 0
    monkeypatch.setattr(writes.time, "time", lambda: 10**10)  # an expired token
    assert "PREVIEW" in q(session, user, 'where from:ada | tag "AI/RAG"', confirm=preview["confirm"])["text"]
    monkeypatch.undo()
    done = q(session, user, 'where from:ada | tag "AI/RAG"', confirm=preview["confirm"])
    assert "new=30" in done["text"] and done["batch_id"]
    created = {t.name: t for t in session.scalars(select(Tag))}
    assert created["RAG"].status == created["AI"].status == "proposed" and created["RAG"].origin == "agent"
    assert created["RAG"].parent_id == created["AI"].id and len(rows_of(session, created["RAG"].id)) == N_RAG


def test_tag_above_the_propose_limit_goes_back_to_proposed(session, user, lib, monkeypatch):
    monkeypatch.setattr(writes, "WRITE_PROPOSE_ABOVE", 28)
    agent_tag = tag(session, user, "Bulk", 80, origin="agent", status="approved")
    human_tag = tag(session, user, "Mine", 81, origin="human", status="approved")
    session.commit()
    for path in ("Bulk", "Mine"):
        preview = q(session, user, f'where from:ada | tag "{path}"')
        q(session, user, f'where from:ada | tag "{path}"', confirm=preview["confirm"])
    session.expire_all()
    assert session.get(Tag, agent_tag.id).status == "proposed" and session.get(Tag, human_tag.id).status == "approved"


def test_roles_untag_state_list_and_view(session, user, lib):
    ai = tag(session, user, "AI", 90)
    rag = tag(session, user, "RAG", 91, ai)
    bots = tag(session, user, "Robots", 92)
    a, b = lib["rag"][0], lib["rag"][1]
    for item_id, tag_id, by, conf, role in ((a, ai.id, "agent", 0.99, "other"), (a, rag.id, "agent", 0.9, "other"), (a, bots.id, "agent", 0.85, "other"),
                                            (b, bots.id, "human", None, "primary"), (b, rag.id, "agent", 0.9, "other"), (b, ai.id, "agent", 0.8, "other")):
        session.add(ItemTag(item_id=item_id, tag_id=tag_id, attached_by=by, confidence=conf, role=role))
    session.commit()
    result = q(session, user, f"where id:{a},{b} | roles auto")
    role = lambda item, t: session.get(ItemTag, (item, t.id)).role  # noqa: E731
    session.expire_all()
    # a: AI is RAG's ancestor, so it does not count: RAG primary, Robots secondary
    assert (role(a, rag), role(a, bots), role(a, ai)) == ("primary", "secondary", "other")
    # b: the primary set by hand stays; the agent's best becomes secondary
    assert (role(b, bots), role(b, rag), role(b, ai)) == ("primary", "secondary", "other")
    run_query(session, user.id, f"where id:{a} | tag \"Robots\" role primary")
    session.expire_all()
    assert (role(a, bots), role(a, rag)) == ("primary", "other")
    TestClient(app, headers={"Authorization": f"Bearer {API_TOKEN}"}).post(f"/api/undo/{result['batch_id']}")

    untagged = q(session, user, f'where id:{a},{b} | untag "Robots"')
    session.expire_all()
    assert "removed=1 by_hand=1" in untagged["text"] and session.get(ItemTag, (b, bots.id)) is not None and session.get(ItemTag, (a, bots.id)) is None

    starred = q(session, user, "where from:bob | star")
    assert "changed=6" in starred["text"] and q(session, user, "where is:starred")["n"] == N_ROBOT
    q(session, user, "where from:bob | head 2 | archive")
    assert q(session, user, "where from:bob")["n"] == N_ROBOT - 2 and q(session, user, "where is:archived | unarchive")["n"] == 2
    assert q(session, user, "where from:bob | read")["n"] == N_ROBOT and q(session, user, "where is:unread from:bob")["n"] == 0
    q(session, user, "where from:bob | unstar")
    assert q(session, user, "where is:starred")["n"] == 0

    listed = q(session, user, 'where from:bob | head 3 | list "To build"')
    assert "added=3" in listed["text"] and "added=3 already=3" in q(session, user, 'where from:bob | list "To build"')["text"]
    assert q(session, user, 'where list:"To build"')["n"] == N_ROBOT and session.scalar(select(func.count()).select_from(List)) == 1
    TestClient(app, headers={"Authorization": f"Bearer {API_TOKEN}"}).post(f"/api/undo/{listed['batch_id']}")
    assert session.scalar(select(func.count()).select_from(ListItem)) == 3

    q(session, user, 'where from:bob type:video | sort likes asc | save view "Robot videos"')
    assert session.scalar(select(SavedView.query).where(SavedView.name == "Robot videos")) == "from:bob type:video sort:likes-asc"
    assert "filter and a sort only" in err(session, user, 'where from:bob | head 2 | save view "x"').message


# --- Label runs -------------------------------------------------------------
def test_run_namespace_leaves_the_real_tree_alone(session, user, lib, client):
    real = tag(session, user, "Real", 100)
    session.add(ItemTag(item_id=lib["cook"][0], tag_id=real.id, role="primary"))
    session.commit()
    run = runs.start(session, user.id, "trial", "label-by-label", budget_usd=1.0)
    assert run.namespace == f"run-{run.id}"
    for author, path in (("ada", "AI/RAG"), ("bob", "Robots")):
        preview = q(session, user, f'where from:{author} | jev "about retrieval or robots?" | tag "{path}" role primary', run_id=run.id)
        if preview["confirm"]:
            q(session, user, f'where from:{author} | jev "about retrieval or robots?" | tag "{path}" role primary', run_id=run.id, confirm=preview["confirm"])
    # the run sees its own tree; `main` and the app's views do not see it
    assert q(session, user, "where is:untagged", run_id=run.id)["n"] == N_COOK
    assert q(session, user, "where #AI/RAG", run_id=run.id)["n"] == N_RAG
    assert q(session, user, "where is:untagged")["n"] == lib["all"] - 1 and "No tag named #AI/RAG" in err(session, user, "where #AI/RAG").message
    assert client.get("/api/stats").json()["untagged"] == lib["all"] - 1 and client.get("/api/stats").json()["tags"] == 1
    assert client.get(f"/api/items/{lib['rag'][0]}").json()["tags"] == []
    status = runs.status(session, user.id, run)
    assert status["coverage"] == pytest.approx(36 / 40) and status["tags"] == 3 and status["untagged"] == N_COOK
    assert status["spend_usd"] == pytest.approx(0.036) and status["budget_usd"] == 1.0
    assert f"run={run.id} $0.0360/$1.00" in q(session, user, "all | count", run_id=run.id)["text"]
    # a run's own budget is a hard stop
    run.params = {**run.params, "budget_usd": 0.0361}
    session.commit()
    assert "would pass run" in err(session, user, 'all | jev "about cooking?"', run_id=run.id).message
    other = runs.start(session, user.id, "second", "groups-first")
    table = runs.compare(session, user.id, [run.id, other.id])
    assert "coverage: 0.9 | 0.0" in table and "strategy: label-by-label | groups-first" in table
    # promote: preview, then the tree arrives in main as proposed, agent-attached
    preview = runs.promote(session, user.id, run)
    assert not preview["applied"] and preview["new_tags"] == 3 and preview["tag_rows"] == 36
    assert session.scalar(select(func.count()).select_from(Tag).where(Tag.namespace == "main")) == 1
    done = runs.promote(session, user.id, run, preview["confirm"])
    assert done["applied"] and done["new"] == 36
    promoted = session.scalar(select(Tag).where(Tag.namespace == "main", Tag.name == "RAG"))
    assert promoted.status == "proposed" and q(session, user, "where primary:#AI/RAG tagged-by:ai")["n"] == N_RAG
    closed = runs.close(session, user.id, run)
    assert closed["status"] == "finished" and session.get(type(run), run.id).coverage == pytest.approx(0.9)
    assert "No running label run" in err(session, user, "all | count", run_id=run.id).message


# --- Routes -----------------------------------------------------------------
def test_query_route(client, lib):
    body = client.post("/api/query", json={"q": "where from:bob | count by author"}).json()
    assert body["n"] == N_ROBOT and body["text"].startswith(f"n={N_ROBOT} spent=$0.0000") and body["stages"][0]["out"] == N_ROBOT
    bad = client.post("/api/query", json={"q": "all | haed 3"})
    assert bad.status_code == 422 and bad.json()["detail"]["position"] == 6
    preview = client.post("/api/query", json={"q": 'where from:ada | tag "T"'}).json()
    applied = client.post("/api/query", json={"q": 'where from:ada | tag "T"', "confirm": preview["confirm"]}).json()
    assert applied["batch_id"] and client.post(f"/api/undo/{applied['batch_id']}").status_code == 204


def test_jev_check_route(client, lib, fake):
    body = {"q": "from:ada,bob", "question": "Is this about robots?", "threshold": 0.8}
    dry = client.post("/api/jev/check", json={**body, "dry_run": True}).json()
    assert dry["n"] == N_RAG + N_ROBOT and dry["estimate_usd"] > 0 and fake.asked == 0
    done = client.post("/api/jev/check", json=body).json()
    assert done["kept"] == N_ROBOT and done["spent_usd"] == pytest.approx(0.036) and fake.asked == 36
    page = client.get("/api/items", params={"q": "from:ada,bob", "snapshot_id": done["snapshot_id"]}).json()
    assert page["total"] == N_ROBOT and {i["id"] for i in page["items"]} == set(lib["robot"]) and page["items"][0]["score"] == 0.95
    assert client.post("/api/jev/check", json={**body, "dry_run": True}).json()["estimate_usd"] == 0  # all cached now
    assert client.post("/api/jev/check", json={**body, "q": "frm:x"}).status_code == 422


# --- add_item and MCP -------------------------------------------------------
def test_page_fetch_rules():
    assert not add_item_module.public_https("http://example.com/")  # https only
    for private in ("https://127.0.0.1/", "https://localhost/", "https://10.0.0.5/x", "https://169.254.169.254/latest", "https://[::1]/"):
        assert not add_item_module.public_https(private), private
    assert add_item_module.fetch_page_meta("https://127.0.0.1:8000/api/health") == {}
    page = '<html><head><title>Plain &amp; simple</title><meta content="About pgvector" name="description"><meta property="og:title" content="OG title"></head>'
    assert add_item_module._meta(page, "og:title") == "OG title" and add_item_module._meta(page, "description") == "About pgvector"
    assert add_item_module._first(r"<title[^>]*>(.*?)</title>", page) == "Plain & simple"


def rpc(client, method, params=None, **headers):
    response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
                           headers={"Accept": "application/json, text/event-stream", **headers})
    return response


def call(client, tool, **arguments):
    result = rpc(client, "tools/call", {"name": tool, "arguments": arguments}).json()["result"]
    assert "structuredContent" not in result  # the text is not sent twice
    return result["content"][0]["text"]


def test_mcp_smoke(user, lib, session, monkeypatch):
    monkeypatch.setattr(add_item_module, "fetch_page_meta", lambda url: {"title": "A page about servos", "description": "Torque tables"})
    with TestClient(app, base_url="http://127.0.0.1:8000") as http:
        assert rpc(http, "tools/list").status_code == 401
        assert rpc(http, "tools/list", Authorization="Bearer wrong").status_code == 401
        http.headers["Authorization"] = f"Bearer {API_TOKEN}"
        tools = {t["name"]: t for t in rpc(http, "tools/list").json()["result"]["tools"]}
        assert set(tools) == {"query", "tags", "edit_item", "add_item", "jobs", "runs"}
        assert 'jev "about RAG / retrieval-augmented generation?" >= 0.9 | tag "AI/RAG"' in tools["query"]["description"]

        assert call(http, "query", pipeline="where from:bob | count by author").startswith(f"n={N_ROBOT} spent=$0.0000")
        assert call(http, "query", pipeline="all | haed 2").startswith('ERROR at character 6: Unknown stage "haed"')
        preview = call(http, "query", pipeline='where from:ada | tag "AI/RAG"')
        token = preview.split('confirm="')[1].split('"')[0]
        assert "new=30" in call(http, "query", pipeline='where from:ada | tag "AI/RAG"', confirm=token)

        tree = call(http, "tags", operations=[{"op": "describe", "path": "AI/RAG", "description": "Retrieval augmented generation"},
                                              {"op": "create", "path": "AI/Agents", "description": "Coding agents"},
                                              {"op": "approve", "path": "AI"}, {"op": "rename", "path": "AI/Agents", "name": "Coding agents"},
                                              {"op": "move", "path": "AI/Coding agents", "parent": ""},
                                              {"op": "merge", "path": "Coding agents", "into": "AI/RAG"}, {"op": "delete", "path": "Nope"}])
        assert "created AI/Agents (proposed)" in tree and "merged Coding agents into AI/RAG" in tree and "FAILED delete Nope" in tree
        assert "AI  30\n  RAG  30 [proposed] — Retrieval augmented generation" in tree

        added = call(http, "add_item", url="https://example.com/servos", note="for the arm", tags=["Robots"])
        assert added.startswith("added item ") and "A page about servos" in added
        item_id = int(added.split()[2].rstrip(":"))
        assert call(http, "add_item", url="https://example.com/servos").startswith(f"already held item {item_id}")
        assert call(http, "add_item").startswith("ERROR: Give a url or some text")
        assert "changed summary, starred, primary_tag" in call(http, "edit_item", id=item_id, summary="Servo torque tables", starred=True, primary_tag="Robots")
        assert f"{item_id} | web | Servo torque tables | Robots*" in call(http, "query", pipeline="where source:web is:starred torque | show id source summary tags")

        assert "saved=41" in call(http, "jobs") and "no_embedding=1" in call(http, "jobs", action="status")
        assert "own_text=" in call(http, "jobs", action="summarize")
        started = call(http, "runs", action="start", name="trial", strategy="groups")
        run_id = int(started.split()[0].split("=")[1])
        assert "No tags in namespace run-" in call(http, "tags", run=run_id)
        assert "coverage=0.0" in call(http, "runs", action="status", run=run_id) and "closed:" in call(http, "runs", action="close", run=run_id)
        assert "PREVIEW" in call(http, "runs", action="promote", run=run_id) and f"run={run_id} trial" in call(http, "runs", action="list")
    web = session.get(Item, item_id)
    assert (web.source, web.title, web.note, web.summary_method, web.url) == ("web", "A page about servos", "for the arm", "agent", "https://example.com/servos")
