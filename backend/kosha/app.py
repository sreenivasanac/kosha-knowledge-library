"""The FastAPI application: REST under /api, media under /media."""
from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from kosha import auth, media
from kosha.api import captures, items, misc, tags
from kosha.config import (
    API_PREFIX,
    APP_NAME,
    APP_VERSION,
    CORS_ORIGINS,
    SESSION_COOKIE,
    SESSION_MAX_AGE_S,
    get_settings,
)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=APP_NAME, version=APP_VERSION,
        openapi_url=f"{API_PREFIX}/openapi.json", docs_url=f"{API_PREFIX}/docs", redoc_url=None,
    )
    app.add_middleware(
        SessionMiddleware, secret_key=settings.kosha_secret_key, session_cookie=SESSION_COOKIE,
        max_age=SESSION_MAX_AGE_S, same_site="lax", https_only=settings.kosha_cookie_secure,
    )
    app.add_middleware(
        CORSMiddleware, allow_origins=CORS_ORIGINS, allow_credentials=True,
        allow_methods=["*"], allow_headers=["*"],
    )
    api = APIRouter(prefix=API_PREFIX)
    api.include_router(auth.router)
    # Everything below needs a signed-in user or a bearer token.
    protected = [Depends(auth.current_user)]
    for module in (captures, items, tags, misc):
        api.include_router(module.router, dependencies=protected)
    app.include_router(api)
    app.include_router(media.router, dependencies=protected)
    return app


app = create_app()
