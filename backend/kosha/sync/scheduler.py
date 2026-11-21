"""In-process daily X API sync, started by `kosha serve`.

A daemon thread wakes every X_SYNC_CHECK_S and runs the sync when the user's
`x_api_sync_enabled` setting is on and the last run is older than
X_SYNC_INTERVAL_S. With the setting off it never touches the network.
"""
import logging
import threading
from datetime import UTC, datetime

from sqlalchemy import select

from kosha.config import X_SYNC_CHECK_S, X_SYNC_INTERVAL_S
from kosha.models import SyncState, User
from kosha.prefs import load_settings
from kosha.sync.x_api import SYNC_SOURCE, sync_bookmarks

log = logging.getLogger(__name__)


def run_due(session, now: datetime | None = None) -> list[dict]:
    """Sync every user whose setting is on and whose last run is old enough."""
    now = now or datetime.now(UTC)
    done = []
    for user_id in session.scalars(select(User.id)).all():
        if not load_settings(session, user_id)["x_api_sync_enabled"]:
            continue
        state = session.get(SyncState, (user_id, SYNC_SOURCE))
        last = (state.state or {}).get("last_run_at") if state else None
        if last and (now - datetime.fromisoformat(last)).total_seconds() < X_SYNC_INTERVAL_S:
            continue
        try:
            done.append(sync_bookmarks(session, user_id))
        except Exception:  # one user's failed sync must not keep the others from theirs
            session.rollback()
            log.exception("X sync failed for user %s", user_id)
    return done


def start_scheduler() -> threading.Thread:
    stop = threading.Event()

    def loop():
        from kosha.db import SessionLocal

        while not stop.wait(X_SYNC_CHECK_S):
            try:
                with SessionLocal() as session:
                    for summary in run_due(session):
                        log.info("X sync: %s", summary)
            except Exception:  # a failed run must not end the schedule
                log.exception("X sync failed")

    thread = threading.Thread(target=loop, name="kosha-x-sync", daemon=True)
    thread.start()
    return thread
