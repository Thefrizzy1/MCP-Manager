"""Assemble the Plutus Web UI app: guards, static mounts, and every router.

``build_ui_app()`` is the single entry point ``main.py`` uses to construct the
FastAPI app. Auth is attached per-router (see each ``ui.api.*`` module); the CSRF
origin guard is installed here as app-wide middleware.
"""
from __future__ import annotations

from fastapi import FastAPI
from starlette.staticfiles import StaticFiles

from ui.api import (
    agents,
    catalog,
    connections,
    discover,
    files,
    health,
    profiles,
    public,
    settings,
    system,
)
from ui.api.deps import csrf_origin_guard
from ui.runtime import DIST_DIR, ICONS_DIR, STATIC_DIR, ui_lifespan

# Routers whose every route is guarded by verify_auth, plus the deliberately
# public one. Order only affects OpenAPI grouping, not resolution.
_AUTHED_ROUTERS = (
    files.router,
    health.router,
    connections.router,
    discover.router,
    catalog.router,
    profiles.router,
    agents.router,
    settings.router,
    system.router,
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

    app.middleware("http")(csrf_origin_guard)

    app.include_router(public.router)
    for r in _AUTHED_ROUTERS:
        app.include_router(r)
    return app
