"""Engine and session factory (sync SQLAlchemy 2 on psycopg 3)."""
from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from kosha.config import STATEMENT_TIMEOUT_MS, get_settings

# Two pools on the same database. `SessionLocal` is for the CLI, migrations
# and background jobs (no time limit: a reindex or an embed run may be long).
# `RequestSession` is for request handlers (REST and MCP): Postgres cancels
# any statement that runs longer than STATEMENT_TIMEOUT_MS.
engine = create_engine(get_settings().database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)
request_engine = create_engine(get_settings().database_url, pool_pre_ping=True,
                               connect_args={"options": f"-c statement_timeout={STATEMENT_TIMEOUT_MS}"})
RequestSession = sessionmaker(request_engine, expire_on_commit=False)

SPEND_LOCK_KEY = 0x4B53  # first key of the per-user advisory lock around "check the budget, then reserve"


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request, committed by the route."""
    with RequestSession() as session:
        yield session


def lock_spend(session: Session, user_id: int) -> None:
    """Serialise this user's budget checks: held until the session's
    transaction ends, so check + reserve cannot interleave with another call's."""
    session.execute(text("SELECT pg_advisory_xact_lock(:key, :user)"), {"key": SPEND_LOCK_KEY, "user": user_id})
