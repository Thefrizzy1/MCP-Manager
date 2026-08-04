"""Pre-made rooms.

What matters here is not that a preset exists but that installing one produces a
floor that *runs*: every seat staffed, every room chained in the declared order,
and the connection list actually narrowed. A template that creates three empty
rooms is worse than no template, because it looks like it worked.
"""
from __future__ import annotations

import pytest

from core import room_presets, workforce


def test_installing_a_pipeline_chains_it_in_order(tmp_path):
    rooms = room_presets.install(tmp_path, "research_pipeline",
                                 provider="openrouter", account_id="acct-1")

    assert [r["label"] for r in rooms] == ["Research", "Office", "Publishing"]
    # The chain is the point: without next_room these are three unrelated rooms.
    assert rooms[0]["next_room"] == rooms[1]["id"]
    assert rooms[1]["next_room"] == rooms[2]["id"]
    assert not rooms[2].get("next_room")


def test_every_seat_is_staffed_and_briefed(tmp_path):
    """An unstaffed seat cannot run, and a seat with no goal produces the same
    generic summary as the seat before it."""
    rooms = room_presets.install(tmp_path, "research_pipeline",
                                 provider="gemini", account_id="acct-9")

    for room in rooms:
        assert room["brief"].strip(), room["label"]
        assert room["mcp_services"], room["label"]
        assert room["seats"], room["label"]
        for seat in room["seats"]:
            assert seat["provider"] == "gemini"
            assert seat["account_id"] == "acct-9"
            assert seat["goal"].strip(), f"{room['label']}/{seat['label']}"
            assert seat["role"] in workforce.ROLES


def test_installing_twice_gives_independent_rooms(tmp_path):
    """Trying a template out is the action most likely to be repeated, so a
    duplicate label must not be an error."""
    first = room_presets.install(tmp_path, "watchtower", provider="p", account_id="a")
    second = room_presets.install(tmp_path, "watchtower", provider="p", account_id="a")

    assert first[0]["label"] == "Watchtower"
    assert second[0]["label"] == "Watchtower 2"
    assert first[0]["id"] != second[0]["id"]
    assert len(workforce.load_rooms(tmp_path)) == 2


def test_a_preset_needs_an_account_to_staff_with(tmp_path):
    with pytest.raises(ValueError):
        room_presets.install(tmp_path, "watchtower", provider="", account_id="")
    with pytest.raises(KeyError):
        room_presets.install(tmp_path, "no-such-preset", provider="p", account_id="a")
    assert workforce.load_rooms(tmp_path) == []


def test_every_declared_preset_installs(tmp_path):
    """The catalogue is hand-written, so a typo'd role or an unknown colour would
    otherwise only surface when a user clicked that one template."""
    for pid in room_presets.PRESETS:
        rooms = room_presets.install(tmp_path, pid, provider="p", account_id="a")
        assert rooms
        for room in rooms:
            assert room["colour"] in room_presets.COLOURS


def test_the_public_list_leaves_the_briefs_behind(tmp_path):
    """The briefs run to hundreds of words each and the Rooms page polls every
    few seconds; a picker needs the shape, not the prompt."""
    public = room_presets.public_presets()
    assert {p["id"] for p in public} == set(room_presets.PRESETS)
    for p in public:
        assert p["label"] and p["description"]
        assert all(r["seats"] > 0 and r["colour"] in room_presets.COLOURS for r in p["rooms"])
    assert "brief" not in str(public)


def test_a_colour_outside_the_palette_falls_back(tmp_path):
    """Free-form colours would let a room be invisible on one of the two themes."""
    assert room_presets.valid_colour("indigo") == "indigo"
    for junk in ("#ff0000", "", "chartreuse", "  TEAL  "):
        expected = "teal" if junk.strip().lower() == "teal" else room_presets.DEFAULT_COLOUR
        assert room_presets.valid_colour(junk) == expected

    room = workforce.add_room(tmp_path, "Manual")
    assert room["colour"] == room_presets.DEFAULT_COLOUR
    workforce.update_room(tmp_path, room["id"], {"colour": "not-a-colour"})
    assert workforce.get_room(tmp_path, room["id"])["colour"] == room_presets.DEFAULT_COLOUR
    workforce.update_room(tmp_path, room["id"], {"colour": "rose"})
    assert workforce.get_room(tmp_path, room["id"])["colour"] == "rose"


def test_rooms_saved_before_colours_existed_still_load(tmp_path):
    import json

    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "workforce.json").write_text(json.dumps({"_v": 1, "rooms": [
        {"id": "old-1", "label": "Old", "brief": "", "mcp_services": [], "seats": []},
    ]}), encoding="utf-8")

    assert workforce.load_rooms(tmp_path)[0]["colour"] == room_presets.DEFAULT_COLOUR
