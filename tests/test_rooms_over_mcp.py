"""Rooms reachable from the MCP router, room chaining reachable from the API,
and the first-run password.

The chain was the motivating bug: core.workforce has honoured ``next_room``
since the handoff landed, but nothing could set it — RoomPatch dropped the field
and the UI had no control — so the pipeline feature existed only in tests.
"""
from __future__ import annotations

import asyncio

import pytest

from core import env_store, workforce


# ── the chain is settable, not just runnable ─────────────────────────────────

def test_update_room_accepts_next_room(tmp_path):
    a = workforce.add_room(tmp_path, "Research")
    b = workforce.add_room(tmp_path, "Write")
    workforce.update_room(tmp_path, a["id"], {"next_room": b["id"]})
    assert workforce.get_room(tmp_path, a["id"])["next_room"] == b["id"]


def test_room_patch_model_carries_next_room():
    """The API schema is the half that was missing — guard it directly."""
    from ui.api.workforce import RoomPatch

    assert "next_room" in RoomPatch.model_fields
    assert RoomPatch(next_room="abc").model_dump(exclude_none=True) == {"next_room": "abc"}
    # Omitted stays omitted, so a label-only edit cannot clear an existing chain.
    assert "next_room" not in RoomPatch(label="x").model_dump(exclude_none=True)


# ── the MCP surface ──────────────────────────────────────────────────────────

def _tool_names():
    from ui.runtime import mcp
    return {t.name for t in asyncio.run(mcp.list_tools())}


def test_room_tools_are_served():
    served = _tool_names()
    assert {"room_list", "room_create", "room_update", "room_delete", "room_add_seat",
            "room_remove_seat", "room_run", "room_result"} <= served


def test_only_reads_are_annotated_read_only():
    """The write switch gates rooms, so the annotations have to be right."""
    from ui.runtime import tools

    ann = {t.name: t.annotations for t in tools.raw_manager.list_tools()
           if t.name.startswith("room_")}
    read_only = {n for n, a in ann.items() if a and a.readOnlyHint}
    assert read_only == {"room_list", "room_result"}


def test_write_switch_blocks_mutating_room_tools():
    from ui.runtime import _agent_capability_disallow

    blocked = set(_agent_capability_disallow(allow_write=False, allow_publish=False))
    assert "mcp__plutus__room_create" in blocked
    assert "mcp__plutus__room_run" in blocked
    assert "mcp__plutus__room_list" not in blocked


def _room_tools(monkeypatch, root):
    """Register the room tools against a stub FastMCP rooted at ``root``.

    Returns ``call(name, **fields)``. The Pydantic input models are defined
    inside the registrar (one closure per registration), so the model is taken
    from each function's own annotation rather than a module-level name.
    """
    import tools.rooms as R

    monkeypatch.setattr(R, "_ROOT", root)
    monkeypatch.setattr("core.profiles.tool_filter", lambda m, allow: m)
    captured: dict = {}

    class _Fake:
        def tool(self, **kw):
            def deco(fn):
                captured[kw["name"]] = fn
                return fn
            return deco

    R.register_room_tools(_Fake())

    def call(name, **fields):
        fn = captured[name]
        params = fn.__annotations__.get("params")
        if params is None:                      # room_list takes no arguments
            return asyncio.run(fn())
        return asyncio.run(fn(params.model_validate(fields)))

    return call


def test_room_run_refuses_from_inside_an_agent_run(monkeypatch, tmp_path):
    """A seat that could start its own room would never stop, and a room started
    mid-run queues behind its own caller. Both are refused, with a reason."""
    from core import agent_runner

    call = _room_tools(monkeypatch, tmp_path)
    room = workforce.add_room(tmp_path, "Loop")
    workforce.add_seat(tmp_path, room["id"], role="researcher",
                       provider="claude", account_id="acct")
    monkeypatch.setattr(agent_runner, "busy", lambda: True)

    out = call("room_run", room_id=room["id"])
    assert "Error" in out and "agent run is in flight" in out


def test_room_run_refuses_an_empty_room(monkeypatch, tmp_path):
    call = _room_tools(monkeypatch, tmp_path)
    room = workforce.add_room(tmp_path, "Nobody")
    assert "no agents in it" in call("room_run", room_id=room["id"])


def test_room_tools_reject_unknown_ids(monkeypatch, tmp_path):
    call = _room_tools(monkeypatch, tmp_path)

    assert "no room with id" in call("room_update", room_id="nope", label="x")

    room = workforce.add_room(tmp_path, "Solo")
    assert "cannot hand off to itself" in call(
        "room_update", room_id=room["id"], next_room=room["id"])
    assert "no room 'ghost'" in call(
        "room_update", room_id=room["id"], next_room="ghost")


def test_building_a_room_through_mcp_round_trips(monkeypatch, tmp_path):
    """The whole point of the ask: build a room end to end over the router."""
    from core import ai_providers

    call = _room_tools(monkeypatch, tmp_path)
    monkeypatch.setattr(ai_providers, "get_account",
                        lambda root, p, a: {"id": a, "label": a})

    assert "Created" in call("room_create", label="Research", brief="find things")
    assert "Created" in call("room_create", label="Write")
    rooms = workforce.load_rooms(tmp_path)
    research = next(r for r in rooms if r["label"] == "Research")
    write = next(r for r in rooms if r["label"] == "Write")

    out = call("room_add_seat", room_id=research["id"], provider="claude",
               account_id="acct", role="researcher", goal="gather sources")
    assert "Seat added" in out

    call("room_update", room_id=research["id"], next_room=write["id"])
    stored = workforce.get_room(tmp_path, research["id"])
    assert stored["next_room"] == write["id"]
    assert stored["brief"] == "find things"
    assert len(stored["seats"]) == 1

    listing = call("room_list")
    assert "Research" in listing and "hands off to" in listing


# ── first-run password ───────────────────────────────────────────────────────

def test_first_run_generates_and_persists_a_password(tmp_path, monkeypatch):
    monkeypatch.delenv("UI_PASSWORD", raising=False)
    env = tmp_path / ".env"
    pw, generated = env_store.ensure_ui_password(env)
    assert generated and len(pw) >= 12
    assert env_store.ui_password_persisted(env)
    # Stable across boots: the second call reuses what it wrote.
    again, generated_again = env_store.ensure_ui_password(env)
    assert again == pw and not generated_again


def test_no_shared_default_password_remains():
    """The old constant was a published credential for a dashboard that reaches
    Docker, SSH and the filesystem."""
    import config

    # The constant is gone, and nothing falls back to it. Checked as a value
    # rather than by grepping the source, which only found the comment that
    # explains why it was removed.
    assert not hasattr(config, "DEFAULT_UI_PASSWORD")
    assert config.cfg.ui_password != "adminadmin"


# ── the slicer can now reach everything ──────────────────────────────────────

def test_every_served_tool_belongs_to_a_category():
    """An uncategorised tool cannot be sliced off, so it rides along in every
    agent prompt. 35 of them (GitHub, GitLab, YouTube, HuggingFace) were ~16% of
    the tool manifest and could not be switched off at all."""
    from core.profiles import infer_tool_categories

    orphans = sorted(n for n in _tool_names() if not infer_tool_categories(n))
    assert orphans == [], orphans


@pytest.mark.parametrize("name,category", [
    ("github_create_pull", "code"),
    ("gitlab_list_issues", "code"),
    ("youtube_search", "media"),
    ("huggingface_search_models", "ai"),
    ("room_run", "meta"),
])
def test_new_categories(name, category):
    from core.profiles import infer_tool_categories

    assert category in infer_tool_categories(name)
