"""Tests run against the `kosha_test` database only. The environment is
pointed at it before any Kosha module that opens a connection is imported."""
import os
import tempfile

from kosha.config import Settings, get_settings

_test_url = Settings().test_database_url
assert _test_url.rsplit("/", 1)[-1].split("?")[0] == "kosha_test", "TEST_DATABASE_URL must point at kosha_test"
os.environ.update({
    "DATABASE_URL": _test_url,
    "KOSHA_MEDIA_DIR": tempfile.mkdtemp(prefix="kosha-test-media-"),
    "KOSHA_ADMIN_EMAIL": "admin@example.test",
    "KOSHA_ADMIN_PASSWORD": "test-password",
    "KOSHA_API_TOKEN": "test-api-token",
    "KOSHA_X_ACCESS_TOKEN": "test-x-token",
    "KOSHA_X_USER_ID": "42",
})
get_settings.cache_clear()

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from kosha.app import app  # noqa: E402
from kosha.auth import create_admin  # noqa: E402
from kosha.config import BACKEND_DIR  # noqa: E402
from kosha.db import SessionLocal, engine  # noqa: E402
from kosha.models import Base  # noqa: E402

API_TOKEN = "test-api-token"


@pytest.fixture(scope="session", autouse=True)
def _schema():
    command.upgrade(Config(str(BACKEND_DIR / "alembic.ini")), "head")


@pytest.fixture(autouse=True)
def _clean(_schema):
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture
def session():
    with SessionLocal() as s:
        yield s


@pytest.fixture
def user(session):
    admin = create_admin(session)
    session.commit()
    return admin


@pytest.fixture
def client(user):
    """A client authenticated with the bearer API token."""
    return TestClient(app, headers={"Authorization": f"Bearer {API_TOKEN}"})
