"""Every LLM and speech-to-text call goes through here: LiteLLM does the
provider call, this module adds the monthly cap and the `spend_ledger` row.

Model ids are user settings (`llm_small_model`, `llm_ask_model`, `stt_model`
in config.DEFAULT_SETTINGS), written the LiteLLM way: "provider/model".
Speech to text goes through `litellm.transcription`, so any provider LiteLLM
knows can be used; which one is decided in `stt_model`.

A call whose provider has no key is refused here (NoKeyError, a 503 at the
API) before anything is reserved or sent.
"""
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import litellm
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from kosha import keys
from kosha.db import lock_spend
from kosha.models import Setting, SpendLedger
from kosha.prefs import load_settings

# --- Tunables ---------------------------------------------------------------
TIMEOUT_S = 120
# Speech to text is billed by the minute of audio. Prices read 21 Nov 2026:
# https://developers.openai.com/api/docs/pricing and
# https://deepinfra.com/openai/whisper-large-v3-turbo ($0.00020 / minute).
# A model missing here is priced from LiteLLM's own map (`stt_price_per_minute`).
STT_PRICE_PER_MINUTE_USD = {"openai/gpt-transcribe": 0.0045, "deepinfra/openai/whisper-large-v3-turbo": 0.0002}
# The speech-to-text model when the user has not chosen one: Whisper
# large-v3-turbo on DeepInfra when a transcription key is set, else OpenAI.
STT_DEFAULT_MODEL = "deepinfra/openai/whisper-large-v3-turbo"
STT_FALLBACK_MODEL = "openai/gpt-transcribe"
# LiteLLM's own `deepinfra/` transcription route returns the text only (LiteLLM 1.110); sent through its
# OpenAI-compatible route to this address, the same request also returns the language and timed segments.
DEEPINFRA_API_BASE = "https://api.deepinfra.com/v1/openai"
STT_SETTING = "stt_model"  # the user's own choice, when the `settings` row exists
NO_KEY = "no LLM key configured"
# ponytail: a chat completion's price is not known before the call, so this
# much is held against the cap while it runs (Ask's largest call is about
# $0.05); estimate from token counts if calls get much larger or smaller.
COMPLETION_RESERVE_USD = 0.05
LEDGER_VIA = "llm"  # meta.via on every row this module writes; the monthly cap sums these

litellm.suppress_debug_info = True


class LLMError(RuntimeError):
    """The provider call failed."""


class BudgetError(LLMError):
    """The month's LLM cap is used up. Nothing was spent."""


class NoKeyError(LLMError):
    """The model's provider has no API key. Nothing was sent."""


def provider_of(model: str) -> str:
    return model.split("/", 1)[0] if "/" in model else "openai"


def transcription_key() -> str | None:
    """The key for speech to text at a provider other than OpenAI: the
    instance key `transcription` (admin settings or KOSHA_TRANSCRIPTION_API_KEY),
    else DEEPINFRA_API_KEY. An empty value counts as not set."""
    return keys.get_key("transcription")


def _api_key(model: str, speech: bool = False) -> str | None:
    """The key to send with a call to `model`, or None when LiteLLM finds the
    provider's key in the process environment itself. Raises NoKeyError when
    there is none, so the provider is never called without one."""
    provider = provider_of(model)
    key = keys.get_key("openai") if provider == "openai" else (transcription_key() if speech else None)
    if key:
        return key
    if provider != "openai" and litellm.validate_environment(model=model).get("keys_in_environment"):
        return None
    raise NoKeyError(f"{model}: {NO_KEY} for {provider} (add it in the admin settings or the environment)")


def stt_model(session: Session, user_id: int) -> str:
    """The speech-to-text model for this user: their own `stt_model` setting
    when they saved one; else the instance's transcription model (admin
    settings, KOSHA_TRANSCRIPTION_PROVIDER / _MODEL); else STT_DEFAULT_MODEL
    when a transcription key is set, and STT_FALLBACK_MODEL (OpenAI) when not."""
    own = session.scalar(select(Setting.value).where(Setting.user_id == user_id, Setting.key == STT_SETTING))
    if own and own != "auto":
        return str(own)
    if not transcription_key():
        return STT_FALLBACK_MODEL
    options = keys.key_options("transcription")
    provider, model = (options.get("provider") or "").strip().lower(), (options.get("model") or "").strip()
    if not model:
        return STT_DEFAULT_MODEL if provider in ("", "deepinfra") else STT_FALLBACK_MODEL
    return model if not provider or model.startswith(provider + "/") else f"{provider}/{model}"


def stt_price_per_minute(model: str) -> float | None:
    """Dollars per minute of audio; None when neither the table above nor
    LiteLLM's price map knows the model (the call is then recorded as unpriced)."""
    if model in STT_PRICE_PER_MINUTE_USD:
        return STT_PRICE_PER_MINUTE_USD[model]
    for name in (model, model.split("/", 1)[-1]):
        per_second = (litellm.model_cost.get(name) or {}).get("input_cost_per_second")
        if per_second:
            return per_second * 60
    return None


def month_spend(session: Session, user_id: int) -> float:
    start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return float(session.scalar(select(func.coalesce(func.sum(SpendLedger.amount_usd), 0)).where(
        SpendLedger.user_id == user_id, SpendLedger.meta["via"].as_string() == LEDGER_VIA, SpendLedger.created_at >= start)))


def check_cap(session: Session, user_id: int, estimate_usd: float = 0.0) -> None:
    cap = load_settings(session, user_id)["llm_monthly_cap_usd"]
    spent = month_spend(session, user_id)
    if spent + estimate_usd >= cap:
        raise BudgetError(f"The monthly LLM cap is reached (${spent:.2f} of ${cap:.2f}; setting llm_monthly_cap_usd)")


def _reserve(session: Session, user_id: int, model: str, operation: str, usd: float) -> SpendLedger:
    """Check the cap and hold `usd` against it as one step: under the user's
    spend lock a provisional ledger row is written and committed, so calls
    running at the same time see each other's reservations and cannot pass
    the cap together. `_settle` or `_release` must follow."""
    lock_spend(session, user_id)
    check_cap(session, user_id, usd)
    row = SpendLedger(user_id=user_id, provider=provider_of(model), operation=operation, units=0,
                      amount_usd=Decimal(f"{usd:.6f}"), meta={"model": model, "via": LEDGER_VIA, "provisional": True})
    session.add(row)
    session.commit()  # ends the transaction, which releases the lock
    return row


def _settle(session: Session, row: SpendLedger, units: int, usd: float, **meta) -> None:
    """The call is paid for: the reservation becomes the real amount, whatever the caller does next."""
    row.units, row.amount_usd = units, Decimal(f"{usd:.6f}")
    row.meta = {k: v for k, v in {**row.meta, **meta}.items() if k != "provisional"}
    session.commit()


def _release(session: Session, row: SpendLedger) -> None:
    """The provider call failed, so nothing is owed: drop the reservation."""
    session.rollback()
    session.delete(row)
    session.commit()


def complete(session: Session, user_id: int, operation: str, model: str, messages: list, **kwargs):
    """One chat completion (tools, images and JSON output go through `kwargs`
    in the OpenAI format). Returns LiteLLM's response; `response.cost_usd` is
    what the call cost. Tests replace `litellm.completion`."""
    api_key = _api_key(model)  # before the reservation: without a key nothing is held or sent
    row = _reserve(session, user_id, model, operation, COMPLETION_RESERVE_USD)
    try:
        response = litellm.completion(model=model, messages=messages, api_key=api_key, timeout=TIMEOUT_S, **kwargs)
    except Exception as exc:  # LiteLLM raises one class per provider error
        _release(session, row)
        raise LLMError(f"{model}: {type(exc).__name__}: {str(exc)[:300]}") from None
    usage = response.usage
    try:
        cost = float(litellm.completion_cost(completion_response=response))
        priced = True
    except Exception:  # a model LiteLLM has no price for: recorded at $0 and flagged
        cost, priced = 0.0, False
    response.cost_usd = cost
    _settle(session, row, usage.total_tokens, cost, unit="tokens",
            input_tokens=usage.prompt_tokens, output_tokens=usage.completion_tokens, **({} if priced else {"unpriced": True}))
    return response


def stt_call(model: str, path: Path, prompt: str | None = None) -> dict:
    """The provider call itself, with no cap and no ledger row (`transcribe`
    adds those; the evaluation script calls this directly). Returns
    {text, lang, segments}. Raises NoKeyError or the provider's own error."""
    api_key = _api_key(model, speech=True)
    # Whisper models can return segments with times and the language; the gpt-*-transcribe models cannot.
    extra = {"response_format": "verbose_json"} if "whisper" in model else {}
    if provider_of(model) == "deepinfra":
        model, extra = "openai/" + model.split("/", 1)[1], {**extra, "api_base": DEEPINFRA_API_BASE}
    with path.open("rb") as audio:
        response = litellm.transcription(model=model, file=audio, api_key=api_key, timeout=TIMEOUT_S * 5,
                                         **extra, **({"prompt": prompt} if prompt else {}))
    data = response if isinstance(response, dict) else response.model_dump()
    languages = data.get("languages") or []
    lang = data.get("language") or (languages[0].get("code") if languages and isinstance(languages[0], dict) else None)
    segments = [{"start": s["start"], "end": s["end"], "text": s["text"].strip()} for s in data.get("segments") or []] or None
    return {"text": (data.get("text") or "").strip(), "lang": lang, "segments": segments}


def transcribe(session: Session, user_id: int, path: Path, model: str, minutes: float, prompt: str | None = None) -> dict:
    """Speech to text for one audio file. Returns {text, lang, segments, usd};
    `segments` is [{start, end, text}] when the model gives timestamps, else
    None. `prompt` is context for the recording (names and terms to expect).
    `minutes` is the real length of the file: it is what the call is charged
    and capped by, so a caller that does not know it must not call."""
    if minutes <= 0:
        raise LLMError(f"{model}: the length of the recording is unknown, so its cost cannot be checked against the cap")
    _api_key(model, speech=True)  # refuse before anything is reserved
    price = stt_price_per_minute(model)
    usd = (price or 0.0) * minutes
    row = _reserve(session, user_id, model, "transcribe", usd)
    try:
        result = stt_call(model, path, prompt)
    except Exception as exc:
        _release(session, row)
        raise LLMError(f"{model}: {type(exc).__name__}: {str(exc)[:300]}") from None
    _settle(session, row, round(minutes * 60), usd, unit="seconds", **({} if price is not None else {"unpriced": True}))
    return {**result, "usd": usd}
