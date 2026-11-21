"""Admin: users, invites, reset links, instance settings and API keys.
Every route needs an admin's session cookie; a bearer token gets 403."""
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from kosha import keys
from kosha.api.items import remove_media_files
from kosha.auth import admin_user, make_link, new_user
from kosha.config import (
    CAP_SETTINGS,
    INVITE_DEFAULT_DAYS,
    INVITE_MAX_DAYS,
    RESET_LINK_HOURS,
    RESET_PATH,
    SIGNUP_MODES,
    SIGNUP_PATH,
)
from kosha.db import get_session
from kosha.models import AuthLink, Item, ItemMedia, User
from kosha.prefs import ADMIN, load_instance, save_instance

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(admin_user)])

# ponytail: storage counts the original files (`item_media.bytes`); previews are not measured.
USERS_SQL = text("""
SELECT u.id, u.email, u.name, u.role, u.disabled, u.created_at, u.last_login_at,
  (SELECT count(*) FROM items i WHERE i.user_id = u.id AND i.is_saved) AS item_count,
  (SELECT coalesce(sum(m.bytes), 0)::bigint FROM item_media m JOIN items i ON i.id = m.item_id
    WHERE i.user_id = u.id AND m.local_path IS NOT NULL) AS storage_bytes
FROM users u WHERE CAST(:id AS bigint) IS NULL OR u.id = :id ORDER BY u.id
""")


class UserIn(BaseModel):
    email: str
    password: str
    name: str | None = Field(None, max_length=200)
    role: Literal["admin", "user"] = "user"


class UserPatch(BaseModel):
    role: Literal["admin", "user"] | None = None
    disabled: bool | None = None
    name: str | None = Field(None, max_length=200)


class InviteIn(BaseModel):
    expires_days: int = Field(INVITE_DEFAULT_DAYS, ge=1, le=INVITE_MAX_DAYS)


class InstancePatch(BaseModel):
    signups: Literal[SIGNUP_MODES] | None = None  # type: ignore[valid-type]
    landing: bool | None = None
    caps: dict[str, float] | None = Field(None, description=f"The most a normal user may set each of: {', '.join(CAP_SETTINGS)}")


class KeyIn(BaseModel):
    name: str
    value: str | None = Field(None, description="The key. Leave out to change only provider/model of `transcription`")
    provider: str | None = None
    model: str | None = None


class KeyName(BaseModel):
    name: str


def _user_row(session: Session, user_id: int) -> dict:
    return dict(session.execute(USERS_SQL, {"id": user_id}).one()._mapping)


def _target(session: Session, user_id: int) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return user


def _keep_an_admin(session: Session, user: User) -> None:
    """Refuse a change that would leave the instance with no admin who can sign in."""
    others = session.scalar(select(func.count()).select_from(User).where(User.role == ADMIN, ~User.disabled, User.id != user.id))
    if user.role == ADMIN and not user.disabled and not others:
        raise HTTPException(status.HTTP_409_CONFLICT, "This is the last admin; make someone else an admin first")


def _base(request: Request) -> str:
    return str(request.base_url).rstrip("/")


# --- Users ------------------------------------------------------------------
@router.get("/users")
def list_users(session: Session = Depends(get_session)) -> list[dict]:
    """`[{id, email, name, role, disabled, created_at, last_login_at, item_count, storage_bytes}]`."""
    return [dict(row._mapping) for row in session.execute(USERS_SQL, {"id": None})]


@router.post("/users", status_code=status.HTTP_201_CREATED)
def create_user(body: UserIn, session: Session = Depends(get_session)) -> dict:
    """Create an account whatever the sign-up mode. The admin passes the password on."""
    user = new_user(session, body.email, body.password, body.name, body.role)
    session.commit()
    return _user_row(session, user.id)


@router.patch("/users/{user_id}")
def patch_user(user_id: int, body: UserPatch, session: Session = Depends(get_session)) -> dict:
    user = _target(session, user_id)
    if (body.role == "user" or body.disabled) and user.role == ADMIN:
        _keep_an_admin(session, user)
    if body.role is not None:
        user.role = body.role
    if body.disabled is not None:
        user.disabled = body.disabled  # their sessions and bearer tokens stop working at once
    if "name" in body.model_fields_set:
        user.name = (body.name or "").strip() or None
    session.commit()
    return _user_row(session, user.id)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: int, admin: User = Depends(admin_user), session: Session = Depends(get_session)):
    """Delete the account with its library, tags, tokens and stored media files. Cannot be undone.
    Not your own account, so the admin doing this always remains."""
    user = _target(session, user_id)
    if user.id == admin.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "You cannot delete your own account")
    files = [path for row in session.execute(
        select(ItemMedia.local_path, ItemMedia.local_preview_path).join(Item, Item.id == ItemMedia.item_id)
        .where(Item.user_id == user.id)) for path in row if path]
    session.delete(user)  # every owned table goes with it (ON DELETE CASCADE)
    session.commit()
    remove_media_files(files)


@router.post("/users/{user_id}/reset-link")
def reset_link(user_id: int, request: Request, admin: User = Depends(admin_user), session: Session = Depends(get_session)) -> dict:
    """A link that lets this user set a new password once: `{url, expires_at}`.
    There is no mail server, so the admin sends it to them."""
    user = _target(session, user_id)
    token, link = make_link(session, "reset", timedelta(hours=RESET_LINK_HOURS), admin.id, user.id)
    session.commit()
    return {"url": f"{_base(request)}{RESET_PATH}{token}", "expires_at": link.expires_at}


# --- Invites ----------------------------------------------------------------
@router.post("/invites", status_code=status.HTTP_201_CREATED)
def create_invite(body: InviteIn, request: Request, admin: User = Depends(admin_user), session: Session = Depends(get_session)) -> dict:
    """A single-use sign-up link: `{id, url, token, expires_at}`; the token is
    shown this once. While sign-ups are `closed` this switches them to
    `invite`, since an invite could not be used otherwise."""
    token, link = make_link(session, "invite", timedelta(days=body.expires_days), admin.id)
    if load_instance(session)["signups"] == "closed":
        save_instance(session, {"signups": "invite"})
    session.commit()
    return {"id": link.id, "url": f"{_base(request)}{SIGNUP_PATH}{token}", "token": token, "expires_at": link.expires_at}


@router.get("/invites")
def list_invites(session: Session = Depends(get_session)) -> list[dict]:
    """`[{id, created_at, expires_at, used_at, used_by, state}]`, newest first;
    `state` is `open`, `used` or `expired`, `used_by` the email that signed up."""
    now = datetime.now(UTC)
    rows = session.execute(select(AuthLink, User.email).outerjoin(User, User.id == AuthLink.user_id)
                           .where(AuthLink.kind == "invite").order_by(AuthLink.id.desc()))
    return [{"id": link.id, "created_at": link.created_at, "expires_at": link.expires_at, "used_at": link.used_at, "used_by": email,
             "state": "used" if link.used_at else ("expired" if link.expires_at <= now else "open")} for link, email in rows]


@router.delete("/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_invite(invite_id: int, session: Session = Depends(get_session)):
    link = session.get(AuthLink, invite_id)
    if link is None or link.kind != "invite":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invite not found")
    session.delete(link)
    session.commit()


@router.delete("/invites", status_code=status.HTTP_204_NO_CONTENT)
def delete_open_invites(session: Session = Depends(get_session)):
    """Revoke every invite that has not been used."""
    for link in session.scalars(select(AuthLink).where(AuthLink.kind == "invite", AuthLink.used_at.is_(None))):
        session.delete(link)
    session.commit()


# --- Instance ---------------------------------------------------------------
@router.get("/instance")
def get_instance(session: Session = Depends(get_session)) -> dict:
    """`{signups, landing, caps}`."""
    return load_instance(session)


@router.patch("/instance")
def patch_instance(body: InstancePatch, session: Session = Depends(get_session)) -> dict:
    values = body.model_dump(exclude_none=True)
    if "caps" in values:
        unknown = set(values["caps"]) - set(CAP_SETTINGS)
        if unknown or any(v < 0 for v in values["caps"].values()):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"caps takes amounts of 0 or more for: {', '.join(CAP_SETTINGS)}")
        values["caps"] = {**load_instance(session)["caps"], **values["caps"]}
    save_instance(session, values)
    session.commit()
    return load_instance(session)


# --- API keys ---------------------------------------------------------------
def _key_name(name: str) -> str:
    if name.strip().lower() not in keys.KEY_NAMES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"name must be one of: {', '.join(keys.KEY_NAMES)}")
    return name.strip().lower()


@router.get("/keys")
def list_keys() -> list[dict]:
    """`[{name, set, source, last4}]` for voyage, typesafe, openai and
    transcription (which also has `provider` and `model`). `source` is `db`
    (saved here; wins) or `env`. The key itself is never returned."""
    return [keys.status(name) for name in keys.KEY_NAMES]


@router.put("/keys")
def put_key(body: KeyIn, session: Session = Depends(get_session)) -> dict:
    """Save a key (encrypted at rest); it replaces the environment's at once."""
    name = _key_name(body.name)
    if not (body.value or "").strip() and not (name == "transcription" and (body.provider or body.model)):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Give the key as value")
    options = {"provider": body.provider, "model": body.model} if name == "transcription" else {}
    keys.set_key(session, name, body.value, **options)
    return keys.status(name)


@router.delete("/keys")
@router.delete("/keys/{name}")
def delete_key(name: str, session: Session = Depends(get_session)) -> dict:
    """Forget the saved key (`DELETE /admin/keys?name=voyage` or `/admin/keys/voyage`);
    the environment's key, if there is one, is used again."""
    keys.delete_key(session, _key_name(name))
    return keys.status(name)


@router.post("/keys/test")
def test_key(body: KeyName) -> dict:
    """One tiny call with the key: `{ok, message}`."""
    ok, message = keys.check_key(_key_name(body.name))
    return {"ok": ok, "message": message}
