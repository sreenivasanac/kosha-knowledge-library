"""Instance API keys: one accessor for every place that calls a paid service.

A key comes from the admin's settings page (stored in `instance_settings`,
encrypted with a key derived from KOSHA_SECRET_KEY) or, failing that, from the
environment. Use `get_key("voyage" | "typesafe" | "openai" | "transcription")`;
never read `get_settings().<x>_api_key` directly. Other secrets stored in
`instance_settings` (the S3 secret) use `encrypt` / `decrypt`. Key values are never
returned by the API and must not be logged.
"""
import base64
import logging
import time

import httpx
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import select
from sqlalchemy.orm import Session

from kosha.config import get_settings
from kosha.db import SessionLocal
from kosha.models import InstanceSetting

log = logging.getLogger(__name__)

# name -> the Settings field that holds the environment's value
KEY_ENV = {"voyage": "voyage_api_key", "typesafe": "typesafe_api_key", "openai": "openai_api_key",
           "transcription": "kosha_transcription_api_key"}
KEY_NAMES = tuple(KEY_ENV)
ROW_PREFIX = "key:"  # instance_settings.key of a stored key
CACHE_TTL_S = 30  # how long another process (the worker, a second server) may keep using a replaced key
TEST_TIMEOUT_S = 20

_cache: dict = {"at": None, "rows": {}}


def _fernet() -> Fernet:
    derived = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"kosha instance api keys").derive(
        get_settings().kosha_secret_key.encode())
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt(value: str) -> str:
    """A secret as the text stored in `instance_settings` (API keys, the S3 secret)."""
    return _fernet().encrypt(value.strip().encode()).decode()


def decrypt(stored: str) -> str | None:
    """The secret back, or None when KOSHA_SECRET_KEY changed since it was saved."""
    try:
        return _fernet().decrypt(stored.encode()).decode()
    except InvalidToken:
        return None


def invalidate() -> None:
    _cache["at"] = None


def _stored() -> dict[str, dict]:
    """The stored rows by key name: `{enc, last4, provider?, model?}`. Read
    through a short cache, since `get_key` runs before every paid call."""
    if _cache["at"] is None or time.monotonic() - _cache["at"] > CACHE_TTL_S:
        with SessionLocal() as session:
            _cache["rows"] = {row.key[len(ROW_PREFIX):]: row.value for row in session.scalars(
                select(InstanceSetting).where(InstanceSetting.key.startswith(ROW_PREFIX)))}
        _cache["at"] = time.monotonic()
    return _cache["rows"]


def _name(name: str) -> str:
    name = name.strip().lower()
    if name not in KEY_ENV:
        raise KeyError(f"Unknown key {name!r}; one of: {', '.join(KEY_NAMES)}")
    return name


def _resolve(name: str) -> tuple[str | None, str | None]:
    """(key, where it came from): the stored key if there is one, else the environment's."""
    row = _stored().get(name)
    if row and row.get("enc"):
        value = decrypt(row["enc"])
        if value is not None:
            return value, "db"
        log.warning("The stored %s key cannot be decrypted (was KOSHA_SECRET_KEY changed?); save it again", name)
    env = get_settings()
    value = getattr(env, KEY_ENV[name]).strip()
    if not value and name == "transcription":  # DEEPINFRA_API_KEY is the transcription key when no other is given
        value = env.deepinfra_api_key.strip()
    return (value, "env") if value else (None, None)


def get_key(name: str) -> str | None:
    """The key to use, or None when it is not set (an empty value counts as not set)."""
    return _resolve(_name(name))[0]


def key_options(name: str) -> dict:
    """What is stored next to a key. For `transcription`: `{provider, model}`
    (from the admin's settings, else KOSHA_TRANSCRIPTION_PROVIDER / _MODEL)."""
    name = _name(name)
    row, env = _stored().get(name) or {}, get_settings()
    if name != "transcription":
        return {}
    return {"provider": row.get("provider") or env.kosha_transcription_provider or None,
            "model": row.get("model") or env.kosha_transcription_model or None}


def status(name: str) -> dict:
    """What the admin page shows: never the key itself."""
    name = _name(name)
    value, source = _resolve(name)
    return {"name": name, "set": value is not None, "source": source, "last4": value[-4:] if value else None,
            **key_options(name)}


def set_key(session: Session, name: str, value: str | None, **options) -> None:
    """Store a key (encrypted) and/or its options. Without `value` the stored
    key is kept and only the options change. Commits."""
    name = _name(name)
    row = dict(_stored().get(name) or {})
    if value:
        row["enc"] = encrypt(value)
    row.update({k: v for k, v in options.items() if v is not None})
    session.merge(InstanceSetting(key=ROW_PREFIX + name, value=row))
    session.commit()
    invalidate()


def delete_key(session: Session, name: str) -> None:
    """Forget the stored key; the environment's, if any, applies again. Commits."""
    row = session.get(InstanceSetting, ROW_PREFIX + _name(name))
    if row is not None:
        session.delete(row)
        session.commit()
    invalidate()


def check_key(name: str) -> tuple[bool, str]:
    """One tiny call with the key: (works, what the service said). Costs
    nothing (OpenAI: the model list) or a fraction of a cent (one word
    embedded, one Jev question)."""
    name = _name(name)
    key = get_key(name)
    if not key:
        return False, "No key is set"
    try:
        if name == "voyage":
            from kosha.search.embeddings import QUERY_MODEL, VOYAGE_URL

            response = httpx.post(VOYAGE_URL, headers={"Authorization": f"Bearer {key}"}, timeout=TEST_TIMEOUT_S,
                                  json={"input": ["ping"], "model": QUERY_MODEL, "input_type": "query"})
        elif name == "typesafe":
            from constella import jev as cj

            cj.Jev(api_key=key).ask([(cj.Question("Is this a test?"), "0", "This is a connection test.")])
            return True, "The key works"
        else:
            provider = "openai" if name == "openai" else (key_options(name)["provider"] or "")
            if provider.lower() != "openai":
                return False, f"Kosha cannot test a key of provider {provider or '(none set)'}; it is used as given"
            response = httpx.get("https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {key}"}, timeout=TEST_TIMEOUT_S)
    except httpx.HTTPError as exc:
        return False, f"Could not reach the service: {type(exc).__name__}"
    except Exception as exc:  # constella raises JevError with the service's answer
        return False, str(exc)[:200]
    if response.status_code == 200:
        return True, "The key works"
    return False, f"The service answered {response.status_code}" + (": the key was refused" if response.status_code in (401, 403) else "")
