"""Accounts and authentication: first-run setup, sign-up (open, by invite or
closed), session cookie for the web app, bearer API tokens for the extension
and MCP. Email and password only; a forgotten password is reset through a
link an admin makes (there is no mail server)."""
import hashlib
import re
import secrets
import time
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from kosha.config import (
    APP_NAME,
    APP_VERSION,
    CSRF_HEADER,
    CSRF_HEADER_VALUE,
    DEMO_NOTICE,
    DOCS_URL,
    GITHUB_URL,
    LOGIN_MAX_FAILURES,
    LOGIN_WINDOW_S,
    PASSWORD_MAX_CHARS,
    PASSWORD_MIN_CHARS,
    get_settings,
)
from kosha.db import get_session
from kosha.models import ApiToken, AuthLink, User
from kosha.prefs import ADMIN, load_instance

_hasher = PasswordHasher()
ENV_TOKEN_NAME = "env"
SETUP_LOCK_KEY = 0x4B55  # advisory lock around "no users yet, so create the first"
LINK_TOKEN_BYTES = 32
EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
EMAIL_MAX_CHARS = 254


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def hash_token(token: str) -> str:
    """API tokens are long random strings, so a plain SHA-256 is enough."""
    return hashlib.sha256(token.encode()).hexdigest()


def password_version(user: User) -> str:
    """Kept in the session cookie: a session ends when the password it was
    opened with is changed or reset."""
    return hashlib.sha256(user.password_hash.encode()).hexdigest()[:16]


def sign_in(request: Request, user: User) -> None:
    request.session["uid"], request.session["pv"] = user.id, password_version(user)
    user.last_login_at = datetime.now(UTC)


def normalize_email(email: str) -> str:
    email = email.strip().lower()
    if len(email) > EMAIL_MAX_CHARS or not EMAIL_RE.fullmatch(email):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "That does not look like an email address")
    return email


def checked_password(password: str) -> str:
    """The server-side password rule: PASSWORD_MIN_CHARS characters at least."""
    if not PASSWORD_MIN_CHARS <= len(password) <= PASSWORD_MAX_CHARS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"The password must have {PASSWORD_MIN_CHARS} to {PASSWORD_MAX_CHARS} characters")
    return password


def new_user(session: Session, email: str, password: str, name: str | None, role: str = "user") -> User:
    """Add an account (flushed, not committed). 409 when the email is taken."""
    user = User(email=normalize_email(email), password_hash=hash_password(checked_password(password)),
                name=(name or "").strip() or None, role=role)
    if session.scalar(select(User.id).where(func.lower(User.email) == user.email)) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")
    session.add(user)
    try:
        session.flush()
    except IntegrityError:  # two sign-ups with the same email at once
        session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists") from None
    return user


def make_link(session: Session, kind: str, lifetime: timedelta, created_by: int, user_id: int | None = None) -> tuple[str, AuthLink]:
    """A single-use invite or reset link: (the token, shown once; its row). Flushed, not committed."""
    token = secrets.token_urlsafe(LINK_TOKEN_BYTES)
    link = AuthLink(kind=kind, token_hash=hash_token(token), user_id=user_id, created_by=created_by,
                    expires_at=datetime.now(UTC) + lifetime)
    session.add(link)
    session.flush()
    return token, link


def use_link(session: Session, kind: str, token: str) -> AuthLink | None:
    """Mark the link used and return it, or None when it is unknown, expired or
    already used. One UPDATE, so two requests cannot both use it."""
    link_id = session.scalar(update(AuthLink).where(
        AuthLink.kind == kind, AuthLink.token_hash == hash_token(token or ""), AuthLink.used_at.is_(None),
        AuthLink.expires_at > func.now()).values(used_at=func.now()).returning(AuthLink.id))
    return session.get(AuthLink, link_id) if link_id is not None else None


def create_admin(session: Session) -> User:
    """Create or update the admin from KOSHA_ADMIN_EMAIL/_PASSWORD, and
    register KOSHA_API_TOKEN as that user's bearer token. Safe to re-run.
    An install that sets these never shows the setup screen."""
    settings = get_settings()
    if not settings.kosha_admin_email or not settings.kosha_admin_password:
        raise ValueError("KOSHA_ADMIN_EMAIL and KOSHA_ADMIN_PASSWORD must be set")
    user = session.scalar(select(User).where(User.email == settings.kosha_admin_email))
    if user is None and session.scalar(select(func.count()).select_from(User)) == 1:
        # The admin's email was changed in the environment: same account.
        user = session.scalar(select(User))
        user.email = settings.kosha_admin_email
    if user is None:
        user = User(email=settings.kosha_admin_email, password_hash="")
        session.add(user)
    user.role = ADMIN
    if not user.password_hash or not verify_password(user.password_hash, settings.kosha_admin_password):
        user.password_hash = hash_password(settings.kosha_admin_password)
    session.flush()
    if settings.kosha_api_token:
        token_hash = hash_token(settings.kosha_api_token)
        token = session.scalar(select(ApiToken).where(ApiToken.user_id == user.id, ApiToken.name == ENV_TOKEN_NAME))
        if token is None:
            session.add(ApiToken(user_id=user.id, name=ENV_TOKEN_NAME, token_hash=token_hash))
        else:
            token.token_hash = token_hash
    return user


def bearer_user(session: Session, header: str) -> User | None:
    """The user an `Authorization: Bearer …` header belongs to, or None."""
    if not header.lower().startswith("bearer "):
        return None
    token = session.scalar(select(ApiToken).where(ApiToken.token_hash == hash_token(header[7:].strip())))
    if token is None:
        return None
    user = session.get(User, token.user_id)
    if user is None or user.disabled:
        return None
    token.last_used_at = datetime.now(UTC)
    return user


def current_user(request: Request, session: Session = Depends(get_session)) -> User:
    """Dependency for every protected route: a bearer token if one is sent,
    otherwise the session cookie."""
    user = None
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        user = bearer_user(session, header)
    elif request.session.get("uid"):
        user = session.get(User, request.session["uid"])
        if user is not None and (user.disabled or request.session.get("pv") != password_version(user)):
            user = None  # disabled, or the password changed since this session was opened
        if (user is not None and request.method not in ("GET", "HEAD", "OPTIONS")
                and request.headers.get(CSRF_HEADER) != CSRF_HEADER_VALUE):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Missing header {CSRF_HEADER}: {CSRF_HEADER_VALUE}")
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    return user


def session_user(request: Request, session: Session = Depends(get_session)) -> User:
    """Dependency for routes only the signed-in person may use (API tokens,
    the password, everything under /admin): the session cookie, never a
    bearer token, so a leaked or agent-held token cannot reach them."""
    if request.headers.get("authorization", "").lower().startswith("bearer ") or not request.session.get("uid"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Sign in to the web app for this; a bearer token cannot do it")
    return current_user(request, session)


def admin_user(user: User = Depends(session_user)) -> User:
    if user.role != ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only an admin can do this")
    return user


router = APIRouter(prefix="/auth", tags=["auth"])
public = APIRouter(tags=["auth"])  # no sign-in needed: what the web app asks before it knows who is there


_login_failures: dict[str, deque] = defaultdict(deque)  # client address -> times of failed logins, invites and reset tokens


def _throttle(request: Request) -> deque:
    """The caller's recent failures; 429 once there are LOGIN_MAX_FAILURES
    within LOGIN_WINDOW_S. Append `time.monotonic()` to it on a failure."""
    # ponytail: in-process and keyed by the client address. `kosha serve`
    # trusts X-Forwarded-For from 127.0.0.1 only, so behind a local reverse
    # proxy this is the real client; add `limit_req` in nginx as well (README).
    failures = _login_failures[request.client.host if request.client else "unknown"]
    now = time.monotonic()
    while failures and now - failures[0] > LOGIN_WINDOW_S:
        failures.popleft()
    if len(failures) >= LOGIN_MAX_FAILURES:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many failed attempts; wait a minute")
    return failures


class LoginIn(BaseModel):
    email: str
    password: str


class SetupIn(BaseModel):
    email: str
    password: str
    name: str | None = Field(None, max_length=200)


class SignupIn(SetupIn):
    invite: str | None = None


class PasswordIn(BaseModel):
    current: str
    new: str


class MePatch(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class ResetIn(BaseModel):
    token: str
    password: str


class UserOut(BaseModel):
    id: int
    email: str
    role: str
    name: str | None
    demo: bool  # this instance is the public demo
    onboarding_completed: bool


def user_out(user: User) -> dict:
    return {"id": user.id, "email": user.email, "role": user.role, "name": user.name,
            "demo": get_settings().kosha_demo, "onboarding_completed": user.onboarding_completed}


def _has_users(session: Session) -> bool:
    return session.scalar(select(User.id).limit(1)) is not None


@public.get("/public-config")
def public_config(session: Session = Depends(get_session)) -> dict:
    """What the web app needs before sign-in: whether to show the setup
    screen, the sign-up form, the landing page, or the demo's credentials."""
    env, instance = get_settings(), load_instance(session)
    demo = {"email": env.kosha_demo_email, "password": env.kosha_demo_password, "notice": DEMO_NOTICE} if env.kosha_demo else None
    return {"app_name": APP_NAME, "version": APP_VERSION, "has_users": _has_users(session),
            "signups": "closed" if demo else instance["signups"], "landing": bool(instance["landing"]),
            "demo": demo, "demo_url": env.kosha_demo_url or None, "links": {"github": GITHUB_URL, "docs": DOCS_URL}}


@public.post("/setup", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def setup(body: SetupIn, request: Request, session: Session = Depends(get_session)):
    """First run: create the admin and sign in. Works only while there is no
    account; of two calls at the same moment one gets 409."""
    session.execute(text("SELECT pg_advisory_xact_lock(:key, 0)"), {"key": SETUP_LOCK_KEY})  # held until commit
    if _has_users(session):
        raise HTTPException(status.HTTP_409_CONFLICT, "This server is already set up; sign in instead")
    user = new_user(session, body.email, body.password, body.name, role=ADMIN)
    user.onboarding = {"account": "done"}
    sign_in(request, user)
    session.commit()
    return user_out(user)


@router.post("/signup", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def signup(body: SignupIn, request: Request, session: Session = Depends(get_session)):
    """Create an account and sign in, if the instance allows it: `open`
    takes anyone, `invite` needs an unused invite token from an admin,
    `closed` answers 403."""
    failures = _throttle(request)
    if not _has_users(session):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This server is not set up yet; create the first account on the setup screen")
    mode = load_instance(session)["signups"]
    if mode == "closed":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Sign-ups are closed on this server; ask its admin for an invite")
    link = None
    if mode == "invite":
        link = use_link(session, "invite", body.invite or "")
        if link is None:
            failures.append(time.monotonic())
            raise HTTPException(status.HTTP_403_FORBIDDEN, "This invite is not valid, was already used, or has expired")
    user = new_user(session, body.email, body.password, body.name)  # a refusal here rolls the invite's use back
    user.onboarding = {"account": "done"}
    if link is not None:
        link.user_id = user.id
    sign_in(request, user)
    session.commit()
    return user_out(user)


@router.post("/login", response_model=UserOut)
def login(body: LoginIn, request: Request, session: Session = Depends(get_session)):
    failures = _throttle(request)
    user = session.scalar(select(User).where(func.lower(User.email) == body.email.strip().lower()))
    # An unknown email answers at once, without running the password hash.
    if user is None or not verify_password(user.password_hash, body.password):
        failures.append(time.monotonic())
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong email or password")
    if user.disabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account is disabled; ask the admin of this server")
    sign_in(request, user)
    session.commit()
    return user_out(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request):
    request.session.clear()


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return user_out(user)


@router.patch("/me", response_model=UserOut)
def update_me(body: MePatch, user: User = Depends(session_user), session: Session = Depends(get_session)):
    """Change the signed-in user's display name (cookie sessions only; a demo refuses it: kosha/demo.py)."""
    name = body.name.strip()
    if not name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The name cannot be empty")
    user.name = name
    session.commit()
    return user_out(user)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(body: PasswordIn, request: Request, user: User = Depends(session_user), session: Session = Depends(get_session)):
    """Change the signed-in user's password. Every other session of the account ends."""
    failures = _throttle(request)
    if not verify_password(user.password_hash, body.current):
        failures.append(time.monotonic())
        raise HTTPException(status.HTTP_403_FORBIDDEN, "The current password is wrong")
    user.password_hash = hash_password(checked_password(body.new))
    request.session["pv"] = password_version(user)  # this session goes on
    session.commit()


@router.post("/reset", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(body: ResetIn, request: Request, session: Session = Depends(get_session)):
    """Set a new password with the token of a reset link an admin made
    (`POST /api/admin/users/{id}/reset-link`). The link works once."""
    failures = _throttle(request)
    password = checked_password(body.password)  # before the link is spent
    link = use_link(session, "reset", body.token)
    user = session.get(User, link.user_id) if link is not None else None
    if user is None:
        failures.append(time.monotonic())
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This reset link is not valid, was already used, or has expired")
    user.password_hash = hash_password(password)
    session.commit()
