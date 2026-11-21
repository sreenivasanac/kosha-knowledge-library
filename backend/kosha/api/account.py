"""API tokens, this month's spend, server-side sync, and the onboarding checklist."""
import secrets
import threading
from datetime import UTC, datetime

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from kosha import llm
from kosha.auth import current_user, hash_token, session_user
from kosha.config import ONBOARDING_ADMIN_STEPS, ONBOARDING_STATES, ONBOARDING_STEPS, get_settings
from kosha.db import SessionLocal, get_session
from kosha.models import ApiToken, SpendLedger, SyncState, User
from kosha.prefs import ADMIN, load_settings
from kosha.sync import x_api

router = APIRouter(tags=["account"])

TOKEN_PREFIX = "kosha_"
TOKEN_BYTES = 32
# Provider in `spend_ledger` -> the user setting that caps its month.
SPEND_CAPS = {"x": "x_api_monthly_cap_usd", "jev": "jev_monthly_budget_usd", "llm": "llm_monthly_cap_usd",
              "voyage": "voyage_monthly_cap_usd"}
# What each cap's `spent_usd` sums, said in the response so it can be read next to the provider totals.
CAP_COUNTS = {
    "x": "every ledger row of provider x",
    "jev": "every ledger row of provider jev",
    "llm": "the app's own LLM and speech-to-text calls (ledger rows with meta.via = llm, written by kosha/llm.py), "
           "whichever provider served them",
    "voyage": "every ledger row of provider voyage (embeddings and reranking)",
}
SPEND_NOTE = ("A cap's spent_usd is what the app checks before a call. A provider's usd is everything in the ledger for it; "
              "capped_usd is the part some cap counts. The rest (calls that scripts made directly, such as docs/experiments runs) "
              "is under no cap and listed in uncapped. Each user has their own spend and caps; on a shared server a normal "
              "user's cap is never above the maximum its admin set.")
SYNC_SOURCES = (x_api.SYNC_SOURCE,)  # what `POST /api/sync` can run on the server


class TokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class SyncIn(BaseModel):
    source: str


def _token_out(token: ApiToken) -> dict:
    return {"id": token.id, "name": token.name, "created_at": token.created_at, "last_used_at": token.last_used_at}


@router.get("/tokens")
def list_tokens(user: User = Depends(session_user), session: Session = Depends(get_session)) -> list[dict]:
    """Names and dates only; a token's secret is shown once, when it is created."""
    return [_token_out(t) for t in session.scalars(select(ApiToken).where(ApiToken.user_id == user.id).order_by(ApiToken.id))]


@router.post("/tokens", status_code=status.HTTP_201_CREATED)
def create_token(body: TokenIn, user: User = Depends(session_user), session: Session = Depends(get_session)) -> dict:
    """Create a bearer token. `token` in the answer is the only time the secret is shown.
    The `/tokens` routes need the web app's session cookie: a bearer token gets 403."""
    secret = TOKEN_PREFIX + secrets.token_urlsafe(TOKEN_BYTES)
    token = ApiToken(user_id=user.id, name=body.name.strip(), token_hash=hash_token(secret))
    session.add(token)
    session.commit()
    return {**_token_out(token), "token": secret}


@router.delete("/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_token(token_id: int, user: User = Depends(session_user), session: Session = Depends(get_session)):
    token = session.get(ApiToken, token_id)
    if token is None or token.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Token not found")
    session.delete(token)
    session.commit()


@router.get("/spend")
def spend(user: User = Depends(current_user), session: Session = Depends(get_session)) -> dict:
    """This calendar month's paid calls by provider, with the caps:
    `{month, total_usd, providers: [{provider, usd, capped_usd, calls}], caps: [{name, setting, cap_usd, spent_usd, counts}],
    uncapped: [{provider, operation, usd, calls}], note}`.
    The `llm` cap covers the app's own LLM and speech-to-text calls (the ones `kosha/llm.py` makes and checks),
    whichever provider served them. A provider's `usd` can be larger than any cap's `spent_usd`: `capped_usd`
    is the part a cap counts, and `uncapped` lists the rest by operation."""
    start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # The cap a row counts against: `llm` for kosha/llm.py's rows, else the provider's own cap, else none.
    cap = case((SpendLedger.meta["via"].as_string() == llm.LEDGER_VIA, "llm"),
               (SpendLedger.provider.in_([name for name in SPEND_CAPS if name != "llm"]), SpendLedger.provider), else_=None)
    rows = session.execute(select(SpendLedger.provider, SpendLedger.operation, cap, func.sum(SpendLedger.amount_usd), func.count())
                           .where(SpendLedger.user_id == user.id, SpendLedger.created_at >= start)
                           .group_by(SpendLedger.provider, SpendLedger.operation, cap)
                           .order_by(SpendLedger.provider, SpendLedger.operation)).all()
    providers: dict[str, dict] = {}
    spent = dict.fromkeys(SPEND_CAPS, 0.0)
    uncapped = []
    for provider, operation, cap_name, usd, calls in rows:
        entry = providers.setdefault(provider, {"provider": provider, "usd": 0.0, "capped_usd": 0.0, "calls": 0})
        entry["usd"] += float(usd)
        entry["calls"] += calls
        if cap_name:
            entry["capped_usd"] += float(usd)
            spent[cap_name] += float(usd)
        else:
            uncapped.append({"provider": provider, "operation": operation, "usd": round(float(usd), 6), "calls": calls})
    settings = load_settings(session, user.id)
    return {
        "month": start.strftime("%Y-%m"),
        "total_usd": round(sum(p["usd"] for p in providers.values()), 6),
        "providers": [{**p, "usd": round(p["usd"], 6), "capped_usd": round(p["capped_usd"], 6)} for p in providers.values()],
        "caps": [{"name": name, "setting": key, "cap_usd": settings[key], "spent_usd": round(spent[name], 6),
                  "counts": CAP_COUNTS[name]} for name, key in SPEND_CAPS.items()],
        "uncapped": uncapped,
        "note": SPEND_NOTE,
    }


_sync_threads: dict[tuple[int, str], threading.Thread] = {}  # one running sync per user and source


@router.get("/sync")
def sync_status(user: User = Depends(current_user), session: Session = Depends(get_session)) -> list[dict]:
    """The last run of each sync source (SYNC_SOURCES), from `sync_state`: `[{source, state, updated_at, running}]`.
    Other jobs keep their state in the same table (`media_text`) and have their own status route."""
    return [{"source": row.source, "state": row.state, "updated_at": row.updated_at,
             "running": (thread := _sync_threads.get((user.id, row.source))) is not None and thread.is_alive()}
            for row in session.scalars(select(SyncState).where(SyncState.user_id == user.id, SyncState.source.in_(SYNC_SOURCES))
                                       .order_by(SyncState.source))]


@router.post("/sync")
def start_sync(body: SyncIn, user: User = Depends(current_user), session: Session = Depends(get_session)) -> dict:
    """Start a sync in the background. `x_api` is the official X API bookmark
    sync: it answers `{status: "disabled" | "not_configured", reason}` without
    starting anything when the setting is off or the server has no X API
    credentials, and stops at the monthly cap.
    (Extension captures arrive on their own through `POST /api/captures`.)"""
    if body.source not in SYNC_SOURCES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"source must be one of: {', '.join(SYNC_SOURCES)}")
    if not load_settings(session, user.id)["x_api_sync_enabled"]:
        return {"source": body.source, "status": "disabled", "reason": "The setting x_api_sync_enabled is off."}
    if user.role != ADMIN:
        return {"source": body.source, "status": "not_configured",
                "reason": "The X API credentials of this server belong to its admin. Capture your bookmarks with the browser extension."}
    env = get_settings()
    missing = [name for name, value in (("KOSHA_X_ACCESS_TOKEN", env.kosha_x_access_token), ("KOSHA_X_USER_ID", env.kosha_x_user_id)) if not value]
    if missing:
        return {"source": body.source, "status": "not_configured",
                "reason": f"No X API credentials on the server: set {' and '.join(missing)} in .env and restart."}
    key, user_id = (user.id, body.source), user.id
    running = _sync_threads.get(key)
    if running is not None and running.is_alive():
        return {"source": body.source, "status": "running"}

    def work():
        with SessionLocal() as own:
            x_api.sync_bookmarks(own, user_id)  # writes its summary to sync_state

    _sync_threads[key] = threading.Thread(target=work, name="kosha-sync", daemon=True)
    _sync_threads[key].start()
    return {"source": body.source, "status": "started"}


# --- Onboarding -------------------------------------------------------------
class OnboardingPatch(BaseModel):
    step: Literal[ONBOARDING_STEPS]  # type: ignore[valid-type]
    state: Literal[ONBOARDING_STATES]  # type: ignore[valid-type]


def _onboarding(user: User) -> dict:
    """`{completed, steps}`; a step is `todo` until the user finishes or skips
    it. The steps that set up the instance (API keys, storage) are an admin's:
    a normal user sees them as `not_applicable`."""
    stored = {"account": "done", **(user.onboarding or {})}  # whoever is signed in has an account
    return {"completed": user.onboarding_completed,
            "steps": {step: "not_applicable" if step in ONBOARDING_ADMIN_STEPS and user.role != ADMIN else stored.get(step, "todo")
                      for step in ONBOARDING_STEPS}}


@router.get("/onboarding")
def get_onboarding(user: User = Depends(current_user)) -> dict:
    """`{completed, steps: {account, sources, ai, media, look}}`, each `done`, `skipped`, `todo` or `not_applicable`."""
    return _onboarding(user)


@router.patch("/onboarding")
def patch_onboarding(body: OnboardingPatch, user: User = Depends(current_user), session: Session = Depends(get_session)) -> dict:
    if body.step in ONBOARDING_ADMIN_STEPS and user.role != ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"The {body.step} step is the admin's")
    user.onboarding = {**(user.onboarding or {}), body.step: body.state}
    session.commit()
    return _onboarding(user)


@router.post("/onboarding/complete")
def complete_onboarding(user: User = Depends(current_user), session: Session = Depends(get_session)) -> dict:
    """The user is through (or closed it): the web app stops opening it."""
    user.onboarding_completed = True
    session.commit()
    return _onboarding(user)
