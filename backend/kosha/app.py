"""The FastAPI application: REST under /api, media under /media, and the
built web app at / when `web/dist` exists."""
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.sessions import SessionMiddleware
from starlette.routing import Route

from kosha import auth, media
from kosha.api import account, ai, captures, items, lists, misc, pipeline, removals, search, tags
from kosha.config import (
    API_PREFIX,
    APP_NAME,
    APP_VERSION,
    CORS_ORIGINS,
    MAX_BODY_BYTES,
    MCP_PATH,
    SESSION_COOKIE,
    SESSION_MAX_AGE_S,
    WEB_DIST_DIR,
    get_settings,
)
from kosha.mcp_server import McpEndpoint

TOO_LARGE = "Request body too large"


class BodyLimitMiddleware:
    """Refuse oversized requests before routing: by Content-Length when it is
    sent, otherwise by counting the body as it streams in."""

    def __init__(self, app, max_bytes: int = MAX_BODY_BYTES):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        length = dict(scope["headers"]).get(b"content-length", b"")
        if length.isdigit() and int(length) > self.max_bytes:
            response = JSONResponse({"detail": TOO_LARGE}, status.HTTP_413_CONTENT_TOO_LARGE)
            return await response(scope, receive, send)
        seen = 0

        async def limited():
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_bytes:  # raised inside the route's body read, so it becomes a 413
                    raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, TOO_LARGE)
            return message

        await self.app(scope, limited, send)


def _serve_web_app(app: FastAPI) -> None:
    """The single-page app: real files as they are, every other path gets
    index.html so client-side routes survive a reload. Registered last, and
    /api and /media never fall through to it."""
    root = WEB_DIST_DIR.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    def web_app(path: str):
        if path.split("/", 1)[0] in (API_PREFIX.strip("/"), "media", MCP_PATH.strip("/")):
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        file = (root / path).resolve()
        if path and file.is_relative_to(root) and file.is_file():
            return FileResponse(file)
        return FileResponse(root / "index.html")


def create_app() -> FastAPI:
    settings = get_settings()
    public_docs = settings.kosha_dev  # otherwise the docs need a signed-in user
    mcp_endpoint = McpEndpoint()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        async with mcp_endpoint.session_manager.run():  # the MCP SDK's task group lives as long as the server
            yield

    app = FastAPI(
        title=APP_NAME, version=APP_VERSION, redoc_url=None, lifespan=lifespan,
        openapi_url=f"{API_PREFIX}/openapi.json" if public_docs else None,
        docs_url=f"{API_PREFIX}/docs" if public_docs else None,
    )
    app.add_middleware(
        SessionMiddleware, secret_key=settings.kosha_secret_key, session_cookie=SESSION_COOKIE,
        max_age=SESSION_MAX_AGE_S, same_site="strict", https_only=settings.kosha_cookie_secure,
    )
    app.add_middleware(
        CORSMiddleware, allow_origins=CORS_ORIGINS, allow_credentials=True,
        allow_methods=["*"], allow_headers=["*"],
    )
    app.add_middleware(BodyLimitMiddleware)
    api = APIRouter(prefix=API_PREFIX)
    api.include_router(auth.router)
    # Everything below needs a signed-in user or a bearer token.
    protected = [Depends(auth.current_user)]
    for module in (captures, items, tags, misc, search, lists, removals, pipeline, ai, account):
        api.include_router(module.router, dependencies=protected)
    if not public_docs:
        api.add_api_route("/openapi.json", lambda: app.openapi(), dependencies=protected, include_in_schema=False)
        api.add_api_route("/docs", lambda: get_swagger_ui_html(openapi_url=f"{API_PREFIX}/openapi.json", title=APP_NAME),
                          dependencies=protected, include_in_schema=False)
    app.include_router(api)
    app.include_router(media.router, dependencies=protected)
    app.router.routes.append(Route(MCP_PATH, endpoint=mcp_endpoint))  # checks the bearer token itself
    if (WEB_DIST_DIR / "index.html").is_file():
        _serve_web_app(app)
    return app


app = create_app()
