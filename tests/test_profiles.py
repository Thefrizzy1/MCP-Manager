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
