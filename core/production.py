"""Productions: an idea turned into an explainer package, plus benchmark comparisons.

A production is a list of *scenes* (who speaks, what they say, what is on screen).
Everything else is derived from it, deterministically:

- ``script.md`` in StudioForge's format (``##`` chapters, ``_(stage directions)_``),
  so the edit on the Windows machine picks it up like any hand-written script;
- a VibeVoice script (``[1]: line``), one block per scene, for TTS through ComfyUI;
- a Remotion project (``remotion/``) whose scene timings come from the real length
  of each scene's audio once it exists, and from a words-per-minute estimate until
  then;
- the StudioForge graphics import (``{"graphics": [...]}``) for benchmark and
  comparison screens, and the benchmark notes table its ``benchmarks.py`` parses.

Benchmark numbers never come from the script writer. A benchmark scene names rows
in the benchmarks table (``bench_ids``) and the numbers are filled in from there,
so a video can only show a time somebody actually measured.

Pure and network-free like ``core/trend_radar.py``; ComfyUI calls, the render job
and reference-video analysis live in ``tools/trend_radar.py``.
"""
from __future__ import annotations

import io
import json
import re
import shutil
import struct
import time
import zipfile
from pathlib import Path

from core import trend_radar as tr

PROD_DIR = "productions"
VISUALS = ("title", "bullets", "code", "benchmark", "comparison", "image", "quote", "clip")
FPS = 30
WPM = 150                       # narration estimate until real audio exists
PAD_SECONDS = 0.5               # breath between scenes
THEME = {                       # the channel's Remotion colour coding
    "bg": "#0a0a0a", "panel": "#151515", "ink": "#f4f4f0", "muted": "#9a9a92",
    "accent": "#FFDD00",        # speed / stats
    "good": "#00FFCC",          # working features / chapters
    "bad": "#FF3B30",           # errors
    "font": "Inter, 'Helvetica Neue', Arial, sans-serif",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS productions (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    idea_id   INTEGER,
    title     TEXT NOT NULL,
    scenes    TEXT NOT NULL DEFAULT '[]',
    speakers  TEXT NOT NULL DEFAULT '{}',
    script_md TEXT NOT NULL DEFAULT '',
    reference TEXT NOT NULL DEFAULT '{}',
    notes     TEXT NOT NULL DEFAULT '',
    status    TEXT NOT NULL DEFAULT 'draft',
    render    TEXT NOT NULL DEFAULT '{}',
    auto      INTEGER NOT NULL DEFAULT 0,
    created   INTEGER NOT NULL,
    updated   INTEGER NOT NULL
);
"""

# Columns added to core/trend_radar's benchmarks table for manual runs and the
# comparison view. Added with ALTER TABLE so existing databases upgrade in place.
_BENCH_COLUMNS = {
    "label": "TEXT NOT NULL DEFAULT ''",
    "hw": "TEXT NOT NULL DEFAULT ''",
    "source": "TEXT NOT NULL DEFAULT 'auto'",
    "grp": "TEXT NOT NULL DEFAULT ''",
    "file": "TEXT NOT NULL DEFAULT ''",
    "mark": "INTEGER NOT NULL DEFAULT 0",
    "notes": "TEXT NOT NULL DEFAULT ''",
}


def _connect(root: Path):
    con = tr._connect(root)
    con.executescript(_SCHEMA)
    have = {r[1] for r in con.execute("PRAGMA table_info(benchmarks)")}
    for col, decl in _BENCH_COLUMNS.items():
        if col not in have:
            con.execute(f"ALTER TABLE benchmarks ADD COLUMN {col} {decl}")
    return con


# ── time helpers ─────────────────────────────────────────────────────────────

_TC = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")


def parse_time(value) -> float:
    """Seconds from ``h:mm:ss`` / ``mm:ss`` / a number. Raises on anything else."""
    if isinstance(value, (int, float)):
        if value < 0:
            raise ValueError("time cannot be negative")
        return float(value)
    s = str(value or "").strip()
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return float(s)
    if not _TC.match(s):
        raise ValueError(f"bad time {value!r} (want MM:SS, H:MM:SS or seconds)")
    secs = 0
    for part in s.split(":"):
        secs = secs * 60 + int(part)
    return float(secs)


def fmt_time(seconds: float | None) -> str:
    """StudioForge's timecode: ``m:ss`` under an hour, ``h:mm:ss`` above."""
    if seconds is None:
        return ""
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


# ── benchmarks: manual rows and comparisons ──────────────────────────────────

def add_manual_benchmark(root: Path, row: dict) -> dict:
    """A run measured by hand (or imported from a StudioForge notes table)."""
    label = (row.get("label") or "").strip()
    if not label:
        raise ValueError("a benchmark needs a label")
    status = (row.get("status") or "ok").strip()
    secs = parse_time(row["time"]) if row.get("time") not in (None, "") else None
    if status == "ok" and secs is None:
        raise ValueError("an ok run needs a time")
    vram = row.get("vram_gb")
    vram_b = int(float(vram) * 1024 ** 3) if vram not in (None, "") else None
    now = int(time.time())
    with tr._LOCK, _connect(root) as con:
        bid = con.execute(
            "INSERT INTO benchmarks (slug, template, started, finished, status, seconds, vram_peak,"
            " detail, label, hw, source, grp, file, mark, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ((row.get("slug") or "").strip()[:80], (row.get("template") or "")[:80], now, now,
             status[:40], secs, vram_b, "", label[:160], (row.get("hw") or "")[:160], "manual",
             (row.get("group") or "").strip().lower()[:60], (row.get("file") or "")[:80],
             1 if row.get("mark") else 0, (row.get("notes") or "")[:1000])).lastrowid
    return get_benchmark(root, int(bid)) or {}


def import_notes_table(root: Path, text: str, *, slug: str = "", hw: str = "") -> int:
    """Import StudioForge's ``group | label | file | time | mark`` table (or CSV)."""
    rows = _parse_table(text)
    n = 0
    for r in rows:
        add_manual_benchmark(root, {"slug": slug, "hw": hw, "label": r.get("label", ""),
                                    "time": r.get("time", ""), "group": r.get("group", ""),
                                    "file": r.get("file", ""), "mark": bool(r.get("mark", "").strip())})
        n += 1
    return n


def _parse_table(text: str) -> list[dict]:
    text = (text or "").lstrip("﻿")
    if "|" in text:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("|")]
        lines = [ln for ln in lines if not re.match(r"^\|[\s:|-]+\|$", ln)]
        if not lines:
            return []
        cells = lambda ln: [c.strip() for c in ln.strip("|").split("|")]
        head = [h.lower() for h in cells(lines[0])]
        return [dict(zip(head, cells(ln))) for ln in lines[1:]]
    import csv
    return [{k.strip().lower(): (v or "") for k, v in r.items()} for r in csv.DictReader(io.StringIO(text))]


def get_benchmark(root: Path, bid: int) -> dict | None:
    with tr._LOCK, _connect(root) as con:
        r = con.execute("SELECT * FROM benchmarks WHERE id=?", (int(bid),)).fetchone()
    return _bench_row(r) if r else None


def _bench_row(r) -> dict:
    d = dict(r)
    d["time"] = fmt_time(d.get("seconds"))
    d["vram_gb"] = round(d["vram_peak"] / 1024 ** 3, 2) if d.get("vram_peak") else None
    d["display"] = d.get("label") or d.get("template") or f"run #{d['id']}"
    return d


def all_benchmarks(root: Path, *, slug: str = "", limit: int = 500) -> list[dict]:
    q, args = "SELECT * FROM benchmarks", []
    if slug:
        q += " WHERE slug=?"
        args.append(slug)
    q += " ORDER BY slug, grp, seconds IS NULL, seconds LIMIT ?"
    args.append(int(limit))
    with tr._LOCK, _connect(root) as con:
        return [_bench_row(r) for r in con.execute(q, args).fetchall()]


def update_benchmark(root: Path, bid: int, updates: dict) -> dict:
    allowed = {"label", "hw", "grp", "mark", "notes", "slug", "file"}
    bad = set(updates) - allowed
    if bad:
        raise ValueError(f"cannot set {sorted(bad)}")
    if not updates:
        return get_benchmark(root, bid) or {}
    sets = ", ".join(f"{k}=?" for k in updates)
    with tr._LOCK, _connect(root) as con:
        n = con.execute(f"UPDATE benchmarks SET {sets} WHERE id=?", (*updates.values(), int(bid))).rowcount
    if not n:
        raise KeyError(bid)
    return get_benchmark(root, bid) or {}


def delete_benchmark(root: Path, bid: int) -> bool:
    with tr._LOCK, _connect(root) as con:
        return con.execute("DELETE FROM benchmarks WHERE id=?", (int(bid),)).rowcount > 0


def compare(left: dict, right: dict) -> dict:
    """Left is the baseline, right the variant, StudioForge's convention."""
    a, b = left.get("seconds"), right.get("seconds")
    out = {"left": left, "right": right, "pct": None, "saved_seconds": None, "faster": None, "label": ""}
    if a and b:
        saved = a - b
        pct = round(saved / a * 100)
        out.update(pct=abs(pct), saved_seconds=int(round(saved)), faster=saved > 0,
                   label=f"{abs(pct)}% {'faster' if saved > 0 else 'slower'} "
                         f"({fmt_time(abs(saved))} {'saved' if saved > 0 else 'added'})")
    return out


def studioforge_graphics(runs: list[dict], *, heading: str, subheading: str = "",
                         specs: list[dict] | None = None, pair: tuple[dict, dict] | None = None) -> dict:
    """The import document StudioForge's benchmark/comparison comps read."""
    graphics = []
    timed = [r for r in runs if r.get("seconds")]
    if timed:
        graphics.append({"id": "benchmark", "props": {
            "theme": "signal", "heading": heading.upper(), "subheading": subheading,
            "runs": [{"label": r["display"], "time": fmt_time(r["seconds"]),
                      **({"accent": True} if r.get("mark") else {})} for r in timed],
            "specs": specs or []}})
    if pair and pair[0].get("seconds") and pair[1].get("seconds"):
        c = compare(*pair)
        graphics.append({"id": "comparison", "props": {
            "theme": "signal", "heading": f"{pair[0]['display']} vs {pair[1]['display']}".upper(),
            "leftId": pair[0]["display"].upper(), "leftTime": fmt_time(pair[0]["seconds"]),
            "rightId": pair[1]["display"].upper(), "rightTime": fmt_time(pair[1]["seconds"]),
            "specs": specs or [],
            "delta": {"pct": c["pct"], "savedSeconds": c["saved_seconds"], "faster": c["faster"],
                      "label": c["label"]}}})
    return {"graphics": graphics}


def notes_table(runs: list[dict]) -> str:
    """Runs as StudioForge's ``benchmarks.py`` notes table."""
    lines = ["| group | label | file | time | mark |", "|---|---|---|---|---|"]
    for r in runs:
        if r.get("seconds"):
            lines.append(f"| {r.get('grp') or 'gguf'} | {r['display']} | {r.get('file') or ''} | "
                         f"{fmt_time(r['seconds'])} | {'x' if r.get('mark') else ''} |")
    return "\n".join(lines) + "\n"


# ── productions ──────────────────────────────────────────────────────────────

def _clean_scenes(scenes: list) -> list[dict]:
    if not isinstance(scenes, list) or not scenes:
        raise ValueError("a production needs at least one scene")
    if len(scenes) > 80:
        raise ValueError("80 scenes max")
    out = []
    for i, s in enumerate(scenes, 1):
        if not isinstance(s, dict):
            raise ValueError(f"scene {i} must be an object")
        vis = s.get("visual") or {"type": "title"}
        vtype = vis.get("type") or "title"
        if vtype not in VISUALS:
            raise ValueError(f"scene {i}: visual.type must be one of {VISUALS}")
        if vtype in ("benchmark", "comparison"):
            ids = vis.get("bench_ids") or []
            if not ids or not all(isinstance(x, int) for x in ids):
                raise ValueError(f"scene {i}: {vtype} needs bench_ids (rows from the benchmarks "
                                 "table); numbers are never typed into a script")
            if vtype == "comparison" and len(ids) != 2:
                raise ValueError(f"scene {i}: comparison needs exactly two bench_ids")
        narration = (s.get("narration") or "").strip()
        if len(narration) > 4000:
            raise ValueError(f"scene {i}: narration over 4000 characters")
        speaker = int(s.get("speaker") or 1)
        if not 1 <= speaker <= 4:
            raise ValueError(f"scene {i}: speaker must be 1-4 (VibeVoice)")
        clean_vis = {"type": vtype}
        for k in ("heading", "code", "image", "clip", "caption", "language"):
            if vis.get(k):
                clean_vis[k] = str(vis[k])[:4000]
        if vis.get("lines"):
            clean_vis["lines"] = [str(x)[:200] for x in vis["lines"]][:8]
        if vis.get("bench_ids"):
            clean_vis["bench_ids"] = [int(x) for x in vis["bench_ids"]][:12]
        if vis.get("clip_start") is not None:
            clean_vis["clip_start"] = float(vis["clip_start"])
        out.append({"id": s.get("id") or f"s{i}", "chapter": (s.get("chapter") or "").strip()[:80],
                    "speaker": speaker, "narration": narration, "visual": clean_vis,
                    "direction": (s.get("direction") or "").strip()[:500],
                    **({"audio": s["audio"]} if isinstance(s.get("audio"), dict) else {})})
    ids = [s["id"] for s in out]
    if len(set(ids)) != len(ids):
        raise ValueError("scene ids must be unique")
    for sid in ids:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", sid):
            raise ValueError(f"scene id {sid!r}: letters, digits, - and _ only")
    return out


def script_md(title: str, scenes: list[dict]) -> str:
    """StudioForge's script format: ``##`` chapters, ``_(stage directions)_``."""
    out = [f"# {title}", ""]
    current = None
    for s in scenes:
        ch = s.get("chapter") or ""
        if ch and ch != current:
            out += [f"## {ch}", ""]
            current = ch
        if s.get("narration"):
            out += [s["narration"], ""]
        vis = s.get("visual") or {}
        note = s.get("direction") or _describe_visual(vis)
        if note:
            out += [f"_({note})_", ""]
    return "\n".join(out).rstrip() + "\n"


def _describe_visual(vis: dict) -> str:
    t = vis.get("type")
    if t == "title":
        return f"Title card: {vis.get('heading', '')}".strip(": ")
    if t == "bullets":
        return "On screen: " + "; ".join(vis.get("lines") or [])
    if t == "code":
        return "Code on screen"
    if t in ("benchmark", "comparison"):
        return f"{t.title()} screen"
    if t == "clip":
        return f"Clip: {vis.get('clip', '')}"
    if t == "image":
        return f"Image: {vis.get('image', '')}"
    return ""


def voice_script(scenes: list[dict]) -> str:
    """VibeVoice's multi-speaker format, one ``[N]: line`` per scene."""
    return "\n".join(f"[{s['speaker']}]: {re.sub(r'\s+', ' ', s['narration'])}"
                     for s in scenes if s.get("narration")) + "\n"


def estimate_seconds(text: str) -> float:
    words = len(re.findall(r"\S+", text or ""))
    return max(2.5, words / WPM * 60)


def scene_seconds(s: dict) -> float:
    a = s.get("audio") or {}
    if a.get("seconds"):
        return float(a["seconds"]) + PAD_SECONDS
    return estimate_seconds(s.get("narration", "")) + PAD_SECONDS


def _row(r) -> dict:
    d = dict(r)
    for k, default in (("scenes", []), ("speakers", {}), ("reference", {}), ("render", {})):
        try:
            d[k] = json.loads(d.get(k) or json.dumps(default))
        except ValueError:
            d[k] = default
    d["seconds"] = round(sum(scene_seconds(s) for s in d["scenes"]), 1)
    d["voiced"] = sum(1 for s in d["scenes"] if (s.get("audio") or {}).get("file"))
    return d


def save_production(root: Path, data: dict, pid: int | None = None) -> dict:
    """Create (pid None) or replace a production's script. Audio already made for a
    scene is kept when the scene id and its narration are unchanged."""
    title = (data.get("title") or "").strip()
    if not title:
        raise ValueError("a production needs a title")
    scenes = _clean_scenes(data.get("scenes") or [])
    speakers = {str(k): str(v)[:40] for k, v in (data.get("speakers") or {"1": "Frizzy"}).items()}
    now = int(time.time())
    with tr._LOCK, _connect(root) as con:
        if pid is not None:
            cur = con.execute("SELECT * FROM productions WHERE id=?", (int(pid),)).fetchone()
            if not cur:
                raise KeyError(pid)
            old = {s["id"]: s for s in json.loads(cur["scenes"] or "[]")}
            for s in scenes:
                prev = old.get(s["id"])
                if prev and prev.get("audio") and prev.get("narration") == s["narration"] \
                        and prev.get("speaker") == s["speaker"]:
                    s["audio"] = prev["audio"]
                else:
                    s.pop("audio", None)
        idea_id = data.get("idea_id")
        if idea_id is not None and not con.execute("SELECT 1 FROM ideas WHERE id=?", (int(idea_id),)).fetchone():
            raise KeyError(f"no idea #{idea_id}")
        md = (data.get("script_md") or "").strip() or script_md(title, scenes)
        ref = data.get("reference") if isinstance(data.get("reference"), dict) else {}
        if pid is None:
            pid = con.execute(
                "INSERT INTO productions (idea_id, title, scenes, speakers, script_md, reference, notes,"
                " created, updated) VALUES (?,?,?,?,?,?,?,?,?)",
                (idea_id, title[:200], json.dumps(scenes), json.dumps(speakers), md,
                 json.dumps(ref), (data.get("notes") or "")[:4000], now, now)).lastrowid
        else:
            con.execute("UPDATE productions SET idea_id=COALESCE(?, idea_id), title=?, scenes=?, speakers=?,"
                        " script_md=?, reference=CASE WHEN ?='{}' THEN reference ELSE ? END, notes=?,"
                        " status='draft', updated=? WHERE id=?",
                        (idea_id, title[:200], json.dumps(scenes), json.dumps(speakers), md,
                         json.dumps(ref), json.dumps(ref), (data.get("notes") or "")[:4000], now, int(pid)))
    return get_production(root, int(pid)) or {}


def get_production(root: Path, pid: int) -> dict | None:
    with tr._LOCK, _connect(root) as con:
        r = con.execute("SELECT * FROM productions WHERE id=?", (int(pid),)).fetchone()
    return _row(r) if r else None


def productions(root: Path, limit: int = 100) -> list[dict]:
    with tr._LOCK, _connect(root) as con:
        rows = con.execute("SELECT * FROM productions ORDER BY updated DESC LIMIT ?", (int(limit),)).fetchall()
    out = []
    for r in rows:
        d = _row(r)
        d["scene_count"] = len(d.pop("scenes"))
        d.pop("script_md", None)
        out.append(d)
    return out


def delete_production(root: Path, pid: int) -> bool:
    with tr._LOCK, _connect(root) as con:
        n = con.execute("DELETE FROM productions WHERE id=?", (int(pid),)).rowcount
    folder = prod_dir(root, pid, create=False)
    if folder.exists():
        shutil.rmtree(folder, ignore_errors=True)
    return n > 0


def set_scene_audio(root: Path, pid: int, scene_id: str, audio: dict | None) -> dict:
    with tr._LOCK, _connect(root) as con:
        r = con.execute("SELECT scenes FROM productions WHERE id=?", (int(pid),)).fetchone()
        if not r:
            raise KeyError(pid)
        scenes = json.loads(r["scenes"])
        for s in scenes:
            if s["id"] == scene_id:
                if audio:
                    s["audio"] = audio
                else:
                    s.pop("audio", None)
                break
        else:
            raise KeyError(scene_id)
        voiced = all((s.get("audio") or {}).get("file") for s in scenes if s.get("narration"))
        con.execute("UPDATE productions SET scenes=?, status=CASE WHEN ? THEN 'voiced' ELSE status END,"
                    " updated=? WHERE id=?", (json.dumps(scenes), voiced, int(time.time()), int(pid)))
    return get_production(root, pid) or {}


def set_auto(root: Path, pid: int, on: bool) -> dict:
    """Approve (or withdraw) a production for the night run."""
    with tr._LOCK, _connect(root) as con:
        if not con.execute("UPDATE productions SET auto=?, updated=? WHERE id=?",
                           (1 if on else 0, int(time.time()), int(pid))).rowcount:
            raise KeyError(pid)
    return get_production(root, pid) or {}


def set_render(root: Path, pid: int, render: dict) -> None:
    status = "rendered" if render.get("state") == "done" else None
    with tr._LOCK, _connect(root) as con:
        con.execute("UPDATE productions SET render=?, status=COALESCE(?, status), updated=? WHERE id=?",
                    (json.dumps(render)[:20000], status, int(time.time()), int(pid)))


def prod_dir(root: Path, pid: int, *, create: bool = True) -> Path:
    p = Path(root) / "data" / PROD_DIR / f"p{int(pid)}"
    if create:
        for sub in ("audio", "out", "assets"):
            (p / sub).mkdir(parents=True, exist_ok=True)
    return p


# ── audio length without ffmpeg ──────────────────────────────────────────────

def audio_seconds(data: bytes) -> float | None:
    """Duration of a WAV or FLAC file from its header; None for other formats."""
    try:
        if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
            pos, rate, block = 12, None, None
            while pos + 8 <= len(data):
                cid, size = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
                if cid == b"fmt ":
                    rate = struct.unpack("<I", data[pos + 12:pos + 16])[0]
                    block = struct.unpack("<H", data[pos + 20:pos + 22])[0]
                elif cid == b"data" and rate and block:
                    size = min(size, len(data) - pos - 8)
                    return round(size / (rate * block), 3)
                pos += 8 + size + (size & 1)
        if data[:4] == b"fLaC":
            info = data[8:8 + 34]                       # STREAMINFO is always first
            packed = int.from_bytes(info[10:18], "big")
            rate = packed >> 44
            total = packed & ((1 << 36) - 1)
            if rate and total:
                return round(total / rate, 3)
    except (struct.error, IndexError):
        return None
    return None


# ── the bench rows a production's scenes point at ───────────────────────────

def resolve_bench(root: Path, scenes: list[dict]) -> dict[int, dict]:
    ids = {i for s in scenes for i in (s.get("visual") or {}).get("bench_ids") or []}
    out = {}
    for i in ids:
        b = get_benchmark(root, i)
        if b:
            out[i] = b
    return out


# ── Remotion project ─────────────────────────────────────────────────────────

def remotion_data(root: Path, prod: dict, *, theme: dict | None = None) -> dict:
    bench = resolve_bench(root, prod["scenes"])
    scenes = []
    for s in prod["scenes"]:
        vis = dict(s["visual"])
        if vis.get("bench_ids"):
            runs = [bench[i] for i in vis["bench_ids"] if i in bench]
            vis["runs"] = [{"label": r["display"], "seconds": r["seconds"], "time": r["time"],
                            "status": r["status"], "accent": bool(r.get("mark"))} for r in runs]
            if vis["type"] == "comparison" and len(runs) == 2:
                c = compare(runs[0], runs[1])
                vis["delta"] = {"pct": c["pct"], "faster": c["faster"], "label": c["label"]}
        audio = s.get("audio") or {}
        scenes.append({"id": s["id"], "chapter": s.get("chapter", ""), "narration": s.get("narration", ""),
                       "visual": vis, "seconds": round(scene_seconds(s), 3),
                       "audio": f"audio/{Path(audio['file']).name}" if audio.get("file") else None})
    return {"title": prod["title"], "fps": FPS, "width": 1920, "height": 1080,
            "theme": {**THEME, **(theme or {})}, "scenes": scenes}


_REMOTION_FILES = {
    "package.json": json.dumps({
        "name": "plutus-explainer", "private": True, "version": "1.0.0",
        "scripts": {"studio": "remotion studio src/index.ts",
                    "render": "remotion render src/index.ts Explainer out/explainer.mp4"},
        "dependencies": {"@remotion/cli": "^4.0.0", "remotion": "^4.0.0",
                         "react": "^18.3.1", "react-dom": "^18.3.1"},
        "devDependencies": {"@types/react": "^18.3.0", "typescript": "^5.6.0"},
    }, indent=2) + "\n",
    "tsconfig.json": json.dumps({"compilerOptions": {
        "target": "ES2020", "module": "ESNext", "moduleResolution": "Bundler", "jsx": "react-jsx",
        "strict": True, "skipLibCheck": True, "resolveJsonModule": True, "esModuleInterop": True,
        "noEmit": True}, "include": ["src"]}, indent=2) + "\n",
    "remotion.config.ts": "import {Config} from '@remotion/cli/config';\n\n"
                          "Config.setVideoImageFormat('jpeg');\nConfig.setOverwriteOutput(true);\n",
    "src/index.ts": "import {registerRoot} from 'remotion';\nimport {RemotionRoot} from './Root';\n\n"
                    "registerRoot(RemotionRoot);\n",
    "src/Root.tsx": """import React from 'react';
import {Composition} from 'remotion';
import {Explainer, totalFrames} from './Explainer';
import data from './data.json';
import type {ExplainerData} from './types';

const d = data as ExplainerData;

export const RemotionRoot: React.FC = () => (
  <Composition
    id="Explainer"
    component={Explainer}
    durationInFrames={totalFrames(d)}
    fps={d.fps}
    width={d.width}
    height={d.height}
    defaultProps={{data: d}}
  />
);
""",
    "src/types.ts": """export type Run = {label: string; seconds: number | null; time: string; status: string; accent: boolean};
export type Visual = {
  type: 'title' | 'bullets' | 'code' | 'benchmark' | 'comparison' | 'image' | 'quote' | 'clip';
  heading?: string;
  lines?: string[];
  code?: string;
  language?: string;
  image?: string;
  clip?: string;
  clip_start?: number;
  caption?: string;
  runs?: Run[];
  delta?: {pct: number | null; faster: boolean | null; label: string};
};
export type Scene = {id: string; chapter: string; narration: string; visual: Visual; seconds: number; audio: string | null};
export type Theme = Record<'bg' | 'panel' | 'ink' | 'muted' | 'accent' | 'good' | 'bad' | 'font', string>;
export type ExplainerData = {title: string; fps: number; width: number; height: number; theme: Theme; scenes: Scene[]};
""",
    "src/Explainer.tsx": """import React from 'react';
import {AbsoluteFill, Audio, Img, OffthreadVideo, Sequence, interpolate, spring, staticFile,
  useCurrentFrame, useVideoConfig} from 'remotion';
import type {ExplainerData, Run, Scene, Theme} from './types';

export const totalFrames = (d: ExplainerData) =>
  Math.max(1, d.scenes.reduce((n, s) => n + Math.round(s.seconds * d.fps), 0));

const useIn = (delay = 0) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  return spring({frame: frame - delay, fps, config: {damping: 200}});
};

const Rise: React.FC<{delay?: number; children: React.ReactNode}> = ({delay = 0, children}) => {
  const p = useIn(delay);
  return <div style={{opacity: p, transform: `translateY(${(1 - p) * 30}px)`}}>{children}</div>;
};

const Chapter: React.FC<{t: Theme; label: string}> = ({t, label}) =>
  label ? (
    <div style={{position: 'absolute', top: 64, left: 96, color: t.good, fontSize: 28, letterSpacing: 4,
      fontWeight: 700, textTransform: 'uppercase'}}>{label}</div>
  ) : null;

const fmt = (s: number | null) => {
  if (s == null) return '';
  const v = Math.round(s), h = Math.floor(v / 3600), m = Math.floor((v % 3600) / 60), x = v % 60;
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(x).padStart(2, '0')}` : `${m}:${String(x).padStart(2, '0')}`;
};

const Bars: React.FC<{t: Theme; runs: Run[]}> = ({t, runs}) => {
  const frame = useCurrentFrame();
  const max = Math.max(...runs.map((r) => r.seconds ?? 0), 1);
  return (
    <div style={{display: 'flex', flexDirection: 'column', gap: 26, width: 1500}}>
      {runs.map((r, i) => {
        const grow = interpolate(frame - 10 - i * 8, [0, 40], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
        const failed = r.status !== 'ok' || r.seconds == null;
        const w = failed ? 1 : (r.seconds! / max) * grow;
        return (
          <div key={i}>
            <div style={{display: 'flex', justifyContent: 'space-between', color: t.ink, fontSize: 34, marginBottom: 8}}>
              <span>{r.label}</span>
              <span style={{color: failed ? t.bad : r.accent ? t.accent : t.ink, fontWeight: 800}}>
                {failed ? r.status.toUpperCase() : fmt((r.seconds ?? 0) * grow)}
              </span>
            </div>
            <div style={{height: 34, background: t.panel, borderRadius: 6, overflow: 'hidden'}}>
              <div style={{height: '100%', width: `${w * 100}%`, borderRadius: 6,
                background: failed ? t.bad : r.accent ? t.accent : t.muted}} />
            </div>
          </div>
        );
      })}
    </div>
  );
};

const Visual: React.FC<{s: Scene; t: Theme}> = ({s, t}) => {
  const v = s.visual;
  const h = v.heading ? (
    <Rise><div style={{color: t.ink, fontSize: v.type === 'title' ? 110 : 64, fontWeight: 900, lineHeight: 1.05,
      marginBottom: 40, maxWidth: 1600}}>{v.heading}</div></Rise>
  ) : null;
  switch (v.type) {
    case 'title':
      return <div>{h}{v.caption && <Rise delay={8}><div style={{color: t.accent, fontSize: 44}}>{v.caption}</div></Rise>}</div>;
    case 'bullets':
      return (
        <div>{h}
          {(v.lines ?? []).map((l, i) => (
            <Rise key={i} delay={6 + i * 10}>
              <div style={{color: t.ink, fontSize: 52, margin: '18px 0', display: 'flex', gap: 24}}>
                <span style={{color: t.accent}}>▸</span><span>{l}</span>
              </div>
            </Rise>
          ))}
        </div>
      );
    case 'code':
      return (
        <div>{h}<Rise delay={6}>
          <pre style={{background: t.panel, color: t.good, fontSize: 34, padding: 40, borderRadius: 12, margin: 0,
            fontFamily: 'JetBrains Mono, Consolas, monospace', whiteSpace: 'pre-wrap', maxWidth: 1600}}>{v.code}</pre>
        </Rise></div>
      );
    case 'benchmark':
      return <div>{h}<Bars t={t} runs={v.runs ?? []} /></div>;
    case 'comparison':
      return (
        <div>{h}<Bars t={t} runs={v.runs ?? []} />
          {v.delta?.label && <Rise delay={50}><div style={{marginTop: 48, fontSize: 64, fontWeight: 900,
            color: v.delta.faster ? t.good : t.bad}}>{v.delta.label}</div></Rise>}
        </div>
      );
    case 'image':
      return (
        <div>{h}{v.image && <Rise delay={4}><Img src={staticFile(v.image)} style={{maxWidth: 1600, maxHeight: 700,
          borderRadius: 12}} /></Rise>}</div>
      );
    case 'quote':
      return <Rise><div style={{color: t.ink, fontSize: 72, fontWeight: 700, maxWidth: 1500, borderLeft: `10px solid ${t.accent}`,
        paddingLeft: 48}}>{v.heading ?? s.narration}</div></Rise>;
    case 'clip':
      return v.clip ? (
        <AbsoluteFill>
          <OffthreadVideo src={staticFile(v.clip)} startFrom={Math.round((v.clip_start ?? 0) * 30)} muted />
          {v.caption && <div style={{position: 'absolute', bottom: 80, left: 96, color: t.ink, fontSize: 44,
            background: 'rgba(0,0,0,.6)', padding: '12px 24px', borderRadius: 8}}>{v.caption}</div>}
        </AbsoluteFill>
      ) : <div style={{color: t.muted, fontSize: 40}}>[clip: drop the file in public/ and set visual.clip]</div>;
  }
};

export const Explainer: React.FC<{data: ExplainerData}> = ({data}) => {
  const t = data.theme;
  let from = 0;
  return (
    <AbsoluteFill style={{background: t.bg, fontFamily: t.font}}>
      {data.scenes.map((s) => {
        const dur = Math.max(1, Math.round(s.seconds * data.fps));
        const seq = (
          <Sequence key={s.id} from={from} durationInFrames={dur} name={s.id}>
            <AbsoluteFill style={{padding: '160px 96px 96px', justifyContent: 'center'}}>
              <Chapter t={t} label={s.chapter} />
              <Visual s={s} t={t} />
            </AbsoluteFill>
            {s.audio && <Audio src={staticFile(s.audio)} />}
          </Sequence>
        );
        from += dur;
        return seq;
      })}
    </AbsoluteFill>
  );
};
""",
}


def write_remotion(root: Path, prod: dict, *, theme: dict | None = None) -> Path:
    """(Re)write the production's Remotion project. node_modules is left alone, so a
    second render does not reinstall."""
    base = prod_dir(root, prod["id"])
    rdir = base / "remotion"
    for rel, text in _REMOTION_FILES.items():
        p = rdir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    (rdir / "src" / "data.json").write_text(json.dumps(remotion_data(root, prod, theme=theme), indent=2),
                                            encoding="utf-8")
    pub = rdir / "public"
    if pub.exists():
        shutil.rmtree(pub)
    (pub / "audio").mkdir(parents=True)
    for f in (base / "audio").glob("*"):
        if f.is_file():
            shutil.copy2(f, pub / "audio" / f.name)
    for f in (base / "assets").glob("*"):
        if f.is_file():
            shutil.copy2(f, pub / f.name)
    (rdir / "out").mkdir(exist_ok=True)
    return rdir


# ── the downloadable package ─────────────────────────────────────────────────

def _readme(prod: dict, has_mp4: bool) -> str:
    return f"""# {prod['title']}

Made by Plutus (Trends > Produce). Everything here is plain files.

## Contents

- `script.md`: the script in StudioForge format (`##` chapters).
- `voice/`: one audio file per scene (VibeVoice via ComfyUI) and `voice_script.txt`
  in VibeVoice's `[1]: line` format, to regenerate the voice elsewhere.
- `benchmarks.md`: measured runs as StudioForge's notes table.
- `studioforge_import.json`: benchmark / comparison screens for StudioForge's Graphics import.
- `remotion/`: the explainer as a Remotion project.
{"- `explainer.mp4`: the rendered video." if has_mp4 else ""}

## Render the explainer on this computer

```
cd remotion
npm install
npx remotion studio      # preview and tweak
npx remotion render src/index.ts Explainer out/explainer.mp4
```

## Hand it to StudioForge

```
studioforge script "<project>" script.md
studioforge package "<project>" --voice voice/<first scene file> --clips remotion/out/explainer.mp4
```

Benchmark screens: StudioForge > Graphics > Import, pick `studioforge_import.json`.
"""


def build_package(root: Path, pid: int, idea: dict | None = None) -> tuple[bytes, str]:
    """The zip to download or drop into the export folder. Returns (bytes, filename)."""
    prod = get_production(root, pid)
    if not prod:
        raise KeyError(pid)
    rdir = write_remotion(root, prod)
    base = prod_dir(root, pid)
    bench = list(resolve_bench(root, prod["scenes"]).values())
    pair = None
    for s in prod["scenes"]:
        ids = (s.get("visual") or {}).get("bench_ids") or []
        if s["visual"]["type"] == "comparison" and len(ids) == 2:
            rows = {b["id"]: b for b in bench}
            if ids[0] in rows and ids[1] in rows:
                pair = (rows[ids[0]], rows[ids[1]])
            break
    mp4 = base / "out" / "explainer.mp4"
    slug = re.sub(r"[^a-z0-9]+", "-", prod["title"].lower()).strip("-")[:60] or f"production-{pid}"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{slug}/README.md", _readme(prod, mp4.exists()))
        z.writestr(f"{slug}/script.md", prod["script_md"])
        z.writestr(f"{slug}/voice/voice_script.txt", voice_script(prod["scenes"]))
        z.writestr(f"{slug}/scenes.json", json.dumps(prod["scenes"], indent=2))
        if idea:
            titles = [idea.get("title", "")] + list(idea.get("alt_titles") or [])
            z.writestr(f"{slug}/titles.md", "\n".join(f"- {t}" for t in titles if t) + "\n")
            if idea.get("description"):
                z.writestr(f"{slug}/description.md", idea["description"] + "\n")
            if idea.get("thumb_svg"):
                z.writestr(f"{slug}/thumbnail_mock.svg", idea["thumb_svg"])
        if bench:
            z.writestr(f"{slug}/benchmarks.md", notes_table(bench))
            z.writestr(f"{slug}/studioforge_import.json", json.dumps(studioforge_graphics(
                bench, heading=f"{prod['title']} render time", pair=pair), indent=2))
        for f in sorted((base / "audio").glob("*")):
            if f.is_file():
                z.write(f, f"{slug}/voice/{f.name}")
        if mp4.exists():
            z.write(mp4, f"{slug}/explainer.mp4")
        for f in sorted(rdir.rglob("*")):
            rel = f.relative_to(rdir)
            if f.is_file() and rel.parts[0] not in ("node_modules", "out", ".cache"):
                z.write(f, f"{slug}/remotion/{rel.as_posix()}")
    return buf.getvalue(), f"{slug}.zip"


# ── reference video: borrow a structure, not words ───────────────────────────

def reference_structure(video: dict, segments: list[tuple[float, str]] | None) -> dict:
    """Pacing of a video you want to imitate: chapter lengths, words per minute,
    the hook, and what viewers replayed. The script writer gets the *shape*, never
    the transcript to copy, so a reference cannot turn into a rewrite."""
    dur = float(video.get("duration") or 0)
    chapters = []
    for c in video.get("chapters") or []:
        chapters.append({"title": c["title"], "start": round(c["start"]), "seconds": round(c["end"] - c["start"])})
    segs = segments or []
    words = sum(len(t.split()) for _, t in segs)
    spoken = (segs[-1][0] - segs[0][0]) if len(segs) > 1 else dur
    wpm = round(words / (spoken / 60)) if spoken > 30 else None
    hook_words = sum(len(t.split()) for s, t in segs if s < 15)
    replay = []
    heat = video.get("heatmap") or []
    if heat:
        bins = heat[1:]
        ranked = sorted(range(len(bins)), key=lambda i: bins[i]["value"], reverse=True)
        picked: list[int] = []
        for i in ranked:
            if all(abs(i - j) > 2 for j in picked):
                picked.append(i)
            if len(picked) >= 4:
                break
        for i in picked:                      # strongest replay first
            b = bins[i]
            topic = next((c["title"] for c in chapters if c["start"] <= b["start"] < c["start"] + c["seconds"]), "")
            replay.append({"at": round(b["start"]), "chapter": topic, "strength": round(b["value"], 2)})
    return {
        "url": f"https://youtu.be/{video.get('id')}", "title": video.get("title", ""),
        "channel": video.get("channel", ""), "views": video.get("views"), "duration": round(dur),
        "chapters": chapters, "chapter_count": len(chapters),
        "avg_chapter_seconds": round(sum(c["seconds"] for c in chapters) / len(chapters)) if chapters else None,
        "words": words, "wpm": wpm, "hook_words_first_15s": hook_words, "most_replayed": replay,
        "note": "Use the shape (chapter count and length, pace, where the payoff lands). "
                "Do not reuse its wording.",
    }
