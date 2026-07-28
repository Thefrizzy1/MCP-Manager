"""Agent ↔ MCP profile ACL: selecting a profile limits the agent to that subset.

Profiles feed the agent wizard by translating to a disallowed-tool set at enqueue
time (works at the full /mcp, so a freshly-created profile applies immediately).
"""
import ui.runtime as rt


def test_empty_or_unknown_profile_means_no_restriction(monkeypatch):
    assert rt._agent_profile_disallow("") == []
    assert rt._agent_profile_disallow(None) == []
    monkeypatch.setattr(rt, "load_profiles", lambda root: [])
    assert rt._agent_profile_disallow("ghost") == []


def test_profile_restricts_to_its_tools(monkeypatch):
    prof = {
        "name": "wx",
        "label": "",
        "intent": "",
        "sections": [],
        "include_tools": ["weather_current", "weather_forecast"],
        "exclude_tools": [],
    }
    monkeypatch.setattr(rt, "load_profiles", lambda root: [prof])
    disallow = set(rt._agent_profile_disallow("wx"))

    allowed = set(rt.resolve_tool_names(prof, rt.all_tool_names()))
    assert allowed, "profile should resolve to a non-empty tool set"
    # The profile's own tools are never disallowed…
    for t in allowed:
        assert f"mcp__plutus__{t}" not in disallow
    # …and an unrelated tool (not in the profile) is denied.
    assert "mcp__plutus__jellyfin_search" in disallow
