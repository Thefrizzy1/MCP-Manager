"""Assemble the Plutus Web UI app: guards, static mounts, and every router.

``build_ui_app()`` is the single entry point ``main.py`` uses to construct the
FastAPI app. Auth is attached per-router (see each ``ui.api.*`` module); the CSRF
origin guard is installed here as app-wide middleware.
"""
from __future__ import annotations

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.staticfiles import StaticFiles

from ui.api import (
    agents,
    auth,
    catalog,
    connections,
    discover,
    files,
    health,
    profiles,
    providers,
    reddit,
    public,
    settings,
    system,
    workforce,
)
from ui.api.deps import csrf_origin_guard
from ui.runtime import DIST_DIR, ICONS_DIR, STATIC_DIR, ui_lifespan

# Routers with no verify_auth: the deliberately public ones. The route-guard test
# holds these to an explicit path allowlist so nothing goes public by accident.
_PUBLIC_ROUTERS = (
    public.router,
    auth.public_router,
)

# Routers whose every route is guarded by verify_auth.
# Order only affects OpenAPI grouping, not resolution.
_AUTHED_ROUTERS = (
    files.router,
    health.router,
    connections.router,
    discover.router,
    catalog.router,
    profiles.router,
    providers.router,
    reddit.router,
    agents.router,
    auth.router,
    settings.router,
    system.router,
    workforce.router,
)


def build_ui_app() -> FastAPI:
    app = FastAPI(title="Plutus MCP UI", lifespan=ui_lifespan)

    if ICONS_DIR.is_dir():
        app.mount("/icons", StaticFiles(directory=str(ICONS_DIR)), name="icons")
    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    if DIST_DIR.is_dir():
        # Hashed assets for the built React app (index.html references /spa/…).
        app.mount("/spa", StaticFiles(directory=str(DIST_DIR)), name="spa")

    # add_middleware(BaseHTTPMiddleware, dispatch=…) rather than the
    # @app.middleware("http") sugar, which Starlette deprecates for removal in 1.0.
    app.add_middleware(BaseHTTPMiddleware, dispatch=csrf_origin_guard)

    for r in _PUBLIC_ROUTERS:
        app.include_router(r)
    for r in _AUTHED_ROUTERS:
        app.include_router(r)
    return app
