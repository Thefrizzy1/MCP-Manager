"""Shared UI runtime: the app-wide singletons and orchestration helpers.

Everything here used to live at module scope in ``main.py``. It is imported by
both ``main.py`` (process orchestration) and the ``ui.api.*`` routers (HTTP
surface). Keeping it in one place means an endpoint can never accidentally
construct a second FastMCP or a second scheduler.

Nothing in this module defines HTTP routes — routers import the names they need.
Behaviour is identical to the pre-split ``main.py``; this was a pure move.
"""
from __future__ import annotations

import asyncio
import logging
import os
import queue
import threading
import time
from pathlib import Path

from config import cfg
from core import agent_permissions
from core import agent_runner
from core import agent_tasks
from core.capabilities import CapabilityCatalog
from core.dashboard_health import gather_service_health
from core.env_store import read_env, update_env
from core.observability import Telemetry
from core.recent_runs import ensure_data_dir
from core.router import RouterRuntime
from core.scheduler import PlutusScheduler, invoke_tool_sync
from core.service_registry import all_services
from core.profiles import load_profiles, resolve_tool_names, tool_filter
from core.tool_cache import beta_cache_background_loop
from core.tool_manager_adapter import ToolRegistryAdapter
from mcp.server.fastmcp import FastMCP
from tools.comfyui import register_comfyui_tools
from tools.fal_tools import register_fal_tools
from tools.infrastructure import register_infrastructure_tools
from tools.media import register_media_tools
from tools.monitoring import register_monitoring_tools
from tools.nextcloud import register_nextcloud_tools
from tools.obsidian import register_obsidian_tools
from tools.personal import register_personal_tools
from tools.photos import register_photo_tools
from tools.prompts import register_prompt_tools
from tools.public_apis_bulk import register_public_apis_bulk
from tools.resources import register_resource_tools
from tools.ssh_smb import register_ssh_smb_tools
from tools.system import register_system_tools
from tools.utilities import register_utility_tools

ROOT = Path(__file__).resolve().parent.parent
logging.basicConfig(level=os.getenv("PLUTUS_LOG_LEVEL", "INFO").upper())
log = logging.getLogger("plutus")

_MCP_INSTRUCTIONS = (
    "Plutus homelab MCP. Self-hosted: Jellyfin, *arrs, Immich, HA, Nextcloud, "
    "Habitica, Docker, OMV, ComfyUI, n8n, Syncthing, Obsidian. Public: fal.ai, "
    "weather, maps, web search."
)


def _load_user_extensions(m, allow: "set[str] | None" = None) -> None:
    ext = ROOT / "extensions" / "__init__.py"
    if not ext.is_file():
        return
    import importlib.util

    spec = importlib.util.spec_from_file_location("plutus_extensions", ext)
    if spec is None or spec.loader is None:
        return
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as ex:
        print(f"⚠️ extensions/__init__.py load failed: {ex}")
        return
    reg = getattr(mod, "register", None)
    if not callable(reg):
        return
    try:
        reg(tool_filter(m, allow))
        print("🔌 extensions: register(mcp) completed")
    except Exception as ex:
        print(f"⚠️ extensions.register(mcp): {ex}")


def register_all_tools(m, allow: "set[str] | None" = None) -> None:
    """Register every tool domain (and user extensions) onto ``m``.

    ``allow`` (a set of tool names) is threaded to every domain registrar so a
    profile instance only ever has its allowed tools registered. ``None`` means
    the full surface.
    """
    register_media_tools(m, allow=allow); register_personal_tools(m, allow=allow); register_photo_tools(m, allow=allow)
    register_system_tools(m, allow=allow); register_comfyui_tools(m, allow=allow); register_utility_tools(m, allow=allow)
    register_obsidian_tools(m, allow=allow); register_monitoring_tools(m, allow=allow); register_nextcloud_tools(m, allow=allow)
    register_infrastructure_tools(m, allow=allow); register_fal_tools(m, allow=allow)
    register_public_apis_bulk(m, allow=allow)
    register_ssh_smb_tools(m, allow=allow)
    register_prompt_tools(m, allow=allow)
    register_resource_tools(m, allow=allow)
    _load_user_extensions(m, allow=allow)


mcp = FastMCP("plutus_mcp", host=cfg.mcp_host, port=cfg.mcp_port, instructions=_MCP_INSTRUCTIONS)
register_all_tools(mcp)

tools = ToolRegistryAdapter(mcp)
# The old global tool gate (core/tool_gate.py) is gone; scoping is now per
# profile (core/profiles.py), each served at its own /mcp/p/<name> endpoint.


def all_tool_names() -> list[str]:
    return tools.tool_names()


def build_mcp(name: str, allow: "set[str] | None"):
    """A FastMCP with only ``allow`` registered (None = the full surface)."""
    m = FastMCP(name, instructions=_MCP_INSTRUCTIONS)
    register_all_tools(m, allow=allow)
    return m


def build_mcp_asgi_app():
    """The ASGI app the MCP process serves: the full surface at /mcp plus one
    mount per profile at /mcp/p/<name>, all behind the bearer gate.

    Profiles are read once at startup — adding/removing one is restart-to-apply
    (the MCP server is a separate process from the UI, so cross-process
    hot-remount would be fragile). See docs/ARCHITECTURE.md.
    """
    from contextlib import AsyncExitStack, asynccontextmanager

    from starlette.applications import Starlette
    from starlette.routing import Mount

    from core.mcp_bearer_middleware import MCPBearerGateMiddleware
    from core.tool_exposure import resolve_exposed

    names = all_tool_names()
    # The main /mcp honours the global tool-exposure "slicer": if categories are
    # disabled, serve a filtered instance so the manifest (and its tokens) shrink.
    exposed = resolve_exposed(ROOT, names)
    main_app = (mcp if exposed is None else build_mcp("plutus", exposed)).streamable_http_app()
    mounted = [("/mcp", main_app)]
    for prof in load_profiles(ROOT):
        allow = resolve_tool_names(prof, names)
        sub = build_mcp(f"plutus-{prof['name']}", allow)
        mounted.append((f"/mcp/p/{prof['name']}", sub.streamable_http_app()))

    @asynccontextmanager
    async def _lifespan(_app):
        async with AsyncExitStack() as stack:
            for _, sub_app in mounted:
                await stack.enter_async_context(sub_app.router.lifespan_context(sub_app))
            yield

    app = Starlette(routes=[Mount(path, app=a) for path, a in mounted], lifespan=_lifespan)
    app.add_middleware(MCPBearerGateMiddleware)
    return app

ICONS_DIR = ROOT / "icons"
STATIC_DIR = ROOT / "ui" / "static"
DIST_DIR = ROOT / "ui" / "static" / "dist"  # built React app (Vite base=/spa/)
ENV_FILE = str(ROOT / ".env")
_health_cache: dict = {}
_health_ts: float = 0.0
_health_lock = asyncio.Lock()
_env_lock = threading.Lock()
telemetry = Telemetry()


def _services_live():
    """Built-in integrations plus entries from data/custom_integrations.json."""
    return all_services(ROOT)


capabilities = CapabilityCatalog(ROOT, tools, _services_live)

agent_scheduler = PlutusScheduler(ROOT)


def load_env():
    """Read .env into a dict. Canonical implementation lives in core.env_store."""
    return read_env()


def save_env(updates: dict):
    """Validate + atomically write .env (and sync cfg). See core.env_store."""
    update_env(updates)


def _agent_mcp_target() -> tuple[str, str]:
    """(mcp_url, bearer_token) the agent uses to reach Plutus's own MCP tools."""
    url = f"http://127.0.0.1:{cfg.mcp_port}/mcp"
    token = ""
    if cfg.mcp_require_bearer:
        token = (read_env().get("MCP_BEARER_TOKEN", "") or "").strip()
    return url, token


def _maybe_notify_agent(rec: dict) -> None:
    cfg2 = agent_runner.load_agent_config(ROOT)
    if not cfg2.get("notify_enabled"):
        return
    if cfg2.get("notify_on") == "error" and rec.get("ok"):
        return
    ok = "OK" if rec.get("ok") else "FAIL"
    msg = f"{ok} agent '{rec.get('label')}' — ${rec.get('cost_usd')}"
    if rec.get("error"):
        msg += f" — {rec['error'][:120]}"
    try:
        invoke_tool_sync(tools.raw_manager, "ntfy_send", {"message": msg, "title": "Plutus agent"})
    except Exception as exc:
        log.warning("agent ntfy failed: %s", exc)


_agent_queue: "queue.Queue" = queue.Queue(maxsize=6)


def _agent_queue_worker() -> None:
    """Single serial worker: runs one agent job at a time, honours the daily cap."""
    while True:
        job = _agent_queue.get()
        try:
            acfg = agent_runner.load_agent_config(ROOT)
            cap = int(acfg.get("max_runs_per_day", 20) or 0)
            if cap and not job.get("force") and agent_runner.runs_today(ROOT) >= cap:
                log.info("Agent daily run cap (%s) reached — skipping scheduled '%s'", cap, job.get("label"))
                continue
            url, token = _agent_mcp_target()
            rec = agent_runner.run_agent(
                ROOT, job["prompt"], label=job.get("label", "agent"),
                mcp_url=url, bearer_token=token,
                disallowed_tools=job.get("disallowed"), model=job.get("model") or None,
            )
            _maybe_notify_agent(rec)
        except Exception as exc:
            log.warning("Agent worker error: %s", exc)
        finally:
            _agent_queue.task_done()


def _agent_service_disallow(selected: list[str] | None) -> list[str]:
    """Per-connection ACL: deny tools that belong to a service the user did NOT
    select. `None` = no restriction. Tools not tied to any service (web, fs,
    utilities, public APIs) stay available so research still works."""
    if selected is None:
        return []
    from core.dashboard_api import tool_to_service_map
    sel = set(selected)
    # Only self-hosted/system services are "connections"; public-API and utility
    # tools (web search/fetch, weather, maps, …) always stay available.
    conn_ids = {s["id"] for s in _services_live() if "public" not in (s.get("section") or "").lower()}
    tmap = tool_to_service_map()
    return sorted(f"mcp__plutus__{t}" for t, svc in tmap.items() if svc in conn_ids and svc not in sel)


def _enqueue_agent(prompt: str, label: str, *, permission: str | None = None,
                   model: str | None = None, force: bool = False,
                   extra_disallowed: list[str] | None = None) -> bool:
    """Queue an agent run. Disallowed-tool set = permission level + per-connection ACL."""
    acfg = agent_runner.load_agent_config(ROOT)
    level = agent_permissions.normalize_level(permission or acfg.get("tool_permission"))
    disallowed = sorted(
        set(agent_permissions.build_disallowed_from_annotations(tools.raw_manager, level))
        | set(extra_disallowed or [])
    )
    try:
        _agent_queue.put_nowait({"prompt": prompt, "label": label, "disallowed": disallowed,
                                 "model": model, "force": force})
        return True
    except queue.Full:
        log.warning("Agent queue full — dropping '%s'", label)
        return False


def _run_agent_bg(prompt: str, label: str = "agent", *, permission: str | None = None,
                  model: str | None = None, force: bool = False,
                  mcp_services: list[str] | None = None) -> None:
    # mcp_services=None means "no per-connection restriction" (back-compat for
    # callers and older schedules that never stored a selection).
    _enqueue_agent(prompt, label, permission=permission, model=model, force=force,
                   extra_disallowed=_agent_service_disallow(mcp_services))


def _run_tool_scheduled(tool_name: str, params: dict):
    return invoke_tool_sync(tools.raw_manager, tool_name, params)


def _run_task_bg(task_id: str, *, force: bool = False) -> None:
    """Resolve a playbook to its (rendered) prompt and run it as an agent."""
    task = agent_tasks.get_task(ROOT, task_id)
    if not task:
        log.warning("Scheduled task %s not found", task_id)
        return
    acfg = agent_runner.load_agent_config(ROOT)
    lib, hint = agent_runner.resolve_library(acfg)
    prompt = agent_tasks.render_prompt(
        task["prompt"], library=lib, date=time.strftime("%Y-%m-%d"), output_hint=hint,
    )
    _run_agent_bg(prompt, task.get("name", "playbook")[:40],
                  permission=task.get("permission") or None, model=task.get("model") or None, force=force)


async def get_health(force=False):
    global _health_cache, _health_ts
    async with _health_lock:
        if force or not _health_cache or (time.time() - _health_ts) > 60.0:
            cache, _ = await asyncio.wait_for(gather_service_health(_services_live(), cfg), timeout=120.0)
            _health_cache, _health_ts = cache, time.time()
        return _health_cache


router_runtime = RouterRuntime(tool_manager=tools.raw_manager, health_fn=get_health, telemetry=telemetry)


def _tool_count():
    return tools.count()


_started_at = time.time()


def _mcp_port_alive(timeout: float = 1.0) -> bool:
    """Quick TCP probe of the MCP port. The MCP server runs in a separate
    process; this lets the healthcheck report 'unhealthy' if it has died even
    though the UI process answering this request is still up."""
    import socket
    try:
        with socket.create_connection(("127.0.0.1", cfg.mcp_port), timeout=timeout):
            return True
    except OSError:
        return False


from contextlib import asynccontextmanager  # noqa: E402


@asynccontextmanager
async def ui_lifespan(_app):
    ensure_data_dir(ROOT)
    agent_tasks.seed_if_empty(ROOT)
    threading.Thread(target=_agent_queue_worker, name="agent-queue", daemon=True).start()
    loop_task = asyncio.create_task(
        beta_cache_background_loop(ROOT, lambda: tools.raw_manager, _services_live)
    )
    agent_scheduler.start(run_agent=_run_agent_bg, run_tool=_run_tool_scheduled, run_task=_run_task_bg)
    yield
    agent_scheduler.shutdown()
    loop_task.cancel()
    try:
        await loop_task
    except asyncio.CancelledError:
        pass
