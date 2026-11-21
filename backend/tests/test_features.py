"""LLM layer, Ask, summaries, media text, tokens, spend, sync, list edits and
the LinkedIn page parser. Every paid API is a fake."""
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from kosha import ask as ask_module
from kosha import jev, llm, media_text, summaries
from kosha.api import account
from kosha.ingest import ingest_envelope
from kosha.models import ApiToken, Item, ItemLink, ItemMedia, List, Setting, SpendLedger, SyncState
from kosha.parsers import parse_capture
from kosha.parsers.linkedin import posted_at_from_label, split_raw_text
from kosha.search.index import reindex
from kosha.search.query import QueryError


# --- Fakes ------------------------------------------------------------------
def reply(content=None, pipelines=(), prompt_tokens=100, completion_tokens=20):
    """A LiteLLM-shaped chat response: text, or one `query` tool call per pipeline."""
    calls = [SimpleNamespace(id=f"call_{n}", function=SimpleNamespace(name="query", arguments=json.dumps({"pipeline": p})))
             for n, p in enumerate(pipelines)]
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=calls or None), finish_reason="stop")],
                           usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                                                 total_tokens=prompt_tokens + completion_tokens))


@pytest.fixture
def fake_llm(monkeypatch):
    """Replaces LiteLLM: `script` holds the replies in order (a callable gets
    the request's keyword arguments); every request is kept in `calls`."""
    state = SimpleNamespace(script=[], calls=[])

    def completion(**kwargs):
        state.calls.append(kwargs)
        nxt = state.script.pop(0)
        return nxt(kwargs) if callable(nxt) else nxt

    monkeypatch.setattr(llm.litellm, "completion", completion)
    monkeypatch.setattr(llm.litellm, "completion_cost", lambda completion_response: 0.001)
    return state


def add_item(session, user, source_id, body, **fields):
    item = Item(user_id=user.id, source="x", source_id=source_id, body=body, author_handle="ada", content_types=["text"], **fields)
    session.add(item)
    session.flush()
    reindex(session, [item.id])
    session.commit()
    return item


# --- LLM layer --------------------------------------------------------------
def test_llm_records_spend_and_stops_at_the_cap(session, user, fake_llm):
    fake_llm.script = [reply("hi")]
    response = llm.complete(session, user.id, "test", "openai/some-model", [{"role": "user", "content": "x"}])
    assert response.cost_usd == 0.001 and fake_llm.calls[0]["model"] == "openai/some-model"
    row = session.scalar(select(SpendLedger))
    assert (row.provider, row.operation, row.units, row.amount_usd) == ("openai", "test", 120, Decimal("0.001"))
    assert row.meta["input_tokens"] == 100 and row.meta["via"] == "llm"

    session.merge(Setting(user_id=user.id, key="llm_monthly_cap_usd", value=0.001))
    session.commit()
    with pytest.raises(llm.BudgetError):
        llm.complete(session, user.id, "test", "openai/some-model", [])
    assert len(fake_llm.calls) == 1  # refused before the provider was called


def test_llm_wraps_provider_errors(session, user, monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("rate limited")
    monkeypatch.setattr(llm.litellm, "completion", boom)
    with pytest.raises(llm.LLMError, match="rate limited"):
        llm.complete(session, user.id, "test", "openai/some-model", [])
    assert session.scalar(select(func.count()).select_from(SpendLedger)) == 0


# --- Ask --------------------------------------------------------------------
def test_ask_is_read_only():
    ask_module.check_read_only('where from:a | hybrid "x" top 5 | jev "Is it?" >= 0.8 | count by author | show id')
    for pipeline in ('all | tag "AI"', "where is:unread | star", "all | score_labels apply", 'all | list "x"', "all | archive"):
        with pytest.raises(QueryError, match="read-only"):
            ask_module.check_read_only(pipeline)


def test_ask_loop_runs_pipelines_and_returns_cited_items(session, user, fake_llm):
    first = add_item(session, user, "1", "pgvector HNSW tuning notes")
    second = add_item(session, user, "2", "sourdough starter schedule")
    fake_llm.script = [
        reply(pipelines=["all | star"]),  # a write: refused, nothing changes
        reply(pipelines=["where pgvector | show id author summary:80", "where nosuchkey:1"]),
        reply(f"The tuning notes are in [{first.id}], see also [{first.id}, {second.id}] and [999999]."),
    ]
    steps_seen = []
    result = ask_module.ask(session, user.id, "that pgvector thing", on_step=steps_seen.append)
    assert result["items"] == [first.id, second.id]  # cited order, once each, unknown ids dropped
    assert [s["pipeline"] for s in result["steps"]] == ["all | star", "where pgvector | show id author summary:80", "where nosuchkey:1"]
    assert result["steps"][0]["note"].startswith("error:") and "read-only" in result["steps"][0]["note"]
    assert result["steps"][1]["n"] == 1 and result["steps"][2]["note"].startswith("error:")
    assert steps_seen == result["steps"]
    assert result["spent"] == {"llm_usd": 0.003, "jev_usd": 0.0} and result["tokens"] == {"input": 300, "output": 60}
    assert not session.scalar(select(Item.starred).where(Item.id == first.id))
    # Bookmark text reaches the model only inside the data delimiters, after the rule that it is untrusted.
    system = fake_llm.calls[0]["messages"][0]["content"]
    assert "untrusted" in system and ask_module.DATA_OPEN in system
    tool_results = [m["content"] for m in fake_llm.calls[2]["messages"] if m["role"] == "tool"]
    assert tool_results[1].startswith(ask_module.DATA_OPEN) and tool_results[1].rstrip().endswith(ask_module.DATA_CLOSE)
    assert tool_results[0].startswith("ERROR") and tool_results[2].startswith("ERROR")


def test_ask_stops_at_the_step_limit(session, user, fake_llm):
    add_item(session, user, "1", "anything")
    fake_llm.script = [reply(pipelines=["all | count"]), reply(pipelines=["all | count", "all | count"]),
                       lambda kwargs: reply("Done." if kwargs["tool_choice"] == "none" else None, pipelines=() if kwargs["tool_choice"] == "none" else ["all | count"])]
    result = ask_module.ask(session, user.id, "count", max_steps=2)
    assert len(result["steps"]) == 2 and result["answer"] == "Done."
    assert fake_llm.calls[-1]["tool_choice"] == "none"  # out of calls: the model must answer


def test_ask_jev_allowance(session, user):
    token = jev.call_allowance.set(0.001)
    try:
        with pytest.raises(jev.BudgetError, match="this question"):
            jev.check_budget(session, user.id, 0.002)
        jev.check_budget(session, user.id, 0.0005)
    finally:
        jev.call_allowance.reset(token)
    jev.check_budget(session, user.id, 0.002)  # no allowance outside Ask


def test_ask_routes(client, session, user, fake_llm):
    item = add_item(session, user, "1", "pgvector HNSW tuning notes")
    fake_llm.script = [reply(pipelines=["where pgvector | show id"]), reply(f"See [{item.id}].")]
    body = client.post("/api/ask", json={"question": "pgvector?"}).json()
    assert body["items"] == [item.id] and body["answer"] == f"See [{item.id}]." and len(body["steps"]) == 1 and body["took_ms"] >= 0
    assert client.post("/api/ask", json={"question": "x", "max_steps": 99}).status_code == 422

    fake_llm.script = [reply(pipelines=["where pgvector | show id"]), reply(f"See [{item.id}].")]
    with client.stream("POST", "/api/ask/stream", json={"question": "pgvector?"}) as response:
        events = [line for line in response.iter_lines() if line.startswith("event:")]
        assert response.headers["content-type"].startswith("text/event-stream")
    assert events == ["event: step", "event: done"]


# --- Summaries --------------------------------------------------------------
def test_rule_summary_takes_the_start_of_a_media_extract(session, user):
    item = add_item(session, user, "1", "watch this")
    session.add(ItemMedia(item_id=item.id, source_key="v", type="video", extract_text="Today we tune HNSW.  " * 40,
                          extract_kind="transcript", extract_status="done"))
    long = add_item(session, user, "2", "First sentence. " * 60)
    session.commit()
    counts = summaries.summarize(session, user.id)
    assert counts["rule"] == 2 and counts["changed"] == 2
    session.refresh(item)
    assert item.summary.startswith("watch this\nTranscript: Today we tune HNSW. Today") and item.summary_method == "rule"
    assert len(item.summary) <= len("watch this\nTranscript: ") + summaries.MEDIA_LEAD_MAX_CHARS
    session.refresh(long)
    assert long.summary.startswith("First sentence.") and "Transcript" not in long.summary
    # Only the items named are touched, and a summary someone wrote is left alone.
    item.summary, item.summary_method = "mine", "human"
    session.commit()
    assert summaries.summarize(session, user.id, [item.id])["changed"] == 0


def test_llm_summaries_batches_and_keeps_only_known_ids(session, user, fake_llm):
    texts = {n: f"text {n}" for n in range(1, summaries.LLM_BATCH_ITEMS + 3)}
    fake_llm.script = [
        reply(json.dumps({"summaries": [{"id": 1, "summary": " One. "}, {"id": 2, "summary": None}, {"id": 777, "summary": "stray"}]})),
        reply("not json"),
    ]
    out, spent = summaries.llm_summaries(session, user.id, texts)
    assert out == {1: "One."} and spent == 0.002 and len(fake_llm.calls) == 2
    sent = fake_llm.calls[0]["messages"]
    assert "untrusted" in sent[0]["content"] and sent[1]["content"].startswith("<items>")


# --- Media text -------------------------------------------------------------
@pytest.fixture
def media_item(session, user):
    item = add_item(session, user, "1", "demo")
    video = ItemMedia(item_id=item.id, source_key="v", type="video", position=0, duration_ms=120_000,
                      original_url="https://video.twimg.com/big.mp4", preview_url="https://pbs.twimg.com/thumb.jpg",
                      variants=[{"url": "https://video.twimg.com/pl.m3u8", "content_type": "application/x-mpegURL"},
                                {"url": "https://video.twimg.com/big.mp4", "bitrate": 2_000_000, "content_type": "video/mp4"},
                                {"url": "https://video.twimg.com/small.mp4", "bitrate": 400_000, "content_type": "video/mp4"}])
    photo = ItemMedia(item_id=item.id, source_key="p", type="photo", position=1, original_url="https://pbs.twimg.com/media/a.jpg")
    evil = ItemMedia(item_id=item.id, source_key="e", type="photo", position=2, original_url="https://evil.example/a.jpg")
    session.add_all([video, photo, evil])
    session.commit()
    return item, video, photo, evil


@pytest.fixture
def fake_media(monkeypatch, fake_llm):
    """No network, no ffmpeg: downloads write a small file, audio is the file itself."""
    fetched, transcripts = [], []

    def download(client, url, dest, max_bytes):
        fetched.append(url)
        Path(dest).write_bytes(b"data")
        return 4

    def transcribe(session, user_id, path, model, minutes, prompt=None):
        transcripts.append((model, minutes, prompt))
        return {"text": "We tune HNSW for pgvector.", "lang": "en", "segments": None, "usd": 0.009}

    monkeypatch.setattr(media_text, "_download", download)
    monkeypatch.setattr(media_text, "_audio_pieces", lambda video, folder: [video])
    monkeypatch.setattr(media_text.llm, "transcribe", transcribe)
    return SimpleNamespace(fetched=fetched, transcripts=transcripts, llm=fake_llm)


def test_media_text_estimate_and_extract(session, user, media_item, fake_media):
    item, video, photo, evil = media_item
    rows = media_text.pending(session, user.id, [item.id])
    assert media_text.estimate(session, user.id, rows) == {
        "n_media": 3, "minutes": 2.0, "images": 2, "estimate_usd": round(2 * 0.0045 + 2 * media_text.IMAGE_ESTIMATE_USD, 4)}
    assert media_text.pending(session, user.id) == [] and len(media_text.pending(session, user.id, everything=True)) == 3
    before = item.content_hash

    fake_media.llm.script = [reply(json.dumps({"description": "A latency chart.", "text": "p99 12ms"}))]
    media_text.start(session, user.id, rows, background=False)
    for row in (video, photo, evil, item):
        session.refresh(row)
    assert fake_media.fetched == ["https://video.twimg.com/small.mp4", "https://pbs.twimg.com/media/a.jpg"]  # smallest mp4; allow-listed hosts only
    assert (video.extract_status, video.extract_kind, video.extract_format, video.extract_lang) == ("done", "transcript", "plain", "en")
    assert fake_media.transcripts == [("openai/gpt-transcribe", 2.0, "demo")]
    assert photo.extract_text == "A latency chart.\nText in image: p99 12ms" and photo.extract_kind == "description"
    assert evil.extract_status == "failed" and evil.extract_text is None
    # Search sees it: summary, content hash and the text-search column were refreshed.
    assert item.content_hash != before and "Transcript: We tune HNSW" in item.summary
    assert session.scalar(select(func.count()).select_from(Item).where(Item.tsv_extra.op("@@")(func.to_tsquery("english", "hnsw & p99")))) == 1
    status = media_text.status(session, user.id)
    assert status["counts"] == {"none": 0, "queued": 0, "done": 2, "failed": 1} and not status["running"]
    assert status["job"]["done"] == 2 and "allowed media host" in status["job"]["errors"][str(evil.id)]
    assert [m.id for m in media_text.pending(session, user.id, [item.id])] == [evil.id]  # a failure can be tried again


def test_media_text_silent_video_gets_its_preview_described(session, user, media_item, fake_media, monkeypatch):
    item, video, _, _ = media_item

    def silent(video_path, folder):
        raise media_text.ExtractError(media_text.SILENT)
    monkeypatch.setattr(media_text, "_audio_pieces", silent)
    fake_media.llm.script = [reply(json.dumps({"description": "A game screenshot.", "text": ""}))]
    spent, reason = media_text.extract_one(session, user.id, video)
    assert reason is None and spent == 0.001 and video.extract_kind == "description" and video.extract_text == "A game screenshot."
    assert fake_media.fetched[-1] == "https://pbs.twimg.com/thumb.jpg"


def test_media_text_routes(client, session, user, media_item, fake_media, monkeypatch):
    item, video, photo, evil = media_item
    assert client.post("/api/media-text/estimate", json={}).status_code == 422
    estimate = client.post("/api/media-text/estimate", json={"item_ids": [item.id]}).json()
    assert (estimate["n_media"], estimate["minutes"], estimate["images"]) == (3, 2.0, 2)
    assert client.post("/api/media-text/estimate", json={"all": True}).json() == estimate
    started = []
    monkeypatch.setattr(media_text, "start", lambda session, user_id, rows, background=True: started.append(len(rows)) or {"queued": len(rows)})
    body = client.post("/api/media-text/start", json={"item_ids": [item.id]})  # the `m` key: one item
    assert body.status_code == 202 and body.json()["queued"] == 3 and started == [3]
    status = client.get("/api/media-text/status").json()
    assert status["counts"]["none"] == 3 and status["running"] is False and isinstance(status["ffmpeg"], bool)
    session.merge(Setting(user_id=user.id, key="llm_monthly_cap_usd", value=0.001))
    session.commit()
    assert client.post("/api/media-text/start", json={"item_ids": [item.id]}).status_code == 402


def test_audio_pieces_without_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setattr(media_text, "ffmpeg_path", lambda: None)
    small = tmp_path / "a.mp4"
    small.write_bytes(b"x" * 10)
    assert media_text._audio_pieces(small, tmp_path) == [small]  # small enough to send as it is
    monkeypatch.setattr(media_text, "STT_UPLOAD_MAX_BYTES", 5)
    with pytest.raises(media_text.ExtractError, match="ffmpeg is not installed"):
        media_text._audio_pieces(small, tmp_path)


# --- Tokens, spend, sync, lists ---------------------------------------------
def test_tokens(client, session, user):
    created = client.post("/api/tokens", json={"name": "laptop"})
    assert created.status_code == 201
    secret = created.json()["token"]
    assert secret.startswith(account.TOKEN_PREFIX) and len(secret) > 40
    listed = client.get("/api/tokens").json()
    assert [t["name"] for t in listed] == ["env", "laptop"] and all("token" not in t and "token_hash" not in t for t in listed)
    assert listed[0]["last_used_at"] is not None and listed[1]["last_used_at"] is None
    # The new token signs in; once deleted it does not.
    assert client.get("/api/health", headers={"Authorization": f"Bearer {secret}"}).status_code == 200
    assert client.delete(f"/api/tokens/{created.json()['id']}").status_code == 204
    assert client.get("/api/health", headers={"Authorization": f"Bearer {secret}"}).status_code == 401
    assert client.delete("/api/tokens/99999").status_code == 404
    assert session.scalar(select(func.count()).select_from(ApiToken)) == 1


def test_spend(client, session, user):
    session.add_all([
        SpendLedger(user_id=user.id, provider="jev", operation="jev", amount_usd=Decimal("0.02")),
        SpendLedger(user_id=user.id, provider="openai", operation="ask", amount_usd=Decimal("0.05"), meta={"via": "llm"}),
        SpendLedger(user_id=user.id, provider="openai", operation="ask", amount_usd=Decimal("0.01"), meta={"via": "llm"}),
        SpendLedger(user_id=user.id, provider="voyage", operation="embed", amount_usd=Decimal("0.30")),
        SpendLedger(user_id=user.id, provider="x", operation="old", amount_usd=Decimal("9"), created_at=datetime(2020, 1, 1, tzinfo=UTC)),
    ])
    session.commit()
    body = client.get("/api/spend").json()
    assert body["month"] == datetime.now(UTC).strftime("%Y-%m") and body["total_usd"] == 0.38
    assert body["providers"] == [{"provider": "jev", "usd": 0.02, "calls": 1}, {"provider": "openai", "usd": 0.06, "calls": 2},
                                 {"provider": "voyage", "usd": 0.3, "calls": 1}]
    assert {c["name"]: (c["cap_usd"], c["spent_usd"]) for c in body["caps"]} == {"x": (3.0, 0.0), "jev": (5.0, 0.02), "llm": (5.0, 0.06)}


def test_sync_routes(client, session, user, monkeypatch):
    ran = []
    monkeypatch.setattr(account.x_api, "sync_bookmarks", lambda own, user_id: ran.append(user_id))
    assert client.post("/api/sync", json={"source": "linkedin"}).status_code == 422
    assert client.post("/api/sync", json={"source": "x_api"}).json() == {"source": "x_api", "status": "disabled"}
    assert not ran  # off by default: X is never called
    assert client.get("/api/sync").json() == []

    session.merge(Setting(user_id=user.id, key="x_api_sync_enabled", value=True))
    session.add(SyncState(user_id=user.id, source="x_api", state={"status": "end", "pages": 1}))
    session.commit()
    assert client.post("/api/sync", json={"source": "x_api"}).json()["status"] == "started"
    account._sync_threads[(user.id, "x_api")].join(timeout=5)
    assert ran == [user.id]
    state = client.get("/api/sync").json()
    assert state[0]["source"] == "x_api" and state[0]["state"]["status"] == "end" and state[0]["running"] is False


def test_list_rename_and_delete(client, session, user):
    item = add_item(session, user, "1", "kept")
    list_id = client.post("/api/lists", json={"name": "Read", "description": "soon"}).json()["id"]
    client.post(f"/api/lists/{list_id}/items", json={"item_ids": [item.id]})
    renamed = client.patch(f"/api/lists/{list_id}", json={"name": "Read later"}).json()
    assert (renamed["name"], renamed["description"], renamed["count"]) == ("Read later", "soon", 1)
    assert client.patch(f"/api/lists/{list_id}", json={"description": None}).json()["description"] is None
    assert client.patch(f"/api/lists/{list_id}", json={"name": ""}).status_code == 422
    assert client.delete(f"/api/lists/{list_id}").status_code == 204
    assert client.get(f"/api/lists/{list_id}").status_code == 404 and client.patch("/api/lists/99999", json={"name": "x"}).status_code == 404
    assert session.scalar(select(func.count()).select_from(List)) == 0 and session.get(Item, item.id) is not None


# --- LinkedIn saved-posts page ----------------------------------------------
def li_post(n, label, raw_head, body, **extra):
    urn = f"urn:li:activity:{n}"
    return {"urn": urn, "url": f"https://www.linkedin.com/feed/update/{urn}", "author_name": "Cy Example",
            "author_url": "https://www.linkedin.com/in/cy-example", "author_headline": "• 2nd", "text": body.replace("\n", ""),
            "posted_label": label, "media": [], "embedded_text": "",
            "raw_text": f"{raw_head}\n\n{label} \n\n{body}\n…see more", **extra}


def test_linkedin_saved_posts_page(session, user):
    # Activity ids carry their creation time: these are 2026-08-25 and 2024-11-20.
    newer, older = 7498113692484616193, 7264855279034716160
    posts = [
        li_post(older, "2yr •", "Status is reachable\nCy Example \n• 2nd\nBuilding search at Example Co",
                "Line one.\n\nLine two https://lnkd.in/abc",
                embedded_text="GitHub - example/pgqueue: a Postgres job queue\n\ngithub.com",
                media=[{"type": "video", "url": None, "poster": "https://media.licdn.com/dms/image/sync/v2/X/articleshare-shrink_800/B/0/1?e=1&t=a"}]),
        li_post(newer, "3mo •", "Example Labs\n12K followers", "A photo post",
                media=[{"type": "video", "url": None, "poster": "https://media.licdn.com/dms/image/v2/L/company-logo_100_100/c/0/1?e=1&t=a"},
                       {"type": "video", "url": None, "poster": "https://media.licdn.com/dms/image/v2/P/feedshare-shrink_480/B/0/1?e=1&t=a"}]),
        li_post(newer + 1, "3mo •", "Cy Example \n• 1st\nTeacher", "Slides attached", embedded_text="Interview guide.pdf", text=None,
                media=[{"type": "video", "url": None, "poster": "https://media.licdn.com/dms/image/v2/D/feedshare-document-cover-images_480/B/0/1?e=1&t=a"}]),
    ]
    meta = {"captured_at": "2026-11-21T12:00:00.000Z"}
    result = parse_capture("linkedin", "saved_posts", {"posts": posts}, meta)
    link_post, photo_post, doc_post = result.items

    assert link_post.raw["author_headline"] == "Building search at Example Co"  # not the "• 2nd" the page field held
    assert link_post.fields["body"] == "Line one.\n\nLine two https://lnkd.in/abc"  # line breaks kept, preview text cut off
    assert link_post.content_types == ["text", "link"] and link_post.media == []
    assert link_post.links == [{"url": "https://github.com/example/pgqueue", "domain": "github.com", "position": 0,
                                "title": "GitHub - example/pgqueue: a Postgres job queue",
                                "image_url": posts[0]["media"][0]["poster"]}]
    assert link_post.fields["posted_at"].date().isoformat() == "2024-11-20"

    assert photo_post.raw["author_headline"] == "12K followers" and photo_post.fields["author_avatar"].endswith("t=a")
    assert photo_post.content_types == ["text", "image"]
    assert [(m["type"], m["original_url"] == m["preview_url"], "?" in m["source_key"]) for m in photo_post.media] == [("photo", True, False)]

    assert doc_post.fields["body"] == "Slides attached"  # `text` was missing: read from the card
    assert doc_post.content_types == ["text", "document"]
    assert doc_post.media[0]["alt_text"] == "Interview guide.pdf" and doc_post.media[0]["original_url"] is None

    # Capture order is save order, newest first; the save-time estimate never rises going down the list.
    assert link_post.saved_order > photo_post.saved_order > doc_post.saved_order
    assert link_post.saved_at_est == doc_post.fields["posted_at"] and photo_post.saved_at_est == doc_post.saved_at_est

    capture = ingest_envelope(session, user.id, {"source": "linkedin", "kind": "saved_posts", "payload": {"posts": posts}, "meta": meta})
    session.commit()
    assert (capture.parse_error, capture.items_new) == (None, 3)
    assert session.scalar(select(ItemLink.domain)) == "github.com"
    again = ingest_envelope(session, user.id, {"source": "linkedin", "kind": "saved_posts", "payload": {"posts": posts}, "meta": meta})
    assert (again.items_new, again.items_updated, session.scalar(select(func.count()).select_from(ItemMedia))) == (0, 0, 2)


def test_linkedin_label_and_card_text():
    captured = datetime(2026, 11, 21, tzinfo=UTC)
    assert posted_at_from_label("4d •", captured) == datetime(2026, 11, 17, tzinfo=UTC)
    assert posted_at_from_label("Reposted from Example • 1yr • ", captured).year == 2025
    assert posted_at_from_label(None, captured) is None and posted_at_from_label("soon", captured) is None
    header, label, body = split_raw_text("Cy \n• 3rd+\nWriter\n\nReposted from Di • 2w • \n\nShort post\n\nA title\n\nexample.com", "A title\n\nexample.com")
    assert (header, label, body) == (["Cy", "• 3rd+", "Writer"], "Reposted from Di • 2w •", "Short post")
    assert split_raw_text("no label here", "") == ([], None, "")
    # A saved link: no author, no text; the preview gives the title and the label the time.
    link = {"urn": "urn:li:article:7697998244987965663", "url": "https://www.linkedin.com/feed/update/urn:li:article:7697998244987965663",
            "author_name": None, "author_url": None, "author_headline": "Saved link • 7yr •", "text": None, "posted_label": None, "media": [],
            "embedded_text": "X-Ray Classification\n\nexample.org", "raw_text": "Saved link • 7yr • \n\nX-Ray Classification\n\nexample.org"}
    saved = parse_capture("linkedin", "saved_posts", {"posts": [link]}, {"captured_at": "2026-11-21T00:00:00Z"}).items[0]
    assert (saved.fields["title"], saved.fields["body"], saved.content_types) == ("X-Ray Classification", None, ["link"])
    assert saved.fields["posted_at"].year == 2019 and saved.raw["author_headline"] is None
    assert saved.links[0]["url"] == "https://example.org/"
    # A post without a time in its id takes it from the label.
    post = {"urn": None, "url": "https://www.linkedin.com/posts/x", "author_name": "Cy", "text": "Hi", "posted_label": "1w •",
            "raw_text": "Cy\n• 2nd\nWriter\n\n1w • \n\nHi", "media": [], "embedded_text": ""}
    item = parse_capture("linkedin", "saved_posts", {"posts": [post]}, {"captured_at": "2026-11-21T00:00:00Z"}).items[0]
    assert item.fields["posted_at"] == datetime(2026, 11, 14, tzinfo=UTC)
