"""The agent database: a writable destination that no configuration can remove.

Every other place an agent can save output depends on something that can be wrong
— Nextcloud credentials, a CalDAV permission, an Obsidian path, a filesystem
allow-list, or the tool slicer. When all of those failed at once, an agent spent an
hour researching and then reported "I don't have tools to write files", and the
work was lost. This is the floor beneath them.
"""
from __future__ import annotations

import pytest

from core import agent_db as DB


def test_a_note_survives_the_write_and_is_read_back(tmp_path):
    rec = DB.write_note(tmp_path, "Findings", "widgets are blue", ["research", "widgets"])
    assert rec["id"] >= 1
    assert rec["title"] == "Findings"
    assert rec["tags"] == ["research", "widgets"]

    # The point of the read-back: "saved" is a fact, not an assumption.
    again = DB.read_note(tmp_path, rec["id"])
    assert again["body"] == "widgets are blue"


def test_a_note_needs_a_title(tmp_path):
    with pytest.raises(ValueError, match="title"):
        DB.write_note(tmp_path, "   ")


def test_updating_keeps_the_id_and_changes_the_body(tmp_path):
    first = DB.write_note(tmp_path, "Draft", "v1")
    second = DB.write_note(tmp_path, "Draft", "v2", note_id=first["id"])
    assert second["id"] == first["id"]
    assert DB.read_note(tmp_path, first["id"])["body"] == "v2"
    assert len(DB.list_notes(tmp_path)) == 1


def test_updating_a_missing_note_is_an_error_not_a_silent_insert(tmp_path):
    with pytest.raises(KeyError):
        DB.write_note(tmp_path, "Ghost", "x", note_id=9999)


def test_search_covers_title_body_and_tags(tmp_path):
    DB.write_note(tmp_path, "Blue widgets", "all about them", ["hardware"])
    DB.write_note(tmp_path, "Red herrings", "nothing here", ["misc"])

    assert [r["title"] for r in DB.search_notes(tmp_path, "blue")] == ["Blue widgets"]
    assert [r["title"] for r in DB.search_notes(tmp_path, "hardware")] == ["Blue widgets"]
    assert [r["title"] for r in DB.search_notes(tmp_path, "nothing")] == ["Red herrings"]
    assert DB.search_notes(tmp_path, "zzz") == []


def test_delete_confirms_the_row_is_gone(tmp_path):
    rec = DB.write_note(tmp_path, "Temp", "x")
    assert DB.delete_note(tmp_path, rec["id"]) is True
    assert DB.read_note(tmp_path, rec["id"]) is None
    assert DB.delete_note(tmp_path, rec["id"]) is False


def test_listing_is_newest_first(tmp_path):
    for i in range(3):
        DB.write_note(tmp_path, f"note {i}")
    titles = [r["title"] for r in DB.list_notes(tmp_path)]
    assert titles[0] == "note 2"


def test_stats_report_a_real_file(tmp_path):
    DB.write_note(tmp_path, "One", "body")
    s = DB.stats(tmp_path)
    assert s["notes"] == 1 and s["size_bytes"] > 0
    assert s["path"].endswith(DB.DB_FILE)


def test_the_store_is_created_on_first_use(tmp_path):
    """No setup step: an agent must be able to save on a fresh install."""
    assert DB.list_notes(tmp_path) == []
    DB.write_note(tmp_path, "First ever")
    assert DB.db_path(tmp_path).exists()


# ── the guarantee that matters ───────────────────────────────────────────────

def test_db_tools_cannot_be_removed_by_the_slicer():
    """This is the whole reason the DB exists. With every category disabled the
    served surface collapsed to 21 tools and nothing could write anywhere."""
    from core.profiles import ALWAYS_EXPOSED, TOOL_CATEGORIES
    from core.tool_exposure import is_tool_exposed

    every_category_off = set(TOOL_CATEGORIES)
    for name in ("db_write_note", "db_read_note", "db_search_notes",
                 "db_list_notes", "db_delete_note", "db_status"):
        assert name in ALWAYS_EXPOSED
        assert is_tool_exposed(name, every_category_off, set()) is True
        # Not even an explicit per-tool switch may remove the last writable store.
        assert is_tool_exposed(name, set(), {name}) is True


def test_a_collapsed_surface_is_warned_about(tmp_path):
    """The failure was invisible: the manifest shrank silently and only an agent,
    mid-task, found out it had no tools."""
    from core import tool_exposure as TE
    from core.profiles import TOOL_CATEGORIES

    names = [f"jellyfin_{i}" for i in range(50)] + ["db_write_note"]
    assert TE.exposure_warning(tmp_path, names) == ""      # nothing disabled yet

    TE.save_exposure(tmp_path, sorted(TOOL_CATEGORIES))
    warning = TE.exposure_warning(tmp_path, names)
    assert "Only 1 of 51 tools" in warning
    assert "no tools" in warning
