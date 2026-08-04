"""Work hours: when a room may start *without anyone watching*.

The distinction this file exists to pin down is which starts are governed. A
schedule and a chain handoff are unattended and honour the window; clicking Run
does not, because a button that silently refuses is worse than one that costs a
few cents.

The other thing worth pinning is the overnight window. "22:00–06:00 on Friday"
is a normal shift and it wraps past midnight, so it cannot be a plain
``start <= t < end`` and its day check belongs to the day the window *opened*.
"""
from __future__ import annotations

import time

import pytest

from core import agent_orchestrator, workforce


def at(weekday: int, hour: int, minute: int = 0) -> time.struct_time:
    """A struct_time on a given weekday (0=Monday). 2026-08-03 was a Monday."""
    return time.struct_time((2026, 8, 3 + weekday, hour, minute, 0, weekday, 215, 0))


DAY_SHIFT = {"label": "Office", "hours": {"enabled": True, "start": "09:00",
                                          "end": "17:00", "days": [0, 1, 2, 3, 4]}}
FRIDAY_NIGHT = {"label": "Night", "hours": {"enabled": True, "start": "22:00",
                                            "end": "06:00", "days": [4]}}


@pytest.mark.parametrize("when, open_", [
    (at(0, 10), True),        # Monday mid-morning
    (at(0, 9), True),         # exactly at opening
    (at(0, 8, 59), False),
    (at(0, 17), False),       # closing is exclusive
    (at(4, 16, 59), True),    # Friday, just before close
    (at(5, 10), False),       # Saturday
    (at(6, 10), False),       # Sunday
])
def test_a_daytime_window(when, open_):
    assert workforce.within_hours(DAY_SHIFT, when) is open_


@pytest.mark.parametrize("when, open_", [
    (at(4, 23), True),        # Friday night, after opening
    (at(4, 22), True),
    (at(4, 21, 59), False),
    (at(5, 3), True),         # Saturday 03:00 still belongs to Friday's shift
    (at(5, 5, 59), True),
    (at(5, 6), False),        # …and it ends
    (at(5, 23), False),       # Saturday night is not ticked
    (at(6, 3), False),        # so Sunday morning is not either
])
def test_an_overnight_window_belongs_to_the_day_it_opened(when, open_):
    assert workforce.within_hours(FRIDAY_NIGHT, when) is open_


def test_hours_that_are_off_never_close_anything():
    assert workforce.within_hours({"hours": {"enabled": False}}, at(6, 3)) is True
    assert workforce.within_hours({}, at(6, 3)) is True


def test_a_window_that_cannot_open():
    """Two ways to describe "never". Both have to actually mean it, or a room
    with no days ticked would run constantly instead of not at all."""
    no_days = {"label": "X", "hours": {"enabled": True, "start": "09:00",
                                       "end": "17:00", "days": []}}
    zero_length = {"label": "X", "hours": {"enabled": True, "start": "09:00",
                                           "end": "09:00", "days": [0]}}
    assert workforce.within_hours(no_days, at(0, 10)) is False
    assert workforce.within_hours(zero_length, at(0, 9)) is False


def test_nonsense_hours_are_repaired_not_rejected():
    """A bad stored value must not make a room unopenable."""
    cleaned = workforce.clean_hours({"enabled": True, "start": "25:99", "end": "",
                                     "days": [0, 9, "tue", 3, 3]})
    assert cleaned["start"] == "09:00" and cleaned["end"] == "17:00"
    assert cleaned["days"] == [0, 3]
    assert workforce.clean_hours("not a dict")["enabled"] is False
    assert workforce.clean_hours(None)["days"] == [0, 1, 2, 3, 4]


def test_the_reason_names_the_room_and_the_window():
    reason = workforce.hours_reason(DAY_SHIFT, at(5, 10))
    assert "Office" in reason and "09:00" in reason and "17:00" in reason
    assert workforce.hours_reason(DAY_SHIFT, at(0, 10)) == ""


# ── which starts are governed ────────────────────────────────────────────────

def _closed_room(tmp_path):
    room = workforce.add_room(tmp_path, "Office")
    # A window that is definitely shut: enabled with no days ticked.
    workforce.update_room(tmp_path, room["id"], {"hours": {
        "enabled": True, "start": "09:00", "end": "17:00", "days": []}})
    workforce.add_seat(tmp_path, room["id"], role="researcher",
                       provider="p", account_id="a")
    return workforce.get_room(tmp_path, room["id"])


def test_an_unattended_start_is_refused_outside_hours(tmp_path):
    room = _closed_room(tmp_path)
    res = agent_orchestrator.launch_room(tmp_path, object(), room["id"],
                                         respect_hours=True)
    assert res["ok"] is False
    assert "work hours" in res["error"]


def test_clicking_run_is_never_refused_by_hours(tmp_path, monkeypatch):
    """Only the scheduler and the chain pass respect_hours. If you clicked Run at
    midnight you meant it."""
    room = _closed_room(tmp_path)
    started: list[str] = []
    monkeypatch.setattr(workforce, "run_room",
                        lambda root, rid, brief, **kw: started.append(rid))

    res = agent_orchestrator.launch_room(tmp_path, _Cfg(), room["id"], block=True)
    assert res["ok"] is True
    assert started == [room["id"]]


class _Cfg:
    mcp_port = 8765
    mcp_require_bearer = False


def test_the_scheduler_asks_for_the_hours_to_be_honoured():
    import inspect

    import ui.runtime as runtime
    assert "respect_hours=True" in inspect.getsource(runtime._run_room_bg)


def test_a_handoff_will_not_start_a_room_that_is_closed(tmp_path):
    """The publishing room's own hours have to hold even when the research room
    that triggered it was inside its."""
    first = workforce.add_room(tmp_path, "Research")
    workforce.add_seat(tmp_path, first["id"], role="researcher",
                       provider="p", account_id="a")
    second = _closed_room(tmp_path)
    workforce.update_room(tmp_path, first["id"], {"next_room": second["id"]})

    ran: list[str] = []

    def fake_run(root, prompt, **kw):
        ran.append(kw.get("label", ""))
        return {"id": "r1", "ok": True, "cost_usd": 0.0, "result": "done", "error": None}

    rec = workforce.run_room(tmp_path, first["id"], "go", run_agent=fake_run)

    assert rec["ok"] is True                      # the first room still succeeded
    assert rec["next_run_id"] == ""
    assert "work hours" in rec["next_skipped"]
    assert len(ran) == 1                          # the closed room never ran
