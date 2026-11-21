"""API tokens, this month's spend, and server-side sync."""
import secrets
import threading
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from kosha.auth import current_user, hash_token, session_user
from kosha.db import SessionLocal, get_session
from kosha.models import ApiToken, SpendLedger, SyncState, User
from kosha.prefs import load_settings
from kosha.sync import x_api

router = APIRouter(tags=["account"])

TOKEN_PREFIX = "kosha_"
TOKEN_BYTES = 32
# Provider in `spend_ledger` -> the user setting that caps its month.
SPEND_CAPS = {"x": "x_api_monthly_cap_usd", "jev": "jev_monthly_budget_usd", "llm": "llm_monthly_cap_usd"}
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
    `{month, total_usd, providers: [{provider, usd, calls}], caps: [{name, setting, cap_usd, spent_usd}]}`.
    The `llm` cap covers every LLM and speech-to-text call, whichever provider served it."""
    start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month = (SpendLedger.user_id == user.id, SpendLedger.created_at >= start)
    rows = session.execute(select(SpendLedger.provider, func.sum(SpendLedger.amount_usd), func.count())
                           .where(*month).group_by(SpendLedger.provider).order_by(SpendLedger.provider)).all()
    by_provider = {provider: float(usd) for provider, usd, _ in rows}
    llm_usd = float(session.scalar(select(func.coalesce(func.sum(SpendLedger.amount_usd), 0)).where(
        *month, SpendLedger.meta["via"].as_string() == "llm")))
    settings = load_settings(session, user.id)
    return {
        "month": start.strftime("%Y-%m"),
        "total_usd": round(sum(by_provider.values()), 6),
        "providers": [{"provider": provider, "usd": round(float(usd), 6), "calls": calls} for provider, usd, calls in rows],
        "caps": [{"name": name, "setting": key, "cap_usd": settings[key],
                  "spent_usd": round(llm_usd if name == "llm" else by_provider.get(name, 0.0), 6)}
                 for name, key in SPEND_CAPS.items()],
    }


_sync_threads: dict[tuple[int, str], threading.Thread] = {}  # one running sync per user and source


@router.get("/sync")
def sync_status(user: User = Depends(current_user), session: Session = Depends(get_session)) -> list[dict]:
    """The last run of each sync, from `sync_state`: `[{source, state, updated_at, running}]`."""
    return [{"source": row.source, "state": row.state, "updated_at": row.updated_at,
             "running": (thread := _sync_threads.get((user.id, row.source))) is not None and thread.is_alive()}
            for row in session.scalars(select(SyncState).where(SyncState.user_id == user.id).order_by(SyncState.source))]


@router.post("/sync")
def start_sync(body: SyncIn, user: User = Depends(current_user), session: Session = Depends(get_session)) -> dict:
    """Start a sync in the background. `x_api` is the official X API bookmark
    sync: it answers `disabled` or `not_configured` without calling X when
    the setting is off or the token is missing, and stops at the monthly cap.
    (Extension captures arrive on their own through `POST /api/captures`.)"""
    if body.source not in SYNC_SOURCES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"source must be one of: {', '.join(SYNC_SOURCES)}")
    if not load_settings(session, user.id)["x_api_sync_enabled"]:
        return {"source": body.source, "status": "disabled"}
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
