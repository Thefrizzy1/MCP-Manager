"""Node queue: work hours, waking, power-off, and who gets a job when."""
from datetime import datetime

import pytest

from core import fleet as F
from core import node_jobs as N

NIGHT = {"enabled": True, "start": "23:00", "end": "05:00", "days": [0, 1, 2, 3, 4, 5, 6],
         "may_wake": True, "may_power_off": True}


def test_work_hours_cross_midnight_and_respect_days():
    assert N.in_hours(NIGHT, datetime(2026, 11, 23, 23, 30)) and N.in_hours(NIGHT, datetime(2026, 11, 24, 4, 59))
    assert not N.in_hours(NIGHT, datetime(2026, 11, 24, 5, 0)) and not N.in_hours(NIGHT, datetime(2026, 11, 24, 12, 0))
    monday_only = dict(NIGHT, days=[0])
    assert N.in_hours(monday_only, datetime(2026, 11, 24, 2, 0))        # Tuesday 02:00 belongs to Monday night
    assert not N.in_hours(monday_only, datetime(2026, 11, 24, 23, 30))
    assert not N.in_hours(dict(NIGHT, enabled=False), datetime(2026, 11, 23, 23, 30)) and not N.in_hours(None)


def test_jobs_only_inside_hours_unless_the_server_woke_it(tmp_path, monkeypatch):
    F.register_machine(tmp_path, "pc", "PC")
    F.set_schedule(tmp_path, "pc", NIGHT)
    N.add(tmp_path, "pc", "comfy_test", {"slug": "sdxl"}, ref="t1")
    assert N.add(tmp_path, "pc", "comfy_test", {"slug": "sdxl"}, ref="t1")["id"] == N.jobs(tmp_path)[0]["id"]
    monkeypatch.setattr(N, "in_hours", lambda *a, **k: False)
    out = N.next_for(tmp_path, "pc")                                    # a person turned it on by day
    assert out["job"] is None and out["waiting"] == 1 and out["may_power_off"] is False
    monkeypatch.setattr(N, "woken_by_us", lambda *a, **k: True)
    job = N.next_for(tmp_path, "pc")["job"]
    assert job["kind"] == "comfy_test" and job["status"] == "running"
    N.finish(tmp_path, job["id"], True, {"runs": []})
    out = N.next_for(tmp_path, "pc")
    assert out["job"] is None and out["may_power_off"] is True          # queue empty and we woke it
    with pytest.raises(ValueError):
        N.add(tmp_path, "pc", "run_any_command")


def test_wake_is_planned_only_when_work_waits_and_the_address_is_known(tmp_path, monkeypatch):
    F.register_machine(tmp_path, "pc", "PC")
    F.set_schedule(tmp_path, "pc", NIGHT)
    monkeypatch.setattr(N, "in_hours", lambda *a, **k: True)
    assert N.plan_wakes(tmp_path) == []                                 # nothing queued
    N.add(tmp_path, "pc", "comfy_test", {"slug": "x"})
    assert N.plan_wakes(tmp_path) == []                                 # never reported: no MAC yet
    F.report_machine(tmp_path, "pc", {"mac": "AA:BB:CC:DD:EE:FF"})
    monkeypatch.setattr(F, "ONLINE_WINDOW", -1)                         # it is off again
    wake = N.plan_wakes(tmp_path)
    assert len(wake) == 1 and wake[0]["machine"] == "nas-host" and wake[0]["payload"]["mac"] == "AA:BB:CC:DD:EE:FF"
    assert N.plan_wakes(tmp_path) == []                                 # not again within 15 minutes
    assert N.woken_by_us(tmp_path, "pc") is True
