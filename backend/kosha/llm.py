"""Every LLM and speech-to-text call goes through here: LiteLLM does the
provider call, this module adds the monthly cap and the `spend_ledger` row.

Model ids are user settings (`llm_small_model`, `llm_ask_model`, `stt_model`
in config.DEFAULT_SETTINGS), written the LiteLLM way: "provider/model".
"""
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import litellm
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from kosha.config import get_settings
from kosha.models import SpendLedger
from kosha.prefs import load_settings

# --- Tunables ---------------------------------------------------------------
TIMEOUT_S = 120
# Speech-to-text is billed by the minute, and LiteLLM's price map has no entry
# for these: https://developers.openai.com/api/docs/pricing (read 21 Nov 2026).
STT_PRICE_PER_MINUTE_USD = {"openai/gpt-transcribe": 0.0045}
LEDGER_VIA = "llm"  # meta.via on every row this module writes; the monthly cap sums these

litellm.suppress_debug_info = True


class LLMError(RuntimeError):
    """The provider call failed."""


class BudgetError(LLMError):
    """The month's LLM cap is used up. Nothing was spent."""


def provider_of(model: str) -> str:
    return model.split("/", 1)[0] if "/" in model else "openai"


def _api_key(model: str) -> str | None:
    """The key from `.env` for OpenAI models; LiteLLM finds other providers' keys in the environment."""
    return (get_settings().openai_api_key or None) if provider_of(model) == "openai" else None


def month_spend(session: Session, user_id: int) -> float:
    start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return float(session.scalar(select(func.coalesce(func.sum(SpendLedger.amount_usd), 0)).where(
        SpendLedger.user_id == user_id, SpendLedger.meta["via"].as_string() == LEDGER_VIA, SpendLedger.created_at >= start)))


def check_cap(session: Session, user_id: int, estimate_usd: float = 0.0) -> None:
    cap = load_settings(session, user_id)["llm_monthly_cap_usd"]
    spent = month_spend(session, user_id)
    if spent + estimate_usd >= cap:
        raise BudgetError(f"The monthly LLM cap is reached (${spent:.2f} of ${cap:.2f}; setting llm_monthly_cap_usd)")


def _record(session: Session, user_id: int, model: str, operation: str, units: int, usd: float, **meta) -> None:
    session.add(SpendLedger(user_id=user_id, provider=provider_of(model), operation=operation, units=units,
                            amount_usd=Decimal(f"{usd:.6f}"), meta={"model": model, "via": LEDGER_VIA, **meta}))
    session.commit()  # the call is paid for: keep the row whatever the caller does next


def complete(session: Session, user_id: int, operation: str, model: str, messages: list, **kwargs):
    """One chat completion (tools, images and JSON output go through `kwargs`
    in the OpenAI format). Returns LiteLLM's response; `response.cost_usd` is
    what the call cost. Tests replace `litellm.completion`."""
    check_cap(session, user_id)
    try:
        response = litellm.completion(model=model, messages=messages, api_key=_api_key(model), timeout=TIMEOUT_S, **kwargs)
    except Exception as exc:  # LiteLLM raises one class per provider error
        raise LLMError(f"{model}: {type(exc).__name__}: {str(exc)[:300]}") from None
    usage = response.usage
    try:
        cost = float(litellm.completion_cost(completion_response=response))
        priced = True
    except Exception:  # a model LiteLLM has no price for: recorded at $0 and flagged
        cost, priced = 0.0, False
    response.cost_usd = cost
    _record(session, user_id, model, operation, usage.total_tokens, cost, unit="tokens",
            input_tokens=usage.prompt_tokens, output_tokens=usage.completion_tokens, **({} if priced else {"unpriced": True}))
    return response


def transcribe(session: Session, user_id: int, path: Path, model: str, minutes: float, prompt: str | None = None) -> dict:
    """Speech to text for one audio file. Returns {text, lang, segments, usd};
    `segments` is [{start, end, text}] when the model gives timestamps, else
    None. `prompt` is context for the recording (names and terms to expect)."""
    usd = STT_PRICE_PER_MINUTE_USD.get(model, 0.0) * minutes
    check_cap(session, user_id, usd)
    try:
        with path.open("rb") as audio:
            response = litellm.transcription(model=model, file=audio, api_key=_api_key(model), timeout=TIMEOUT_S * 5,
                                             **({"prompt": prompt} if prompt else {}))
    except Exception as exc:
        raise LLMError(f"{model}: {type(exc).__name__}: {str(exc)[:300]}") from None
    data = response if isinstance(response, dict) else response.model_dump()
    languages = data.get("languages") or []
    lang = data.get("language") or (languages[0].get("code") if languages and isinstance(languages[0], dict) else None)
    segments = [{"start": s["start"], "end": s["end"], "text": s["text"].strip()} for s in data.get("segments") or []] or None
    _record(session, user_id, model, "transcribe", round(minutes * 60), usd, unit="seconds",
            **({} if model in STT_PRICE_PER_MINUTE_USD else {"unpriced": True}))
    return {"text": (data.get("text") or "").strip(), "lang": lang, "segments": segments, "usd": usd}
