"""A seat can redirect the seats after it.

Before this, a seat that discovered the brief was wrong could only say so in its
output, where it arrived as one more paragraph of material rather than as an
instruction to do something different.
"""
from __future__ import annotations

import asyncio

import pytest

from core import workforce


def _room_with_seats(root, n=3):
    room = workforce.add_room(root, "Pipeline")
    for i in range(n):
        workforce.add_seat(root, room["id"], role="researcher", provider="claude",
                           account_id="acct", label=f"Seat {i + 1}")
    return workforce.get_room(root, room["id"])


def test_advice_is_stored_and_read_back(tmp_path):
    items = workforce.add_advice(tmp_path, "run-1", "use polars, not pandas",
                                 author="Researcher")
    assert len(items) == 1
    assert workforce.load_advice(tmp_path, "run-1")[0]["note"] == "use polars, not pandas"


def test_advice_is_per_run(tmp_path):
    workforce.add_advice(tmp_path, "run-1", "a")
    assert workforce.load_advice(tmp_path, "run-2") == []


def test_empty_advice_is_refused(tmp_path):
    with pytest.raises(ValueError):
        workforce.add_advice(tmp_path, "run-1", "   ")


def test_advice_is_capped(tmp_path):
    """A seat stuck in a loop must not crowd the brief out of the prompt."""
    for i in range(workforce.MAX_ADVICE):
        workforce.add_advice(tmp_path, "run-1", f"note {i}")
    with pytest.raises(ValueError, match="limit"):
        workforce.add_advice(tmp_path, "run-1", "one too many")


def test_a_traversal_run_id_reads_nothing(tmp_path):
    assert workforce.load_advice(tmp_path, "../../etc/passwd") == []
    assert workforce.load_advice(tmp_path, "") == []


def test_advice_lands_above_the_brief_not_inside_the_material(tmp_path):
    room = _room_with_seats(tmp_path)
    prompt = workforce.render_seat_prompt(
        room, room["seats"][1], "the original brief", [],
        advice=[{"author": "Researcher", "note": "the library was deprecated"}])

    assert "Redirection from earlier seats" in prompt
    assert "the library was deprecated" in prompt
    assert prompt.index("Redirection") > prompt.index("Room brief"), \
        "advice should follow the brief it qualifies"
    assert "override the brief" in prompt


def test_no_advice_adds_no_section(tmp_path):
    room = _room_with_seats(tmp_path)
    assert "Redirection" not in workforce.render_seat_prompt(
        room, room["seats"][0], "brief", [], advice=[])


def test_advice_reaches_later_seats_only(tmp_path):
    """The seat that writes it has already run; the ones before it are finished."""
    room = _room_with_seats(tmp_path, 3)
    seen: list[str] = []

    def fake_run(root, prompt, **kw):
        seen.append(prompt)
        # The first seat discovers the brief is wrong and says so.
        if len(seen) == 1:
            workforce.add_advice(tmp_path, workforce.LIVE["run_id"],
                                 "skip the API, it is retired", author="Seat 1")
        return {"ok": True, "cost_usd": 0.0, "result": "done", "id": f"r{len(seen)}"}

    rec = workforce.run_room(tmp_path, room["id"], "original brief", run_agent=fake_run)

    assert rec["ok"], rec.get("error")
    assert len(seen) == 3
    assert "skip the API" not in seen[0], "the seat that wrote it should not be re-briefed"
    assert "skip the API" in seen[1]
    assert "skip the API" in seen[2]


def test_live_run_is_visible_across_processes(tmp_path):
    """The tool is served by the MCP process while the room may run in the UI
    process — a module global would be written in one and read in the other."""
    room = _room_with_seats(tmp_path, 1)
    captured = {}

    def fake_run(root, prompt, **kw):
        captured.update(workforce.read_live(tmp_path))
        return {"ok": True, "cost_usd": 0.0, "result": "ok", "id": "r1"}

    rec = workforce.run_room(tmp_path, room["id"], "brief", run_agent=fake_run)
    assert captured.get("run_id") == rec["id"]
    assert captured.get("running") is True
    # And it is cleared once the room finishes.
    assert workforce.read_live(tmp_path)["running"] is False


# ── the tool ─────────────────────────────────────────────────────────────────

def _tools(monkeypatch, root):
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
        return asyncio.run(fn(params.model_validate(fields)) if params else fn())

    return call


def test_room_advise_outside_a_room_says_so(monkeypatch, tmp_path):
    call = _tools(monkeypatch, tmp_path)
    out = call("room_advise", note="do it differently")
    assert "Error" in out and "no room run to advise" in out


def test_room_advise_ignores_a_finished_run(monkeypatch, tmp_path):
    """run_id outlives the run. Falling back to it would let a single agent
    leave advice for a room that stopped hours ago."""
    call = _tools(monkeypatch, tmp_path)
    workforce.LIVE.update(room_id="r", run_id="old-1", seat_id="", running=False)
    workforce.publish_live(tmp_path)
    assert "no room run to advise" in call("room_advise", note="too late")


def test_room_advise_targets_the_live_run(monkeypatch, tmp_path):
    call = _tools(monkeypatch, tmp_path)
    workforce.LIVE.update(room_id="r", run_id="live-1", seat_id="s", running=True)
    workforce.publish_live(tmp_path)
    try:
        out = call("room_advise", note="use the other dataset")
        assert "Recorded and read back" in out
        assert workforce.load_advice(tmp_path, "live-1")[0]["note"] == "use the other dataset"
    finally:
        workforce.LIVE.update(running=False, run_id="", room_id="", seat_id="")
        workforce.publish_live(tmp_path)


def test_room_advise_rejects_an_unknown_run(monkeypatch, tmp_path):
    call = _tools(monkeypatch, tmp_path)
    assert "no room run 'ghost'" in call("room_advise", note="x", run_id="ghost")
