"""Rooms: a team of agents running in order on a shared brief.

The properties that matter, and why:

- a seat inherits the *room's* connections, because that is what makes it a room
  rather than a folder of unrelated agents
- each seat sees what the seats before it produced, because a manager placed after
  a researcher has to be able to audit and redirect that work
- a room is only ever a sequence of ordinary agent runs, so nothing about how a
  single agent is launched needed to change
"""
from __future__ import annotations

import pytest

from core import workforce as W


@pytest.fixture
def room(tmp_path):
    r = W.add_room(tmp_path, "Research room", mcp_services=["websearch", "nextcloud"])
    W.add_seat(tmp_path, r["id"], role="researcher", provider="gemini",
               account_id="personal", goal="Find the facts", model="gemini-2.5-pro")
    W.add_seat(tmp_path, r["id"], role="manager", provider="claude",
               account_id="work", goal="Check the research and direct the build")
    W.add_seat(tmp_path, r["id"], role="developer", provider="codex",
               account_id="personal", goal="Write it into Nextcloud",
               model="gpt-5.1-codex")
    return W.get_room(tmp_path, r["id"])


def _recording_runner(outputs=None):
    """Stands in for agent_runner.run_agent, recording how each seat was invoked."""
    calls: list[dict] = []
    outs = list(outputs or [])

    def run(root, prompt, *, label="", mcp_services=None, provider="", account_id="",
            model=""):
        calls.append({"prompt": prompt, "label": label, "mcp_services": mcp_services,
                      "provider": provider, "account_id": account_id, "model": model})
        nxt = outs.pop(0) if outs else {}
        return {"id": f"run-{len(calls)}", "ok": nxt.get("ok", True),
                "cost_usd": nxt.get("cost_usd", 0.1),
                "result": nxt.get("result", f"output {len(calls)}"),
                "error": nxt.get("error")}

    return run, calls


# ── rooms and seats ──────────────────────────────────────────────────────────

def test_a_room_holds_its_connections_and_its_seats(tmp_path, room):
    assert room["mcp_services"] == ["websearch", "nextcloud"]
    assert [s["role"] for s in room["seats"]] == ["researcher", "manager", "developer"]


def test_duplicate_room_names_are_refused(tmp_path):
    W.add_room(tmp_path, "Ops")
    with pytest.raises(ValueError, match="already exists"):
        W.add_room(tmp_path, "ops")


def test_a_seat_needs_an_account_to_run_it(tmp_path):
    r = W.add_room(tmp_path, "Empty")
    with pytest.raises(ValueError, match="provider account"):
        W.add_seat(tmp_path, r["id"], role="manager", provider="claude", account_id="")


def test_unknown_roles_are_refused(tmp_path):
    r = W.add_room(tmp_path, "Empty")
    with pytest.raises(ValueError, match="unknown role"):
        W.add_seat(tmp_path, r["id"], role="astronaut", provider="claude", account_id="a")


def test_seats_can_be_reordered(tmp_path, room):
    ids = [s["id"] for s in room["seats"]]
    reordered = W.reorder_seats(tmp_path, room["id"], [ids[1], ids[0], ids[2]])
    assert [s["id"] for s in reordered["seats"]] == [ids[1], ids[0], ids[2]]


def test_reorder_must_list_every_seat(tmp_path, room):
    """A partial list would silently drop agents out of the room."""
    ids = [s["id"] for s in room["seats"]]
    with pytest.raises(ValueError, match="exactly"):
        W.reorder_seats(tmp_path, room["id"], ids[:2])


def test_removing_a_seat_leaves_the_rest(tmp_path, room):
    ids = [s["id"] for s in room["seats"]]
    assert W.remove_seat(tmp_path, room["id"], ids[1]) is True
    assert [s["id"] for s in W.get_room(tmp_path, room["id"])["seats"]] == [ids[0], ids[2]]


def test_a_corrupt_store_reads_as_empty(tmp_path):
    (tmp_path / "data").mkdir()
    W._path(tmp_path).write_text("{not json", encoding="utf-8")
    assert W.load_rooms(tmp_path) == []


# ── the handoff ──────────────────────────────────────────────────────────────

def test_every_seat_inherits_the_rooms_connections(tmp_path, room):
    run, calls = _recording_runner()
    W.run_room(tmp_path, room["id"], "Investigate X", run_agent=run)

    assert len(calls) == 3
    for c in calls:
        assert c["mcp_services"] == ["websearch", "nextcloud"]


def test_each_seat_runs_on_its_own_provider_account(tmp_path, room):
    run, calls = _recording_runner()
    W.run_room(tmp_path, room["id"], "Investigate X", run_agent=run)
    assert [(c["provider"], c["account_id"]) for c in calls] == [
        ("gemini", "personal"), ("claude", "work"), ("codex", "personal"),
    ]


def test_each_seat_carries_its_own_model(tmp_path, room):
    """A room mixes providers, so one shared model id cannot work: 'gpt-5.1-codex'
    means nothing to Gemini and 'gemini-2.5-pro' means nothing to Codex."""
    run, calls = _recording_runner()
    W.run_room(tmp_path, room["id"], "Investigate X", run_agent=run)
    assert [c["model"] for c in calls] == ["gemini-2.5-pro", "", "gpt-5.1-codex"]


def test_a_seat_sees_what_came_before_it(tmp_path, room):
    """The manager has to be able to audit the research; the developer has to see
    what the manager decided."""
    run, calls = _recording_runner([
        {"result": "FINDINGS: widgets are blue"},
        {"result": "DECISION: build the blue widget"},
        {"result": "done"},
    ])
    W.run_room(tmp_path, room["id"], "Investigate widgets", run_agent=run)

    researcher, manager, developer = (c["prompt"] for c in calls)
    assert "FINDINGS" not in researcher, "the first seat has no prior work"
    assert "Investigate widgets" in researcher

    assert "FINDINGS: widgets are blue" in manager
    assert "manager of this room" in manager

    assert "FINDINGS: widgets are blue" in developer
    assert "DECISION: build the blue widget" in developer


def test_a_seats_own_goal_reaches_its_prompt(tmp_path, room):
    run, calls = _recording_runner()
    W.run_room(tmp_path, room["id"], "brief", run_agent=run)
    assert "Find the facts" in calls[0]["prompt"]
    assert "Check the research and direct the build" in calls[1]["prompt"]


def test_long_output_is_truncated_before_the_next_seat(tmp_path, room):
    """Otherwise a long pipeline grows its prompt without limit and a later step
    blows its context window."""
    run, calls = _recording_runner([{"result": "x" * (W.MAX_HANDOFF_CHARS + 5000)}])
    W.run_room(tmp_path, room["id"], "brief", run_agent=run)
    assert "…(truncated)" in calls[1]["prompt"]
    assert len(calls[1]["prompt"]) < W.MAX_HANDOFF_CHARS + 3000


# ── stopping ─────────────────────────────────────────────────────────────────

def test_a_failed_seat_stops_the_room(tmp_path, room):
    run, calls = _recording_runner([
        {"result": "ok"},
        {"ok": False, "error": "auth expired", "result": ""},
    ])
    rec = W.run_room(tmp_path, room["id"], "brief", run_agent=run)

    assert len(calls) == 2, "the developer must not run after the manager failed"
    assert rec["ok"] is False
    assert "auth expired" in rec["error"]


def test_the_room_budget_stops_a_runaway(tmp_path, room):
    """A room multiplies spend by its seat count, so the cap is checked between
    steps rather than only per individual run."""
    run, calls = _recording_runner([{"cost_usd": 9.0}, {"cost_usd": 9.0}])
    rec = W.run_room(tmp_path, room["id"], "brief", run_agent=run, max_cost_usd=5.0)

    assert len(calls) == 1
    assert rec["ok"] is False and "cap" in rec["error"]


def test_an_empty_room_refuses_to_run(tmp_path):
    r = W.add_room(tmp_path, "Nobody home")
    run, _ = _recording_runner()
    with pytest.raises(ValueError, match="no agents"):
        W.run_room(tmp_path, r["id"], "brief", run_agent=run)


# ── records ──────────────────────────────────────────────────────────────────

def test_the_run_is_recorded_step_by_step(tmp_path, room):
    run, _ = _recording_runner()
    rec = W.run_room(tmp_path, room["id"], "brief", run_agent=run)

    assert rec["ok"] is True
    assert [s["role"] for s in rec["steps"]] == ["researcher", "manager", "developer"]
    # Each step points at the ordinary agent run behind it, so its transcript and
    # cost are already viewable in the normal history.
    assert all(s["run_id"] for s in rec["steps"])
    assert rec["cost_usd"] == pytest.approx(0.3)

    stored = W.get_room_run(tmp_path, rec["id"])
    assert stored["steps"] == rec["steps"]
    assert W.get_room_run(tmp_path, "../etc/passwd") is None


def test_live_state_clears_when_the_room_finishes(tmp_path, room):
    run, _ = _recording_runner()
    W.run_room(tmp_path, room["id"], "brief", run_agent=run)
    assert W.LIVE["running"] is False and W.LIVE["seat_id"] == ""


# ── room-to-room handoff (research → write → review pipeline) ──────────────────

def test_a_room_hands_off_to_its_next_room(tmp_path):
    a = W.add_room(tmp_path, "Research")
    b = W.add_room(tmp_path, "Write")
    W.add_seat(tmp_path, a["id"], role="researcher", provider="claude", account_id="x")
    W.add_seat(tmp_path, b["id"], role="writer", provider="claude", account_id="x")
    W.update_room(tmp_path, a["id"], {"next_room": b["id"]})

    run, calls = _recording_runner([{"result": "the findings"}, {"result": "the draft"}])
    rec = W.run_room(tmp_path, a["id"], "brief", run_agent=run)

    assert rec["ok"] and rec["next_run_id"]        # handoff actually happened
    assert len(calls) == 2                          # one seat in each room ran
    # room B's seat received room A's output as its inbox
    assert "Handed to this room" in calls[1]["prompt"]
    assert "the findings" in calls[1]["prompt"]


def test_handoff_refuses_a_cycle_instead_of_looping_forever(tmp_path):
    a = W.add_room(tmp_path, "A")
    b = W.add_room(tmp_path, "B")
    W.add_seat(tmp_path, a["id"], role="researcher", provider="claude", account_id="x")
    W.add_seat(tmp_path, b["id"], role="researcher", provider="claude", account_id="x")
    W.update_room(tmp_path, a["id"], {"next_room": b["id"]})
    W.update_room(tmp_path, b["id"], {"next_room": a["id"]})   # A -> B -> A cycle

    run, calls = _recording_runner()
    rec = W.run_room(tmp_path, a["id"], "brief", run_agent=run)

    assert rec["ok"]
    assert len(calls) == 2   # A's seat, then B's — B->A is refused (A already in chain)


def test_a_room_without_a_next_room_just_finishes(tmp_path):
    a = W.add_room(tmp_path, "Solo")
    W.add_seat(tmp_path, a["id"], role="researcher", provider="claude", account_id="x")
    run, calls = _recording_runner()
    rec = W.run_room(tmp_path, a["id"], "brief", run_agent=run)
    assert rec["ok"] and rec["next_run_id"] == "" and len(calls) == 1
