"""Media storage: the local backend, Range serving, the admin routes and the
migration. The S3 half runs against a real S3-compatible server when
KOSHA_TEST_S3_ENDPOINT, KOSHA_TEST_S3_ACCESS_KEY_ID and KOSHA_TEST_S3_SECRET
are set (see backend/README.md), and is skipped otherwise."""
import os
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from kosha import auth, storage
from kosha.api import storage_admin
from kosha.app import app
from kosha.config import CSRF_HEADER, CSRF_HEADER_VALUE
from kosha.models import Item, ItemMedia
from tests.routes_fix import ensure_routes

ensure_routes(storage_admin.router)

LOGIN = {"email": "admin@example.test", "password": "test-password"}
S3_ENDPOINT = os.environ.get("KOSHA_TEST_S3_ENDPOINT")
needs_s3 = pytest.mark.skipif(not S3_ENDPOINT, reason="no S3 test server (KOSHA_TEST_S3_ENDPOINT)")


@pytest.fixture
def admin(user, session):
    """A browser session of the admin (the storage routes refuse bearer tokens)."""
    auth._login_failures.clear()
    user.role = "admin"
    session.commit()
    browser = TestClient(app, headers={CSRF_HEADER: CSRF_HEADER_VALUE})
    assert browser.post("/api/auth/login", json=LOGIN).status_code == 200
    return browser


@pytest.fixture
def local(tmp_path, session):
    storage.save_config(session, {"backend": "local", "local_dir": str(tmp_path / "media")})
    return storage.get_storage(session)


def put(store, key: str, data: bytes, tmp_path) -> None:
    src = tmp_path / "upload"
    src.write_bytes(data)
    store.put_file(key, src)


def media_row(session, user, key: str) -> ItemMedia:
    item = Item(user_id=user.id, source="web", source_id=uuid.uuid4().hex)
    session.add(item)
    session.flush()
    row = ItemMedia(item_id=item.id, source_key="k", type="video", mime_type="video/mp4", local_path=key)
    session.add(row)
    session.commit()
    return row


def test_local_storage(local, tmp_path):
    put(local, "7/a.bin", b"0123456789", tmp_path)
    assert local.size("7/a.bin") == 10 and local.size("7/none") is None
    assert b"".join(local.stream("7/a.bin", 2, 5)) == b"2345" and b"".join(local.stream("7/a.bin", 8)) == b"89"
    assert list(local.list()) == [("7/a.bin", 10)] and storage.usage(local) == {"files": 1, "bytes": 10}
    with pytest.raises(storage.StorageError):
        local.put_file("../outside", tmp_path / "upload")
    local.delete("7/a.bin")
    local.delete("7/a.bin")  # deleting what is gone is not an error
    assert local.size("7/a.bin") is None
    assert storage.self_test(local)["ok"]


def test_byte_range():
    assert storage._byte_range("bytes=0-3", 10) == (0, 3) and storage._byte_range("bytes=4-", 10) == (4, 9)
    assert storage._byte_range("bytes=-3", 10) == (7, 9) and storage._byte_range("bytes=2-99", 10) == (2, 9)
    assert storage._byte_range(None, 10) is None and storage._byte_range("bytes=0-1,4-5", 10) is None
    with pytest.raises(ValueError):
        storage._byte_range("bytes=10-", 10)


class _Remote(storage.LocalStorage):
    """A local folder that behaves like a bucket: no file path to hand out."""
    name = "s3"

    def local_path(self, key):
        return None


def test_proxy_serves_ranges(client, session, user, local, tmp_path, monkeypatch):
    put(local, "1/v.mp4", b"0123456789", tmp_path)
    row = media_row(session, user, "1/v.mp4")
    # From disk: Starlette's own Range handling.
    part = client.get(f"/media/{row.id}", headers={"Range": "bytes=2-5"})
    assert part.status_code == 206 and part.content == b"2345"
    # Proxied from a bucket: ours.
    monkeypatch.setattr(storage, "build", lambda session, backend=None: _Remote(local.root))
    whole = client.get(f"/media/{row.id}")
    assert whole.status_code == 200 and whole.content == b"0123456789" and whole.headers["accept-ranges"] == "bytes"
    part = client.get(f"/media/{row.id}", headers={"Range": "bytes=2-5"})
    assert part.status_code == 206 and part.content == b"2345" and part.headers["content-range"] == "bytes 2-5/10"
    assert client.get(f"/media/{row.id}", headers={"Range": "bytes=-2"}).content == b"89"
    assert client.get(f"/media/{row.id}", headers={"Range": "bytes=50-"}).status_code == 416


def test_admin_routes(admin, client, user, local, tmp_path):
    assert client.get("/api/admin/storage").status_code == 403  # a bearer token is not enough
    put(local, "1/a.jpg", b"abc", tmp_path)
    got = admin.get("/api/admin/storage").json()
    assert got["backend"] == "local" and got["usage"] == {"files": 1, "bytes": 3} and got["s3"]["key_set"] is False
    assert admin.post("/api/admin/storage/test").json()["ok"] is True
    patched = admin.patch("/api/admin/storage", json={"s3": {"bucket": "b", "region": "auto", "access_key_id": "id",
                                                               "secret_access_key": "very-secret"}}).json()
    assert patched["s3"]["bucket"] == "b" and patched["s3"]["key_set"] is True and "very-secret" not in str(patched)
    assert "secret" not in str({k: v for k, v in patched["s3"].items() if k != "key_set"})
    # Switching to a backend that cannot be built is refused and undone.
    assert admin.patch("/api/admin/storage", json={"backend": "s3", "s3": {"bucket": ""}}).status_code == 422
    assert admin.get("/api/admin/storage").json()["backend"] == "local"
    assert admin.post("/api/admin/storage/migrate", json={"to": "local"}).status_code == 409  # already active


# --- Against a real S3-compatible server --------------------------------------------
@pytest.fixture
def s3(session, tmp_path):
    """A fresh bucket on the test server, configured as the (inactive) S3 backend."""
    import boto3

    bucket = f"kosha-test-{uuid.uuid4().hex[:12]}"
    keys = {"aws_access_key_id": os.environ["KOSHA_TEST_S3_ACCESS_KEY_ID"], "aws_secret_access_key": os.environ["KOSHA_TEST_S3_SECRET"]}
    client = boto3.client("s3", endpoint_url=S3_ENDPOINT, region_name="us-east-1", **keys)
    client.create_bucket(Bucket=bucket)
    storage.save_config(session, {"backend": "local", "local_dir": str(tmp_path / "media"), "s3": {
        "endpoint": S3_ENDPOINT, "region": "us-east-1", "bucket": bucket, "prefix": "kosha/media", "path_style": True,
        "serve_mode": "proxy", "access_key_id": keys["aws_access_key_id"], "secret_access_key": keys["aws_secret_access_key"]}})
    yield storage.build(session, "s3")
    for obj in client.list_objects_v2(Bucket=bucket).get("Contents", []):
        client.delete_object(Bucket=bucket, Key=obj["Key"])
    client.delete_bucket(Bucket=bucket)


@needs_s3
def test_s3_storage(s3, session, tmp_path):
    put(s3, "7/a.bin", b"0123456789", tmp_path)
    assert s3.size("7/a.bin") == 10 and s3.size("7/none") is None
    assert b"".join(s3.stream("7/a.bin")) == b"0123456789" and b"".join(s3.stream("7/a.bin", 2, 5)) == b"2345"
    assert list(s3.list()) == [("7/a.bin", 10)]  # keys come back without the prefix
    s3.download("7/a.bin", tmp_path / "back")
    assert (tmp_path / "back").read_bytes() == b"0123456789"
    assert httpx.get(s3.url("7/a.bin")).content == b"0123456789"  # presigned
    assert httpx.get(s3.url("7/a.bin"), headers={"Range": "bytes=8-"}).content == b"89"
    s3.delete("7/a.bin")
    assert s3.size("7/a.bin") is None
    assert storage.self_test(s3) == {"ok": True, "message": "Write, read, range read and delete all work on the s3 storage"}


@needs_s3
def test_s3_serving_and_migration(s3, admin, client, session, user, tmp_path, monkeypatch):
    local = storage.get_storage(session)
    files = {f"{n}/{n}.bin": os.urandom(100 + n * 37) for n in range(50)}
    for key, data in files.items():
        put(local, key, data, tmp_path)
    row = media_row(session, user, "3/3.bin")
    assert admin.post("/api/admin/storage/test", json={"backend": "s3"}).json()["ok"] is True

    # local -> S3: everything arrives, sizes are checked, S3 becomes active, the folder is untouched.
    state = storage.start_migration(session, "s3", background=False)
    assert (state["copied"], state["failed"], state["switched"]) == (50, 0, True) and not state["running"]
    assert admin.get("/api/admin/storage/migrate").json()["switched"] is True
    assert storage.load_config(session)["backend"] == "s3" and dict(s3.list()) == {k: len(v) for k, v in files.items()}
    assert len(list(local.list())) == 50
    assert admin.get("/api/admin/storage").json()["usage"]["files"] == 50

    # Served through the app from the bucket, with Range.
    part = client.get(f"/media/{row.id}", headers={"Range": "bytes=10-19"})
    assert part.status_code == 206 and part.content == files["3/3.bin"][10:20]
    assert client.get(f"/media/{row.id}").content == files["3/3.bin"]
    # Presigned mode: a redirect to an address that works without credentials.
    storage.save_config(session, {"s3": {"serve_mode": "presigned"}})
    redirect = client.get(f"/media/{row.id}", follow_redirects=False)
    assert redirect.status_code == 307 and httpx.get(redirect.headers["location"]).content == files["3/3.bin"]
    storage.save_config(session, {"s3": {"public_base_url": "https://cdn.example.test/"}})
    assert client.get(f"/media/{row.id}", follow_redirects=False).headers["location"] == "https://cdn.example.test/kosha/media/3/3.bin"

    # S3 -> local into an empty folder; a file added to the bucket meanwhile comes along.
    put(s3, "99/new.bin", b"late", tmp_path)
    storage.save_config(session, {"local_dir": str(tmp_path / "back")})
    state = storage.start_migration(session, "local", background=False)
    assert (state["copied"], state["failed"], state["switched"]) == (51, 0, True)
    back = storage.get_storage(session)
    assert back.name == "local" and dict(back.list()) == {**{k: len(v) for k, v in files.items()}, "99/new.bin": 4}
    assert (tmp_path / "back" / "3" / "3.bin").read_bytes() == files["3/3.bin"]
    assert len(list(s3.list())) == 51  # the source is never deleted

    # A failed copy leaves the active backend alone.
    (tmp_path / "back" / "3" / "3.bin").write_bytes(b"changed")
    monkeypatch.setattr(storage.S3Storage, "put_file", lambda self, key, src: (_ for _ in ()).throw(OSError("bucket full")))
    state = storage.start_migration(session, "s3", background=False)
    assert state["failed"] == 1 and not state["switched"] and storage.load_config(session)["backend"] == "local"
