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


# ── the research room setup script ───────────────────────────────────────────

def _fake_accounts(root, providers: dict):
    import json
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "data" / "ai_accounts.json").write_text(json.dumps(providers), encoding="utf-8")


def _run_setup(root, monkeypatch, argv=("setup",)):
    import sys

    import scripts.setup_research_room as S

    monkeypatch.setattr(S, "ROOT", root)
    monkeypatch.setattr(sys, "argv", list(argv))
    return S.main()


def test_setup_refuses_without_a_provider_account(tmp_path, monkeypatch, capsys):
    _fake_accounts(tmp_path, {})
    assert _run_setup(tmp_path, monkeypatch) == 1
    assert "No provider accounts" in capsys.readouterr().out


def test_setup_spreads_seats_across_every_account(tmp_path, monkeypatch):
    """Six seats on one account burns that account's limit six times a night."""
    _fake_accounts(tmp_path, {
        "claude": [{"id": "a1", "label": "one"}, {"id": "a2", "label": "two"}],
        "gemini": [{"id": "g1", "label": "three"}],
    })
    assert _run_setup(tmp_path, monkeypatch) == 0

    room = workforce.load_rooms(tmp_path)[0]
    used = {f"{s['provider']}/{s['account_id']}" for s in room["seats"]}
    assert len(room["seats"]) == 6
    assert len(used) == 3, f"seats did not spread across accounts: {used}"


def test_setup_is_idempotent(tmp_path, monkeypatch):
    _fake_accounts(tmp_path, {"claude": [{"id": "a1", "label": "one"}]})
    _run_setup(tmp_path, monkeypatch)
    _run_setup(tmp_path, monkeypatch)
    assert len(workforce.load_rooms(tmp_path)) == 1
    assert len(schedule_store.load_schedules(tmp_path)) == 1
    assert len(workforce.load_rooms(tmp_path)[0]["seats"]) == 6


def test_setup_schedules_the_room_nightly(tmp_path, monkeypatch):
    _fake_accounts(tmp_path, {"claude": [{"id": "a1", "label": "one"}]})
    _run_setup(tmp_path, monkeypatch)

    sc = schedule_store.load_schedules(tmp_path)[0]
    room = workforce.load_rooms(tmp_path)[0]
    assert sc["kind"] == "room"
    assert sc["payload"]["room_id"] == room["id"]
    assert sc["enabled"] is True


def test_the_room_gets_research_tools_and_no_homelab_control(tmp_path, monkeypatch):
    """The connection list is also the tool slice. This team reads and writes
    files; it has no business restarting containers or opening SSH."""
    _fake_accounts(tmp_path, {"claude": [{"id": "a1", "label": "one"}]})
    _run_setup(tmp_path, monkeypatch)

    services = set(workforce.load_rooms(tmp_path)[0]["mcp_services"])
    assert {"websearch", "github", "huggingface", "comfyui", "agent_db"} <= services
    assert not (services & {"docker", "ssh", "omv", "homeassistant", "tailscale"})


def test_every_connection_the_room_asks_for_actually_exists(tmp_path, monkeypatch):
    """A typo'd connection id grants nothing silently — the ACL denies by id."""
    from ui.runtime import _services_live

    _fake_accounts(tmp_path, {"claude": [{"id": "a1", "label": "one"}]})
    _run_setup(tmp_path, monkeypatch)

    known = {s["id"] for s in _services_live()}
    asked = set(workforce.load_rooms(tmp_path)[0]["mcp_services"])
    assert asked <= known, f"unknown connection ids: {sorted(asked - known)}"


def test_the_brief_pins_output_to_a_dated_folder(tmp_path, monkeypatch):
    """Without this every night overwrites the last one — the room's working
    folder is fixed per room, not per run."""
    _fake_accounts(tmp_path, {"claude": [{"id": "a1", "label": "one"}]})
    _run_setup(tmp_path, monkeypatch)

    brief = workforce.load_rooms(tmp_path)[0]["brief"]
    assert "TODAY'S DATE" in brief
    assert "dashboard.html" in brief and "scripts/" in brief


def test_the_api_accepts_every_kind_the_store_does():
    """The store and the scheduler both understood a nightly room while the HTTP
    model still rejected one with a 422, so the feature was reachable only by
    writing the file directly."""
    import re

    from ui.api.agents import ScheduleBody

    pattern = next(m.pattern for m in ScheduleBody.model_fields["kind"].metadata
                   if hasattr(m, "pattern"))
    for kind in schedule_store.VALID_KINDS:
        assert re.match(pattern, kind), f"the API rejects kind {kind!r}"


def test_a_room_schedule_round_trips_through_the_api_model():
    from ui.api.agents import ScheduleBody

    body = ScheduleBody(kind="room", cron="0 3 * * *", payload={"room_id": "abc"})
    assert body.kind == "room"
    assert schedule_store._normalize(body.model_dump())["payload"]["room_id"] == "abc"
