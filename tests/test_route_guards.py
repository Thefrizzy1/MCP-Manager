"""Every UI route must require auth, or be on the explicit public allowlist.

This is the safety net for the ``main.py`` -> ``ui.api.*`` split (workstream A):
because auth is attached at the router level, a new endpoint added to an authed
router is guarded automatically. This test fails if any route escapes that — the
only routes allowed to be public are the ones named below.
"""
from __future__ import annotations

from starlette.routing import Mount

from ui.api import build_ui_app
from ui.api.deps import verify_auth

# The only routes that may be reached without Basic auth. `/` and `/ui` are
# redirects to the authed SPA; `/server/health` is the Docker liveness probe;
# the rest are FastAPI's own schema/docs routes (public in the pre-split app too).
PUBLIC_PATHS = {
    "/",
    "/ui",
    "/server/health",
    "/openapi.json",
    "/docs",
    "/redoc",
    "/docs/oauth2-redirect",
}


def _dependency_calls(dependant) -> list:
    calls = []
    for dep in dependant.dependencies:
        calls.append(dep.call)
        calls.extend(_dependency_calls(dep))
    return calls


def _api_routes():
    app = build_ui_app()
    for route in app.routes:
        if isinstance(route, Mount):
            continue  # /static, /icons — static file mounts, not data endpoints
        if not hasattr(route, "dependant"):
            continue
        yield route


def test_every_route_is_guarded_or_allowlisted():
    unguarded = []
    for route in _api_routes():
        guarded = verify_auth in _dependency_calls(route.dependant)
        if not guarded and route.path not in PUBLIC_PATHS:
            unguarded.append(f"{sorted(route.methods or [])} {route.path}")
    assert not unguarded, "Unguarded, non-allowlisted routes:\n  " + "\n  ".join(unguarded)


def test_public_routes_are_actually_public():
    """The allowlisted app routes must NOT carry verify_auth — otherwise the
    healthcheck/redirects would 401. (Skips FastAPI's own docs routes.)"""
    app_public = {"/", "/ui", "/server/health"}
    for route in _api_routes():
        if route.path in app_public:
            assert verify_auth not in _dependency_calls(route.dependant), route.path


def test_core_surfaces_present():
    """Guard against a router being dropped from the assembly by accident."""
    paths = {r.path for r in _api_routes()}
    for expected in (
        "/app",
        "/api/v1/dashboard",
        "/api/v1/agent/status",
        "/service/test/{sid}",
        "/env/save",
        "/api/v1/files/list",
    ):
        assert expected in paths, f"missing route {expected}"
