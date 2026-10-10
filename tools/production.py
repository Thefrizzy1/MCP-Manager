"""
tools/production.py: the Produce side of the Trends tab.

- VibeVoice voiceover on the ComfyUI machine (COMFYUI_URL, usually the Windows PC
  with the real GPU), from a workflow template you export once.
- The explainer render: a generated Remotion project rendered with the Node that
  ships in the Plutus image, or downloaded as a zip and rendered elsewhere.
- Reference-video analysis (structure and pace, never the words).
- Wake-on-LAN for the ComfyUI machine before a night run.
- The night run itself, off by default.
- MCP tools so Claude (the Idea Studio, or you from Claude Desktop) can write a
  production, voice it, render it and read the benchmark comparisons.

Pure logic (scenes, script formats, benchmarks, the Remotion project) is in
``core/production.py``.
"""
import asyncio
import json
import os
import random
import re
import shutil
import socket
import time
import uuid
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from config import cfg
from core import production as pr
from core import trend_radar as tr

_ROOT = Path(__file__).resolve().parents[1]
NL = "\n"
AUDIO_EXT = (".flac", ".wav", ".mp3", ".ogg", ".opus", ".m4a")
_TASKS: set = set()
# One render and one voice job at a time: both saturate the machine they run on.
JOBS: dict[str, dict] = {}


def _job(kind: str) -> dict:
    return JOBS.setdefault(kind, {"running": False})


def _spawn(kind: str, coro) -> dict:
    j = _job(kind)
    if j.get("running"):
        raise RuntimeError(f"a {kind} job is already running")
    j.clear()
    j.update(running=True, started=int(time.time()), log=[], error=None, result=None)

    async def run():
        try:
            j["result"] = await coro
        except Exception as e:
            j["error"] = f"{type(e).__name__}: {str(e)[:300]}"
        finally:
            j["running"] = False
            j["finished"] = int(time.time())

    t = asyncio.get_running_loop().create_task(run())
    _TASKS.add(t)
    t.add_done_callback(_TASKS.discard)
    return {k: v for k, v in j.items()}


# ── Wake-on-LAN ──────────────────────────────────────────────────────────────

def magic_packet(mac: str) -> bytes:
    raw = bytes.fromhex(re.sub(r"[^0-9A-Fa-f]", "", mac))
    if len(raw) != 6:
        raise ValueError("MAC address must have 6 bytes")
    return b"\xff" * 6 + raw * 16


async def comfy_up() -> bool:
    if not cfg.is_configured("comfyui_url"):
        return False
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as c:
            return (await c.get(f"{cfg.comfyui_url.rstrip('/')}/system_stats")).status_code == 200
    except Exception:
        return False


async def wake_comfy(conf: dict) -> str:
    """Wake the ComfyUI machine if it is asleep and a MAC is configured.

    Needs the Plutus container on the host network (or a macvlan) for the
    broadcast to reach the LAN; from a default Docker bridge it never leaves.
    """
    if await comfy_up():
        return "awake"
    w = conf.get("wake") or {}
    if not w.get("mac"):
        return "asleep, no MAC configured"
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    try:
        s.sendto(magic_packet(w["mac"]), (w.get("broadcast") or "255.255.255.255", 9))
    finally:
        s.close()
    deadline = time.monotonic() + float(w.get("wait_min", 4)) * 60
    while time.monotonic() < deadline:
        await asyncio.sleep(10)
        if await comfy_up():
            return "woken"
    return "sent wake packet, ComfyUI did not answer in time"


# ── VibeVoice via ComfyUI ────────────────────────────────────────────────────

def fill_voice(template: dict, *, text: str, speaker: int, seed: int) -> dict:
    """The workflow with the narration put in. Text is JSON-escaped, so quotes and
    newlines in a script cannot break the workflow apart."""
    esc = lambda s: json.dumps(s)[1:-1]
    raw = json.dumps(template)
    raw = raw.replace("{{text}}", esc(text)).replace("{{line}}", esc(f"[{speaker}]: {text}"))
    raw = raw.replace('"{{seed}}"', str(seed)).replace("{{seed}}", str(seed))
    return json.loads(raw)


def audio_outputs(entry: dict) -> list[dict]:
    """Every audio file a ComfyUI history entry produced, whatever node saved it."""
    found = []
    for out in (entry.get("outputs") or {}).values():
        for items in out.values():
            if not isinstance(items, list):
                continue
            for it in items:
                if isinstance(it, dict) and str(it.get("filename", "")).lower().endswith(AUDIO_EXT):
                    found.append(it)
    return found


async def voice_scene(root: Path, prod: dict, scene: dict, conf: dict, log: list) -> dict:
    tpl = (conf.get("voice") or {}).get("template") or {}
    if not tpl:
        raise RuntimeError("no VibeVoice workflow saved (Trends > Settings > Voice)")
    base = cfg.comfyui_url.rstrip("/")
    timeout = int((conf.get("voice") or {}).get("timeout_min", 20)) * 60
    wf = fill_voice(tpl, text=scene["narration"], speaker=scene["speaker"], seed=random.randint(1, 2**31))
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as c:
        r = await c.post(f"{base}/prompt", json={"prompt": wf, "client_id": uuid.uuid4().hex})
        if r.status_code == 400:
            raise RuntimeError(f"ComfyUI refused the voice workflow: {r.text[:300]}")
        r.raise_for_status()
        pid = r.json()["prompt_id"]
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            await asyncio.sleep(3)
            h = (await c.get(f"{base}/history/{pid}")).json()
            if pid not in h:
                continue
            status = (h[pid].get("status") or {})
            if status.get("status_str") == "error":
                msgs = [m for m in status.get("messages") or [] if m and m[0] == "execution_error"]
                raise RuntimeError("VibeVoice failed: " + (msgs[0][1].get("exception_message", "")[:300]
                                                          if msgs else "unknown error"))
            files = audio_outputs(h[pid])
            if not files:
                raise RuntimeError("the workflow finished but saved no audio (add a SaveAudio node)")
            f = files[-1]
            a = await c.get(f"{base}/view", params={"filename": f["filename"], "subfolder": f.get("subfolder", ""),
                                                    "type": f.get("type", "output")})
            a.raise_for_status()
            ext = Path(f["filename"]).suffix.lower()
            dest = pr.prod_dir(root, prod["id"]) / "audio" / f"{scene['id']}{ext}"
            dest.write_bytes(a.content)
            secs = pr.audio_seconds(a.content)
            if secs is None:
                secs = round(pr.estimate_seconds(scene["narration"]), 2)
                log.append(f"{scene['id']}: {ext} length estimated from words (save as FLAC/WAV for exact)")
            log.append(f"{scene['id']}: {secs:.1f}s in {time.monotonic() - t0:.0f}s")
            return {"file": dest.name, "seconds": secs, "made": int(time.time())}
        raise RuntimeError(f"no audio after {timeout // 60} min")


async def voice_production(root: Path, pid: int, *, only: list[str] | None = None, redo: bool = False) -> dict:
    conf = tr.load_config(root)
    j = _job("voice")
    log = j.setdefault("log", [])
    state = await wake_comfy(conf)
    log.append(f"ComfyUI: {state}")
    if not await comfy_up():
        raise RuntimeError("ComfyUI is not reachable (is the PC on and COMFYUI_URL set?)")
    prod = pr.get_production(root, pid)
    if not prod:
        raise KeyError(pid)
    done = 0
    for s in prod["scenes"]:
        if not s.get("narration") or (only and s["id"] not in only):
            continue
        if (s.get("audio") or {}).get("file") and not redo:
            continue
        j["current"] = s["id"]
        audio = await voice_scene(root, prod, s, conf, log)
        pr.set_scene_audio(root, pid, s["id"], audio)
        done += 1
    j["current"] = None
    return {"voiced": done}


# ── Remotion render ──────────────────────────────────────────────────────────

def find_browser() -> str:
    for c in (os.environ.get("REMOTION_BROWSER", ""), "/usr/bin/chromium", "/usr/bin/chromium-browser",
              "/usr/bin/google-chrome", "/opt/google/chrome/chrome"):
        if c and Path(c).exists():
            return c
    return ""


def render_available() -> dict:
    npx = shutil.which("npx")
    return {"ok": bool(npx), "npx": npx or "", "browser": find_browser() or "remotion download",
            "note": "" if npx else "Node is not on this machine: download the package and render on the PC."}


async def _run(cmd: list[str], cwd: Path, log: list, timeout: int) -> int:
    proc = await asyncio.create_subprocess_exec(*cmd, cwd=str(cwd), stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.STDOUT)
    t0 = time.monotonic()
    assert proc.stdout
    while True:
        try:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=max(1, timeout - (time.monotonic() - t0)))
        except asyncio.TimeoutError:
            proc.kill()
            log.append("timed out")
            return -1
        if not line:
            break
        text = line.decode(errors="replace").rstrip()
        if text:
            log.append(text[-300:])
            del log[:-200]
    return await proc.wait()


async def render_production(root: Path, pid: int) -> dict:
    avail = render_available()
    if not avail["ok"]:
        raise RuntimeError(avail["note"])
    prod = pr.get_production(root, pid)
    if not prod:
        raise KeyError(pid)
    rdir = pr.write_remotion(root, prod)
    log = _job("render").setdefault("log", [])
    pr.set_render(root, pid, {"state": "running", "started": int(time.time())})
    if not (rdir / "node_modules" / "remotion").exists():
        log.append("npm install (first render of this production)")
        if await _run(["npm", "install", "--no-audit", "--no-fund"], rdir, log, 900) != 0:
            pr.set_render(root, pid, {"state": "failed", "error": "npm install failed", "log": log[-30:]})
            raise RuntimeError("npm install failed; see the render log")
    out = rdir / "out" / "explainer.mp4"
    cmd = ["npx", "remotion", "render", "src/index.ts", "Explainer", str(out)]
    if find_browser():
        # A full Chrome/Chromium has dropped the old headless mode Remotion uses by
        # default; chrome-for-testing mode drives the new one. Without a local
        # browser Remotion downloads its own headless shell on first render.
        cmd += ["--browser-executable", find_browser(), "--chrome-mode=chrome-for-testing"]
    code = await _run(cmd, rdir, log, 3600)
    if code != 0 or not out.exists():
        err = "render failed; often missing Chromium libraries in the container (see log)"
        pr.set_render(root, pid, {"state": "failed", "error": err, "log": log[-30:]})
        raise RuntimeError(err)
    final = pr.prod_dir(root, pid) / "out" / "explainer.mp4"
    shutil.copy2(out, final)
    info = {"state": "done", "file": "explainer.mp4", "bytes": final.stat().st_size, "finished": int(time.time())}
    pr.set_render(root, pid, info)
    return info


# ── package & export ─────────────────────────────────────────────────────────

def export_package(root: Path, pid: int) -> dict:
    prod = pr.get_production(root, pid)
    if not prod:
        raise KeyError(pid)
    idea = tr.get_idea(root, prod["idea_id"]) if prod.get("idea_id") else None
    data, name = pr.build_package(root, pid, idea)
    (pr.prod_dir(root, pid) / "out" / name).write_bytes(data)
    conf = tr.load_config(root)
    copied = ""
    if conf.get("export_dir"):
        dest = Path(conf["export_dir"])
        if dest.is_dir():
            (dest / name).write_bytes(data)
            copied = str(dest / name)
        else:
            copied = f"export folder {dest} not found"
    return {"file": name, "bytes": len(data), "copied_to": copied}


# ── reference video ──────────────────────────────────────────────────────────

async def analyse_reference(url: str) -> dict:
    from core import youtube_scrape as ys
    vid = ys.parse_video_id(url)
    video = await asyncio.to_thread(ys.video_info, vid)
    try:
        segs = (await asyncio.to_thread(ys.fetch_transcript, video))["segments"]
    except Exception:
        segs = []
    return pr.reference_structure(video, segs)


# ── the night run ────────────────────────────────────────────────────────────

async def auto_run(root: Path) -> dict:
    """What auto mode does at night. Every step is the same one a button runs."""
    conf = tr.load_config(root)
    a = conf.get("auto") or {}
    if not a.get("enabled"):
        return {"skipped": "auto mode is off"}
    report: dict = {"explainers": [], "voiced": [], "rendered": [], "packaged": [], "errors": []}
    cap = int(a.get("max_items", 2))
    if a.get("explainers"):
        made = {p["idea_id"] for p in pr.productions(root, 500)}
        for i in tr.ideas(root, status="shortlisted", limit=50):
            if i["id"] not in made and len(report["explainers"]) < cap:
                queue_explainer(root, i["id"])
                report["explainers"].append(i["id"])
    todo = [p for p in pr.productions(root, 200) if p.get("auto")][:cap]
    for p in todo:
        try:
            if a.get("voice", True) and p["voiced"] < p["scene_count"]:
                _job("voice").update(running=True, log=[])
                try:
                    await voice_production(root, p["id"])
                finally:
                    _job("voice")["running"] = False
                report["voiced"].append(p["id"])
            if a.get("render", True) and render_available()["ok"] and (p.get("render") or {}).get("state") != "done":
                _job("render").update(running=True, log=[])
                try:
                    await render_production(root, p["id"])
                finally:
                    _job("render")["running"] = False
                report["rendered"].append(p["id"])
            if a.get("package", True):
                export_package(root, p["id"])
                report["packaged"].append(p["id"])
        except Exception as e:
            report["errors"].append(f"#{p['id']}: {str(e)[:200]}")
    return report


EXPLAINER_PROMPT = """\
Write an explainer production for Trend Radar idea #{idea_id} for the_frizzy1.
Read CLAUDE.md and your skills first (youtube-script / frizzy-youtube rules apply).

1. trend_radar_idea {idea_id} for the angle, hook and evidence.
2. {reference_step}
3. trend_radar_benchmarks for measured runs on this model. Benchmark and
   comparison scenes reference rows by bench_ids; never type a time or a number
   into narration that is not in that table. If nothing is measured yet, add a
   scene whose visual says what will be measured and narrate it as a plan.
4. Call trend_radar_save_production with idea_id {idea_id}, a title, and scenes:
   each scene has a chapter (these become YouTube chapters), speaker 1, narration
   (spoken, 1-4 sentences, no stage directions), and a visual (title, bullets,
   code, benchmark, comparison, image, quote or clip). Aim for 4-8 minutes.
5. Finish with the production id and one line on the structure you chose.
"""


def explainer_prompt(idea_id: int, reference_url: str = "") -> str:
    ref = (f"trend_radar_reference with {reference_url}: match its shape (chapter count, "
           "chapter length, pace, where the payoff lands), not its words."
           if reference_url else "No reference video: use the channel's usual structure.")
    return EXPLAINER_PROMPT.format(idea_id=idea_id, reference_step=ref)


# The UI process installs a callable here that queues an agent run in the studio
# folder; tools/ cannot import the UI layer, so it is injected rather than imported.
EXPLAINER_LAUNCHER = {"fn": None}


def queue_explainer(root: Path, idea_id: int, reference_url: str = "") -> bool:
    fn = EXPLAINER_LAUNCHER["fn"]
    if not fn:
        return False
    fn(explainer_prompt(idea_id, reference_url), f"Idea Studio: explainer #{idea_id}")
    return True


# ── rendering for MCP ────────────────────────────────────────────────────────

def render_prod(p: dict) -> str:
    lines = [f"## Production #{p['id']}: {p['title']}",
             f"{len(p['scenes'])} scenes · ~{pr.fmt_time(p['seconds'])} · {p['voiced']} voiced · "
             f"status {p['status']} · auto {'on' if p.get('auto') else 'off'}"]
    if (p.get("reference") or {}).get("url"):
        lines.append(f"reference: {p['reference']['url']}")
    for s in p["scenes"]:
        a = s.get("audio") or {}
        lines.append(f"- `{s['id']}` [{s.get('chapter', '')}] ({s['visual']['type']}) "
                     f"{'🔊 ' + str(a.get('seconds')) + 's ' if a.get('file') else ''}{s['narration'][:140]}")
    return NL.join(lines)


def register_production_tools(mcp: FastMCP, *, allow: "set[str] | None" = None):
    from core.profiles import tool_filter
    mcp = tool_filter(mcp, allow)

    class SceneIn(BaseModel):
        model_config = ConfigDict(extra="forbid")
        id: str = Field(default="", max_length=40)
        chapter: str = Field(default="", max_length=80)
        speaker: int = Field(default=1, ge=1, le=4)
        narration: str = Field(default="", max_length=4000)
        visual: dict = Field(default_factory=lambda: {"type": "title"},
                             description="type: title|bullets|code|benchmark|comparison|image|quote|clip; "
                                         "heading, lines[], code, image, clip, caption; benchmark/comparison "
                                         "take bench_ids from trend_radar_benchmarks")
        direction: str = Field(default="", max_length=500, description="b-roll / stage note for the edit")

    class SaveInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        production_id: int = Field(default=0, ge=0, description="0 = new; else replaces that production's script")
        idea_id: int | None = Field(default=None, ge=1)
        title: str = Field(..., min_length=3, max_length=200)
        scenes: list[SceneIn] = Field(..., min_length=1, max_length=80)
        reference_url: str = Field(default="", max_length=300)
        notes: str = Field(default="", max_length=4000)

    @mcp.tool(name="trend_radar_save_production",
              annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False})
    async def trend_radar_save_production(params: SaveInput) -> str:
        """Save an explainer production (scenes → StudioForge script.md, VibeVoice script,
        Remotion project). Re-saving keeps audio for scenes whose narration is unchanged."""
        data = params.model_dump()
        data["scenes"] = [s for s in data["scenes"]]
        if params.reference_url:
            try:
                data["reference"] = await analyse_reference(params.reference_url)
            except Exception as e:
                data["reference"] = {"url": params.reference_url, "error": str(e)[:200]}
        try:
            p = pr.save_production(_ROOT, data, params.production_id or None)
        except (ValueError, KeyError) as e:
            return f"Error: {e}"
        return f"Saved production #{p['id']} ({len(p['scenes'])} scenes, ~{pr.fmt_time(p['seconds'])})."

    class ProdInput(BaseModel):
        model_config = ConfigDict(extra="forbid")
        production_id: int = Field(default=0, ge=0, description="0 = list all")

    @mcp.tool(name="trend_radar_production", annotations={"readOnlyHint": True})
    async def trend_radar_production(params: ProdInput) -> str:
        """List explainer productions, or show one with its scenes and voice status."""
        if params.production_id:
            p = pr.get_production(_ROOT, params.production_id)
            return render_prod(p) if p else f"Error: no production #{params.production_id}."
        rows = pr.productions(_ROOT)
        return NL.join(f"- #{p['id']} {p['title']} · {p['scene_count']} scenes · {p['voiced']} voiced · "
                       f"{p['status']}" for p in rows) or "No productions yet."

    class RefInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        url: str = Field(..., min_length=5, max_length=300)

    @mcp.tool(name="trend_radar_reference", annotations={"readOnlyHint": True, "openWorldHint": True})
    async def trend_radar_reference(params: RefInput) -> str:
        """The structure of a video to imitate: chapters and their lengths, words per
        minute, hook length, most-replayed moments. Shape only, never its words."""
        try:
            return json.dumps(await analyse_reference(params.url), indent=2)
        except Exception as e:
            return f"Error: could not read that video ({str(e)[:200]})"

    class VoiceInput(BaseModel):
        model_config = ConfigDict(extra="forbid")
        production_id: int = Field(..., ge=1)
        scene_ids: list[str] = Field(default_factory=list, description="empty = every scene without audio")
        redo: bool = False

    @mcp.tool(name="trend_radar_voice",
              annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False})
    async def trend_radar_voice(params: VoiceInput) -> str:
        """Voice a production with VibeVoice on the ComfyUI machine (wakes it first if
        Wake-on-LAN is set). Runs in the background; check trend_radar_production."""
        try:
            _spawn("voice", voice_production(_ROOT, params.production_id, only=params.scene_ids or None,
                                             redo=params.redo))
        except RuntimeError as e:
            return f"Error: {e}"
        return "Voicing started on ComfyUI."

    class RenderInput(BaseModel):
        model_config = ConfigDict(extra="forbid")
        production_id: int = Field(..., ge=1)
        package_only: bool = Field(default=False, description="Skip the render, just build the download zip")

    @mcp.tool(name="trend_radar_render",
              annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False})
    async def trend_radar_render(params: RenderInput) -> str:
        """Render the explainer with Remotion on the Plutus server (background), or
        just build the package zip (script.md, voice, benchmarks, Remotion project)."""
        if params.package_only:
            try:
                r = export_package(_ROOT, params.production_id)
            except KeyError:
                return "Error: no such production."
            return f"Package {r['file']} ({r['bytes'] // 1024} KB){' → ' + r['copied_to'] if r['copied_to'] else ''}"
        try:
            _spawn("render", render_production(_ROOT, params.production_id))
        except RuntimeError as e:
            return f"Error: {e}"
        return "Render started; it can take a while on a CPU-only server."

    class BenchListInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        slug: str = Field(default="", max_length=80, description="Model slug, empty = all")

    @mcp.tool(name="trend_radar_benchmarks", annotations={"readOnlyHint": True})
    async def trend_radar_benchmarks(params: BenchListInput) -> str:
        """Every measured run (auto from ComfyUI and manual), with ids to reference
        from a production's benchmark/comparison scenes."""
        rows = pr.all_benchmarks(_ROOT, slug=params.slug)
        if not rows:
            return "No benchmark runs yet."
        return NL.join(f"- id {b['id']} · {b['slug'] or '-'} · {b['display']} · {b['status']} · "
                       f"{b['time'] or '-'}{' · ' + str(b['vram_gb']) + ' GB' if b['vram_gb'] else ''}"
                       f"{' · ' + b['hw'] if b['hw'] else ''} · {b['source']}" for b in rows)

    class AddBenchInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        slug: str = Field(default="", max_length=80)
        label: str = Field(..., min_length=1, max_length=160)
        time: str = Field(default="", description="MM:SS, H:MM:SS or seconds; empty for a failed run")
        status: str = Field(default="ok", description="ok | oom | error | dnf")
        vram_gb: float | None = None
        hw: str = Field(default="", max_length=160)
        group: str = Field(default="", max_length=60)
        notes: str = Field(default="", max_length=1000)

    @mcp.tool(name="trend_radar_add_benchmark",
              annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False})
    async def trend_radar_add_benchmark(params: AddBenchInput) -> str:
        """Record a run you measured yourself. Only for real measurements."""
        try:
            b = pr.add_manual_benchmark(_ROOT, params.model_dump())
        except ValueError as e:
            return f"Error: {e}"
        return f"Recorded benchmark id {b['id']}: {b['display']} {b['time'] or b['status']}."

    class AutoInput(BaseModel):
        model_config = ConfigDict(extra="forbid")
        wait: bool = Field(default=False, description="False: run in the background (schedules use this)")

    @mcp.tool(name="trend_radar_auto_run",
              annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False})
    async def trend_radar_auto_run(params: AutoInput) -> str:
        """The night run: voice, render and package productions approved for auto, and
        draft explainers for shortlisted ideas if enabled. Does nothing when auto mode is off."""
        if params.wait:
            return json.dumps(await auto_run(_ROOT))
        try:
            _spawn("auto", auto_run(_ROOT))
        except RuntimeError as e:
            return f"Error: {e}"
        return "Auto run started."
