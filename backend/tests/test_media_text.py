"""Speech-to-text provider choice, the no-key refusal and the speech pre-filter. No network."""
import json
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from kosha import keys, llm, media_text
from kosha.models import Item, ItemMedia, Setting, SpendLedger

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
DEEPINFRA = "deepinfra/openai/whisper-large-v3-turbo"


@pytest.fixture
def no_keys(monkeypatch):
    """An instance with no API key at all, whatever `.env` holds."""
    monkeypatch.setattr(keys, "get_key", lambda name: None)
    monkeypatch.setattr(llm, "transcription_key", lambda: None)


def test_no_key_is_a_clean_503_before_any_call(client, session, user, no_keys, monkeypatch):
    monkeypatch.setattr(llm.litellm, "completion", lambda **kw: pytest.fail("the provider was called without a key"))
    monkeypatch.setattr(llm.litellm, "transcription", lambda **kw: pytest.fail("the provider was called without a key"))
    answer = client.post("/api/ask", json={"question": "What did I save about pgvector?"})
    assert answer.status_code == 503 and llm.NO_KEY in answer.json()["detail"]
    with pytest.raises(llm.NoKeyError):
        llm.transcribe(session, user.id, "nothing.mp3", "openai/gpt-transcribe", 1.0)
    assert session.scalar(select(SpendLedger.id)) is None  # nothing was reserved either
    monkeypatch.setattr(keys, "get_key", lambda name: "")  # an empty key is no key
    assert client.post("/api/ask", json={"question": "What did I save about pgvector?"}).status_code == 503


def test_stt_model_choice(session, user, monkeypatch):
    monkeypatch.setattr(keys, "key_options", lambda name: {"provider": None, "model": None})
    monkeypatch.setattr(llm, "transcription_key", lambda: None)
    assert llm.stt_model(session, user.id) == "openai/gpt-transcribe"  # no DeepInfra key: the OpenAI fallback
    monkeypatch.setattr(llm, "transcription_key", lambda: "di-key")
    assert llm.stt_model(session, user.id) == DEEPINFRA
    monkeypatch.setattr(keys, "key_options", lambda name: {"provider": "groq", "model": "whisper-large-v3"})
    assert llm.stt_model(session, user.id) == "groq/whisper-large-v3"  # the instance's own choice
    session.add(Setting(user_id=user.id, key="stt_model", value="openai/gpt-transcribe"))
    session.commit()
    assert llm.stt_model(session, user.id) == "openai/gpt-transcribe"  # the user's own choice wins
    assert llm.stt_price_per_minute(DEEPINFRA) == 0.0002 and llm.stt_price_per_minute("nobody/unknown") is None


def test_transcribe_through_deepinfra(session, user, tmp_path, monkeypatch):
    calls = []

    def transcription(**kwargs):
        calls.append(kwargs)
        return {"text": " Hello there. ", "language": "en", "segments": [{"start": 0.2, "end": 2.0, "text": " Hello there."}]}

    monkeypatch.setattr(llm, "transcription_key", lambda: "di-key")
    monkeypatch.setattr(llm.litellm, "transcription", transcription)
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    result = llm.transcribe(session, user.id, audio, DEEPINFRA, minutes=30, prompt="names")
    assert result == {"text": "Hello there.", "lang": "en", "segments": [{"start": 0.2, "end": 2.0, "text": "Hello there."}], "usd": 30 * 0.0002}
    sent = calls[0]
    assert (sent["model"], sent["api_base"], sent["api_key"]) == ("openai/openai/whisper-large-v3-turbo", llm.DEEPINFRA_API_BASE, "di-key")
    assert sent["response_format"] == "verbose_json" and sent["prompt"] == "names"
    row = session.scalar(select(SpendLedger))
    assert (row.provider, float(row.amount_usd), row.meta["model"], row.units) == ("deepinfra", 0.006, DEEPINFRA, 1800)


@needs_ffmpeg
def test_speech_seconds(tmp_path):
    tone = tmp_path / "tone.mp3"  # 5 s of a sine tone: sound, but nobody speaking
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=5", str(tone)], check=True)
    assert media_text.speech_seconds(tone) < 0.5 and media_text.has_speech([tone]) is False
    broken = tmp_path / "broken.mp3"
    broken.write_bytes(b"not audio")
    assert media_text.speech_seconds(broken) is None and media_text.has_speech([broken]) is True  # fail open


def test_no_speech_skips_the_paid_call(session, user, tmp_path, monkeypatch):
    item = Item(user_id=user.id, source="x", source_id="1", body="demo")
    session.add(item)
    session.flush()
    video = ItemMedia(item_id=item.id, source_key="v", type="video", duration_ms=60_000,
                      original_url="https://video.twimg.com/a.mp4", preview_url="https://pbs.twimg.com/thumb.jpg")
    session.add(video)
    session.commit()
    monkeypatch.setattr(media_text, "_download", lambda client, url, dest, max_bytes: dest.write_bytes(b"data"))
    monkeypatch.setattr(media_text, "_audio_pieces", lambda source, folder: [source])
    monkeypatch.setattr(media_text, "speech_seconds", lambda piece, enough=None: 0.4)
    monkeypatch.setattr(llm, "transcribe", lambda *a, **k: pytest.fail("a clip without speech was sent for transcription"))
    described = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content=json.dumps({"description": "A screen recording of a terminal.", "text": ""})))], cost_usd=0.0003)
    monkeypatch.setattr(llm, "complete", lambda *a, **k: described)
    state = media_text.start(session, user.id, [video], background=False) and media_text.status(session, user.id)["job"]
    session.refresh(video)
    assert (video.extract_status, video.extract_kind, video.extract_text) == ("done", "description", "A screen recording of a terminal.")
    assert (state["done"], state["no_speech"], state["spent_usd"]) == (1, 1, 0.0003)


def test_each_piece_is_charged_by_its_own_length_and_an_unpriced_model_is_refused(session, user, tmp_path, monkeypatch):
    item = Item(user_id=user.id, source="x", source_id="2", body="a talk")
    session.add(item)
    session.flush()
    video = ItemMedia(item_id=item.id, source_key="v", type="video", duration_ms=21 * 60_000, original_url="https://video.twimg.com/a.mp4")
    session.add(video)
    session.commit()
    pieces = [tmp_path / "audio_000.mp3", tmp_path / "audio_001.mp3"]
    monkeypatch.setattr(media_text, "_download", lambda client, url, dest, max_bytes: dest.write_bytes(b"data"))
    monkeypatch.setattr(media_text, "_audio_pieces", lambda source, folder: pieces)
    monkeypatch.setattr(media_text, "has_speech", lambda pieces: True)
    monkeypatch.setattr(media_text, "probe_duration_ms", lambda path: {pieces[0]: 20 * 60_000, pieces[1]: 60_000}[path])
    minutes = []

    def transcribe(session_, user_id, path, model, length, prompt=None):
        minutes.append(length)
        return {"text": "words", "lang": "en", "segments": None, "usd": 0.0}

    monkeypatch.setattr(llm, "transcribe", transcribe)
    media_text._transcribe(session, user.id, video, tmp_path)
    assert minutes == [20, 1]  # not 10.5 each

    monkeypatch.undo()
    monkeypatch.setattr(llm, "_api_key", lambda model, speech=False: "k")
    monkeypatch.setattr(llm, "stt_call", lambda *a, **k: pytest.fail("a model with no known price was called"))
    with pytest.raises(llm.LLMError, match="no price"):
        llm.transcribe(session, user.id, pieces[0], "nobody/unknown-stt", 1.0)
    assert session.scalar(select(SpendLedger.id)) is None


def test_start_resumes_files_left_queued(session, user, monkeypatch):
    started = []
    monkeypatch.setattr(media_text, "_worker", None)
    monkeypatch.setattr(media_text.threading, "Thread", lambda **kw: SimpleNamespace(start=lambda: started.append(kw["name"]), is_alive=lambda: True))
    assert media_text.start(session, user.id, []) == {"queued": 0} and started == ["kosha-media-text"]  # no new rows, the worker still starts
