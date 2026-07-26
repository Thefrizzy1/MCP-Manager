"""MCP App widget (workstream E): the plutus_status tool degrades to markdown,
the ui:// resource is registered self-contained, and nothing fetches the network.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from tools.apps import CONNECTIONS_TEMPLATE, register_app_tools, status_markdown


def test_status_markdown_is_a_meaningful_fallback():
    rows = [
        {"id": "jellyfin", "label": "Jellyfin", "configured": True, "tools": 2},
        {"id": "sonarr", "label": "Sonarr", "configured": False, "tools": 6},
    ]
    md = status_markdown(rows)
    assert "Plutus connections" in md
    assert "1/2 configured" in md
    assert "Jellyfin" in md and "Sonarr" in md
    assert "| Service | Status | Tools |" in md


def test_widget_registers_tool_and_ui_resource():
    m = FastMCP("t")
    register_app_tools(m)
    tool_names = {t.name for t in m._tool_manager.list_tools()}
    assert "plutus_status" in tool_names
    templates = {t.uri_template for t in m._resource_manager.list_templates()}
    statics = {str(r.uri) for r in m._resource_manager.list_resources()}
    assert "ui://plutus/connections" in (statics | templates)


def test_template_is_self_contained_no_network():
    # No external fetches from the sandboxed iframe.
    for bad in ("http://", "https://", "src=", "fetch(", "XMLHttpRequest", "cdn."):
        assert bad not in CONNECTIONS_TEMPLATE, f"template must not reference {bad!r}"


def test_ui_meta_and_mime():
    from tools.apps import _UI_MIME, _UI_URI

    assert _UI_MIME == "text/html;profile=mcp-app"
    assert _UI_URI == "ui://plutus/connections"
