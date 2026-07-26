"""Playbooks -> MCP prompts (workstream B2): registration, placeholder args, and
pure-substitution rendering (no model call)."""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

import tools.prompts as pm
from tools.prompts import _placeholders, _render


def test_placeholders_are_ordered_and_unique():
    assert _placeholders("a {{LIBRARY}} {{DATE}} b {{LIBRARY}}") == ["LIBRARY", "DATE"]
    assert _placeholders("no tokens here") == []


def test_render_substitutes_defaults_and_overrides():
    out = _render("Read {{LIBRARY}} on {{DATE}}", {})
    assert "{{" not in out  # every token filled from defaults
    out2 = _render("X {{LIBRARY}} Y", {"library": "/custom/path"})
    assert "/custom/path" in out2
    assert _render("{{UNKNOWN}}", {}) == ""  # unknown token -> empty, not left raw


def test_playbooks_register_as_prompts(monkeypatch):
    fake = [
        {"id": "pb-one", "name": "One", "description": "d1", "prompt": "Read {{LIBRARY}} on {{DATE}}"},
        {"id": "pb-two", "name": "Two", "prompt": "no placeholders"},
        {"id": "", "prompt": "skipped: no id"},
    ]
    monkeypatch.setattr(pm.agent_tasks, "load_tasks", lambda root: fake)
    m = FastMCP("t")
    pm.register_prompt_tools(m, allow=None)
    got = {p.name: p for p in m._prompt_manager.list_prompts()}
    assert set(got) == {"pb-one", "pb-two"}  # empty-id row skipped
    assert [a.name for a in got["pb-one"].arguments] == ["library", "date"]
    assert all(a.required is False for a in got["pb-one"].arguments)
    assert got["pb-two"].arguments == []


def test_profile_allow_gets_no_prompts(monkeypatch):
    """Playbook ids aren't tool names, so a filtered profile gets no prompts."""
    monkeypatch.setattr(pm.agent_tasks, "load_tasks", lambda root: [{"id": "pb", "prompt": "x"}])
    m = FastMCP("t")
    pm.register_prompt_tools(m, allow={"web_search"})
    assert m._prompt_manager.list_prompts() == []
