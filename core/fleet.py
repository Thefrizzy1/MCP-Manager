"""core/fleet.py — the machines and the workers.

Machines report in: each one sends its resources (CPU, memory, GPU, free disk),
what it is doing and what it will do next. One that has not reported for a
while shows as offline. Machines that cannot report yet can be registered by
hand so the overview still shows what they are for.

Workers are the roles that do the channel's work. Each has abilities; an
ability is ``running`` (it happens on a schedule without anyone asking),
``ready`` (the tools exist, a session uses them on request) or ``planned``.
A worker with nothing running or ready is shown grey.
"""
from __future__ import annotations

import os
import shutil
import socket
import time
from pathlib import Path

from core.atomic_json import read_json, write_json

ONLINE_WINDOW = 12 * 60          # a report every 5 minutes; two missed = offline

# id -> label, what it is for, abilities as (text, state)
WORKERS: dict[str, dict] = {
    "researcher": {
        "label": "Researcher",
        "about": "Watches the niche so ideas and scripts start from facts.",
        "abilities": [
            ("Spot new models and trends (Trend Radar)", "running"),
            ("Watch and transcribe videos in the niche", "ready"),
            ("Track competitor channels and what works for them", "ready"),
            ("Read Reddit and video comments for requests", "ready"),
            ("Find public workflows and note what they do well and badly", "planned"),
        ],
    },
    "workflow_maker": {
        "label": "Workflow Maker",
        "about": "Turns new models into tested ComfyUI workflows.",
        "abilities": [
            ("Build a workflow in the house style", "running"),
            ("Test on the GPU server and critique the result", "running"),
            ("Second test on the 12 GB card", "planned"),
            ("Publish to GitHub and Hugging Face after your OK", "running"),
        ],
    },
    "editor": {
        "label": "Editor",
        "about": "Prepares the edit so filming is the only manual part.",
        "abilities": [
            ("Render motion graphics before filming", "planned"),
            ("Record screen takes of workflow runs", "planned"),
            ("Build the rough cut timeline", "planned"),
            ("Cut Shorts from finished videos", "planned"),
        ],
    },
    "manager": {
        "label": "Manager",
        "about": "Keeps dates, mail and the daily picture in order.",
        "abilities": [
            ("Keep the calendar: planned dates and reviews from mail", "planned"),
            ("Draft replies to sponsor mail with rates and free slots", "planned"),
            ("Morning page: what was done, numbers, weak spots", "planned"),
        ],
    },
    "publisher": {
        "label": "Publisher",
        "about": "Gets finished work out, only with your OK.",
        "abilities": [
            ("Upload pack: titles, description, chapters, thumbnails", "planned"),
            ("Schedule the video and Shorts", "planned"),
            ("Post to Reddit, CivitAI and socials", "planned"),
        ],
    },
}


def _path(root: Path) -> Path:
    return Path(root) / "data" / "fleet.json"


def _load(root: Path) -> dict:
    data = read_json(_path(root), {})
    data = data if isinstance(data, dict) else {}
    data.setdefault("machines", {})
    data.setdefault("workers", {})
    return data


def register_machine(root: Path, machine_id: str, name: str = "", role: str = "",
                     can: list[str] | None = None) -> dict:
    """Add or describe a machine: what it is called, what it is for, what it can do."""
    mid = (machine_id or "").strip().lower()
    if not mid:
        raise ValueError("machine id is required")
    data = _load(root)
    row = data["machines"].get(mid) or {"id": mid, "specs": {}, "doing": "", "next": "", "seen": 0}
    if name:
        row["name"] = name[:80]
    if role:
        row["role"] = role[:200]
    if can is not None:
        row["can"] = [str(c)[:120] for c in can][:12]
    row.setdefault("name", mid)
    data["machines"][mid] = row
    write_json(_path(root), data)
    return row


def report_machine(root: Path, machine_id: str, specs: dict | None = None, doing: str = "",
                   next_up: str = "") -> dict:
    """A machine's sign of life: resources, current work, next planned work."""
    mid = (machine_id or "").strip().lower()
    if not mid:
        raise ValueError("machine id is required")
    data = _load(root)
    row = data["machines"].get(mid) or {"id": mid, "name": mid}
    if specs:
        keep = ("cpu", "cores", "ram_gb", "gpu", "vram_gb", "gpu_util", "vram_used_gb",
                "disk_free_gb", "disk_total_gb", "os", "load")
        row["specs"] = {k: specs[k] for k in keep if k in specs}
    row.update(doing=(doing or "").strip()[:200], next=(next_up or "").strip()[:200], seen=int(time.time()))
    data["machines"][mid] = row
    write_json(_path(root), data)
    return row


def report_worker(root: Path, worker: str, text: str, busy: bool = True) -> dict:
    """A worker says what it is doing (or that it finished)."""
    if worker not in WORKERS:
        raise KeyError(f"unknown worker {worker!r}; one of {', '.join(WORKERS)}")
    data = _load(root)
    row = {"text": (text or "")[:300], "ts": int(time.time()), "busy": bool(busy)}
    data["workers"][worker] = row
    write_json(_path(root), data)
    return row


def this_machine(disk_path: str = "/") -> dict:
    """Resources of the machine this process runs on (the one hosting Plutus)."""
    ram = 0.0
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    ram = int(line.split()[1]) / 1e6
                    break
    except OSError:
        pass
    try:
        du = shutil.disk_usage(disk_path)
        free, total = round(du.free / 1e9), round(du.total / 1e9)
    except OSError:
        free = total = 0
    try:
        load = round(os.getloadavg()[0], 2)
    except OSError:
        load = None
    return {"cores": os.cpu_count() or 0, "ram_gb": round(ram) if ram else 0,
            "disk_free_gb": free, "disk_total_gb": total, "load": load,
            "os": os.getenv("PLUTUS_HOST_NAME") or socket.gethostname()}


def machines(root: Path, self_disk: str = "/") -> list[dict]:
    data = _load(root)
    now = time.time()
    rows = []
    host = dict(data["machines"].get("nas") or {"id": "nas", "name": "NAS"})
    host.update(specs=this_machine(self_disk), seen=int(now), online=True, here=True)
    host.setdefault("role", "Always on. Hosts Plutus, the files and the schedules.")
    rows.append(host)
    for mid, row in data["machines"].items():
        if mid == "nas":
            continue
        r = dict(row)
        r["online"] = bool(r.get("seen")) and now - r["seen"] < ONLINE_WINDOW
        rows.append(r)
    return rows


def workers(root: Path, live: dict[str, dict] | None = None) -> list[dict]:
    """Every worker with its abilities and what it is doing.

    ``live`` lets the caller pass activity it knows from elsewhere (the Trend
    Radar's last run, the model lab's heartbeat) keyed by worker id.
    """
    data = _load(root)
    out = []
    for wid, spec in WORKERS.items():
        act = (live or {}).get(wid) or data["workers"].get(wid) or {}
        states = [s for _t, s in spec["abilities"]]
        out.append({
            "id": wid, "label": spec["label"], "about": spec["about"],
            "abilities": [{"text": t, "state": s} for t, s in spec["abilities"]],
            "active": any(s in ("running", "ready") for s in states),
            "busy": bool(act.get("busy")), "text": act.get("text", ""), "ts": act.get("ts", 0),
            "next": act.get("next", ""),
        })
    return out
