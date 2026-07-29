"""Profiles: resolution order, name validation, fail-closed default, and that a
profile FastMCP instance really only has its allowed tools registered.

This is the safety net for killing the tool-gate monkeypatch (workstream B1).
The old gate filtered at list-time and could fail open; profiles filter at
registration, so these tests assert a disallowed tool literally does not exist
on the profile instance.
"""
from __future__ import annotations

import pytest

from core import profiles as P
from ui.runtime import all_tool_names, build_mcp

ALL = all_tool_names()


def _registered(mcp) -> set[str]:
    return {t.name for t in mcp._tool_manager.list_tools()}


def test_name_validation(tmp_path):
    P.save_profiles(tmp_path, [{"name": "research-1", "intent": "web"}])  # ok
    with pytest.raises(ValueError):
        P.save_profiles(tmp_path, [{"name": "Bad Name"}])  # space + uppercase
    with pytest.raises(ValueError):
        P.save_profiles(tmp_path, [{"name": "a"}, {"name": "a"}])  # duplicate


def test_empty_profile_is_fail_closed():
    """A profile with no sections/intent/includes must resolve to the always-
    exposed meta tool only — never the full surface."""
    allow = P.resolve_tool_names(
        {"name": "empty", "sections": [], "intent": "", "include_tools": [], "exclude_tools": []},
        ALL,
    )
    assert allow == (P.ALWAYS_EXPOSED & set(ALL))
    assert "plutus_tool_slicer" in allow
    assert "ssh_run" not in allow


def test_section_resolution_isolates():
    web = P.resolve_tool_names({"name": "w", "sections": ["search"]}, ALL)
    media = P.resolve_tool_names({"name": "m", "sections": ["media"]}, ALL)
    assert "web_search" in web and "web_search" not in media
    assert "jellyfin_search" in media and "jellyfin_search" not in web
    assert web != media


def test_include_and_exclude():
    prof = {
        "name": "x",
        "sections": ["media"],
        "intent": "",
        "include_tools": ["get_context", "not_a_real_tool"],
        "exclude_tools": ["jellyfin_search"],
    }
    allow = P.resolve_tool_names(prof, ALL)
    assert "get_context" in allow                 # explicit include
    assert "not_a_real_tool" not in allow          # typo can't inject
    assert "jellyfin_search" not in allow          # explicit exclude wins
    assert any(t.startswith("sonarr_") for t in allow)  # rest of the section stays


def test_build_mcp_only_registers_allowed():
    web = P.resolve_tool_names({"name": "w", "sections": ["search"]}, ALL)
    media = P.resolve_tool_names({"name": "m", "sections": ["media"]}, ALL)
    web_mcp = build_mcp("plutus-w", web)
    media_mcp = build_mcp("plutus-m", media)
    assert _registered(web_mcp) == web            # exactly the allowed set, nothing else
    assert _registered(media_mcp) == media
    assert "jellyfin_search" not in _registered(web_mcp)
    assert "web_search" not in _registered(media_mcp)


def test_full_instance_registers_everything():
    full = build_mcp("plutus-full", None)
    assert _registered(full) == set(ALL)


def test_asgi_app_serves_mcp_behind_bearer():
    from core.mcp_bearer_middleware import MCPBearerGateMiddleware
    from ui.runtime import build_mcp_asgi_app

    app = build_mcp_asgi_app()
    paths = [getattr(r, "path", None) for r in app.routes]
    assert "/mcp" in paths
    assert any(m.cls is MCPBearerGateMiddleware for m in app.user_middleware)


def _no_bearer_gate(monkeypatch):
    """Pin the bearer gate off for routing tests.

    These assert *where* the transport is mounted, not whether auth works, and
    must not depend on whether a .env happens to exist (it does locally, it does
    not on CI) or on what an earlier test left in the gate's TTL cache.
    """
    import core.mcp_bearer_middleware as mw

    monkeypatch.setattr(mw, "read_env", lambda: {})
    mw._cache = None
    mw._cache_ts = 0.0


def test_mcp_endpoint_answers_at_exactly_slash_mcp(monkeypatch):
    """/mcp must serve the transport itself — not redirect.

    Mounting FastMCP's app (which routes its endpoint at /mcp) under /mcp nested
    the paths into /mcp/mcp and turned the advertised /mcp into a 307 -> /mcp/
    -> 404. Every client config, the OAuth metadata and the agent runner point at
    /mcp, so a redirect here is a broken server: behind a TLS-terminating proxy
    the 307's Location even downgrades to http://. Assert the real request.
    """
    from starlette.testclient import TestClient

    from ui.runtime import build_mcp_asgi_app

    _no_bearer_gate(monkeypatch)
    init = {
        "jsonrpc": "2.0", "id": 0, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "pytest", "version": "1"}},
    }
    headers = {"Accept": "application/json, text/event-stream"}

    with TestClient(build_mcp_asgi_app()) as client:
        r = client.post("/mcp", json=init, headers=headers, follow_redirects=False)
        assert r.status_code == 200, f"/mcp returned {r.status_code} (redirect/404 = broken endpoint)"
        assert r.headers.get("mcp-session-id")
        # The nested path the old Mount produced must not be the live one.
        assert client.post("/mcp/mcp", json=init, headers=headers,
                           follow_redirects=False).status_code == 404


def test_built_instances_are_reachable_from_non_localhost():
    """FastMCP turns on loopback-only DNS-rebinding protection when host is the
    default 127.0.0.1, which answers 421 to any other Host header. Plutus binds
    0.0.0.0 and is reached over LAN/Tailscale, so a built instance that inherits
    the loopback default serves nobody."""
    m = build_mcp("plutus-probe", {"web_search"})
    sec = m.settings.transport_security
    assert sec is None or not sec.enable_dns_rebinding_protection, (
        "build_mcp inherited loopback-only rebinding protection -> 421 for remote clients"
    )


def test_profile_endpoint_serves_at_its_advertised_path(tmp_path, monkeypatch):
    """A profile is advertised at /mcp/p/<name> (ui/api/profiles.py) — the same
    nesting bug put it at /mcp/p/<name>/mcp. The non-loopback Host header
    TestClient sends also covers the 421 rebinding-protection regression."""
    from starlette.testclient import TestClient

    import core.tool_exposure
    import ui.runtime as R

    _no_bearer_gate(monkeypatch)
    monkeypatch.setattr(R, "load_profiles", lambda _root: [{"name": "web", "intent": "web"}])
    # A FastMCP's session manager can only be run() once, and ui.runtime.mcp is a
    # module singleton another test in this file has already started. Returning a
    # non-None exposure makes build_mcp_asgi_app construct a fresh main instance.
    monkeypatch.setattr(core.tool_exposure, "resolve_exposed", lambda _root, names: set(names))

    init = {
        "jsonrpc": "2.0", "id": 0, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "pytest", "version": "1"}},
    }
    with TestClient(R.build_mcp_asgi_app()) as client:
        r = client.post("/mcp/p/web", json=init,
                        headers={"Accept": "application/json, text/event-stream"},
                        follow_redirects=False)
        assert r.status_code == 200, f"/mcp/p/web returned {r.status_code}"
