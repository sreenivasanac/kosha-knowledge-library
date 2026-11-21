"""Daily bookmark sync through the official X API.

Pages newest first and stops at the first bookmark already held, after
X_API_STALE_PAGES pages that add nothing, at the end of the list, or when the
month's spend would pass the user's cap. A walk stopped by the cap or a failed
request is continued by the next run from the page it stopped at. Every page is stored as an `api_page`
capture and goes through the same parser and merge as any other capture, and
is recorded in the spend ledger at $0.001 per bookmark returned.
"""
from datetime import UTC, datetime
from decimal import Decimal

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from kosha.config import (
    X_API_BASE,
    X_API_COST_PER_BOOKMARK_USD,
    X_API_PAGE_SIZE,
    X_API_STALE_PAGES,
    get_settings,
)
from kosha.ingest import ingest_envelope, store_capture
from kosha.models import DeletedItem, Item, SpendLedger, SyncState, User
from kosha.prefs import ADMIN, load_settings

PROVIDER = "x"
SYNC_SOURCE = "x_api"
COST = Decimal(str(X_API_COST_PER_BOOKMARK_USD))
RESUME_KEY = "resume_tokens"  # in `sync_state`: pagination tokens of walks that stopped before their end
# Not verified against the live API (this module is only tested with mocked
# HTTP): X's docs show both `tweet.*` and `post.*` spellings. The parser reads
# either spelling in the response.
REQUEST_PARAMS = {
    "max_results": X_API_PAGE_SIZE,
    "expansions": "author_id,attachments.media_keys,referenced_tweets.id,article.cover_media",
    "tweet.fields": "created_at,lang,public_metrics,entities,attachments,referenced_tweets,note_tweet,article,conversation_id,in_reply_to_user_id",
    "media.fields": "url,preview_image_url,variants,duration_ms,width,height,alt_text,type",
    "user.fields": "name,username,profile_image_url,verified",
}


def month_spend(session: Session, user_id: int, now: datetime | None = None) -> Decimal:
    start = (now or datetime.now(UTC)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return session.scalar(select(func.coalesce(func.sum(SpendLedger.amount_usd), 0)).where(
        SpendLedger.user_id == user_id, SpendLedger.provider == PROVIDER, SpendLedger.created_at >= start)) or Decimal(0)


def sync_bookmarks(session: Session, user_id: int, client: httpx.Client | None = None) -> dict:
    """Run one sync. Returns {"status", "pages", "items_new", "spent_usd"}; the
    same summary is written to `sync_state`."""
    prefs, env = load_settings(session, user_id), get_settings()
    if not prefs["x_api_sync_enabled"]:
        return {"status": "disabled", "pages": 0, "items_new": 0, "spent_usd": 0.0}
    # The server's X credentials are one X account: the admin's. Run for anyone
    # else, the sync would copy the admin's bookmarks into that user's library.
    if not env.kosha_x_access_token or not env.kosha_x_user_id or session.get(User, user_id).role != ADMIN:
        return {"status": "not_configured", "pages": 0, "items_new": 0, "spent_usd": 0.0}

    cap = Decimal(str(prefs["x_api_monthly_cap_usd"]))
    spent_before = month_spend(session, user_id)
    spent, pages, items_new, token, status = Decimal(0), 0, 0, None, "end"
    fetched: list[dict] = []  # envelopes, newest page first
    # A walk starts at the top of the list (None) or where an earlier run had
    # to stop (the cap, a failed request). Without the saved tokens, the pages
    # behind such a stop would never be fetched: the next run meets a held
    # bookmark on its first page and stops there.
    saved = session.get(SyncState, (user_id, SYNC_SOURCE))
    waiting: list[str | None] = [None, *((saved.state or {}).get(RESUME_KEY) or [] if saved else [])]
    error: Exception | None = None
    own_client = client is None
    client = client or httpx.Client(timeout=30)
    try:
        while waiting and status != "cap_reached":
            token, stale, status = waiting.pop(0), 0, "end"
            resumed = token is not None
            while True:
                # A page is billed per bookmark returned, so budget for a full one.
                if spent_before + spent + COST * X_API_PAGE_SIZE > cap:
                    status = "cap_reached"
                    break
                response = client.get(
                    f"{X_API_BASE}/users/{env.kosha_x_user_id}/bookmarks",
                    params={**REQUEST_PARAMS, **({"pagination_token": token} if token else {})},
                    headers={"Authorization": f"Bearer {env.kosha_x_access_token}"},
                )
                if resumed and response.status_code == 400:  # X no longer knows the saved token: give it up
                    token = None
                    break
                response.raise_for_status()
                resumed = False
                payload = response.json()
                ids = [post["id"] for post in payload.get("data") or []]
                cost = COST * len(ids)
                pages += 1
                spent += cost
                envelope = {"source": "x", "kind": "api_page", "payload": payload,
                            "meta": {"operation": "api", "captured_at": datetime.now(UTC).isoformat(), "client": "kosha-sync"}}
                session.add(SpendLedger(user_id=user_id, provider=PROVIDER, operation="bookmarks_page",
                                        units=len(ids), amount_usd=cost, meta={"page": pages}))
                store_capture(session, user_id, envelope)
                session.commit()  # the page is paid for: keep it and its ledger row
                fetched.append(envelope)
                token = (payload.get("meta") or {}).get("next_token")
                scope = lambda model: (model.user_id == user_id, model.source == "x", model.source_id.in_(ids))  # noqa: E731
                if ids and session.scalar(select(Item.id).where(*scope(Item), Item.is_saved).limit(1)) is not None:
                    status, token = "reached_known", None
                    break
                removed = set(session.scalars(select(DeletedItem.source_id).where(*scope(DeletedItem)))) if ids else set()
                stale = stale + 1 if not set(ids) - removed else 0
                if stale >= X_API_STALE_PAGES:
                    status, token = "stale", None
                    break
                if not token:
                    break
    except Exception as exc:  # recorded below, with where to resume, then raised again
        error = exc
    finally:
        if own_client:
            client.close()
        # Parse oldest page first, so newer bookmarks get the higher `seq`. In
        # `finally` so pages already paid for are parsed even if a later
        # request failed.
        for envelope in reversed(fetched):
            capture = ingest_envelope(session, user_id, envelope)
            session.commit()
            items_new += capture.items_new
            if capture.parse_error:
                status = "parse_error"
    # Where the walks that did not finish go on next time: the page the cap or the failure stopped at, and the walks not begun.
    resume = [t for t in (token, *waiting) if t]
    if error is not None:
        # A failed run is not a run: `last_run_at` stays, so the scheduler tries again at its next check.
        session.merge(SyncState(user_id=user_id, source=SYNC_SOURCE, state={**((saved.state or {}) if saved else {}), RESUME_KEY: resume}))
        session.commit()
        raise error
    summary = {"status": status, "pages": pages, "items_new": items_new, "spent_usd": float(spent)}
    session.merge(SyncState(user_id=user_id, source=SYNC_SOURCE,
                            state={**summary, "last_run_at": datetime.now(UTC).isoformat(), RESUME_KEY: resume}))
    session.commit()
    return summary
