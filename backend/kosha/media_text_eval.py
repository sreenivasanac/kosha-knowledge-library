"""Compare speech-to-text providers on the same real clips from the library:

    uv run python -m kosha.media_text_eval --n 20

Picks `n` short videos that the speech pre-filter says have speech (the same
ones every run: the order is seeded), transcribes each with the DeepInfra
default and with the OpenAI fallback, and prints word-level agreement between
the two, cost and latency. A provider without a key is left out, and results
are kept in a cache file, so the half that could run is not paid for twice
when the other key arrives. Nothing is written to the library except one
`spend_ledger` row per provider (operation `transcribe_eval`, under no cap).
"""
import argparse
import difflib
import json
import random
import re
import tempfile
import time
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from kosha import llm
from kosha import media_text as mt
from kosha.config import REPO_ROOT
from kosha.db import SessionLocal
from kosha.models import Item, ItemMedia, SpendLedger

# --- Tunables ---------------------------------------------------------------
MODELS = (llm.STT_DEFAULT_MODEL, llm.STT_FALLBACK_MODEL)
CLIP_MIN_S, CLIP_MAX_S = 30, 150  # short clips keep the bill small and still have enough words to compare
MIN_SPEECH_S = 10.0  # a clip counts as speech with at least this much of it
SEED = 20261121
DEFAULT_MAX_USD = 0.30
CACHE = REPO_ROOT / "data" / "media_text_eval.json"


def words(text: str) -> list[str]:
    """Lower-case words without punctuation; Chinese and Japanese, written without spaces, count by character."""
    return re.findall(r"[\u3040-\u30ff\u4e00-\u9fff]|[^\W\u3040-\u30ff\u4e00-\u9fff]+(?:'[^\W\u3040-\u30ff\u4e00-\u9fff]+)*", text.lower())


def agreement(a: str, b: str) -> float:
    """Share of words the two transcripts have in common, in order (1.0 = the same words)."""
    wa, wb = words(a), words(b)
    if not wa and not wb:
        return 1.0
    matched = sum(block.size for block in difflib.SequenceMatcher(None, wa, wb, autojunk=False).get_matching_blocks())
    return matched / max(len(wa), len(wb))


def pick_clips(session, n: int, folder: Path) -> list[dict]:
    """The first `n` videos, in a fixed random order, that download and have speech: [{id, minutes, audio}]."""
    rows = session.scalars(select(ItemMedia).where(
        ItemMedia.type == "video", ItemMedia.duration_ms.between(CLIP_MIN_S * 1000, CLIP_MAX_S * 1000)).order_by(ItemMedia.id)).all()
    random.Random(SEED).shuffle(rows)
    clips = []
    for media in rows:
        if len(clips) == n:
            break
        work = folder / str(media.id)
        work.mkdir()
        try:
            pieces = mt._audio_pieces(mt._fetch(session, media, work, mt.VIDEO_MAX_BYTES), work)
        except mt.ExtractError:
            continue
        if len(pieces) == 1 and (mt.speech_seconds(pieces[0]) or 0) >= MIN_SPEECH_S:
            clips.append({"id": media.id, "item_id": media.item_id, "minutes": media.duration_ms / 60000, "audio": pieces[0]})
    return clips


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--n", type=int, default=20, help="number of clips")
    parser.add_argument("--max-usd", type=float, default=DEFAULT_MAX_USD, help="stop before spending more than this")
    parser.add_argument("--cache", type=Path, default=CACHE, help="results kept between runs")
    args = parser.parse_args()

    cache = json.loads(args.cache.read_text()) if args.cache.is_file() else {}
    models = []
    for model in MODELS:
        try:
            llm._api_key(model, speech=True)
            models.append(model)
        except llm.NoKeyError:
            print(f"- {model}: no key set, left out (its cached results, if any, are still used)")
    with SessionLocal() as session, tempfile.TemporaryDirectory(prefix="kosha-stt-eval-") as folder:
        clips = pick_clips(session, args.n, Path(folder))
        minutes = sum(c["minutes"] for c in clips)
        print(f"{len(clips)} speech clips, {minutes:.1f} minutes")
        todo = {m: [c for c in clips if f"{m}|{c['id']}" not in cache] for m in models}
        cost = sum((llm.stt_price_per_minute(m) or 0) * c["minutes"] for m, cs in todo.items() for c in cs)
        if cost > args.max_usd:
            raise SystemExit(f"This run would cost about ${cost:.3f}, over --max-usd {args.max_usd}. Nothing was sent.")
        for model, model_clips in todo.items():
            spent = 0.0
            for clip in model_clips:
                started = time.perf_counter()
                try:
                    result = llm.stt_call(model, clip["audio"])
                except Exception as exc:  # one failed clip must not lose the rest
                    print(f"  {model} failed on media {clip['id']}: {type(exc).__name__}: {str(exc)[:120]}")
                    continue
                usd = (llm.stt_price_per_minute(model) or 0) * clip["minutes"]
                spent += usd
                cache[f"{model}|{clip['id']}"] = {"text": result["text"], "lang": result["lang"], "usd": usd,
                                                  "seconds": round(time.perf_counter() - started, 2), "minutes": clip["minutes"]}
                args.cache.parent.mkdir(parents=True, exist_ok=True)
                args.cache.write_text(json.dumps(cache, ensure_ascii=False, indent=1))
            if spent:
                owner = session.scalar(select(Item.user_id).where(Item.id == clips[0]["item_id"]))
                session.add(SpendLedger(user_id=owner, provider=llm.provider_of(model), operation="transcribe_eval",
                                        units=len(model_clips), amount_usd=Decimal(f"{spent:.6f}"), meta={"model": model}))
                session.commit()

    print(f"\n{'model':<44}{'clips':>6}{'minutes':>9}{'usd':>9}{'$/hour':>8}{'s/clip':>8}{'x realtime':>11}")
    for model in MODELS:
        got = [cache[k] for c in clips if (k := f"{model}|{c['id']}") in cache]
        if got:
            mins, usd, secs = sum(g["minutes"] for g in got), sum(g["usd"] for g in got), sum(g["seconds"] for g in got)
            print(f"{model:<44}{len(got):>6}{mins:>9.1f}{usd:>9.4f}{usd / mins * 60:>8.3f}{secs / len(got):>8.1f}{mins * 60 / secs:>11.0f}")
    pairs = [(cache[a], cache[b]) for c in clips
             if (a := f"{MODELS[0]}|{c['id']}") in cache and (b := f"{MODELS[1]}|{c['id']}") in cache]
    if pairs:
        scores = sorted(agreement(a["text"], b["text"]) for a, b in pairs)
        print(f"\nWord-level agreement between the two on {len(pairs)} clips: mean {sum(scores) / len(scores):.1%}, "
              f"median {scores[len(scores) // 2]:.1%}, lowest {scores[0]:.1%}")
    else:
        print("\nAgreement needs both providers' results on the same clips; set the missing key and run this again.")


if __name__ == "__main__":
    assert agreement("Hello, world. It's fine!", "hello world its fine") == 0.75 and agreement("a b c", "A, b; c.") == 1.0
    main()
