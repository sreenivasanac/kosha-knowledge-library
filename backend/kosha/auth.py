"""Authentication: one admin account, session cookie for the web app, bearer
API tokens for the extension and MCP."""
import hashlib
from datetime import UTC, datetime

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from kosha.config import get_settings
from kosha.db import get_session
from kosha.models import ApiToken, User

_hasher = PasswordHasher()
ENV_TOKEN_NAME = "env"


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


def create_admin(session: Session) -> User:
    """Create or update the single admin from KOSHA_ADMIN_EMAIL/_PASSWORD, and
    register KOSHA_API_TOKEN as that user's bearer token. Safe to re-run."""
    settings = get_settings()
    if not settings.kosha_admin_email or not settings.kosha_admin_password:
        raise ValueError("KOSHA_ADMIN_EMAIL and KOSHA_ADMIN_PASSWORD must be set")
    user = session.scalar(select(User).where(User.email == settings.kosha_admin_email))
    if user is None:
        user = User(email=settings.kosha_admin_email, password_hash="")
        session.add(user)
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


def current_user(request: Request, session: Session = Depends(get_session)) -> User:
    """Dependency for every protected route: a bearer token if one is sent,
    otherwise the session cookie."""
    user = None
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        token = session.scalar(select(ApiToken).where(ApiToken.token_hash == hash_token(header[7:].strip())))
        if token:
            token.last_used_at = datetime.now(UTC)
            user = session.get(User, token.user_id)
    elif request.session.get("uid"):
        user = session.get(User, request.session["uid"])
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    return user


router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    id: int
    email: str


@router.post("/login", response_model=UserOut)
def login(body: LoginIn, request: Request, session: Session = Depends(get_session)):
    user = session.scalar(select(User).where(User.email == body.email))
    if user is None or not verify_password(user.password_hash, body.password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong email or password")
    request.session["uid"] = user.id
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request):
    request.session.clear()


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return user
