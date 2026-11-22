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
# How an agent comes to work: "schedule" is a daily time it runs by itself,
# "trigger" says in words what else starts it.
WORKERS: dict[str, dict] = {
    "manager": {
        "home": "nas", "model": "fable", "schedule": "06:30",
        "label": "Channel Manager", "trigger": "When you write to it or add an idea",
        "about": "The producer. Knows every video, hands work to the right agent, answers you.",
        "abilities": [
            ("Answer you in the chat and delegate the work", "running"),
            ("Set up a new idea: decide if it needs research, start the script", "running"),
            ("File finished videos into the archive", "running"),
            ("Put sponsor and review dates from mail into the calendar", "running"),
            ("Morning page: what was done, numbers, weak spots", "planned"),
        ],
    },
    "researcher": {
        "home": "gpu-server", "model": "opus", "schedule": "04:30",
        "label": "Researcher", "trigger": "When a video needs research",
        "about": "Watches the niche and researches each video so scripts start from facts.",
        "abilities": [
            ("Research a video idea: facts, sources, angles", "running"),
            ("Spot new models and trends (Trend Radar)", "running"),
            ("Watch and transcribe new videos in the niche", "running"),
            ("Read Reddit and video comments for model requests", "running"),
            ("Write the daily research brief", "running"),
        ],
    },
    "writer": {
        "home": "gpu-server", "model": "opus", "schedule": "",
        "label": "Writer", "trigger": "When research is done or a script is asked for",
        "about": "Writes scripts in the channel's voice that are easy to read aloud.",
        "abilities": [
            ("Write the script with hook, structure and pacing", "running"),
            ("Write the read-off version for filming", "running"),
            ("Revise from your comments, keeping old versions", "running"),
        ],
    },
    "editor": {
        "home": "windows-pc", "model": "opus", "schedule": "",
        "label": "Senior Editor", "trigger": "When you press Start edit",
        "about": "Responsible for the whole cut and for what the other editors make.",
        "abilities": [
            ("Plan the cut: structure, takes, pacing, where graphics go", "running"),
            ("Brief the Motion Graphics Artist", "running"),
            ("Build and change the timeline in the editing program", "planned"),
            ("Change one part of a cut without redoing the rest", "planned"),
        ],
    },
    "motion": {
        "home": "gpu-server", "model": "opus", "schedule": "",
        "label": "Motion Graphics", "trigger": "When the Senior Editor asks for graphics",
        "about": "Junior editor. Makes titles, lower thirds, diagrams and transitions.",
        "abilities": [
            ("List every graphic a video needs, with timing and text", "running"),
            ("Render graphics with Remotion", "planned"),
        ],
    },
    "thumbnail": {
        "home": "windows-pc", "model": "opus", "schedule": "",
        "label": "Thumbnail Maker", "trigger": "When a video has no thumbnail or you ask",
        "about": "Designs thumbnails in the channel's style from frames and cutouts.",
        "abilities": [
            ("Thumbnail concepts with title pairings", "running"),
            ("Render and revise thumbnails from your marks", "planned"),
        ],
    },
    "shorts": {
        "home": "windows-pc", "model": "opus", "schedule": "",
        "label": "Shorts Editor", "trigger": "When a video is live and you press Make Shorts",
        "about": "Finds the moments that work on their own and plans the vertical cuts.",
        "abilities": [
            ("Pick short-form moments from the published video", "running"),
            ("Cut the vertical edits with captions", "planned"),
        ],
    },
    "publisher": {
        "home": "nas", "model": "opus", "schedule": "",
        "label": "Publisher", "trigger": "When a cut is finished",
        "about": "Gets finished work out, only with your OK.",
        "abilities": [
            ("Upload pack: titles, description, chapters, tags", "running"),
            ("Upload and schedule on YouTube", "planned"),
            ("Check what is live and track Shorts", "planned"),
        ],
    },
    "workflow_maker": {
        "home": "gpu-server", "model": "opus", "schedule": "",
        "label": "Workflow Maker", "trigger": "When models are queued",
        "about": "Turns new models into tested ComfyUI workflows.",
        "abilities": [
            ("Build a workflow in the house style", "running"),
            ("Test on the GPU server and critique the result", "running"),
            ("Second test on the 12 GB card", "ready"),
            ("Publish to GitHub and Hugging Face after your OK", "running"),
        ],
    },
    "workflow_manager": {
        "home": "gpu-server", "model": "opus", "schedule": "07:00",
        "label": "Workflow Manager", "trigger": "",
        "about": "Keeps the published workflows healthy.",
        "abilities": [
            ("Check every download link and the repo's consistency", "running"),
            ("Watch open pull requests", "running"),
            ("Notice a new ComfyUI version", "running"),
            ("Open the fix for a broken link or note by itself", "planned"),
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


FORMS = ("server", "laptop")


def register_machine(root: Path, machine_id: str, name: str = "", role: str = "",
                     can: list[str] | None = None, form: str = "") -> dict:
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
    if form in FORMS:
        row["form"] = form                       # how it is drawn: a rack unit or a laptop
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
                "disk_free_gb", "disk_total_gb", "os", "load", "mac", "boot_ts")
        row["specs"] = {k: specs[k] for k in keep if k in specs}
        if specs.get("mac"):
            row["mac"] = str(specs["mac"])[:17]      # remembered while it is off, to wake it
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


MODELS = ("opus", "fable", "sonnet", "haiku")


def set_schedule(root: Path, machine_id: str, schedule: dict) -> dict:
    """When a machine may be switched on to work by itself.

    ``start``/``end`` are HH:MM (a window that crosses midnight is fine),
    ``days`` are 0 = Monday .. 6 = Sunday, ``may_wake`` lets the server power it
    on at the start, ``may_power_off`` lets it shut down when its work is done.
    """
    import re
    data = _load(root)
    row = data["machines"].get(machine_id)
    if row is None:
        raise KeyError(f"no machine {machine_id!r}")
    for key in ("start", "end"):
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(schedule.get(key, ""))):
            raise ValueError(f"{key} must be a time like 03:00")
    days = sorted({int(d) for d in schedule.get("days", range(7)) if 0 <= int(d) <= 6})
    row["schedule"] = {"enabled": bool(schedule.get("enabled", True)), "start": schedule["start"],
                       "end": schedule["end"], "days": days,
                       "may_wake": bool(schedule.get("may_wake")),
                       "may_power_off": bool(schedule.get("may_power_off"))}
    data["machines"][machine_id] = row
    write_json(_path(root), data)
    return row


def set_worker(root: Path, worker: str, model: str = "", home: str = "",
               schedule: str | None = None, enabled: bool | None = None) -> None:
    """Change an agent's model, home machine, daily run time ('' = none) or switch it off."""
    if worker not in WORKERS:
        raise KeyError(f"unknown worker {worker!r}")
    data = _load(root)
    if model:
        if model not in MODELS:
            raise ValueError(f"model must be one of {MODELS}")
        data["workers"][worker + ":model"] = model
    if home:
        data["workers"][worker + ":home"] = home[:60]
    if schedule is not None:
        import re
        if schedule and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", schedule):
            raise ValueError("schedule must be a time like 04:30, or empty")
        data["workers"][worker + ":schedule"] = schedule
    if enabled is not None:
        data["workers"][worker + ":enabled"] = bool(enabled)
    write_json(_path(root), data)


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


def next_daily(at: str, now: float | None = None) -> int:
    """Unix time of the next HH:MM (local), or 0 when there is no schedule."""
    if not at:
        return 0
    now = now or time.time()
    lt = time.localtime(now)
    hh, mm = (int(x) for x in at.split(":"))
    target = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, hh, mm, 0, 0, 0, -1))
    return int(target if target > now else target + 86400)


def workers(root: Path, live: dict[str, dict] | None = None,
            states: dict[str, dict] | None = None) -> list[dict]:
    """Every agent: abilities, schedule, what it is doing and on which machine.

    ``states`` is the studio's view (running task, queue, last run) keyed by
    agent; ``live`` is activity known from elsewhere (the lab's heartbeat).
    """
    data = _load(root)
    out = []
    for wid, spec in WORKERS.items():
        st = (states or {}).get(wid) or {}
        now_run = st.get("now") or {}
        last = st.get("last") or {}
        act = (live or {}).get(wid) or data["workers"].get(wid) or {}
        home = data["workers"].get(wid + ":home") or spec["home"]
        busy = bool(now_run) or bool(act.get("busy"))
        text = (f"{now_run.get('kind', '').replace('_', ' ')} · {now_run.get('job', '')[9:] or 'channel'}"
                if now_run else act.get("text") or last.get("text", ""))
        states_list = [s for _t, s in spec["abilities"]]
        out.append({
            "id": wid, "label": spec["label"], "about": spec["about"],
            "abilities": [{"text": t, "state": s} for t, s in spec["abilities"]],
            "active": any(s in ("running", "ready") for s in states_list),
            "home": home, "at": (now_run.get("machine") or home) if busy else home,
            "model": data["workers"].get(wid + ":model") or spec["model"],
            "busy": busy, "text": text,
            "since": now_run.get("started", 0),
            "ts": max(act.get("ts", 0), last.get("finished", 0)),
            "queued": st.get("queued", 0), "next": act.get("next", ""),
            "schedule": data["workers"].get(wid + ":schedule", spec["schedule"]),
            "next_run": next_daily(data["workers"].get(wid + ":schedule", spec["schedule"])),
            "trigger": spec["trigger"],
            "enabled": data["workers"].get(wid + ":enabled", True),
        })
    return out
