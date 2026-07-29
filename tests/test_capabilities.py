"""Fine-grained functional capability grouping + read/write scope."""
from pathlib import Path

from core.capabilities import build_capability_registry, capability_for_tool, scope_for_tool


def test_functional_grouping_splits_nextcloud():
    # The coarse grouping lumped all of Nextcloud into "cloud"; functional
    # grouping must separate calendar / tasks / notes / contacts / files.
    assert capability_for_tool("nextcloud_list_calendars") == "calendar"
    assert capability_for_tool("nextcloud_add_event") == "calendar"
    assert capability_for_tool("nextcloud_get_tasks") == "tasks"
    assert capability_for_tool("nextcloud_create_note") == "notes"
    assert capability_for_tool("nextcloud_list_contacts") == "contacts"
    assert capability_for_tool("nextcloud_list_files") == "files"


def test_scope_read_vs_write():
    assert scope_for_tool("nextcloud_list_calendars") == "read"
    assert scope_for_tool("fs_read_file") == "read"
    assert scope_for_tool("nextcloud_add_event") == "write"
    assert scope_for_tool("fs_write_file") == "write"
    assert scope_for_tool("send_email") == "write"


def test_registry_has_scope_counts_and_calendar_capability():
    tools = [
        "nextcloud_list_calendars", "nextcloud_add_event", "nextcloud_delete_event",
        "nextcloud_get_tasks", "fs_read_file", "fs_write_file", "youtube_search",
    ]
    reg = build_capability_registry(Path("."), tools, service_by_tool={}, include_tools=True)
    caps = {c["name"]: c for c in reg["capabilities"]}
    assert "calendar" in caps and "cloud" not in caps  # functional, not coarse
    cal = caps["calendar"]
    assert cal["read"] == 1 and cal["write"] == 2      # list=read, add/delete=write
    # per-tool scope is exposed for the router
    scopes = {t["name"]: t["scope"] for t in cal["tools"]}
    assert scopes["nextcloud_list_calendars"] == "read"
    assert scopes["nextcloud_add_event"] == "write"


def test_unknown_tool_falls_back():
    assert capability_for_tool("totally_unknown_tool", "someservice") == "someservice"
    assert capability_for_tool("totally_unknown_tool") == "misc"
