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
import threading
import time
from pathlib import Path

from config import cfg
from core import agent_runner
from core import agent_tasks
from core.agent_orchestrator import AgentOrchestrator
from core.capabilities import CapabilityCatalog
from core.dashboard_health import gather_service_health
from core.env_store import read_env, update_env
from core.observability import Telemetry
from core.recent_runs import ensure_data_dir
from core.router import RouterRuntime
from core.scheduler import PlutusScheduler
from core.service_registry import all_services
from core.profiles import load_profiles, resolve_tool_names, tool_filter
from core.tool_cache import beta_cache_background_loop
from core.tool_manager_adapter import ToolRegistryAdapter
from mcp.server.fastmcp import FastMCP
from tools.agent_db import register_agent_db_tools
from tools.rooms import register_room_tools
from tools.agents import register_agent_tools
from tools.apps import register_app_tools
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
from tools.scrape import register_scrape_tools
from tools.social import register_social_tools
from tools.ssh_smb import register_ssh_smb_tools
from tools.system import register_system_tools
from tools.utilities import register_utility_tools
from tools.youtube import register_youtube_tools
from tools.huggingface import register_huggingface_tools
from tools.github import register_github_tools
from tools.gitlab import register_gitlab_tools

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
    register_youtube_tools(m, allow=allow)
    register_huggingface_tools(m, allow=allow)
    register_github_tools(m, allow=allow)
    register_gitlab_tools(m, allow=allow)
    register_social_tools(m, allow=allow)
    register_scrape_tools(m, allow=allow)
    register_agent_tools(m, allow=allow)
    register_ssh_smb_tools(m, allow=allow)
    register_prompt_tools(m, allow=allow)
    register_resource_tools(m, allow=allow)
    register_app_tools(m, allow=allow)
    register_agent_db_tools(m, allow=allow)
    register_room_tools(m, allow=allow)
    _load_user_extensions(m, allow=allow)


mcp = FastMCP("plutus_mcp", host=cfg.mcp_host, port=cfg.mcp_port, instructions=_MCP_INSTRUCTIONS)
register_all_tools(mcp)

tools = ToolRegistryAdapter(mcp)
# The old global tool gate (core/tool_gate.py) is gone; scoping is now per
# profile (core/profiles.py), each served at its own /mcp/p/<name> endpoint.


def all_tool_names() -> list[str]:
    return tools.tool_names()


def build_mcp(name: str, allow: "set[str] | None"):
    """A FastMCP with only ``allow`` registered (None = the full surface).

    ``host``/``port`` must match the main instance above. FastMCP auto-enables
    DNS-rebinding protection when host is a loopback address (its default), which
    allows only ``127.0.0.1``/``localhost`` Host headers and answers 421 to
    everything else — so an instance built without them serves nothing but
    localhost. That silently broke every /mcp/p/<name> profile endpoint, and the
    main /mcp too whenever the tool slicer built a filtered instance.
    """
    m = FastMCP(name, host=cfg.mcp_host, port=cfg.mcp_port, instructions=_MCP_INSTRUCTIONS)
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

    from core.mcp_bearer_middleware import MCPBearerGateMiddleware
    from core.oauth_routes import oauth_routes
    from core.tool_exposure import resolve_exposed

    def _serve_at(server, path: str):
        """A FastMCP sub-app whose endpoint sits at exactly ``path``.

        ``streamable_http_app()`` puts its endpoint on a plain ``Route`` at
        ``settings.streamable_http_path`` (default ``/mcp``), so ``Mount``-ing it
        under ``/mcp`` would nest the two into ``/mcp/mcp`` and leave the real
        ``/mcp`` as a 307 to ``/mcp/`` that 404s. Point the sub-app's own path at
        the absolute URL we advertise instead, and lift its route into the outer
        app. Safe because Plutus supplies auth itself (MCPBearerGateMiddleware),
        so FastMCP never attaches sub-app middleware that lifting would drop.
        """
        server.settings.streamable_http_path = path
        return server.streamable_http_app()

    names = all_tool_names()
    # The main /mcp honours the global tool-exposure "slicer": if categories are
    # disabled, serve a filtered instance so the manifest (and its tokens) shrink.
    exposed = resolve_exposed(ROOT, names)
    apps = [_serve_at(mcp if exposed is None else build_mcp("plutus", exposed), "/mcp")]
    for prof in load_profiles(ROOT):
        allow = resolve_tool_names(prof, names)
        sub = build_mcp(f"plutus-{prof['name']}", allow)
        apps.append(_serve_at(sub, f"/mcp/p/{prof['name']}"))

    @asynccontextmanager
    async def _lifespan(_app):
        async with AsyncExitStack() as stack:
            for sub_app in apps:
                await stack.enter_async_context(sub_app.router.lifespan_context(sub_app))
            yield

    # OAuth provider endpoints (discovery, register, authorize, token) live on the
    # MCP origin so browser connectors can sign in. Read live from .env so the
    # Settings toggle takes effect on the next server start.
    routes: list = []
    if str(read_env().get("MCP_OAUTH_ENABLED", "")).strip().lower() in ("true", "1", "yes"):
        routes.extend(oauth_routes())
    for sub_app in apps:
        routes.extend(sub_app.routes)

    app = Starlette(routes=routes, lifespan=_lifespan)
    app.add_middleware(MCPBearerGateMiddleware)
    return app

ICONS_DIR = ROOT / "icons"
STATIC_DIR = ROOT / "ui" / "static"
DIST_DIR = ROOT / "ui" / "static" / "dist"  # built React app (Vite base=/spa/)
ENV_FILE = str(ROOT / ".env")
_health_cache: dict = {}
_health_states: dict = {}
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


# The agent execution engine (the serial queue + its worker + the run
# invocation) now lives in core.agent_orchestrator, UI-free and injectable. This
# module constructs the one instance and keeps thin wrappers for the callers that
# still import these names from ui.runtime (ui.api.agents / workforce / providers).
agent_orchestrator = AgentOrchestrator(ROOT, cfg, tools, log=log)
_agent_queue = agent_orchestrator.queue


def _agent_mcp_target() -> tuple[str, str]:
    """(mcp_url, bearer_token) the agent uses to reach Plutus's own MCP tools.
    Wrapper over the shared core helper; kept because ui.api imports it here."""
    return agent_orchestrator.mcp_target()


_enqueue_agent = agent_orchestrator.enqueue


def _agent_service_disallow(selected: "list[str] | None") -> list[str]:
    """Per-connection ACL wrapper — the implementation is
    core.agent_orchestrator.service_disallow. Kept here because ui.api and the
    preset/scope tests import this name from ui.runtime."""
    return agent_orchestrator.service_disallow(selected)


def apply_preset(prompt: str, preset: str | None,
                 mcp_services: list[str] | None,
                 allow_write: bool, allow_publish: bool):
    """Fold a preset into one launch's settings.

    A preset supplies defaults for the three things every launch has to decide —
    which connections, how far it may go, and where the output lands — and the
    third was previously not decided at all, so an agent put an hour of research
    in a reply that then got truncated.

    It never *widens* an explicit choice. Connections are taken only when the
    caller passed none, and the capability switches are AND-ed: a read-only
    preset cannot be talked into writing by a stale tick box, and ticking
    read-only always wins over a preset that allows writes.

    Returns ``(prompt, mcp_services, allow_write, allow_publish)``.
    """
    if not preset:
        return prompt, mcp_services, allow_write, allow_publish

    from core import agent_presets

    spec = agent_presets.get_preset(preset)
    if mcp_services is None and spec["services"] is not None:
        mcp_services = list(spec["services"])
    allow_write = allow_write and spec["allow_write"]
    allow_publish = allow_publish and spec["allow_publish"]
    block = agent_presets.preamble(preset, root=ROOT)
    if block:
        prompt = f"{block}\n\n{prompt}"
    return prompt, mcp_services, allow_write, allow_publish


def _agent_capability_disallow(allow_write: bool, allow_publish: bool) -> list[str]:
    """What the wizard's write/post switches forbid, on top of the connection ACL.

    A separate axis from connections: picking Nextcloud says *where* an agent may
    act, these say *how far*. Read-only and may-not-publish are the two questions
    people actually want to answer before letting something loose on a web page.
    """
    from core.agent_permissions import capability_disallow

    return capability_disallow(tools.raw_manager, allow_write=allow_write,
                               allow_publish=allow_publish)


def _agent_profile_disallow(profile_name: str | None) -> list[str]:
    """Restrict an agent to a named MCP profile's tool subset: deny every Plutus
    tool the profile does not include. '' / None / unknown profile = no restriction.

    Computed at enqueue time against the full /mcp surface, so a profile applies
    to the next run immediately — no MCP-server restart needed (unlike the
    /mcp/p/<name> mounts, which are built once at startup)."""
    name = (profile_name or "").strip()
    if not name:
        return []
    prof = next((p for p in load_profiles(ROOT) if p.get("name") == name), None)
    if not prof:
        return []
    names = all_tool_names()
    allowed = set(resolve_tool_names(prof, names))
    if not allowed:
        return []
    return sorted(f"mcp__plutus__{t}" for t in names if t not in allowed)


# _enqueue_agent is agent_orchestrator.enqueue (bound above). The connection
# selection, folded into extra_disallowed by the caller, is the only tool gate.


def _run_agent_bg(prompt: str, label: str = "agent", *,
                  model: str | None = None, force: bool = False,
                  mcp_services: list[str] | None = None, profile: str | None = None,
                  provider: str = "", account_id: str = "",
                  allow_write: bool = True, allow_publish: bool = False,
                  preset: str | None = None,
                  smart_fallback: bool = True) -> None:
    # mcp_services=None means "no per-connection restriction" (back-compat for
    # callers and older schedules that never stored a selection). `profile` is no
    # longer offered in the UI; it stays here so schedules saved before it was
    # removed keep working.
    #
    prompt, mcp_services, allow_write, allow_publish = apply_preset(
        prompt, preset, mcp_services, allow_write, allow_publish)

    extra = sorted(set(_agent_service_disallow(mcp_services))
                   | set(_agent_profile_disallow(profile))
                   | set(_agent_capability_disallow(allow_write, allow_publish)))
    _enqueue_agent(prompt, label, model=model, force=force, extra_disallowed=extra,
                   mcp_services=mcp_services, provider=provider, account_id=account_id,
                   smart_fallback=smart_fallback)


_run_tool_scheduled = agent_orchestrator.run_tool_scheduled


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
                  model=task.get("model") or None, force=force)


_HEALTH_TTL = 60.0


async def get_health(force=False):
    """Cached service health.

    The refresh can take up to two minutes against slow/unreachable services, so
    it must not be held under a lock that every reader queues behind — one dead
    NAS would stall the whole dashboard. Readers with a usable cache get it
    immediately; only one refresh runs at a time and the rest serve stale.
    """
    global _health_cache, _health_states, _health_ts
    fresh = _health_cache and (time.time() - _health_ts) <= _HEALTH_TTL
    if fresh and not force:
        return _health_cache

    if not force and _health_lock.locked() and _health_cache:
        # A refresh is already in flight — serve stale. But a caller that asked to
        # force must not be fobbed off with stale data; it queues on the lock below
        # and gets a genuinely fresh gather.
        return _health_cache

    async with _health_lock:
        # Someone may have refreshed while we waited for the lock.
        if not force and _health_cache and (time.time() - _health_ts) <= _HEALTH_TTL:
            return _health_cache
        try:
            cache, rows = await asyncio.wait_for(
                gather_service_health(_services_live(), cfg), timeout=120.0
            )
            _health_cache = cache
            _health_states = {r["id"]: r.get("state") for r in rows}
        except Exception as exc:
            # Stamp the clock even on failure. Without this a persistently failing
            # probe left _health_ts stale, so every subsequent request immediately
            # launched another 120s gather — a self-inflicted thundering herd.
            _health_ts = time.time()
            log.warning("health refresh failed: %s", exc)
            if _health_cache:
                return _health_cache
            raise
        _health_ts = time.time()
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
    # Guarantee a login credential exists even if the UI runs standalone (or was
    # spawned before main.py seeded). Idempotent; see core/ui_users.
    from config import allow_empty_ui_password
    if cfg.ui_enabled and not allow_empty_ui_password():
        from core import ui_users
        ui_users.ensure_seed(ROOT)
    agent_tasks.seed_if_empty(ROOT)
    agent_orchestrator.start_worker()
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
