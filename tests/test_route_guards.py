"""Every UI router must require auth, or be the explicit public allowlist.

This checks the router objects directly rather than introspecting the assembled
app's route tree — FastAPI/Starlette changed how included routers appear in
``app.routes`` (flattened APIRoutes vs. ``_IncludedRouter`` wrappers) across
versions, so router-level checks are the version-robust way to prove the
guarantee: auth is attached per-router, so no endpoint on an authed router can
be added unguarded.
"""
from __future__ import annotations

from ui.api import _AUTHED_ROUTERS, build_ui_app
from ui.api import public
from ui.api.deps import verify_auth

# The only paths reachable without Basic auth.
PUBLIC_PATHS = {"/", "/ui", "/server/health"}


def _has_verify_auth(router) -> bool:
    return any(getattr(d, "dependency", None) is verify_auth for d in router.dependencies)


def _router_paths(router) -> set[str]:
    return {r.path for r in router.routes if hasattr(r, "path")}


def test_every_authed_router_requires_verify_auth():
    for router in _AUTHED_ROUTERS:
        assert _has_verify_auth(router), f"authed router is missing verify_auth: {router!r}"


def test_public_router_only_serves_allowlisted_paths():
    assert not _has_verify_auth(public.router), "public router must not require auth"
    for path in _router_paths(public.router):
        assert path in PUBLIC_PATHS, f"public router serves a non-allowlisted path: {path}"


def test_core_surfaces_present():
    """Guard against a router being dropped from the assembly by accident."""
    paths: set[str] = set(_router_paths(public.router))
    for router in _AUTHED_ROUTERS:
        paths |= _router_paths(router)
    for expected in (
        "/app",
        "/api/v1/dashboard",
        "/api/v1/agent/status",
        "/service/test/{sid}",
        "/env/save",
        "/api/v1/files/list",
        "/api/v1/profiles",
    ):
        assert expected in paths, f"missing route {expected}"


def test_app_assembles():
    """The app still builds (routers include cleanly on the installed FastAPI)."""
    app = build_ui_app()
    assert len(app.routes) > 0
