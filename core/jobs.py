"""core/jobs.py — the job board: one job per video folder on the NAS.

The YouTube folder on the NAS is the backbone. Every top-level folder named
``YYYYMMDD_Title`` is a job; folders starting with ``_`` or ``00_`` are shared
library material and never show up as jobs. Nothing in the video folders is ever
written by this module — the stage each job is at is *read off the files that
exist* (a SCRIPT.md, an ``edit`` folder, thumbnails in ``upload``), and the few
things files cannot say (published, a deadline, "archive this") live in
``data/jobs_state.json``.

Stages, in order: research, script, film, edit, packaging, publish, shorts.
A stage is ``done`` when a completion signal exists or a later stage has
evidence, ``active`` when it has evidence of its own, otherwise ``todo``. A
manual override in the state file always wins.
"""
from __future__ import annotations

import os
import re
import time
from datetime import date, datetime
from pathlib import Path

from core.atomic_json import read_json, write_json

STAGES = ("research", "script", "film", "edit", "packaging", "publish", "shorts")
STAGE_LABELS = {
    "research": "Research", "script": "Script", "film": "Film", "edit": "Edit",
    "packaging": "Packaging", "publish": "Publish", "shorts": "Shorts",
}
STATUSES = ("todo", "active", "done")

_JOB_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})_(.+)$")
_VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".mxf"}
_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
_TEXT_EXT = {".md", ".txt", ".json", ".srt", ".csv"}
_SKIP_DIRS = {"_old", "node_modules", ".git", "__pycache__"}

# Top-level folder names (lower-cased) that count as evidence for a stage. Real
# folders are named by hand, typos included, so film is matched by keyword too.
_STAGE_DIRS = {
    "research": {"research", "benchmarks", "tests", "results", "samples"},
    "film": {"recordings", "ecordings", "obs", "obs recordings", "voice over", "voice",
             "audio", "video", "videos", "talking head", "generations", "source clips"},
    "edit": {"edit"},
    "packaging": {"upload"},
    "shorts": {"shorts"},
}
_FILM_KEYWORDS = ("record", "talking head", "voice", "screen", "filmed")
_STAGE_FILES = {
    "research": (re.compile(r"^(plan|research|results|inventory|review)\b.*\.(md|pdf|txt)$", re.I),),
    "script": (re.compile(r"^(script|filming|read off|one sheet|graphics timing)\b.*\.(md|pdf|txt)$", re.I),),
    "edit": (re.compile(r".*\.fcpxml$", re.I), re.compile(r"^subtitles\.", re.I)),
}
_FINAL_EXPORT = re.compile(r"\((1080p|4k|2160p|final)\)\.(mp4|mov)$", re.I)
_SCAN_CAP = 600            # files looked at per stage folder; enough for dates, bounded on SMB
ARCHIVE_AFTER_DAYS = 30    # untouched this long => out of the active view
_CACHE_TTL = 120.0
_cache: dict = {"ts": 0.0, "root": "", "jobs": []}


def youtube_root() -> Path:
    """Where the NAS YouTube folder is mounted inside this process."""
    return Path(os.getenv("YOUTUBE_ROOT", "/data/youtube"))


def _state_path(root: Path) -> Path:
    return Path(root) / "data" / "jobs_state.json"


def _activity_path(root: Path) -> Path:
    return Path(root) / "data" / "jobs_activity.json"


def load_state(root: Path) -> dict:
    data = read_json(_state_path(root), {})
    return data if isinstance(data, dict) else {}


def _visible(name: str) -> bool:
    return not name.startswith(".") and name.lower() not in _SKIP_DIRS


def _walk_mtimes(folder: Path, depth: int = 2) -> tuple[int, float, float]:
    """(file count, oldest mtime, newest mtime) under ``folder``, bounded."""
    count, lo, hi = 0, 0.0, 0.0
    stack = [(folder, 0)]
    while stack and count < _SCAN_CAP:
        cur, d = stack.pop()
        try:
            entries = list(os.scandir(cur))
        except OSError:
            continue
        for e in entries:
            if not _visible(e.name):
                continue
            try:
                if e.is_dir(follow_symlinks=False):
                    if d < depth:
                        stack.append((Path(e.path), d + 1))
                    continue
                m = e.stat().st_mtime
            except OSError:
                continue
            count += 1
            lo = m if not lo or m < lo else lo
            hi = m if m > hi else hi
            if count >= _SCAN_CAP:
                break
    return count, lo, hi


def _stage_of_dir(name: str) -> str | None:
    low = name.lower().strip()
    for stage, names in _STAGE_DIRS.items():
        if low in names:
            return stage
    if any(k in low for k in _FILM_KEYWORDS):
        return "film"
    return None


def _stage_of_file(name: str) -> str | None:
    for stage, patterns in _STAGE_FILES.items():
        if any(p.match(name) for p in patterns):
            return stage
    if Path(name).suffix.lower() in _VIDEO_EXT:
        return "film"          # raw camera / OBS files dropped at the top of the folder
    return None


def _pick_thumbnails(upload: Path) -> list[str]:
    """Thumbnail candidates in upload/, best guess first."""
    try:
        imgs = [e for e in os.scandir(upload)
                if e.is_file() and _visible(e.name) and Path(e.name).suffix.lower() in _IMAGE_EXT]
    except OSError:
        return []

    def rank(e):
        low = e.name.lower()
        pri = (0 if low.startswith("thumbnail") else 1 if "fav" in low or "final" in low
               else 2 if low.startswith("thumb") else 3)
        return (pri, -e.stat().st_mtime)

    return [f"upload/{e.name}" for e in sorted(imgs, key=rank)[:12]]


def scan_job(folder: Path, state: dict | None = None) -> dict | None:
    """Read one video folder into a job record. None if it is not a job folder."""
    m = _JOB_RE.match(folder.name)
    if not m or not folder.is_dir():
        return None
    try:
        start = date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        return None
    st = (state or {}).get(folder.name) or {}
    title = m[4].replace("_", " ").strip()

    ev: dict[str, dict] = {s: {"files": 0, "first": 0.0, "last": 0.0, "items": []} for s in STAGES}

    def add(stage: str, label: str, n: int, lo: float, hi: float) -> None:
        if not n:
            return
        e = ev[stage]
        e["files"] += n
        e["first"] = lo if not e["first"] or (lo and lo < e["first"]) else e["first"]
        e["last"] = max(e["last"], hi)
        if len(e["items"]) < 8:
            e["items"].append(label)

    script = final_export = ""
    upload_pack = False
    top_files: list[str] = []
    try:
        entries = sorted(os.scandir(folder), key=lambda e: e.name.lower())
    except OSError:
        return None
    for e in entries:
        if not _visible(e.name):
            continue
        if e.is_dir(follow_symlinks=False):
            stage = _stage_of_dir(e.name)
            if stage:
                add(stage, e.name + "/", *_walk_mtimes(Path(e.path)))
            continue
        top_files.append(e.name)
        stage = _stage_of_file(e.name)
        if stage:
            mt = e.stat().st_mtime
            add(stage, e.name, 1, mt, mt)
        if e.name.lower() == "script.md":
            script = e.name

    edit_dir = folder / "edit"
    if edit_dir.is_dir():
        try:
            for e in os.scandir(edit_dir):
                if e.is_file() and _FINAL_EXPORT.search(e.name):
                    final_export = f"edit/{e.name}"
        except OSError:
            pass
    thumbs = _pick_thumbnails(folder / "upload")
    upload_pack = (folder / "upload" / "UPLOAD PACK.md").is_file()

    signals = {
        "edit": bool(final_export),
        "packaging": bool(thumbs) and upload_pack,
        "publish": bool(st.get("publish_url")),
    }
    overrides = st.get("stages") or {}
    stages = []
    later_evidence = False
    for s in reversed(STAGES):
        e = ev[s]
        has = e["files"] > 0
        if s == "publish":
            # Files cannot prove a video went live; only a saved URL does.
            auto = "done" if signals["publish"] else "todo"
        elif s == "packaging":
            # Packaging often comes first on this channel, so it is judged alone.
            auto = "done" if signals["packaging"] or signals["publish"] \
                else "active" if has else "todo"
        else:
            auto = "done" if signals.get(s) or later_evidence or signals["publish"] \
                else "active" if has else "todo"
        status = overrides.get(s) if overrides.get(s) in STATUSES else auto
        stages.append({
            "id": s, "label": STAGE_LABELS[s], "status": status, "auto": auto,
            "manual": s in overrides, "files": e["files"],
            "first": int(e["first"]) or None, "last": int(e["last"]) or None,
            "evidence": e["items"],
        })
        if s != "packaging":      # thumbnails existing says nothing about the edit
            later_evidence = later_evidence or has
    stages.reverse()

    current = next((s["id"] for s in stages if s["status"] != "done"), "done")
    last_activity = max([s["last"] or 0 for s in stages] + [0])
    if not last_activity:
        try:
            last_activity = int(folder.stat().st_mtime)
        except OSError:
            last_activity = 0
    idle_days = int((time.time() - last_activity) / 86400) if last_activity else None

    archived = st.get("archived")
    if archived is None:        # automatic: finished, or untouched for a month
        archived = current == "done" or (idle_days is not None and idle_days > ARCHIVE_AFTER_DAYS)
    if st.get("pinned"):
        archived = False

    return {
        "id": folder.name, "title": st.get("title") or title,
        "start": start.isoformat(), "sponsored": "sponsor" in folder.name.lower(),
        "stages": stages, "current": current,
        "last_activity": last_activity or None, "idle_days": idle_days,
        "archived": bool(archived), "archived_manual": st.get("archived") is not None,
        "pinned": bool(st.get("pinned")),
        "deadline": st.get("deadline") or "", "note": st.get("note") or "",
        "publish_url": st.get("publish_url") or "",
        "script": script, "final_export": final_export,
        "thumbnails": thumbs, "thumbnail": st.get("thumbnail") or (thumbs[0] if thumbs else ""),
        "top_files": top_files[:60],
    }


def list_jobs(root: Path, *, force: bool = False) -> dict:
    """Every job on the NAS, newest first, with the folder-level health of the scan."""
    yt = youtube_root()
    now = time.time()
    if (not force and _cache["jobs"] and _cache["root"] == str(yt)
            and now - _cache["ts"] < _CACHE_TTL):
        jobs = _cache["jobs"]
    else:
        jobs = []
        if yt.is_dir():
            state = load_state(root)
            try:
                names = sorted(os.listdir(yt), reverse=True)
            except OSError:
                names = []
            for name in names:
                job = scan_job(yt / name, state)
                if job:
                    jobs.append(job)
        _cache.update(ts=now, root=str(yt), jobs=jobs)
    return {"root": str(yt), "mounted": yt.is_dir(), "stages": [
        {"id": s, "label": STAGE_LABELS[s]} for s in STAGES], "jobs": jobs,
        "scanned": int(_cache["ts"])}


def get_job(root: Path, job_id: str) -> dict | None:
    folder = job_folder(job_id)
    return scan_job(folder, load_state(root)) if folder else None


def job_folder(job_id: str) -> Path | None:
    """The folder for a job id, or None. The id must be a plain folder name."""
    if not job_id or "/" in job_id or "\\" in job_id or job_id in (".", "..") \
            or not _JOB_RE.match(job_id):
        return None
    folder = youtube_root() / job_id
    return folder if folder.is_dir() else None


def resolve_file(job_id: str, rel: str) -> Path | None:
    """A file inside a job folder, refusing anything that escapes it."""
    folder = job_folder(job_id)
    if not folder or not rel:
        return None
    target = Path(os.path.realpath(folder / rel))
    base = os.path.realpath(folder)
    if not str(target).startswith(base + os.sep) or not target.is_file():
        return None
    return target


def file_kind(path: Path) -> str:
    ext = path.suffix.lower()
    return "image" if ext in _IMAGE_EXT else "text" if ext in _TEXT_EXT \
        else "video" if ext in _VIDEO_EXT else "pdf" if ext == ".pdf" else "other"


def list_folder(job_id: str, rel: str = "") -> list[dict] | None:
    """One level of a job folder, for the detail drawer."""
    folder = job_folder(job_id)
    if not folder:
        return None
    target = Path(os.path.realpath(folder / rel)) if rel else folder
    base = os.path.realpath(folder)
    if str(target) != base and not str(target).startswith(base + os.sep):
        return None
    out = []
    try:
        for e in sorted(os.scandir(target), key=lambda e: (not e.is_dir(), e.name.lower())):
            if e.name.startswith("."):
                continue
            is_dir = e.is_dir(follow_symlinks=False)
            st = e.stat()
            out.append({"name": e.name, "rel": os.path.relpath(e.path, base), "dir": is_dir,
                        "size": 0 if is_dir else st.st_size, "mtime": int(st.st_mtime),
                        "kind": "dir" if is_dir else file_kind(Path(e.name))})
    except OSError:
        return None
    return out[:400]


_ALLOWED_FIELDS = {"archived", "pinned", "deadline", "note", "publish_url", "title", "thumbnail"}


def update_job(root: Path, job_id: str, changes: dict) -> dict:
    """Apply manual facts to a job: stage overrides, archive, deadline, note, URL.

    ``changes["stages"]`` maps a stage id to todo/active/done, or to "auto" to drop
    the override and go back to what the files say. ``archived`` accepts None to
    return to automatic archiving.
    """
    if not job_folder(job_id):
        raise KeyError(f"no job folder named {job_id!r}")
    state = load_state(root)
    st = dict(state.get(job_id) or {})
    for key, val in changes.items():
        if key == "stages":
            cur = dict(st.get("stages") or {})
            for sid, status in (val or {}).items():
                if sid not in STAGES:
                    raise ValueError(f"unknown stage {sid!r}")
                if status in (None, "", "auto"):
                    cur.pop(sid, None)
                elif status in STATUSES:
                    cur[sid] = status
                else:
                    raise ValueError(f"unknown status {status!r}")
            st["stages"] = cur
        elif key in _ALLOWED_FIELDS:
            if key == "deadline" and val:
                datetime.strptime(str(val), "%Y-%m-%d")      # raises ValueError if malformed
            if val is None or val == "":
                st.pop(key, None)
            else:
                st[key] = val
        else:
            raise ValueError(f"unknown field {key!r}")
    st["updated"] = int(time.time())
    state[job_id] = st
    write_json(_state_path(root), state)
    _cache["ts"] = 0.0
    return get_job(root, job_id) or {}


def log_activity(root: Path, text: str, job_id: str = "", who: str = "") -> dict:
    """Record one finished piece of work. Feeds the board and the morning page."""
    rows = read_json(_activity_path(root), [])
    rows = rows if isinstance(rows, list) else []
    row = {"ts": int(time.time()), "job": job_id or "", "who": (who or "")[:60],
           "text": (text or "").strip()[:500]}
    rows.append(row)
    write_json(_activity_path(root), rows[-2000:])
    return row


def recent_activity(root: Path, job_id: str = "", limit: int = 50) -> list[dict]:
    rows = read_json(_activity_path(root), [])
    rows = rows if isinstance(rows, list) else []
    if job_id:
        rows = [r for r in rows if r.get("job") == job_id]
    return list(reversed(rows[-limit:]))
