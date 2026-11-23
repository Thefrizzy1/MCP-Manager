"""core/node_jobs.py — work handed to the machines.

The server keeps one queue. A node (a small program on each machine) asks for
its next job, does it and reports back. Jobs are of a few fixed kinds; a node
never runs arbitrary commands.

Scheduling lives here too: a machine with work hours is only given jobs inside
them (or when the server itself switched it on), is woken at the start of its
hours when work is waiting, and is told it may power off again when its queue
is empty and the server was the one that woke it.
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from core import fleet
from core.atomic_json import read_json, write_json

KINDS = ("wake", "comfy_test", "render_html")
STALE_AFTER = 3 * 3600          # a job "running" this long was lost with its machine
WAKE_EVERY = 15 * 60            # do not send another wake sooner than this
WOKEN_WINDOW = 12 * 3600        # a wake counts as "we switched it on" for this long


def _path(root: Path) -> Path:
    return Path(root) / "data" / "node_jobs.json"


def _load(root: Path) -> dict:
    data = read_json(_path(root), {})
    data = data if isinstance(data, dict) else {}
    data.setdefault("jobs", [])
    data.setdefault("wakes", {})
    return data


def add(root: Path, machine: str, kind: str, payload: dict | None = None, ref: str = "") -> dict:
    """Queue a job for a machine. The same open ``ref`` again returns the existing job."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    data = _load(root)
    for j in data["jobs"]:
        if ref and j.get("ref") == ref and j["machine"] == machine and j["status"] in ("queued", "running"):
            return j
    job = {"id": int(time.time() * 1000), "machine": machine, "kind": kind, "payload": payload or {},
           "ref": ref, "status": "queued", "created": int(time.time()), "started": 0, "finished": 0,
           "result": {}}
    data["jobs"] = data["jobs"][-400:] + [job]
    write_json(_path(root), data)
    return job


def jobs(root: Path, machine: str = "", status: str = "") -> list[dict]:
    return [j for j in _load(root)["jobs"]
            if (not machine or j["machine"] == machine) and (not status or j["status"] == status)]


def in_hours(schedule: dict | None, now: datetime | None = None) -> bool:
    """Is ``now`` inside the machine's work hours? A window may cross midnight."""
    if not schedule or not schedule.get("enabled"):
        return False
    now = now or datetime.now()
    start, end = schedule["start"], schedule["end"]
    hm = f"{now:%H:%M}"
    days = schedule.get("days") or list(range(7))
    if start <= end:
        return now.weekday() in days and start <= hm < end
    if hm >= start:                              # before midnight: today's window
        return now.weekday() in days
    if hm < end:                                 # after midnight: yesterday's window
        return (now.weekday() - 1) % 7 in days
    return False


def woken_by_us(root: Path, machine: str, boot_ts: int = 0) -> bool:
    """Did the server switch this machine on (rather than a person)?"""
    last = int(_load(root)["wakes"].get(machine, 0))
    if not last or time.time() - last > WOKEN_WINDOW:
        return False
    return not boot_ts or boot_ts >= last - 120


def next_for(root: Path, machine: str, kinds: list[str] | None = None, boot_ts: int = 0) -> dict:
    """What a node should do now: a job, or idle with whether it may power off."""
    data = _load(root)
    now = int(time.time())
    for j in data["jobs"]:                       # a machine that died mid-job gets it again
        if j["status"] == "running" and now - j["started"] > STALE_AFTER:
            j["status"] = "queued"
    m = next((x for x in fleet.machines(root) if x["id"] == machine), None) or {}
    sched = m.get("schedule")
    ours = woken_by_us(root, machine, boot_ts)
    allowed = sched is None or in_hours(sched) or ours
    job = None
    if allowed:
        for j in data["jobs"]:
            if j["machine"] == machine and j["status"] == "queued" and (not kinds or j["kind"] in kinds):
                j.update(status="running", started=now)
                job = j
                break
    write_json(_path(root), data)
    if job:
        return {"job": job}
    waiting = sum(1 for j in data["jobs"] if j["machine"] == machine and j["status"] == "queued")
    return {"job": None, "waiting": waiting,
            "may_power_off": bool(ours and sched and sched.get("may_power_off") and not waiting)}


def finish(root: Path, job_id: int, ok: bool, result: dict | None = None) -> dict:
    data = _load(root)
    for j in data["jobs"]:
        if j["id"] == job_id:
            j.update(status="done" if ok else "failed", finished=int(time.time()), result=result or {})
            write_json(_path(root), data)
            return j
    raise KeyError(f"no job {job_id}")


def plan_wakes(root: Path, now: datetime | None = None) -> list[dict]:
    """Queue a wake for every machine that is off, in its hours, allowed to be
    woken and has work waiting. Returns the wake jobs it queued."""
    data = _load(root)
    out = []
    for m in fleet.machines(root):
        sched = m.get("schedule") or {}
        if m.get("here") or m.get("online") or not sched.get("may_wake") or not in_hours(sched, now):
            continue
        mac = (m.get("specs") or {}).get("mac") or m.get("mac")
        if not mac:
            continue                              # it has never reported, so its address is unknown
        if not any(j["machine"] == m["id"] and j["status"] == "queued" for j in data["jobs"]):
            continue
        if time.time() - int(data["wakes"].get(m["id"], 0)) < WAKE_EVERY:
            continue
        data["wakes"][m["id"]] = int(time.time())
        write_json(_path(root), data)
        out.append(add(root, m.get("wake_via") or "nas-host", "wake",
                       {"mac": mac, "target": m["id"], "broadcast": m.get("broadcast", "")},
                       ref=f"wake-{m['id']}-{int(time.time()) // WAKE_EVERY}"))
        data = _load(root)
    return out


async def loop(root: Path, every: int = 60) -> None:
    import asyncio
    await asyncio.sleep(45)
    while True:
        try:
            await asyncio.to_thread(plan_wakes, root)
        except Exception:
            pass
        await asyncio.sleep(every)
