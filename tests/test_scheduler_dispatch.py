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
