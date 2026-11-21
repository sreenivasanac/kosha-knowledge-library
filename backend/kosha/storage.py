"""Media storage: where the files behind `item_media.local_path` and
`local_preview_path` live. Those columns hold a key ("<item id>/<media id>.jpg")
relative to the storage root, so the same rows work on either backend:

- `local`: a directory (the default; a NAS or rclone mount is a directory too)
- `s3`: any S3-compatible bucket through boto3 (AWS S3, Cloudflare R2,
  Backblaze B2, Hetzner Object Storage, Garage, MinIO, SeaweedFS)

The configuration is one instance-wide value: the `KOSHA_STORAGE_BACKEND` and
`KOSHA_S3_*` environment variables, overridden by the `storage` row of
`instance_settings` that an admin saves through `/api/admin/storage`. The S3
secret is stored there encrypted with kosha.keys' cipher, or read from the
environment.
"""
import copy
import json
import shutil
import tempfile
import threading
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse
from pydantic_settings import BaseSettings, SettingsConfigDict
import boto3
from boto3.exceptions import S3UploadFailedError
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import select
from sqlalchemy.orm import Session

from kosha import keys
from kosha.config import REPO_ROOT, get_settings
from kosha.models import InstanceSetting

# --- Tunables ---------------------------------------------------------------
SETTING_KEY = "storage"  # the `instance_settings` row that holds the saved configuration
BACKENDS = ("local", "s3")
SERVE_MODES = ("proxy", "presigned")
PRESIGN_TTL_S = 300  # how long a presigned download address works
CHUNK_BYTES = 1024 * 1024  # proxy streaming and local reads
MIGRATE_MAX_PASSES = 3  # a pass copies what the target lacks; files added meanwhile are caught by the next
MIGRATE_ERRORS_KEPT = 20
TEST_BYTES = b"kosha storage test 0123456789"


class _Env(BaseSettings):
    """The storage variables of the repo-root `.env` (config.Settings ignores them)."""
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    kosha_storage_backend: str = "local"
    kosha_s3_endpoint: str = ""
    kosha_s3_region: str = "auto"
    kosha_s3_bucket: str = ""
    kosha_s3_prefix: str = ""
    kosha_s3_path_style: bool = False
    kosha_s3_serve_mode: str = "proxy"
    kosha_s3_public_base_url: str = ""
    kosha_s3_access_key_id: str = ""
    kosha_s3_secret_access_key: str = ""


class StorageError(RuntimeError):
    """The storage is not usable as configured; the message is shown to the admin."""


# What a read or write can raise on either backend.
ERRORS = (StorageError, OSError, BotoCoreError, ClientError, S3UploadFailedError)


# --- The two backends ---------------------------------------------------------
class LocalStorage:
    name = "local"

    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root) or path == self.root:
            raise StorageError(f"Not a storage key: {key!r}")
        return path

    def local_path(self, key: str) -> Path | None:
        """The file itself, for callers that can read from disk (FileResponse, ffmpeg)."""
        path = self._path(key)
        return path if path.is_file() else None

    def put_file(self, key: str, src: Path) -> None:
        """Copy `src` in under `key`; written as `.part` and renamed, so a reader never sees half a file."""
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        shutil.copyfile(src, part)
        part.replace(dest)

    def size(self, key: str) -> int | None:
        path = self._path(key)
        return path.stat().st_size if path.is_file() else None

    def stream(self, key: str, start: int = 0, end: int | None = None) -> Iterator[bytes]:
        """Bytes `start` to `end` inclusive (to the end of the file without `end`)."""
        left = None if end is None else end - start + 1
        with self._path(key).open("rb") as fh:
            fh.seek(start)
            while left is None or left > 0:
                chunk = fh.read(CHUNK_BYTES if left is None else min(CHUNK_BYTES, left))
                if not chunk:
                    return
                left = None if left is None else left - len(chunk)
                yield chunk

    def download(self, key: str, dest: Path) -> None:
        shutil.copyfile(self._path(key), dest)

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def list(self) -> Iterator[tuple[str, int]]:
        """Every stored file as (key, bytes)."""
        if not self.root.is_dir():
            return
        for path in self.root.rglob("*"):
            if path.is_file() and not path.name.endswith(".part"):
                yield path.relative_to(self.root).as_posix(), path.stat().st_size


class S3Storage:
    name = "s3"

    def __init__(self, s3: dict, access_key_id: str, secret_access_key: str):
        if not s3.get("bucket"):
            raise StorageError("S3 storage needs a bucket")
        if not access_key_id or not secret_access_key:
            raise StorageError("S3 storage needs an access key id and a secret")
        self.bucket = s3["bucket"]
        self.prefix = s3.get("prefix", "").strip("/") + "/" if s3.get("prefix", "").strip("/") else ""
        self.public_base_url = (s3.get("public_base_url") or "").rstrip("/")
        self.client = boto3.client(
            "s3", endpoint_url=s3.get("endpoint") or None, region_name=s3.get("region") or "auto",
            aws_access_key_id=access_key_id, aws_secret_access_key=secret_access_key,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path" if s3.get("path_style") else "auto"},
                # boto3's newer default adds checksum headers that several
                # S3-compatible servers (Garage, older MinIO, B2) refuse.
                request_checksum_calculation="when_required", response_checksum_validation="when_required"))

    def local_path(self, key: str) -> None:
        return None

    def put_file(self, key: str, src: Path) -> None:
        self.client.upload_file(str(src), self.bucket, self.prefix + key)

    def size(self, key: str) -> int | None:
        try:
            return self.client.head_object(Bucket=self.bucket, Key=self.prefix + key)["ContentLength"]
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return None
            raise

    def stream(self, key: str, start: int = 0, end: int | None = None) -> Iterator[bytes]:
        ranged = {"Range": f"bytes={start}-{'' if end is None else end}"} if start or end is not None else {}
        body = self.client.get_object(Bucket=self.bucket, Key=self.prefix + key, **ranged)["Body"]
        try:
            yield from body.iter_chunks(CHUNK_BYTES)
        finally:
            body.close()

    def download(self, key: str, dest: Path) -> None:
        self.client.download_file(self.bucket, self.prefix + key, str(dest))

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self.prefix + key)

    def list(self) -> Iterator[tuple[str, int]]:
        for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=self.prefix):
            for obj in page.get("Contents", []):
                yield obj["Key"][len(self.prefix):], obj["Size"]

    def url(self, key: str) -> str:
        """Where a browser can fetch the object itself: the public address
        when the bucket has one, else an address signed for PRESIGN_TTL_S."""
        if self.public_base_url:
            return f"{self.public_base_url}/{self.prefix}{key}"
        return self.client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": self.prefix + key}, ExpiresIn=PRESIGN_TTL_S)


# --- Configuration ------------------------------------------------------------
def _saved(session: Session) -> dict:
    return session.scalar(select(InstanceSetting.value).where(InstanceSetting.key == SETTING_KEY)) or {}


def credentials(session: Session) -> tuple[str, str]:
    """(access key id, secret): what the admin saved (the secret encrypted
    the way kosha.keys encrypts API keys), else the environment's."""
    env, s3 = _Env(), _saved(session).get("s3") or {}
    # None when KOSHA_SECRET_KEY changed since it was saved: the admin must enter it again
    secret = (keys.decrypt(s3["secret_enc"]) if s3.get("secret_enc") else None) or ""
    return s3.get("access_key_id") or env.kosha_s3_access_key_id.strip(), secret or env.kosha_s3_secret_access_key.strip()


def load_config(session: Session) -> dict:
    """`{backend, local_dir, s3: {endpoint, region, bucket, prefix, path_style,
    serve_mode, public_base_url}}`: the environment, overridden by the saved
    row. No secrets."""
    env = _Env()
    config = {
        "backend": env.kosha_storage_backend if env.kosha_storage_backend in BACKENDS else "local",
        "local_dir": str(get_settings().kosha_media_dir),
        "s3": {"endpoint": env.kosha_s3_endpoint, "region": env.kosha_s3_region, "bucket": env.kosha_s3_bucket,
               "prefix": env.kosha_s3_prefix, "path_style": env.kosha_s3_path_style,
               "serve_mode": env.kosha_s3_serve_mode if env.kosha_s3_serve_mode in SERVE_MODES else "proxy",
               "public_base_url": env.kosha_s3_public_base_url},
    }
    saved = _saved(session)
    config.update({k: saved[k] for k in ("backend", "local_dir") if saved.get(k)})
    config["s3"].update({k: v for k, v in (saved.get("s3") or {}).items() if k in config["s3"]})
    return config


def save_config(session: Session, changes: dict) -> dict:
    """Merge `changes` (the shape of load_config, any part of it; `s3` may
    also carry `access_key_id` and `secret_access_key`) into the saved row.
    The secret is stored encrypted. Commits."""
    saved = _saved(session)
    s3 = {**(saved.get("s3") or {}), **(changes.get("s3") or {})}
    secret = s3.pop("secret_access_key", None)
    if secret:
        s3["secret_enc"] = keys.encrypt(secret)
    session.merge(InstanceSetting(key=SETTING_KEY, value={**saved, **{k: v for k, v in changes.items() if k != "s3"}, "s3": s3}))
    session.commit()
    return load_config(session)


@lru_cache(maxsize=8)
def _build(backend: str, config_json: str, access_key_id: str, secret_access_key: str):
    """One client per distinct configuration (a boto3 client is slow to make)."""
    config = json.loads(config_json)
    if backend == "s3":
        return S3Storage(config["s3"], access_key_id, secret_access_key)
    return LocalStorage(Path(config["local_dir"]))


def build(session: Session, backend: str | None = None):
    """The storage for `backend` (default: the active one) as configured now."""
    config = load_config(session)
    backend = backend or config["backend"]
    if backend not in BACKENDS:
        raise StorageError(f"Unknown storage backend {backend!r}; use one of {', '.join(BACKENDS)}")
    return _build(backend, json.dumps(config, sort_keys=True), *(credentials(session) if backend == "s3" else ("", "")))


def get_storage(session: Session | None = None):
    """The active storage. Without a session one is opened for the lookup."""
    if session is not None:
        return build(session)
    from kosha.db import SessionLocal

    with SessionLocal() as own:
        return build(own)


def delete(key: str, session: Session | None = None) -> None:
    """Remove one stored file from the active storage (item hard-delete)."""
    get_storage(session).delete(key)


# --- Serving ------------------------------------------------------------------
def _byte_range(header: str | None, size: int) -> tuple[int, int] | None:
    """`bytes=a-b`, `bytes=a-` or `bytes=-n` as (first, last) inclusive. None
    when there is no usable single range (the whole object is sent then);
    ValueError when the range lies outside the object (416)."""
    if not header or not header.startswith("bytes=") or "," in header:
        return None
    first, _, last = header[6:].strip().partition("-")
    try:
        if not first:  # the last n bytes
            start, end = max(size - int(last), 0), size - 1
        else:
            start, end = int(first), min(int(last), size - 1) if last else size - 1
    except ValueError:
        return None
    if start >= size or start > end:
        raise ValueError("range outside the object")
    return start, end


def response(session: Session, key: str, range_header: str | None, media_type: str | None) -> Response | None:
    """The HTTP answer for one stored file, or None when the storage does not
    hold it. Local files and proxied S3 objects honour `Range` (video seeking);
    in `presigned` mode the browser is sent to the bucket instead."""
    config = load_config(session)
    store = build(session)
    path = store.local_path(key)
    if path is not None:
        return FileResponse(path, media_type=media_type)  # Starlette answers Range requests itself
    if store.name == "local":
        return None
    if config["s3"]["serve_mode"] == "presigned":
        return RedirectResponse(store.url(key))
    size = store.size(key)
    if size is None:
        return None
    headers = {"Accept-Ranges": "bytes"}
    try:
        span = _byte_range(range_header, size)
    except ValueError:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
    if span is None:
        return StreamingResponse(store.stream(key), media_type=media_type, headers={**headers, "Content-Length": str(size)})
    start, end = span
    return StreamingResponse(store.stream(key, start, end), status_code=206, media_type=media_type, headers={
        **headers, "Content-Length": str(end - start + 1), "Content-Range": f"bytes {start}-{end}/{size}"})


# --- Admin: usage, self-test, migration -----------------------------------------
def usage(store) -> dict:
    # ponytail: lists every object on each call (a few S3 pages for a personal
    # library); cache it or count from item_media.bytes if the bucket gets large.
    files = total = 0
    for _, size in store.list():
        files, total = files + 1, total + size
    return {"files": files, "bytes": total}


def self_test(store) -> dict:
    """Write, read, range-read and delete one small object: `{ok, message}`."""
    key = f".kosha-storage-test-{uuid.uuid4().hex}"
    try:
        with tempfile.TemporaryDirectory(prefix="kosha-storage-test-") as folder:
            src = Path(folder) / "probe"
            src.write_bytes(TEST_BYTES)
            store.put_file(key, src)
        if store.size(key) != len(TEST_BYTES):
            return {"ok": False, "message": "The object was written but its size does not match"}
        if b"".join(store.stream(key)) != TEST_BYTES:
            return {"ok": False, "message": "The object read back differs from what was written"}
        if b"".join(store.stream(key, 6, 12)) != TEST_BYTES[6:13]:
            return {"ok": False, "message": "Range reads return the wrong bytes (video seeking would not work)"}
        store.delete(key)
        if store.size(key) is not None:
            return {"ok": False, "message": "The object could not be deleted"}
    except Exception as exc:  # boto3, the network and the file system each raise their own classes
        return {"ok": False, "message": f"{type(exc).__name__}: {str(exc)[:300]}"}
    return {"ok": True, "message": f"Write, read, range read and delete all work on the {store.name} storage"}


_migration: dict = {"running": False}
_migration_lock = threading.Lock()


def migration_status() -> dict:
    with _migration_lock:
        return copy.deepcopy(_migration)


def _copy_missing(src, dst, state: dict) -> int:
    """One pass: copy every source object the target lacks or holds at another
    size, then check the size that arrived. Returns how many were copied."""
    have = dict(dst.list())
    objects = list(src.list())
    state["total"] = len(objects)
    copied = 0
    with tempfile.TemporaryDirectory(prefix="kosha-storage-migrate-") as folder:
        for key, size in objects:
            if have.get(key) == size:
                continue
            try:
                path = src.local_path(key)
                if path is None:
                    path = Path(folder) / "object"
                    src.download(key, path)
                dst.put_file(key, path)
                arrived = dst.size(key)
                if arrived != size:
                    raise StorageError(f"{size} bytes sent, {arrived} stored")
            except Exception as exc:
                state["failed"] += 1
                state["errors"] = (state["errors"] + [f"{key}: {type(exc).__name__}: {str(exc)[:160]}"])[-MIGRATE_ERRORS_KEPT:]
                continue
            copied += 1
            state["copied"] += 1
            state["bytes"] += size
    return copied


def run_migration(session: Session, to: str) -> dict:
    """Copy every object of the active backend to `to`, verify sizes, and
    only then make `to` the active backend. The source is never deleted.
    Returns the final state (also at `migration_status()`)."""
    src, dst = build(session), build(session, to)
    state = _migration
    try:
        for _ in range(MIGRATE_MAX_PASSES):
            state["failed"], state["errors"] = 0, []
            if _copy_missing(src, dst, state) == 0:
                break
        have = dict(dst.list())
        missing = [key for key, size in src.list() if have.get(key) != size]
        if state["failed"] or missing:
            state["message"] = f"{state['failed'] or len(missing)} objects could not be copied; the active backend is unchanged"
        else:
            save_config(session, {"backend": to})
            state["switched"] = True
            state["message"] = f"All {state['total']} objects are on the {to} storage, which is now active. The old copy was left in place."
    except Exception as exc:
        state["message"] = f"Stopped: {type(exc).__name__}: {str(exc)[:300]}; the active backend is unchanged"
    finally:
        state.update(running=False, finished_at=datetime.now(UTC).isoformat())
    return migration_status()


def start_migration(session: Session, to: str, background: bool = True) -> dict:
    """Begin copying to the other backend. Raises StorageError when `to` is
    already active, is not configured, or a migration is running."""
    config = load_config(session)
    if to not in BACKENDS or to == config["backend"]:
        raise StorageError(f"`to` must be the backend that is not active (active: {config['backend']})")
    build(session, to)  # refuses an unconfigured target before anything starts
    # ponytail: progress lives in this process's memory; it is lost on a restart
    # (run the migration again: objects already copied are skipped).
    with _migration_lock:
        if _migration.get("running"):
            raise StorageError("A migration is already running")
        _migration.clear()
        _migration.update(running=True, **{"from": config["backend"]}, to=to, total=0, copied=0, bytes=0, failed=0,
                          errors=[], switched=False, message=None, started_at=datetime.now(UTC).isoformat(), finished_at=None)
    if not background:
        return run_migration(session, to)

    def work():
        from kosha.db import SessionLocal

        with SessionLocal() as own:
            run_migration(own, to)

    threading.Thread(target=work, name="kosha-storage-migrate", daemon=True).start()
    return migration_status()
