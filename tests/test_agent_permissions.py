"""Agent tool-permission levels → Claude Code --disallowedTools list."""
from core import agent_permissions as ap

LIVE = ["jellyfin_search", "docker_stop_container", "obsidian_write_note",
        "ssh_run", "fs_write_file", "sonarr_queue"]


def test_normalize_level():
    assert ap.normalize_level("safe") == "safe"
    assert ap.normalize_level("all") == "all"
    assert ap.normalize_level("bogus") == "safe"        # default
    assert ap.normalize_level(None) == "safe"


def test_all_blocks_nothing():
    assert ap.build_disallowed(LIVE, "all") == []


def test_safe_blocks_dangerous_but_allows_note_writing():
    dis = ap.build_disallowed(LIVE, "safe")
    assert "mcp__plutus__docker_stop_container" in dis
    assert "mcp__plutus__ssh_run" in dis
    # note-writing stays available so playbooks can persist to the library
    assert "mcp__plutus__obsidian_write_note" not in dis
    assert "mcp__plutus__fs_write_file" not in dis
    # pure reads never blocked
    assert "mcp__plutus__jellyfin_search" not in dis


def test_strict_read_blocks_all_writes():
    dis = ap.build_disallowed(LIVE, "strict_read")
    assert "mcp__plutus__obsidian_write_note" in dis
    assert "mcp__plutus__fs_write_file" in dis
    assert "mcp__plutus__docker_stop_container" in dis
    assert "mcp__plutus__jellyfin_search" not in dis


def test_only_live_tools_included():
    # a blocked tool that isn't registered shouldn't appear
    dis = ap.build_disallowed(["jellyfin_search"], "safe")
    assert dis == []
