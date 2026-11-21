"""Per-user settings: stored overrides on top of config.DEFAULT_SETTINGS."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from kosha.config import DEFAULT_SETTINGS
from kosha.models import Setting


def load_settings(session: Session, user_id: int) -> dict:
    stored = {s.key: s.value for s in session.scalars(select(Setting).where(Setting.user_id == user_id))}
    return {**DEFAULT_SETTINGS, **{k: v for k, v in stored.items() if k in DEFAULT_SETTINGS}}
