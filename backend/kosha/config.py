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
MAX_BODY_BYTES = 20 * 1024 * 1024  # larger requests are refused with 413
# Cookie-authenticated requests that change something must carry this header
# (a cross-site form cannot set it). Bearer-token requests are exempt.
CSRF_HEADER = "X-Requested-With"
CSRF_HEADER_VALUE = "kosha"
LOGIN_MAX_FAILURES = 5  # failed logins per client address ...
LOGIN_WINDOW_S = 60  # ... within this many seconds, then 429
WEB_DIST_DIR = REPO_ROOT / "web" / "dist"  # the built web app, served at / when present

# --- Lists ------------------------------------------------------------------
PAGE_SIZES = (25, 50, 100)
DEFAULT_PAGE_SIZE = 50

# --- Tags -------------------------------------------------------------------
DEFAULT_NAMESPACE = "main"
EMBEDDING_DIM = 1024

# --- Media ------------------------------------------------------------------
MEDIA_CONCURRENCY = 4
MEDIA_TIMEOUT_S = 60
# Downloads go only to these hosts (or their subdomains), over https, and a
# redirect is followed only to another of them.
MEDIA_ALLOWED_HOSTS = ("pbs.twimg.com", "video.twimg.com", "abs.twimg.com", "media.licdn.com",
                       "static.licdn.com", "i.ytimg.com")
MEDIA_MAX_REDIRECTS = 5
MEDIA_MAX_BYTES_IMAGE = 25 * 1024 * 1024
MEDIA_MAX_BYTES_VIDEO = 500 * 1024 * 1024

# --- Official X API ---------------------------------------------------------
X_API_BASE = "https://api.x.com/2"
X_API_COST_PER_BOOKMARK_USD = 0.001
X_API_PAGE_SIZE = 100
X_API_STALE_PAGES = 3  # stop after this many pages that add nothing new
X_SYNC_INTERVAL_S = 24 * 60 * 60  # the in-process scheduler syncs this often ...
X_SYNC_CHECK_S = 15 * 60  # ... and looks whether it is time this often

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
    # Send the session cookie over HTTPS only. Set KOSHA_COOKIE_SECURE=false for plain-http local use.
    kosha_cookie_secure: bool = True
    # Local development: the API docs and schema are served without signing in.
    kosha_dev: bool = False
    # Official X API (optional; the daily sync does nothing without them).
    kosha_x_access_token: str = ""
    kosha_x_user_id: str = ""
    # Voyage embeddings (semantic search). Without it search is keyword-only.
    voyage_api_key: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
