"""Admin: where media is stored (kosha/storage.py), a self-test, and the
copy from one backend to the other."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from kosha import storage
from kosha.auth import admin_user
from kosha.db import get_session


# Every route needs an admin's session cookie; a bearer token is refused.
router = APIRouter(prefix="/admin/storage", tags=["admin"], dependencies=[Depends(admin_user)])


class S3In(BaseModel):
    endpoint: str | None = None
    region: str | None = None
    bucket: str | None = None
    prefix: str | None = None
    path_style: bool | None = None
    serve_mode: Literal["proxy", "presigned"] | None = None
    public_base_url: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = None  # write-only: stored encrypted, never returned


class StorageIn(BaseModel):
    backend: Literal["local", "s3"] | None = None
    local_dir: str | None = None
    s3: S3In | None = None


class BackendIn(BaseModel):
    backend: Literal["local", "s3"] | None = None  # default: the active one


class MigrateIn(BaseModel):
    to: Literal["local", "s3"]


def _out(session: Session) -> dict:
    config = storage.load_config(session)
    try:
        usage = storage.usage(storage.build(session))
    except storage.ERRORS as exc:  # an unreachable bucket must not hide the form that fixes it
        usage = {"files": None, "bytes": None, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    return {**config, "s3": {**config["s3"], "key_set": all(storage.credentials(session))}, "usage": usage}


@router.get("")
def get_storage(session: Session = Depends(get_session)) -> dict:
    """`{backend, local_dir, s3: {endpoint, region, bucket, prefix, path_style,
    serve_mode, public_base_url, key_set}, usage: {files, bytes}}`. No secrets."""
    return _out(session)


@router.patch("")
def patch_storage(body: StorageIn, session: Session = Depends(get_session)) -> dict:
    """Change any part of the configuration. Setting `backend` switches at
    once without copying anything; to move the files use `POST …/migrate`."""
    changes = body.model_dump(exclude_none=True)
    storage.save_config(session, changes)
    try:
        storage.build(session)  # the active backend must still be constructible
    except storage.ERRORS as exc:
        if "backend" in changes:  # do not leave the instance pointing at a backend that cannot work
            storage.save_config(session, {"backend": "local" if changes["backend"] == "s3" else "s3"})
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    return _out(session)


@router.post("/test")
def test_storage(body: BackendIn | None = None, session: Session = Depends(get_session)) -> dict:
    """Write, read, range-read and delete one small object on the active
    backend, or on `backend` (to try S3 settings before migrating): `{ok, message}`."""
    try:
        store = storage.build(session, body.backend if body else None)
    except storage.ERRORS as exc:
        return {"ok": False, "message": str(exc)}
    return storage.self_test(store)


@router.post("/migrate", status_code=status.HTTP_202_ACCEPTED)
def migrate(body: MigrateIn, session: Session = Depends(get_session)) -> dict:
    """Copy every object to the other backend in the background; it becomes
    the active one only after a complete, size-checked copy. The source is
    left as it is. Progress: `GET …/migrate`."""
    try:
        return storage.start_migration(session, body.to)
    except storage.ERRORS as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None


@router.get("/migrate")
def migrate_status() -> dict:
    """`{running, from, to, total, copied, bytes, failed, errors, switched, message, started_at, finished_at}`."""
    return storage.migration_status()
