"""Scheduler job dispatch — the payload's per-connection ACL must travel into the
agent runner, so a scheduled agent is not silently governed by a mutable global
default (offline; no APScheduler needed — we build the job callable directly).

Permission levels are gone: the connection selection is the only tool gate, so a
`permission` key left in an older stored payload must be ignored rather than
forwarded (forwarding it would blow up on the runner's signature)."""
from pathlib import Path

from core.scheduler import PlutusScheduler


def _capture_scheduler():
    sched = PlutusScheduler(Path("."))
    calls = []
    sched._run_agent = lambda prompt, label, **kw: calls.append((prompt, label, kw))
    return sched, calls


def test_agent_schedule_forwards_the_connection_acl():
    sched, calls = _capture_scheduler()
    job = sched._make_job({
        "id": "s1", "kind": "agent", "name": "nightly",
        "payload": {"prompt": "research", "mcp_services": ["jellyfin", "sonarr"]},
    })
    job()
    assert len(calls) == 1
    prompt, label, kw = calls[0]
    assert prompt == "research"
    assert label == "sched:nightly"
    assert kw["mcp_services"] == ["jellyfin", "sonarr"]


def test_stored_permission_key_is_ignored_not_forwarded():
    """Schedules saved while permission levels existed still carry the key. The
    runner no longer accepts it, so forwarding it would raise TypeError inside the
    job and the schedule would silently stop firing."""
    sched, calls = _capture_scheduler()
    job = sched._make_job({
        "id": "s1b", "kind": "agent", "name": "legacy",
        "payload": {"prompt": "research", "permission": "all", "mcp_services": ["jellyfin"]},
    })
    job()
    assert len(calls) == 1, "legacy payload must still dispatch"
    _, _, kw = calls[0]
    assert "permission" not in kw
    assert kw["mcp_services"] == ["jellyfin"]


def test_legacy_agent_schedule_without_an_acl_is_unrestricted():
    # A schedule created before the per-connection ACL existed carries no key.
    sched, calls = _capture_scheduler()
    job = sched._make_job({
        "id": "s2", "kind": "agent", "name": "old",
        "payload": {"prompt": "hello"},
    })
    job()
    _, _, kw = calls[0]
    assert kw["mcp_services"] is None


def test_dispatch_swallows_runner_errors():
    sched = PlutusScheduler(Path("."))

    def boom(*a, **k):
        raise RuntimeError("runner down")

    sched._run_agent = boom
    # A failing run must not propagate out of the scheduled job.
    sched._make_job({"id": "s3", "kind": "agent", "name": "x",
                     "payload": {"prompt": "p"}})()


# ── the day of the week a weekly schedule actually fires ─────────────────────

def test_cron_day_of_week_follows_standard_cron_not_apschedulers():
    """The bug this exists for: APScheduler's from_crontab numbers the
    day-of-week field 0=Monday, while standard cron — and every crontab, every
    online helper, and Plutus's own UI — means 0=Sunday.

    So a schedule saved as "Friday" (5) fired on Saturday and "Sunday" (0) fired
    on Monday. Every weekly schedule was a day late, and nothing said so: the run
    simply did not happen when the user expected it.
    """
    import calendar
    from datetime import datetime

    from core.scheduler import cron_trigger

    base = datetime(2026, 7, 31, 8, 0)          # a Friday, after the fire time
    expected = ["Sunday", "Monday", "Tuesday", "Wednesday",
                "Thursday", "Friday", "Saturday"]
    for dow, want in enumerate(expected):
        trigger = cron_trigger(f"0 7 * * {dow}", "UTC")
        nxt = trigger.get_next_fire_time(None, base.replace(tzinfo=trigger.timezone))
        assert calendar.day_name[nxt.weekday()] == want, f"cron dow={dow}"


def test_seven_is_sunday_too():
    """Standard cron accepts both 0 and 7 for Sunday."""
    import calendar
    from datetime import datetime

    from core.scheduler import cron_trigger

    trigger = cron_trigger("0 7 * * 7", "UTC")
    nxt = trigger.get_next_fire_time(
        None, datetime(2026, 7, 31, 8, 0, tzinfo=trigger.timezone))
    assert calendar.day_name[nxt.weekday()] == "Sunday"


def test_ranges_lists_and_steps_survive_the_rewrite():
    """A hand-typed cron is a supported input, so the translation has to handle
    the punctuation rather than only bare numbers."""
    from core.scheduler import _dow_to_names

    assert _dow_to_names("*") == "*"
    assert _dow_to_names("1-5") == "mon-fri"
    assert _dow_to_names("0,6") == "sun,sat"
    assert _dow_to_names("*/2") == "*/2", "a step is every-N, not a day"
    assert _dow_to_names("1-5/2") == "mon-fri/2"
    # Names the user already typed are left alone.
    assert _dow_to_names("mon-fri") == "mon-fri"


def test_the_other_fields_are_untouched():
    from core.scheduler import cron_trigger

    trigger = cron_trigger("30 6 1 * *", "UTC")
    fields = {f.name: str(f) for f in trigger.fields}
    assert fields["minute"] == "30" and fields["hour"] == "6"
    assert fields["day"] == "1" and fields["day_of_week"] == "*"


def test_a_malformed_expression_still_raises():
    """Silently accepting nonsense would mean a schedule that never fires."""
    import pytest

    from core.scheduler import cron_trigger

    with pytest.raises(Exception):
        cron_trigger("not a cron", "UTC")

