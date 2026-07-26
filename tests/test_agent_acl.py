"""The agent ACL derived from annotations must never block *less* than the old
hand-maintained DANGEROUS/WRITE lists (workstream B4).

strict_read blocks every non-read tool; safe blocks destructive tools; both keep
the curated sets as a safety-net override. These tests pin the superset property
and the two behaviours that matter: research reads stay available, and
note-writing stays available under 'safe'.
"""
from __future__ import annotations

from core import agent_permissions as ap
from ui.runtime import tools


def test_all_level_blocks_nothing():
    assert ap.build_disallowed_from_annotations(tools.raw_manager, "all") == []


def test_strict_read_is_superset_of_hardcoded():
    old = set(ap.build_disallowed(tools.tool_names(), "strict_read"))
    new = set(ap.build_disallowed_from_annotations(tools.raw_manager, "strict_read"))
    assert old <= new


def test_safe_is_superset_of_hardcoded():
    old = set(ap.build_disallowed(tools.tool_names(), "safe"))
    new = set(ap.build_disallowed_from_annotations(tools.raw_manager, "safe"))
    assert old <= new


def test_safe_allows_note_writing():
    """Research playbooks must keep note-writing available under 'safe'."""
    new = set(ap.build_disallowed_from_annotations(tools.raw_manager, "safe"))
    for note_tool in ("obsidian_write_note", "fs_write_file", "nextcloud_create_note"):
        assert f"mcp__plutus__{note_tool}" not in new


def test_read_only_web_tools_allowed_in_both_levels():
    strict = set(ap.build_disallowed_from_annotations(tools.raw_manager, "strict_read"))
    safe = set(ap.build_disallowed_from_annotations(tools.raw_manager, "safe"))
    assert "mcp__plutus__web_search" not in strict
    assert "mcp__plutus__web_search" not in safe
