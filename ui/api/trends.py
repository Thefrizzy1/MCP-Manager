"""Trends tab: the Trend Radar, the Idea Studio and their two schedules.

The radar's logic lives in ``core/trend_radar.py`` (scoring, ideas, backtest) and
``tools/trend_radar.py`` (collectors). This module is the dashboard's view of it,
plus the one thing only the UI process can do: queue an Idea Studio agent run in
the studio folder so Claude Code picks up the channel's own CLAUDE.md and skills.

Long jobs (a collect pass, a backfill) run as background tasks; the page polls
``/overview`` and sees ``jobs`` change, rather than holding a request open for the
minutes a full YouTube pass can take.
"""
from __future__ import annotations

import asyncio
import io
import re
import time
import zipfile

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from core import schedule_store
from core import trend_radar as tr
from ui.api.deps import verify_auth
from ui.runtime import ROOT

router = APIRouter(dependencies=[Depends(verify_auth)])

COLLECT_ORIGIN = "trend_radar_collect"
STUDIO_ORIGIN = "trend_radar_studio"
AUTO_ORIGIN = "trend_radar_auto"
STUDIO_LABEL = "Idea Studio"

# One slot per kind of background job; a second click while one runs is refused.
_JOBS: dict[str, dict] = {}


def _job_view() -> dict:
    return {k: {kk: vv for kk, vv in v.items() if kk != "task"} for k, v in _JOBS.items()}


def _start_job(kind: str, coro) -> dict:
    cur = _JOBS.get(kind)
    if cur and cur.get("running"):
        raise HTTPException(409, f"a {kind} is already running")
    state = {"running": True, "started": int(time.time()), "finished": None, "result": None, "error": None}

    async def _wrap():
        try:
            state["result"] = await coro
        except Exception as e:                        # surfaced in the UI, never raised
            state["error"] = f"{type(e).__name__}: {str(e)[:300]}"
        finally:
            state["running"] = False
            state["finished"] = int(time.time())

    state["task"] = asyncio.create_task(_wrap())
    _JOBS[kind] = state
    return {k: v for k, v in state.items() if k != "task"}


# ── schedules ────────────────────────────────────────────────────────────────

def _find_schedule(origin: str) -> dict | None:
    return next((s for s in schedule_store.load_schedules(ROOT)
                 if (s.get("payload") or {}).get("origin") == origin), None)


def sync_schedules(conf: dict) -> dict:
    """Make the two stored schedules match the radar's settings.

    They are ordinary Plutus schedules (visible and runnable on the Agents page)
    tagged with ``payload.origin`` so this function can find and update them
    instead of piling up duplicates on every save.
    """
    from ui.runtime import agent_scheduler

    wanted = {
        COLLECT_ORIGIN: {
            "name": "Trend Radar · collect", "kind": "tool", "cron": conf["schedule_cron"],
            "enabled": bool(conf["schedule_enabled"]),
            "payload": {"origin": COLLECT_ORIGIN, "tool": "trend_radar_collect",
                        # A full pass outlasts the scheduler's 120 s tool timeout
                        # (YouTube pages are slow), so the schedule starts it in
                        # the background and the run log records the outcome.
                        "params": {"wait": False}},
        },
        STUDIO_ORIGIN: {
            # A tool schedule, not a stored prompt: the prompt (with the brief and
            # skills inlined) is built when it fires, so later uploads count.
            "name": "Trend Radar · Idea Studio", "kind": "tool", "cron": conf["studio_cron"],
            "enabled": bool(conf["studio_enabled"]),
            "payload": {"origin": STUDIO_ORIGIN, "tool": "trend_radar_studio_run", "params": {}},
        },
        AUTO_ORIGIN: {
            "name": "Trend Radar · night run", "kind": "tool", "cron": conf["auto"]["cron"],
            "enabled": bool(conf["auto"].get("enabled")),
            "payload": {"origin": AUTO_ORIGIN, "tool": "trend_radar_auto_run", "params": {"wait": False}},
        },
    }
    out = {}
    for origin, entry in wanted.items():
        cur = _find_schedule(origin)
        if cur:
            out[origin] = schedule_store.update_schedule(ROOT, cur["id"], {**entry, "timezone": cur.get("timezone")})
        else:
            out[origin] = schedule_store.add_schedule(ROOT, entry)
    try:
        agent_scheduler.reschedule()
    except Exception:
        pass                    # scheduler unavailable: the store is still right
    return out


def _schedule_info() -> dict:
    from ui.runtime import agent_scheduler

    try:
        nxt = agent_scheduler.next_run_times()
    except Exception:
        nxt = {}
    info = {}
    for key, origin in (("collect", COLLECT_ORIGIN), ("studio", STUDIO_ORIGIN), ("auto", AUTO_ORIGIN)):
        s = _find_schedule(origin)
        info[key] = None if not s else {
            "id": s["id"], "cron": s["cron"], "enabled": s["enabled"], "next": nxt.get(s["id"]),
            "last_run": s.get("last_run"), "last_status": s.get("last_status"),
            "last_detail": s.get("last_detail"),
        }
    return info


# ── overview & candidates ────────────────────────────────────────────────────

@router.get("/api/v1/trends/overview")
async def api_trends_overview():
    conf = tr.load_config(ROOT)
    if _find_schedule(COLLECT_ORIGIN) is None or _find_schedule(AUTO_ORIGIN) is None:
        sync_schedules(conf)                  # first open: create the schedules once
    cands = tr.candidates(ROOT, limit=150)
    runs = tr.runs(ROOT, 6)
    ideas = tr.ideas(ROOT, limit=60)
    return {
        "candidates": cands,
        "ideas": ideas,
        "runs": runs,
        "schedules": _schedule_info(),
        "jobs": _job_view(),
        "stats": {
            "tracked": len(cands),
            "prep": sum(1 for c in cands if c["action"] == "PREP NOW"),
            "shoot": sum(1 for c in cands if c["action"].startswith("SHOOT")),
            "ideas": len(ideas),
            "threshold": conf["alert_threshold"],
        },
    }


@router.get("/api/v1/trends/candidates/{slug}")
async def api_trends_candidate(slug: str):
    c = tr.get_candidate(ROOT, slug)
    if not c:
        raise HTTPException(404, "candidate not found")
    conf = tr.load_config(ROOT)
    sigs = tr.signals_for(ROOT, slug)
    bt = tr.backtest(sigs, conf, family=c["family"]) if sigs else None
    return {"candidate": c, "signals": sigs, "ideas": tr.ideas(ROOT, candidate=slug, include_archived=True),
            "benchmarks": tr.benchmarks(ROOT, slug), "backtest": bt,
            "templates": sorted((conf.get("benchmark") or {}).get("templates", {}).keys())}


class StatusBody(BaseModel):
    status: str
    notes: str | None = Field(default=None, max_length=2000)


@router.post("/api/v1/trends/candidates/{slug}/status")
async def api_trends_candidate_status(slug: str, body: StatusBody):
    try:
        c = tr.set_status(ROOT, slug, body.status, body.notes)
    except KeyError:
        raise HTTPException(404, "candidate not found")
    except ValueError as e:
        raise HTTPException(400, str(e))
    tr.rescore(ROOT, tr.load_config(ROOT))
    return {"ok": True, "candidate": c}


class MergeBody(BaseModel):
    into: str = Field(..., min_length=1, max_length=80)


@router.post("/api/v1/trends/candidates/{slug}/merge")
async def api_trends_candidate_merge(slug: str, body: MergeBody):
    try:
        n = tr.merge(ROOT, slug, body.into)
    except KeyError:
        raise HTTPException(404, "target candidate not found")
    tr.rescore(ROOT, tr.load_config(ROOT))
    return {"ok": True, "moved": n}


@router.get("/api/v1/trends/signals")
async def api_trends_signals(limit: int = 100, kind: str = ""):
    return {"signals": tr.recent_signals(ROOT, min(max(limit, 1), 500), kind)}


# ── jobs ─────────────────────────────────────────────────────────────────────

class CollectBody(BaseModel):
    sources: list[str] = Field(default_factory=list)


@router.post("/api/v1/trends/collect")
async def api_trends_collect(body: CollectBody):
    from tools.trend_radar import run_collect
    return {"ok": True, "job": _start_job("collect", run_collect(ROOT, only=body.sources or None))}


class BackfillBody(BaseModel):
    name: str = Field(..., min_length=2, max_length=80)
    include_youtube: bool = True


@router.post("/api/v1/trends/backfill")
async def api_trends_backfill(body: BackfillBody):
    from tools.trend_radar import backfill
    return {"ok": True, "job": _start_job("backfill", backfill(ROOT, body.name,
                                                               include_youtube=body.include_youtube))}


class BenchBody(BaseModel):
    slug: str
    template: str = ""


@router.post("/api/v1/trends/benchmark")
async def api_trends_benchmark(body: BenchBody):
    from tools.trend_radar import run_benchmark
    c = tr.get_candidate(ROOT, body.slug)
    if not c:
        raise HTTPException(404, "candidate not found")
    conf = tr.load_config(ROOT)
    return {"ok": True, "job": _start_job("benchmark", run_benchmark(
        ROOT, body.slug, body.template or c["family"], conf))}


@router.get("/api/v1/trends/backtest")
async def api_trends_backtest():
    return tr.backtest_all(ROOT, tr.load_config(ROOT))


class ResearchBody(BaseModel):
    query: str = Field(..., min_length=2, max_length=120)
    period: str = "month"


@router.post("/api/v1/trends/research")
async def api_trends_research(body: ResearchBody):
    from tools.trend_radar import pattern_report, title_research
    period = body.period if body.period in ("today", "week", "month", "year", "relevance") else "month"
    try:
        res = await title_research(body.query, limit=20, period=period)
    except Exception as e:
        raise HTTPException(502, f"YouTube search failed: {str(e)[:200]}")
    res["patterns"] = pattern_report(res["rows"])
    return res


# ── config ───────────────────────────────────────────────────────────────────

@router.get("/api/v1/trends/config")
async def api_trends_config_get():
    return {"config": tr.load_config(ROOT), "defaults": tr.DEFAULT_CONFIG,
            "default_studio_prompt": tr.DEFAULT_STUDIO_PROMPT}


@router.post("/api/v1/trends/config")
async def api_trends_config_set(body: dict):
    before = tr.load_config(ROOT)["families"]
    try:
        conf = tr.save_config(ROOT, body or {})
    except ValueError as e:
        raise HTTPException(400, str(e))
    if conf["families"] != before:
        tr.rematch_all(ROOT, conf)      # new names re-file what is already stored
    sync_schedules(conf)
    tr.rescore(ROOT, conf)
    return {"ok": True, "config": conf, "schedules": _schedule_info()}


# ── ideas ────────────────────────────────────────────────────────────────────

@router.get("/api/v1/trends/ideas")
async def api_trends_ideas(status: str = "", include_archived: bool = False):
    return {"ideas": tr.ideas(ROOT, status=status, include_archived=include_archived, limit=300)}


@router.get("/api/v1/trends/ideas/{iid}")
async def api_trends_idea(iid: int):
    i = tr.get_idea(ROOT, iid)
    if not i:
        raise HTTPException(404, "idea not found")
    return {"idea": i}


@router.post("/api/v1/trends/ideas/{iid}")
async def api_trends_idea_update(iid: int, body: dict):
    try:
        i = tr.set_idea(ROOT, iid, dict(body or {}))
    except KeyError:
        raise HTTPException(404, "idea not found")
    except ValueError as e:
        raise HTTPException(400, str(e))
    tr.rescore(ROOT, tr.load_config(ROOT))
    return {"ok": True, "idea": i}


# ── Idea Studio ──────────────────────────────────────────────────────────────

def _studio_runs(limit: int = 8) -> list[dict]:
    from core import agent_runner
    rows = [r for r in agent_runner.list_runs(ROOT, 80)
            if STUDIO_LABEL.lower() in (r.get("label") or "").lower()]
    return [{k: r.get(k) for k in ("id", "label", "started", "finished", "ok", "error", "cost_usd")}
            for r in rows[:limit]]


@router.get("/api/v1/trends/studio")
async def api_trends_studio():
    conf = tr.load_config(ROOT)
    return {"brief": tr.load_studio_brief(ROOT), "prompt": tr.studio_prompt(conf),
            "prompt_chars": len(tr.studio_prompt(conf, ROOT)), "services": list(tr.STUDIO_SERVICES),
            "skills": tr.list_skills(ROOT), "knowledge": tr.list_knowledge(ROOT),
            "folder": str(tr.studio_dir(ROOT)), "runs": _studio_runs(),
            "schedule": _schedule_info()["studio"]}


@router.post("/api/v1/trends/studio/run")
async def api_trends_studio_run():
    conf = tr.load_config(ROOT)
    _launch_explainer(tr.studio_prompt(conf, ROOT), f"sched:{STUDIO_LABEL} (manual)")
    return {"ok": True}


class BriefBody(BaseModel):
    text: str = Field(..., max_length=100_000)


@router.post("/api/v1/trends/studio/brief")
async def api_trends_studio_brief(body: BriefBody):
    try:
        tr.save_studio_brief(ROOT, body.text)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


def skill_files_from_upload(filename: str, data: bytes) -> tuple[str, dict[str, bytes]]:
    """(skill name, {relpath: bytes}) from an uploaded SKILL.md, .zip or .skill.

    Zips exported from claude.ai usually hold one top folder; it is stripped so
    SKILL.md lands at the skill's top level.
    """
    name = ""
    if filename.lower().endswith((".zip", ".skill")):
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise ValueError("not a valid zip")
        entries = [i for i in zf.infolist() if not i.is_dir() and not i.filename.startswith("__MACOSX")]
        skill_md = [i.filename for i in entries if i.filename.split("/")[-1] == "SKILL.md"]
        if not skill_md:
            raise ValueError("the zip has no SKILL.md")
        top = min(skill_md, key=len)
        prefix = top[: -len("SKILL.md")]
        files = {}
        for i in entries:
            if not i.filename.startswith(prefix):
                continue
            if i.file_size > 10 * 1024 * 1024:
                raise ValueError(f"{i.filename} is over 10 MB")
            files[i.filename[len(prefix):]] = zf.read(i)
        name = prefix.strip("/").split("/")[-1] if prefix else ""
    elif filename.endswith(".md"):
        files = {"SKILL.md": data}
    else:
        raise ValueError("upload a SKILL.md, a .zip or a .skill file")
    m = re.search(rb"^name:\s*([\w-]+)", files["SKILL.md"], re.M)
    if m:
        name = m.group(1).decode().lower()
    return name, files


@router.post("/api/v1/trends/studio/skills")
async def api_trends_studio_skill_upload(file: UploadFile = File(...), name: str = Form(default="")):
    data = await file.read()
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(400, "upload over 25 MB")
    try:
        found, files = skill_files_from_upload(file.filename or "", data)
        skill = tr.install_skill(ROOT, (name or found or "").strip(), files)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "skill": skill, "skills": tr.list_skills(ROOT)}


@router.delete("/api/v1/trends/studio/skills/{name}")
async def api_trends_studio_skill_delete(name: str):
    if not tr.remove_skill(ROOT, name):
        raise HTTPException(404, "skill not found")
    return {"ok": True, "skills": tr.list_skills(ROOT)}


@router.post("/api/v1/trends/studio/knowledge")
async def api_trends_studio_knowledge_upload(file: UploadFile = File(...)):
    data = await file.read()
    try:
        tr.save_knowledge(ROOT, file.filename or "note.md", data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "knowledge": tr.list_knowledge(ROOT)}


@router.delete("/api/v1/trends/studio/knowledge/{name}")
async def api_trends_studio_knowledge_delete(name: str):
    if not tr.remove_knowledge(ROOT, name):
        raise HTTPException(404, "file not found")
    return {"ok": True, "knowledge": tr.list_knowledge(ROOT)}


@router.get("/api/v1/trends/ideas/{iid}/thumb.svg")
async def api_trends_idea_thumb(iid: int):
    """The idea's thumbnail mock as an image.

    Served as its own SVG document with a no-script CSP and shown through
    ``<img>``, so markup an agent wrote can draw but never run.
    """
    from fastapi.responses import Response

    i = tr.get_idea(ROOT, iid)
    if not i or not i.get("thumb_svg"):
        raise HTTPException(404, "no thumbnail")
    return Response(i["thumb_svg"], media_type="image/svg+xml", headers={
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:",
        "X-Content-Type-Options": "nosniff", "Cache-Control": "no-cache"})


# ── finished ─────────────────────────────────────────────────────────────────

@router.get("/api/v1/trends/done")
async def api_trends_done():
    """Suggestions (cross-referenced with your uploads) plus the Finished drawer."""
    return {"suggestions": tr.suggest_done(ROOT), "finished": tr.finished(ROOT)}


class DoneBody(BaseModel):
    idea_ids: list[int] | None = None
    slugs: list[str] | None = None


@router.post("/api/v1/trends/done/apply")
async def api_trends_done_apply(body: DoneBody):
    res = tr.apply_done(ROOT, body.idea_ids, body.slugs)
    tr.rescore(ROOT, tr.load_config(ROOT))
    return {"ok": True, **res}


# ── Produce: explainer productions ───────────────────────────────────────────

from core import production as pr  # noqa: E402


def _launch_explainer(prompt: str, label: str) -> None:
    """Queue a studio-scoped agent run (Idea Studio and explainers share it)."""
    from core.service_registry import all_services
    from ui.runtime import _run_agent_bg
    conf = tr.load_config(ROOT)
    have = {s["id"] for s in all_services(ROOT)}
    _run_agent_bg(prompt, label, model=conf.get("studio_model") or None, force=True,
                  mcp_services=[x for x in tr.STUDIO_SERVICES if x in have],
                  allow_write=True, allow_publish=False, cwd=str(tr.studio_dir(ROOT)))


def _install_launcher() -> None:
    """tools/ cannot import the UI; the UI hands it the one function it needs."""
    try:
        from tools import production as tp
        tp.EXPLAINER_LAUNCHER["fn"] = _launch_explainer
    except Exception:
        pass


_install_launcher()


def _jobs_view() -> dict:
    from tools import production as tp
    return {k: {kk: (vv[-40:] if kk == "log" else vv) for kk, vv in v.items()} for k, v in tp.JOBS.items()}


@router.get("/api/v1/trends/productions")
async def api_prod_list():
    from tools import production as tp
    return {"productions": pr.productions(ROOT), "jobs": _jobs_view(), "render": tp.render_available(),
            "comfy_up": await tp.comfy_up(), "voice_ready": bool(tr.load_config(ROOT)["voice"].get("template"))}


@router.get("/api/v1/trends/productions/{pid}")
async def api_prod_get(pid: int):
    p = pr.get_production(ROOT, pid)
    if not p:
        raise HTTPException(404, "production not found")
    bench = pr.resolve_bench(ROOT, p["scenes"])
    return {"production": p, "voice_script": pr.voice_script(p["scenes"]),
            "bench": {str(k): v for k, v in bench.items()},
            "has_video": (pr.prod_dir(ROOT, pid, create=False) / "out" / "explainer.mp4").exists()}


@router.post("/api/v1/trends/productions")
async def api_prod_create(body: dict):
    try:
        p = pr.save_production(ROOT, body or {})
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "production": p}


@router.post("/api/v1/trends/productions/{pid}")
async def api_prod_save(pid: int, body: dict):
    try:
        p = pr.save_production(ROOT, body or {}, pid)
    except KeyError:
        raise HTTPException(404, "production not found")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "production": p}


@router.delete("/api/v1/trends/productions/{pid}")
async def api_prod_delete(pid: int):
    if not pr.delete_production(ROOT, pid):
        raise HTTPException(404, "production not found")
    return {"ok": True}


class AutoBody(BaseModel):
    on: bool


@router.post("/api/v1/trends/productions/{pid}/auto")
async def api_prod_auto(pid: int, body: AutoBody):
    try:
        return {"ok": True, "production": pr.set_auto(ROOT, pid, body.on)}
    except KeyError:
        raise HTTPException(404, "production not found")


class VoiceBody(BaseModel):
    scene_ids: list[str] = Field(default_factory=list)
    redo: bool = False


@router.post("/api/v1/trends/productions/{pid}/voice")
async def api_prod_voice(pid: int, body: VoiceBody):
    from tools import production as tp
    if not pr.get_production(ROOT, pid):
        raise HTTPException(404, "production not found")
    try:
        return {"ok": True, "job": tp._spawn("voice", tp.voice_production(ROOT, pid, only=body.scene_ids or None,
                                                                          redo=body.redo))}
    except RuntimeError as e:
        raise HTTPException(409, str(e))


@router.post("/api/v1/trends/productions/{pid}/render")
async def api_prod_render(pid: int):
    from tools import production as tp
    if not pr.get_production(ROOT, pid):
        raise HTTPException(404, "production not found")
    try:
        return {"ok": True, "job": tp._spawn("render", tp.render_production(ROOT, pid))}
    except RuntimeError as e:
        raise HTTPException(409, str(e))


@router.post("/api/v1/trends/productions/{pid}/package")
async def api_prod_package(pid: int):
    from tools import production as tp
    try:
        return {"ok": True, **tp.export_package(ROOT, pid)}
    except KeyError:
        raise HTTPException(404, "production not found")


@router.get("/api/v1/trends/productions/{pid}/download")
async def api_prod_download(pid: int):
    from fastapi.responses import Response
    from tools import production as tp
    try:
        info = tp.export_package(ROOT, pid)
    except KeyError:
        raise HTTPException(404, "production not found")
    data = (pr.prod_dir(ROOT, pid) / "out" / info["file"]).read_bytes()
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{info["file"]}"'})


def _prod_file(pid: int, sub: str, name: str):
    from fastapi.responses import FileResponse
    base = (pr.prod_dir(ROOT, pid, create=False) / sub).resolve()
    f = (base / name).resolve()
    if base not in f.parents or not f.is_file():
        raise HTTPException(404, "file not found")
    return FileResponse(f)


@router.get("/api/v1/trends/productions/{pid}/audio/{name}")
async def api_prod_audio(pid: int, name: str):
    return _prod_file(pid, "audio", name)


@router.get("/api/v1/trends/productions/{pid}/video")
async def api_prod_video(pid: int):
    return _prod_file(pid, "out", "explainer.mp4")


@router.post("/api/v1/trends/productions/{pid}/assets")
async def api_prod_asset(pid: int, file: UploadFile = File(...)):
    """An image or clip a scene can show (visual.image / visual.clip = its name)."""
    if not pr.get_production(ROOT, pid):
        raise HTTPException(404, "production not found")
    name = re.sub(r"[^A-Za-z0-9._-]", "_", (file.filename or "asset").split("/")[-1])
    if not name.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".mov", ".webm")):
        raise HTTPException(400, "images (.png .jpg .webp .gif) or clips (.mp4 .mov .webm)")
    data = await file.read()
    if len(data) > 300 * 1024 * 1024:
        raise HTTPException(400, "over 300 MB")
    (pr.prod_dir(ROOT, pid) / "assets" / name).write_bytes(data)
    return {"ok": True, "name": name}


class ExplainerBody(BaseModel):
    reference_url: str = Field(default="", max_length=300)


@router.post("/api/v1/trends/ideas/{iid}/explainer")
async def api_idea_explainer(iid: int, body: ExplainerBody):
    from tools import production as tp
    if not tr.get_idea(ROOT, iid):
        raise HTTPException(404, "idea not found")
    _launch_explainer(tp.explainer_prompt(iid, body.reference_url.strip(), ROOT), f"Idea Studio: explainer #{iid}")
    return {"ok": True}


@router.get("/api/v1/trends/comfy")
async def api_comfy_status():
    from tools import production as tp
    conf = tr.load_config(ROOT)
    return {"up": await tp.comfy_up(), "wake_configured": bool(conf["wake"].get("mac"))}


@router.post("/api/v1/trends/comfy/wake")
async def api_comfy_wake():
    from tools import production as tp
    return {"ok": True, "state": await tp.wake_comfy(tr.load_config(ROOT))}


@router.post("/api/v1/trends/auto/run")
async def api_auto_run():
    from tools import production as tp
    try:
        return {"ok": True, "job": tp._spawn("auto", tp.auto_run(ROOT))}
    except RuntimeError as e:
        raise HTTPException(409, str(e))


# ── Benchmarks: comparisons ──────────────────────────────────────────────────

@router.get("/api/v1/trends/benchmarks")
async def api_bench_list(slug: str = ""):
    return {"benchmarks": pr.all_benchmarks(ROOT, slug=slug),
            "models": [{"slug": c["slug"], "name": c["name"]} for c in tr.candidates(ROOT, include_dismissed=True)]}


class BenchAdd(BaseModel):
    slug: str = ""
    label: str
    time: str = ""
    status: str = "ok"
    vram_gb: float | None = None
    hw: str = ""
    group: str = ""
    file: str = ""
    mark: bool = False
    notes: str = ""


@router.post("/api/v1/trends/benchmarks")
async def api_bench_add(body: BenchAdd):
    try:
        return {"ok": True, "benchmark": pr.add_manual_benchmark(ROOT, body.model_dump())}
    except ValueError as e:
        raise HTTPException(400, str(e))


class BenchImport(BaseModel):
    text: str = Field(..., max_length=200_000)
    slug: str = ""
    hw: str = ""


@router.post("/api/v1/trends/benchmarks/import")
async def api_bench_import(body: BenchImport):
    try:
        return {"ok": True, "imported": pr.import_notes_table(ROOT, body.text, slug=body.slug, hw=body.hw)}
    except ValueError as e:
        raise HTTPException(400, str(e))


class BenchExport(BaseModel):
    ids: list[int] = Field(default_factory=list)
    pair: list[int] = Field(default_factory=list)
    heading: str = "Render time"
    subheading: str = ""
    specs: list[dict] = Field(default_factory=list)


@router.post("/api/v1/trends/benchmarks/export")
async def api_bench_export(body: BenchExport):
    rows = {b["id"]: b for b in pr.all_benchmarks(ROOT)}
    runs = [rows[i] for i in body.ids if i in rows]
    pair = tuple(rows[i] for i in body.pair if i in rows)
    return {"studioforge": pr.studioforge_graphics(runs, heading=body.heading, subheading=body.subheading,
                                                   specs=body.specs, pair=pair if len(pair) == 2 else None),
            "notes_table": pr.notes_table(runs),
            "comparison": pr.compare(*pair) if len(pair) == 2 else None}


@router.post("/api/v1/trends/benchmarks/{bid}")
async def api_bench_update(bid: int, body: dict):
    try:
        return {"ok": True, "benchmark": pr.update_benchmark(ROOT, bid, dict(body or {}))}
    except KeyError:
        raise HTTPException(404, "benchmark not found")
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/api/v1/trends/benchmarks/{bid}")
async def api_bench_delete(bid: int):
    if not pr.delete_benchmark(ROOT, bid):
        raise HTTPException(404, "benchmark not found")
    return {"ok": True}
