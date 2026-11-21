"""All configuration and branding in one place.

Constants are plain module values; secrets and per-machine values come from the
repo-root `.env` through `Settings`. Never log or print Settings values.
"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# --- Branding ---------------------------------------------------------------
APP_NAME = "Kosha"
APP_VERSION = "0.1.0"

# --- Paths ------------------------------------------------------------------
BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent

# --- Server -----------------------------------------------------------------
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8000
API_PREFIX = "/api"
CORS_ORIGINS = ["http://localhost:5173"]
SESSION_COOKIE = "kosha_session"
SESSION_MAX_AGE_S = 60 * 60 * 24 * 30

# --- Lists ------------------------------------------------------------------
PAGE_SIZES = (25, 50, 100)
DEFAULT_PAGE_SIZE = 50

# --- Tags -------------------------------------------------------------------
DEFAULT_NAMESPACE = "main"
EMBEDDING_DIM = 1024

# --- Media ------------------------------------------------------------------
MEDIA_CONCURRENCY = 4
MEDIA_TIMEOUT_S = 60

# --- Official X API ---------------------------------------------------------
X_API_BASE = "https://api.x.com/2"
X_API_COST_PER_BOOKMARK_USD = 0.001
X_API_PAGE_SIZE = 100
X_API_STALE_PAGES = 3  # stop after this many pages that add nothing new

# --- User settings (stored per user in `settings`; these are the defaults) --
DEFAULT_SETTINGS: dict = {
    "store_images": True,
    "store_videos": False,
    "x_api_sync_enabled": False,
    "x_api_monthly_cap_usd": 3.0,
    "page_size": DEFAULT_PAGE_SIZE,
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    database_url: str
    test_database_url: str = ""
    kosha_secret_key: str
    kosha_admin_email: str = ""
    kosha_admin_password: str = ""
    kosha_api_token: str = ""
    kosha_media_dir: Path = REPO_ROOT / "data" / "media"
    # Send the session cookie over HTTPS only; turn on behind the reverse proxy.
    kosha_cookie_secure: bool = False
    # Official X API (optional; the daily sync does nothing without them).
    kosha_x_access_token: str = ""
    kosha_x_user_id: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
