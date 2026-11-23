"""Machines and workers registry; the calendar feed."""
import pytest

from core import calendar_feed as C
from core import fleet as F


def test_machines_report_in_and_go_offline(tmp_path, monkeypatch):
    F.register_machine(tmp_path, "windows-pc", "Windows PC", "Filming and the 12 GB test", ["OBS", "DaVinci"])
    F.report_machine(tmp_path, "gpu-server", {"gpu": "RTX", "vram_gb": 24, "secret": "x"}, "Testing Anima", "17 queued")
    rows = {m["id"]: m for m in F.machines(tmp_path)}
    assert rows["nas"]["online"] and rows["nas"]["here"]
    assert rows["gpu-server"]["online"] and rows["gpu-server"]["doing"] == "Testing Anima"
    assert "secret" not in rows["gpu-server"]["specs"]
    assert rows["windows-pc"]["online"] is False and rows["windows-pc"]["can"] == ["OBS", "DaVinci"]
    monkeypatch.setattr(F.time, "time", lambda: 10**10)
    assert {m["id"]: m for m in F.machines(tmp_path)}["gpu-server"]["online"] is False


def test_workers_are_grey_until_something_runs(tmp_path):
    ws = {w["id"]: w for w in F.workers(tmp_path, {"researcher": {"text": "scan", "ts": 5, "busy": True}})}
    assert ws["researcher"]["active"] and ws["researcher"]["busy"]
    assert set(ws) >= {"manager", "writer", "editor", "motion", "thumbnail", "shorts", "publisher"}
    F.report_worker(tmp_path, "manager", "Added 3 sponsor dates", busy=False)
    assert {w["id"]: w for w in F.workers(tmp_path)}["manager"]["text"].startswith("Added")
    with pytest.raises(KeyError):
        F.report_worker(tmp_path, "intern", "hi")


def test_calendar_collects_every_source_and_writes_valid_ics(tmp_path):
    C.add_event(tmp_path, "2026-11-27", "Draft due: sponsor, with; commas", "Send the draft\nto review",
                "https://mail/x", kind="sponsor", ref="s1")
    C.add_event(tmp_path, "2026-11-28", "Moved", ref="s1")                 # same ref replaces
    assert len(C.custom_events(tmp_path)) == 1
    jobs = [{"id": "20261111_Local Relight", "title": "Local Relight", "deadline": "2026-12-01",
             "current": "shorts", "note": "", "publish_url": "", "published": {"id": "abc"}}]
    uploads = [{"id": "abc", "title": "I Replaced Relight", "published": "2026-11-13", "kind": "video", "views": 1270},
               {"id": "nodate", "title": "x", "published": "", "kind": "video"}]
    lab = {"k5": {"status": "published", "updated": 1795300000, "name": "Kandinsky 5", "pr": "https://x/pull/3"}}
    events = C.collect(tmp_path, jobs, uploads, lab)
    assert [e["kind"] for e in events] == ["publish", "workflow", "sponsor", "deadline"]   # a re-add keeps the kind
    assert "1,270 views" in events[0]["description"] and "20261111_Local Relight" in events[0]["description"]
    ics = C.to_ics(events)
    assert ics.startswith("BEGIN:VCALENDAR\r\n") and ics.count("BEGIN:VEVENT") == 4
    assert "DTSTART;VALUE=DATE:20261113" in ics and "DTEND;VALUE=DATE:20261114" in ics
    assert all(len(line.encode()) <= 75 for line in ics.split("\r\n"))
    with pytest.raises(ValueError):
        C.add_event(tmp_path, "27.11.2026", "bad date")


def test_the_feed_needs_the_right_token(tmp_path):
    t = C.token(tmp_path)
    assert C.check_token(tmp_path, t) and not C.check_token(tmp_path, "") and not C.check_token(tmp_path, t + "x")
    assert C.token(tmp_path) == t and C.token(tmp_path, regenerate=True) != t


def test_machine_work_hours_and_worker_settings(tmp_path):
    F.register_machine(tmp_path, "windows-pc", "Windows PC")
    row = F.set_schedule(tmp_path, "windows-pc", {"start": "03:00", "end": "06:00", "days": [0, 2, 9],
                                                 "may_wake": True, "may_power_off": True})
    assert row["schedule"] == {"enabled": True, "start": "03:00", "end": "06:00", "days": [0, 2],
                               "may_wake": True, "may_power_off": True}
    with pytest.raises(ValueError):
        F.set_schedule(tmp_path, "windows-pc", {"start": "3am", "end": "06:00"})
    with pytest.raises(KeyError):
        F.set_schedule(tmp_path, "toaster", {"start": "03:00", "end": "06:00"})
    ws = {w["id"]: w for w in F.workers(tmp_path)}
    assert ws["manager"]["model"] == "fable" and ws["editor"]["home"] == "windows-pc"
    F.set_worker(tmp_path, "manager", model="opus", home="gpu-server")
    ws = {w["id"]: w for w in F.workers(tmp_path)}
    assert ws["manager"]["model"] == "opus" and ws["manager"]["home"] == "gpu-server"
    with pytest.raises(ValueError):
        F.set_worker(tmp_path, "manager", model="gpt")


def test_the_newest_change_wins_and_a_hand_move_survives_old_mail(tmp_path):
    C.add_event(tmp_path, "2026-11-27", "Soundboost: preview due", ref="sb", evidence="mail", evidence_at="2026-11-18T10:00")
    assert C.move_event(tmp_path, "sb", "2026-11-24")["date"] == "2026-11-24"
    kept = C.add_event(tmp_path, "2026-11-27", "Soundboost: preview due", ref="sb", evidence="same old mail",
                       evidence_at="2026-11-18T10:00")
    assert kept["date"] == "2026-11-24" and "kept" in kept
    newer = C.add_event(tmp_path, "2026-11-26", "Soundboost: preview due", ref="sb", evidence="new mail",
                        evidence_at="2099-01-01T09:00")
    assert newer["date"] == "2026-11-26"
    h = newer["history"]
    assert [(x["by"], x["date"], x["applied"]) for x in h] == [
        ("agent", "2026-11-27", True), ("you", "2026-11-24", True), ("agent", "2026-11-27", False), ("agent", "2026-11-26", True)]
    with pytest.raises(KeyError):
        C.move_event(tmp_path, "nope", "2026-11-24")
