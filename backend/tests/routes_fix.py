"""Routers that kosha/app.py does not include yet are mounted here for the
tests that need them. A no-op once app.py includes the router."""
from fastapi import APIRouter, Depends

from kosha import auth
from kosha.app import app
from kosha.config import API_PREFIX


def ensure_routes(router: APIRouter) -> None:
    paths = {getattr(route, "path", None) for route in app.routes}
    if all(API_PREFIX + route.path in paths for route in router.routes):
        return
    api = APIRouter(prefix=API_PREFIX)
    api.include_router(router, dependencies=[Depends(auth.current_user)])
    app.router.routes[0:0] = api.routes  # ahead of the web app's catch-all route
