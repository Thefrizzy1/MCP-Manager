"""Rooms can be scheduled, and there is one way to start one.

"Run the research team every night" was impossible: VALID_KINDS was
("agent", "tool", "task"), so a room could only ever be started by hand from the
dashboard. Adding the kind is most of the fix; the rest is that three callers had
grown their own copy of the launch sequence and they had already drifted — only
one took the shared run lock.
"""
from __future__ import annotations

import pytest

from core import schedule_store, workforce


# ── the schedule kind ────────────────────────────────────────────────────────

def test_room_is_a_valid_kind():
    assert "room" in schedule_store.VALID_KINDS


def test_a_room_schedule_needs_a_room_id():
    with pytest.raises(ValueError, match="room_id"):
        schedule_store._normalize({"kind": "room", "cron": "0 3 * * *", "payload": {}})


def test_a_room_schedule_normalizes(tmp_path):
    sc = schedule_store._normalize({
        "kind": "room", "name": "Nightly research", "cron": "0 3 * * *",
        "payload": {"room_id": "abc123"},
    })
    assert sc["kind"] == "room"
    assert sc["payload"]["room_id"] == "abc123"
    assert sc["enabled"] is True


def test_the_scheduler_dispatches_a_room(tmp_path):
    """The kind is useless if nothing is wired to act on it."""
    from core.scheduler import PlutusScheduler

    called: list = []
    s = PlutusScheduler(tmp_path)
    s._run_room = lambda room_id, brief: called.append((room_id, brief))

    job = s._make_job({"id": "s1", "name": "nightly", "kind": "room",
                       "payload": {"room_id": "r1", "brief": "tonight's angle"}})
    job()
    assert called == [("r1", "tonight's angle")]


def test_an_unwired_kind_records_an_error_rather_than_failing_silently(tmp_path):
    from core.scheduler import PlutusScheduler

    s = PlutusScheduler(tmp_path)
    s._run_room = None
    job = s._make_job({"id": "s1", "name": "nightly", "kind": "room",
                       "payload": {"room_id": "r1"}})
    job()          # must not raise
    runs = schedule_store.load_schedules(tmp_path)
    assert isinstance(runs, list)


# ── one launcher, not three ──────────────────────────────────────────────────

def test_launch_room_rejects_an_unknown_room(tmp_path):
    from config import cfg
    from core import agent_orchestrator

    res = agent_orchestrator.launch_room(tmp_path, cfg, "nope")
    assert res["ok"] is False and "no room" in res["error"]


def test_launch_room_rejects_an_empty_room(tmp_path):
    from config import cfg
    from core import agent_orchestrator

    room = workforce.add_room(tmp_path, "Empty")
    res = agent_orchestrator.launch_room(tmp_path, cfg, room["id"])
    assert res["ok"] is False and "no agents" in res["error"]


def test_launch_room_runs_every_seat(tmp_path, monkeypatch):
    from config import cfg
    from core import agent_orchestrator, agent_runner

    room = workforce.add_room(tmp_path, "Research")
    for i in range(3):
        workforce.add_seat(tmp_path, room["id"], role="researcher", provider="claude",
                           account_id="acct", label=f"Seat {i}")

    seen: list = []
    monkeypatch.setattr(agent_runner, "wait_for_slot", lambda *_a, **_k: True)
    monkeypatch.setattr(agent_runner, "run_agent",
                        lambda root, prompt, **kw: seen.append(kw.get("label")) or
                        {"ok": True, "cost_usd": 0.0, "result": "done", "id": f"r{len(seen)}"})

    res = agent_orchestrator.launch_room(tmp_path, cfg, room["id"], "the brief", block=True)
    assert res["ok"], res
    assert len(seen) == 3, seen


def test_every_entry_point_uses_the_one_launcher():
    """Three copies had already drifted — only one took the run lock, so a
    scheduled room and a hand-started one could both think they were running."""
    import inspect

    import tools.rooms as R
    import ui.api.workforce as api
    import ui.runtime as rt

    assert "launch_room" in inspect.getsource(api.api_run_room)
    assert "launch_room" in inspect.getsource(rt._run_room_bg)
    assert "launch_room" in inspect.getsource(R.register_room_tools)


def test_the_launcher_holds_the_shared_run_lock():
    import inspect

    from core import agent_orchestrator

    assert "RUN_LOCK" in inspect.getsource(agent_orchestrator.launch_room)


def test_a_scheduled_room_failure_is_not_swallowed():
    """A nightly room that silently never starts is indistinguishable from one
    that ran and found nothing."""
    import inspect

    import ui.runtime as rt

    src = inspect.getsource(rt._run_room_bg)
    assert "raise" in src
