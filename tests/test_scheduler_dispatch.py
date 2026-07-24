"""Scheduler job dispatch — the payload's permission + per-connection ACL must
travel into the agent runner, so a scheduled agent is not silently governed by
a mutable global default (offline; no APScheduler needed — we build the job
callable directly)."""
from pathlib import Path

from core.scheduler import PlutusScheduler


def _capture_scheduler():
    sched = PlutusScheduler(Path("."))
    calls = []
    sched._run_agent = lambda prompt, label, **kw: calls.append((prompt, label, kw))
    return sched, calls


def test_agent_schedule_forwards_permission_and_acl():
    sched, calls = _capture_scheduler()
    job = sched._make_job({
        "id": "s1", "kind": "agent", "name": "nightly",
        "payload": {"prompt": "research", "permission": "all",
                    "mcp_services": ["jellyfin", "sonarr"]},
    })
    job()
    assert len(calls) == 1
    prompt, label, kw = calls[0]
    assert prompt == "research"
    assert label == "sched:nightly"
    assert kw["permission"] == "all"
    assert kw["mcp_services"] == ["jellyfin", "sonarr"]


def test_legacy_agent_schedule_defaults_are_safe():
    # A schedule created before per-run permission existed carries neither key.
    sched, calls = _capture_scheduler()
    job = sched._make_job({
        "id": "s2", "kind": "agent", "name": "old",
        "payload": {"prompt": "hello"},
    })
    job()
    _, _, kw = calls[0]
    # None => the runner applies the configured default (safe), not "all".
    assert kw["permission"] is None
    assert kw["mcp_services"] is None


def test_dispatch_swallows_runner_errors():
    sched = PlutusScheduler(Path("."))

    def boom(*a, **k):
        raise RuntimeError("runner down")

    sched._run_agent = boom
    # A failing run must not propagate out of the scheduled job.
    sched._make_job({"id": "s3", "kind": "agent", "name": "x",
                     "payload": {"prompt": "p"}})()
