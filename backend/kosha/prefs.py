"""Settings. Per user: stored overrides on top of config.DEFAULT_SETTINGS.
Per instance (set by an admin): the sign-up mode, the landing page switch and
the maximum of each spending cap."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from kosha.config import CAP_SETTINGS, DEFAULT_SETTINGS, DEFAULT_SIGNUP_MODE, get_settings
from kosha.models import InstanceSetting, Setting, User

ADMIN = "admin"


def load_instance(session: Session) -> dict:
    """`{signups, landing, caps}`. `caps` holds, for each setting in
    CAP_SETTINGS, the most a normal user may set it to; the default is that
    setting's own default, so a new user can spend what a default install could."""
    stored = {row.key: row.value for row in session.scalars(
        select(InstanceSetting).where(InstanceSetting.key.in_(("signups", "landing", "caps"))))}
    return {"signups": stored.get("signups", DEFAULT_SIGNUP_MODE),
            "landing": stored.get("landing", get_settings().kosha_landing),
            "caps": {**{key: DEFAULT_SETTINGS[key] for key in CAP_SETTINGS}, **(stored.get("caps") or {})}}


def save_instance(session: Session, values: dict) -> None:
    for key, value in values.items():
        session.merge(InstanceSetting(key=key, value=value))


def load_settings(session: Session, user_id: int) -> dict:
    """The user's settings. For anyone but an admin the spending caps are held
    at or under the instance's maximum, whatever the user stored: the paid
    calls run on the instance's keys, so the admin has the last word."""
    stored = {s.key: s.value for s in session.scalars(select(Setting).where(Setting.user_id == user_id))}
    settings = {**DEFAULT_SETTINGS, **{k: v for k, v in stored.items() if k in DEFAULT_SETTINGS}}
    user = session.get(User, user_id)
    if user is not None and user.role != ADMIN:
        for key, most in load_instance(session)["caps"].items():
            settings[key] = min(settings[key], most)
    return settings
