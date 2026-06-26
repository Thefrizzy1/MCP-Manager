"""main.py — Plutus MCP v5 — MCP on 8765, Web UI on 8766"""
from __future__ import annotations


def _ensure_plutus_runtime_dependencies() -> None:
    """Validate dependencies; auto-install only when PLUTUS_AUTO_INSTALL=1."""
    import importlib
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent
    missing = ""
    for mod in (
        "uvicorn",
        "fastapi",
        "httpx",
        "pydantic",
        "dotenv",
        "mcp.server.fastmcp",
    ):
        try:
            importlib.import_module(mod)
        except ImportError:
            missing = mod
            break
    else:
        return

    if os.getenv("PLUTUS_AUTO_INSTALL", "").strip().lower() not in {"1", "true", "yes"}:
        print(
            "Plutus: missing Python package: "
            f"{missing}\nRun:\n  {sys.executable} -m pip install -r \"{root / 'requirements.txt'}\"\n"
            "Or set PLUTUS_AUTO_INSTALL=1 for development auto-install.",
            file=sys.stderr,
            flush=True,
        )
        sys.exit(1)

    print("Plutus: missing Python packages — installing from requirements.txt …", flush=True)
    r = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(root / "requirements.txt")],
        cwd=str(root),
    )
    if r.returncode != 0:
        print(
            "Plutus: pip install failed. Run manually:\n"
            f"  {sys.executable} -m pip install -r \"{root / 'requirements.txt'}\"",
            flush=True,
        )
        sys.exit(1)
    os.execv(sys.executable, [sys.executable, *sys.argv])


_ensure_plutus_runtime_dependencies()

import asyncio, errno, json, logging, multiprocessing, os, secrets, sys, threading, time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.request import urlopen
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from starlette.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field
from mcp.server.fastmcp import FastMCP
from config import DEFAULT_UI_PASSWORD, allow_empty_ui_password, cfg, is_ui_writable_env_key
from core.batch_health import run_health_batch_for_ui
from core.capabilities import CapabilityCatalog
from core.dashboard_api import build_dashboard_payload
from core.dashboard_health import build_health_report_markdown, gather_service_health, probe_service_row
from core.discover_services import probe_host
from core.custom_integrations import load_raw, save_raw
from core.service_registry import all_services
from core.updates_github import check_github_release
from core.wizard_scan import build_wizard_scan
from core.mcp_bearer_middleware import MCPBearerGateMiddleware
from core.recent_runs import append_recent, ensure_data_dir, load_recent
from core.invoke_tool import invoke_mcp_tool_fn
from core.observability import Telemetry
from core.rate_limit import LoginRateLimiter
from core.env_store import read_env, update_env
from core.result_status import text_looks_successful
from core.router import RouterRuntime
from core.smoke_service_tools import run_service_smoke_tools
from core.tool_manager_adapter import ToolRegistryAdapter
from core.tool_cache import (
    DEFAULT_PREFS as BETA_CACHE_DEFAULT_PREFS,
    beta_cache_background_loop,
    load_entries,
    load_prefs,
    record_tool_output,
    refresh_all_cached_tools,
    save_prefs,
)
from ui.render import dashboard_page
from tools.media import register_media_tools
from tools.personal import register_personal_tools
from tools.photos import register_photo_tools
from tools.system import register_system_tools
from tools.comfyui import register_comfyui_tools
from tools.utilities import register_utility_tools
from tools.obsidian import register_obsidian_tools
from tools.monitoring import register_monitoring_tools
from tools.nextcloud import register_nextcloud_tools
from tools.infrastructure import register_infrastructure_tools
from tools.fal_tools import register_fal_tools
from tools.public_apis_bulk import register_public_apis_bulk
from tools.ssh_smb import register_ssh_smb_tools
from core.tool_gate import apply_tool_gate_patch, build_tool_slice, load_gate, set_active_intent, set_section_disabled, set_tool_enabled

ROOT = Path(__file__).resolve().parent
logging.basicConfig(level=os.getenv("PLUTUS_LOG_LEVEL", "INFO").upper())
log = logging.getLogger("plutus")

mcp = FastMCP("plutus_mcp", host=cfg.mcp_host, port=cfg.mcp_port,
    instructions="Plutus homelab MCP. Self-hosted: Jellyfin, *arrs, Immich, HA, Nextcloud, Habitica, Docker, OMV, ComfyUI, n8n, Syncthing, Obsidian. Public: fal.ai, weather, maps, web search.")

register_media_tools(mcp); register_personal_tools(mcp); register_photo_tools(mcp)
register_system_tools(mcp); register_comfyui_tools(mcp); register_utility_tools(mcp)
register_obsidian_tools(mcp); register_monitoring_tools(mcp); register_nextcloud_tools(mcp)
register_infrastructure_tools(mcp); register_fal_tools(mcp)
register_public_apis_bulk(mcp)
register_ssh_smb_tools(mcp)


def _load_user_extensions() -> None:
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
        reg(mcp)
        print("🔌 extensions: register(mcp) completed")
    except Exception as ex:
        print(f"⚠️ extensions.register(mcp): {ex}")


_load_user_extensions()

tools = ToolRegistryAdapter(mcp)
apply_tool_gate_patch(tools.raw_manager, ROOT)

_icons_dir = ROOT / "icons"
_static_dir = ROOT / "ui" / "static"
security = HTTPBasic()
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


@asynccontextmanager
async def _ui_lifespan(_app: FastAPI):
    loop_task = asyncio.create_task(
        beta_cache_background_loop(ROOT, lambda: tools.raw_manager, _services_live)
    )
    yield
    loop_task.cancel()
    try:
        await loop_task
    except asyncio.CancelledError:
        pass


ui_app = FastAPI(title="Plutus MCP UI", lifespan=_ui_lifespan)
if _icons_dir.is_dir():
    ui_app.mount("/icons", StaticFiles(directory=str(_icons_dir)), name="icons")
if _static_dir.is_dir():
    ui_app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


_CSRF_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@ui_app.middleware("http")
async def _csrf_origin_guard(request: Request, call_next):
    """Reject cross-site state-changing requests. A browser auto-sends `Origin`
    on cross-site POSTs; the dashboard's own fetch() is same-origin so it
    matches. Non-browser clients (curl, n8n) send no Origin and pass through —
    they authenticate with Basic auth and aren't subject to CSRF. Set
    PLUTUS_DISABLE_CSRF=1 to disable (e.g. an unusual proxy setup)."""
    if request.method not in _CSRF_SAFE_METHODS and os.getenv("PLUTUS_DISABLE_CSRF", "").strip().lower() not in ("1", "true", "yes"):
        origin = request.headers.get("origin")
        if origin:
            from urllib.parse import urlparse
            origin_host = (urlparse(origin).hostname or "").lower()
            allowed = {
                h.split(",")[0].split(":")[0].strip().lower()
                for h in (request.headers.get("host"), request.headers.get("x-forwarded-host"))
                if h
            }
            if origin_host and allowed and origin_host not in allowed:
                return JSONResponse(
                    {"detail": "Cross-origin request rejected (CSRF protection). Set PLUTUS_DISABLE_CSRF=1 if this is a false positive."},
                    status_code=403,
                )
    return await call_next(request)


_login_limiter = LoginRateLimiter()


def _client_key(request: Request) -> str:
    return request.client.host if request and request.client else "unknown"


def verify_auth(request: Request, creds: HTTPBasicCredentials = Depends(security)):
    if not (cfg.ui_password or allow_empty_ui_password()):
        raise HTTPException(
            503,
            "UI_PASSWORD is not set. Add it to .env (see .env.example), or set "
            "PLUTUS_ALLOW_EMPTY_UI_PASSWORD=1 for isolated local development only.",
        )
    key = _client_key(request)
    remaining = _login_limiter.locked_for(key, time.time())
    if remaining > 0:
        raise HTTPException(
            429,
            f"Too many failed logins. Try again in {int(remaining) + 1}s.",
            headers={"Retry-After": str(int(remaining) + 1)},
        )
    user_ok = secrets.compare_digest(creds.username, cfg.ui_username)
    if cfg.ui_password:
        pass_ok = secrets.compare_digest(creds.password, cfg.ui_password)
    else:
        pass_ok = allow_empty_ui_password() and not creds.password
    if not (user_ok and pass_ok):
        locked = _login_limiter.record_failure(key, time.time())
        if locked > 0:
            raise HTTPException(
                429,
                f"Too many failed logins. Locked for {int(locked)}s.",
                headers={"Retry-After": str(int(locked))},
            )
        raise HTTPException(401, "Bad credentials", headers={"WWW-Authenticate": "Basic"})
    _login_limiter.record_success(key)
    return creds

def load_env():
    """Read .env into a dict. Canonical implementation lives in core.env_store."""
    return read_env()

def save_env(updates: dict):
    """Validate + atomically write .env (and sync cfg). See core.env_store."""
    update_env(updates)

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

@ui_app.get("/", response_class=HTMLResponse)
async def root(): return RedirectResponse(url="/ui")

@ui_app.get("/ui", response_class=HTMLResponse)
async def ui(creds=Depends(verify_auth)):
    health = await get_health()
    ensure_data_dir(ROOT); recent = load_recent(ROOT)
    html = dashboard_page(health_cache=health, tool_count=_tool_count(), recent=recent)
    return HTMLResponse(
        content=html,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"},
    )

@ui_app.get("/health/refresh")
async def health_refresh(creds=Depends(verify_auth)):
    global _health_cache, _health_ts
    async with _health_lock:
        cache, _ = await gather_service_health(_services_live(), cfg)
        _health_cache, _health_ts = cache, time.time()
    return cache

@ui_app.post("/api/v1/health/regression-check")
async def api_v1_health_regression_check(request: Request, creds=Depends(verify_auth)):
    """Run the tool health batch, diff against the saved baseline, and report
    regressions (tools that worked before and fail now). ?notify=1 pushes an
    ntfy alert when regressions exist. ?dry=1 skips updating the baseline.

    Intended to be hit on a schedule (n8n cron / scheduled-tasks / cron curl).
    """
    from core.health_regression import run_regression_check

    notify_on = (request.query_params.get("notify") or "").strip().lower() in {"1", "true", "yes"}
    dry = (request.query_params.get("dry") or "").strip().lower() in {"1", "true", "yes"}

    notify_fn = None
    if notify_on:
        ntfy_tool = tools.get_tool("ntfy_send")
        if ntfy_tool is not None:
            async def notify_fn(message: str):  # noqa: ANN001 - local helper
                await invoke_mcp_tool_fn(
                    ntfy_tool.fn,
                    payload={"message": message, "title": "Plutus health regression", "priority": "high"},
                )

    return await run_regression_check(
        ROOT, tools.raw_manager, notify=notify_fn, update_baseline=not dry
    )


@ui_app.get("/api/v1/mcp/selftest")
async def api_v1_mcp_selftest(creds=Depends(verify_auth)):
    """Probe the live MCP endpoint the way a client would, so the Connection
    Manager can show green/red before you paste a config. Sends an `initialize`
    request to 127.0.0.1:<mcp_port>/mcp with the Bearer token when required.
    """
    import httpx

    url = f"http://127.0.0.1:{cfg.mcp_port}/mcp"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if cfg.mcp_require_bearer:
        tok = (load_env().get("MCP_BEARER_TOKEN", "") or "").strip()
        if tok:
            headers["Authorization"] = f"Bearer {tok}"
    body = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "plutus-selftest", "version": "1"},
        },
    }
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.post(url, headers=headers, json=body)
        status = r.status_code
        if status in (401, 403):
            return {"ok": False, "reachable": True, "status": status,
                    "detail": "Reachable, but the Bearer token was rejected or missing. Regenerate/enable it under MCP Bearer Auth."}
        if status == 503:
            return {"ok": False, "reachable": True, "status": status,
                    "detail": "Reachable, but MCP auth is required and no token is set. Generate one under MCP Bearer Auth."}
        if 200 <= status < 500:
            return {"ok": True, "reachable": True, "status": status,
                    "detail": f"MCP endpoint responded (HTTP {status}). Clients should be able to connect."}
        return {"ok": False, "reachable": True, "status": status,
                "detail": f"MCP endpoint returned HTTP {status}."}
    except Exception as e:
        return {"ok": False, "reachable": False, "status": 0,
                "detail": f"Could not reach the MCP endpoint on port {cfg.mcp_port}: {type(e).__name__}. Is the MCP process running?"}


@ui_app.post("/health/full-report")
async def health_full_report(creds=Depends(verify_auth)):
    """Refresh service probes, run zero-arg tool batch, return markdown + structured rows."""
    global _health_cache, _health_ts
    async with _health_lock:
        cache, svc_rows = await gather_service_health(_services_live(), cfg)
        _health_cache, _health_ts = cache, time.time()
    tool_rows = await run_health_batch_for_ui(tools.raw_manager)
    md = build_health_report_markdown(svc_rows, tool_rows)
    return {"health": cache, "services": svc_rows, "tools": tool_rows, "markdown": md}

@ui_app.get("/service/test/{sid}")
async def test_service(sid: str, creds=Depends(verify_auth)):
    svc = next((s for s in _services_live() if s["id"] == sid), None)
    if not svc:
        return JSONResponse({"ok": False, "error": "Unknown service"})
    row = await probe_service_row(svc, cfg)
    o = row.get("ok")
    detail = (row.get("detail") or "").strip()
    summary = (row.get("summary") or "").strip()
    lines = [summary] if summary else []
    if detail:
        lines.extend(["", detail])
    output = "\n".join(lines).strip()
    if o is True:
        tri = "ok"
    elif o is False:
        tri = "fail"
    elif row.get("kind") == "unconfigured":
        tri = "uncfg"
    else:
        tri = "unknown"
    return {
        "ok": o is True,
        "output": output or summary or "(no output)",
        "summary": summary,
        "detail": detail,
        "kind": row.get("kind"),
        "tri": tri,
        "error": None,
    }


@ui_app.post("/service/smoke-tools/{sid}")
async def service_smoke_tools(sid: str, creds=Depends(verify_auth)):
    """Run each integration tool with server-side hardcoded payloads (safe/smoke only)."""
    svc = next((s for s in _services_live() if s["id"] == sid), None)
    if not svc:
        return JSONResponse({"error": "Unknown service"}, status_code=404)
    tool_entries = svc.get("tools", [])
    rep = await run_service_smoke_tools(tools.raw_manager, tool_entries)
    return rep


@ui_app.get("/api/v1/dashboard")
async def api_v1_dashboard(request: Request, creds=Depends(verify_auth)):
    """Structured dashboard JSON. Query: ?sections=networking,main,tools,services,auth,recent (comma-sep). Omit for all."""
    raw = (request.query_params.get("sections") or "").strip()
    sections = {s.strip().lower() for s in raw.split(",") if s.strip()}
    health = await get_health()
    recent = load_recent(ROOT)
    tool_names = capabilities.tool_names()
    payload = build_dashboard_payload(
        health_cache=health,
        tool_names=tool_names,
        recent=list(recent or []),
        sections=sections,
        local_ip_hint=cfg.mcp_lan_host,
    )
    return payload


@ui_app.get("/api/v1/capabilities")
async def api_v1_capabilities(request: Request, creds=Depends(verify_auth)):
    include_tools = (request.query_params.get("include_tools") or "").strip().lower() in {"1", "true", "yes"}
    return capabilities.payload(include_tools=include_tools)


class RouteBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)


@ui_app.post("/api/v1/router/route")
async def api_v1_router_route(body: RouteBody, creds=Depends(verify_auth)):
    return await router_runtime.handle(body.text)


@ui_app.get("/api/v1/observability")
async def api_v1_observability(creds=Depends(verify_auth)):
    return telemetry.snapshot()


class DiscoverBody(BaseModel):
    host: str = Field(..., min_length=1, max_length=253)


@ui_app.post("/api/v1/discover")
async def api_v1_discover(body: DiscoverBody, creds=Depends(verify_auth)):
    """Probe common service ports on a single host (wizard)."""
    hits = await probe_host(body.host)
    return {"host": body.host.strip(), "hits": hits}


class WizardScanBody(BaseModel):
    host: str = Field(..., min_length=1, max_length=253)
    include_port_scan: bool = True


@ui_app.post("/api/v1/wizard/scan")
async def api_v1_wizard_scan(body: WizardScanBody, creds=Depends(verify_auth)):
    """Docker-aware URL suggestions (published ports) plus optional LAN port probes."""
    return await build_wizard_scan(
        body.host,
        include_port_scan=body.include_port_scan,
        services=_services_live(),
    )


class ToolGateToggleBody(BaseModel):
    tool: str = Field(..., min_length=1, max_length=160)
    enabled: bool = True


class ToolGateSectionBody(BaseModel):
    section: str = Field(..., min_length=3, max_length=32)
    disabled: bool = True


@ui_app.get("/api/v1/tools/gate")
async def api_v1_tools_gate_get(creds=Depends(verify_auth)):
    return load_gate(ROOT)


@ui_app.get("/api/v1/tools/slicer")
async def api_v1_tools_slicer(intent: str = "", creds=Depends(verify_auth)):
    return build_tool_slice(ROOT, intent=intent)


@ui_app.post("/api/v1/tools/gate/tool")
async def api_v1_tools_gate_tool(body: ToolGateToggleBody, creds=Depends(verify_auth)):
    set_tool_enabled(ROOT, body.tool, body.enabled)
    capabilities.invalidate()
    return {"ok": True}


@ui_app.post("/api/v1/tools/gate/section")
async def api_v1_tools_gate_section(body: ToolGateSectionBody, creds=Depends(verify_auth)):
    try:
        set_section_disabled(ROOT, body.section, body.disabled)
    except ValueError as e:
        raise HTTPException(400, str(e))
    capabilities.invalidate()
    return {"ok": True}


class IntentBody(BaseModel):
    intent: str = ""


@ui_app.get("/api/v1/tools/intent")
async def api_v1_tools_intent_get(creds=Depends(verify_auth)):
    """Read the active server-wide tool-slicer intent."""
    g = load_gate(ROOT)
    cur = g.get("active_intent", "")
    return {"active_intent": cur, "slice": build_tool_slice(ROOT, cur)}


@ui_app.post("/api/v1/tools/intent")
async def api_v1_tools_intent_set(body: IntentBody, creds=Depends(verify_auth)):
    """Persist the active intent. MCP clients must reconnect to see the new manifest."""
    normalized = set_active_intent(ROOT, body.intent)
    capabilities.invalidate()
    return {"ok": True, "active_intent": normalized, "slice": build_tool_slice(ROOT, normalized)}


# ─── SSH host manager endpoints ───────────────────────────────────────────────

class SSHHostBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    host: str = Field(..., min_length=1, max_length=253)
    user: str = "root"
    port: int = 22
    key_path: str | None = None
    password: str | None = None
    readonly: bool = True


class HostNameBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)


def _read_json_env(key: str) -> list[dict]:
    raw = load_env().get(key, "[]") or "[]"
    try:
        val = json.loads(raw)
        return val if isinstance(val, list) else []
    except Exception as exc:
        log.warning("Invalid JSON in %s: %s", key, exc)
        return []


def _write_json_env(key: str, value: list[dict]) -> None:
    encoded = json.dumps(value, ensure_ascii=False)
    save_env({key: encoded})
    # Keep the in-memory cfg in sync so MCP tools see the change without restart.
    if key == "SSH_HOSTS":
        cfg.ssh_hosts_json = encoded
    elif key == "SMB_SHARES":
        cfg.smb_shares_json = encoded


def _append_named_env_item(key: str, label: str, entry: dict) -> list[dict]:
    rows = _read_json_env(key)
    name = entry["name"]
    if any(row.get("name") == name for row in rows):
        raise HTTPException(400, f"{label} '{name}' already exists.")
    rows.append(entry)
    _write_json_env(key, rows)
    return rows


def _remove_named_env_item(key: str, label: str, name: str) -> list[dict]:
    rows = _read_json_env(key)
    kept = [row for row in rows if row.get("name") != name]
    if len(kept) == len(rows):
        raise HTTPException(404, f"{label} '{name}' not found.")
    _write_json_env(key, kept)
    return kept


@ui_app.get("/api/v1/ssh/hosts")
async def api_v1_ssh_hosts(creds=Depends(verify_auth)):
    return {"hosts": _read_json_env("SSH_HOSTS")}


@ui_app.post("/api/v1/ssh/hosts")
async def api_v1_ssh_hosts_add(body: SSHHostBody, creds=Depends(verify_auth)):
    entry: dict = {
        "name": body.name,
        "host": body.host,
        "user": body.user or "root",
        "port": body.port or 22,
        "readonly": body.readonly,
    }
    if body.key_path and body.key_path.strip():
        entry["key"] = body.key_path.strip()
    if body.password:
        entry["password"] = body.password
    return {"ok": True, "hosts": _append_named_env_item("SSH_HOSTS", "Host", entry)}


@ui_app.post("/api/v1/ssh/hosts/remove")
async def api_v1_ssh_hosts_remove(body: HostNameBody, creds=Depends(verify_auth)):
    return {"ok": True, "hosts": _remove_named_env_item("SSH_HOSTS", "Host", body.name)}


# ─── SMB share manager endpoints ──────────────────────────────────────────────

class SMBShareBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    server: str = Field(..., min_length=1, max_length=253)
    share: str = Field(..., min_length=1, max_length=128)
    user: str = "guest"
    password: str = ""
    mount: str = Field(..., min_length=1, max_length=512)


@ui_app.get("/api/v1/smb/shares")
async def api_v1_smb_shares(creds=Depends(verify_auth)):
    return {"shares": _read_json_env("SMB_SHARES")}


@ui_app.post("/api/v1/smb/shares")
async def api_v1_smb_shares_add(body: SMBShareBody, creds=Depends(verify_auth)):
    entry = {
        "name": body.name,
        "server": body.server,
        "share": body.share,
        "user": body.user or "guest",
        "password": body.password or "",
        "mount": body.mount,
    }
    return {"ok": True, "shares": _append_named_env_item("SMB_SHARES", "Share", entry)}


@ui_app.post("/api/v1/smb/shares/remove")
async def api_v1_smb_shares_remove(body: HostNameBody, creds=Depends(verify_auth)):
    return {"ok": True, "shares": _remove_named_env_item("SMB_SHARES", "Share", body.name)}


@ui_app.get("/settings/custom-integrations")
async def settings_custom_integrations_get(creds=Depends(verify_auth)):
    return load_raw(ROOT)


@ui_app.post("/settings/custom-integrations")
async def settings_custom_integrations_post(request: Request, creds=Depends(verify_auth)):
    global _health_cache, _health_ts
    try:
        data = await request.json()
        if not isinstance(data, dict):
            raise HTTPException(400, "Expected a JSON object at the root")
        save_raw(ROOT, data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except json.JSONDecodeError:  # invalid JSON body from client
        raise HTTPException(400, "Body must be valid JSON")
    async with _health_lock:
        _health_cache, _health_ts = {}, 0.0
    capabilities.invalidate()
    return {"ok": True}


@ui_app.post("/api/v1/settings/check-updates")
async def api_settings_check_updates(creds=Depends(verify_auth)):
    return await check_github_release(os.getenv("PLUTUS_UPDATES_REPO", ""))


@ui_app.get("/api/v1/mcp/connections")
async def api_v1_mcp_connections(request: Request, creds=Depends(verify_auth)):
    """Build downloadable MCP connection configs for every common client.

    ?include_token=1 embeds the real Bearer token in the snippets (only when
    MCP_REQUIRE_BEARER is on and a token exists). Otherwise the token is omitted
    so the page can be shared/screenshotted safely.
    """
    from core.mcp_export import build_connection_exports

    include_token = (request.query_params.get("include_token") or "").strip().lower() in {"1", "true", "yes"}
    mcp_http_url = f"http://{cfg.mcp_lan_host}:{cfg.mcp_port}/mcp"
    pub_b = (cfg.public_mcp_base or "").strip().rstrip("/")
    mcp_https_url = pub_b + "/mcp" if pub_b.startswith(("http://", "https://")) else ""
    primary = mcp_https_url or mcp_http_url
    sse_primary = primary[: -len("/mcp")] + "/sse" if primary.endswith("/mcp") else primary
    is_http = primary.startswith("http://")
    token = ""
    if include_token and cfg.mcp_require_bearer:
        token = (load_env().get("MCP_BEARER_TOKEN", "") or "").strip()
    payload = build_connection_exports(
        mcp_url=primary,
        sse_url=sse_primary,
        is_http=is_http,
        token=token,
    )
    payload["bearer_required"] = bool(cfg.mcp_require_bearer)
    payload["token_available"] = bool((load_env().get("MCP_BEARER_TOKEN", "") or "").strip())
    return payload


class SettingsResetBody(BaseModel):
    scopes: list[str] = Field(default_factory=lambda: ["urls"])


@ui_app.post("/env/save")
async def env_save(request: Request, creds=Depends(verify_auth)):
    try:
        data = await request.json()
        if not isinstance(data, dict):
            raise HTTPException(400, "Expected a JSON object")
        save_env(data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@ui_app.post("/api/v1/settings/reset")
async def api_v1_settings_reset(body: SettingsResetBody, creds=Depends(verify_auth)):
    """Reset selected settings to shipped defaults (does not remove service API keys)."""
    global _health_cache, _health_ts
    scopes = {s.strip().lower() for s in (body.scopes or []) if s.strip()}
    if not scopes:
        raise HTTPException(400, "Provide at least one scope: urls, weather, custom_integrations, beta_cache")
    unknown = scopes - {"urls", "weather", "custom_integrations", "beta_cache"}
    if unknown:
        raise HTTPException(400, f"Unknown scope(s): {', '.join(sorted(unknown))}")
    updates: dict = {}
    if "urls" in scopes:
        updates["PUBLIC_MCP_BASE"] = ""
        updates["MCP_LAN_HOST"] = "192.168.1.111"
        updates["MCP_REQUIRE_BEARER"] = False
    if "weather" in scopes:
        updates["WEATHER_DEFAULT_LOCATION"] = "Hamburg"
    if updates:
        save_env(updates)
    if "custom_integrations" in scopes:
        save_raw(ROOT, {"version": 1, "integrations": []})
    if "beta_cache" in scopes:
        save_prefs(ROOT, dict(BETA_CACHE_DEFAULT_PREFS))
    async with _health_lock:
        _health_cache, _health_ts = {}, 0.0
    return {"ok": True, "scopes": sorted(scopes)}

@ui_app.post("/settings/generate-token")
async def generate_token(creds=Depends(verify_auth)):
    token = secrets.token_hex(32)
    try:
        save_env({"MCP_BEARER_TOKEN": token})
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"token": token}


_MAX_CA_PEM_BYTES = 512_000


@ui_app.post("/settings/upload-cert")
async def upload_cert(request: Request, creds=Depends(verify_auth)):
    content = await request.body()
    if len(content) > _MAX_CA_PEM_BYTES:
        raise HTTPException(400, f"CA bundle too large (max {_MAX_CA_PEM_BYTES} bytes)")
    os.makedirs(ROOT / "data", exist_ok=True)
    with open(ROOT / "data" / "ca.pem", "wb") as f:
        f.write(content)
    return {"ok": True}


@ui_app.get("/api/v1/beta/cache/prefs")
async def api_beta_cache_prefs_get(creds=Depends(verify_auth)):
    return load_prefs(ROOT)


class BetaCachePrefsBody(BaseModel):
    enabled: bool | None = None
    refresh_hours: float | None = None
    refresh_scope: str | None = None
    disabled_service_ids: list[str] | None = None
    disabled_tool_names: list[str] | None = None


@ui_app.post("/api/v1/beta/cache/prefs")
async def api_beta_cache_prefs_post(body: BetaCachePrefsBody, creds=Depends(verify_auth)):
    cur = load_prefs(ROOT)
    if body.enabled is not None:
        cur["enabled"] = body.enabled
    if body.refresh_hours is not None:
        cur["refresh_hours"] = max(0.25, float(body.refresh_hours))
    if body.refresh_scope is not None:
        rs = str(body.refresh_scope).strip().lower()
        allowed = {"all", "public_apis", "selfhosted_only", "information"}
        if rs not in allowed:
            raise HTTPException(400, f"refresh_scope must be one of: {', '.join(sorted(allowed))}")
        cur["refresh_scope"] = rs
    if body.disabled_service_ids is not None:
        cur["disabled_service_ids"] = body.disabled_service_ids
    if body.disabled_tool_names is not None:
        cur["disabled_tool_names"] = body.disabled_tool_names
    save_prefs(ROOT, cur)
    return {"ok": True, "prefs": cur}


@ui_app.get("/api/v1/beta/cache/entries")
async def api_beta_cache_entries_get(creds=Depends(verify_auth)):
    return load_entries(ROOT)


@ui_app.post("/api/v1/beta/cache/refresh")
async def api_beta_cache_refresh(creds=Depends(verify_auth)):
    rep = await refresh_all_cached_tools(ROOT, tools.raw_manager, _services_live())
    return {"ok": True, **rep}


class ToolRunBody(BaseModel):
    tool: str; params: dict = {}

@ui_app.post("/tool/run")
async def tool_run(body: ToolRunBody, creds=Depends(verify_auth)):
    try:
        started = time.perf_counter()
        tool = tools.get_tool(body.tool)
        if not tool:
            return JSONResponse({"error": f"Tool '{body.tool}' not available (disabled or not registered)"})
        result = await asyncio.wait_for(invoke_mcp_tool_fn(tool.fn, payload=body.params), timeout=30.0)
        ensure_data_dir(ROOT); append_recent(ROOT, {"tool":body.tool,"ts":time.strftime("%H:%M")})
        text = json.dumps(result, ensure_ascii=False) if isinstance(result, (dict, list)) else str(result)
        ok = text_looks_successful(text)
        telemetry.record(
            route="tool.direct",
            status="ok" if ok else "fail",
            latency_ms=int((time.perf_counter() - started) * 1000),
            detail=body.tool,
        )
        try:
            record_tool_output(ROOT, body.tool, text, ok=ok)
        except Exception as exc:
            log.warning("Failed to record tool output for %s: %s", body.tool, exc)
        if isinstance(result, (dict, list)):
            return JSONResponse({"result": result})
        return JSONResponse({"result": text})
    except asyncio.TimeoutError: return JSONResponse({"error":f"Timeout (30s) running {body.tool}"})
    except Exception as e: return JSONResponse({"error":str(e)})

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


@ui_app.get("/server/health")
async def server_health():
    """Unauthenticated liveness probe (used by the Docker healthcheck).

    Returns 503 if the MCP server (the actual product, in a separate process)
    is unreachable — so 'container healthy' means both halves are up, not just
    the UI answering this request.
    """
    from core.version_info import VERSION
    mcp_alive = await asyncio.to_thread(_mcp_port_alive)
    body = {
        "status": "ok" if mcp_alive else "degraded",
        "version": VERSION,
        "tools": _tool_count(),
        "mcp_port": cfg.mcp_port,
        "mcp_alive": mcp_alive,
        "ui_port": cfg.ui_port,
        "uptime_s": int(time.time() - _started_at),
    }
    return JSONResponse(body, status_code=200 if mcp_alive else 503)

def _is_address_in_use(exc: OSError) -> bool:
    if exc.errno == errno.EADDRINUSE:
        return True
    # Windows: WSAEADDRINUSE
    if getattr(exc, "winerror", None) == 10048:
        return True
    return False


def run_ui():
    try:
        uvicorn.run(ui_app, host="0.0.0.0", port=cfg.ui_port, log_level="warning")
    except OSError as e:
        if _is_address_in_use(e):
            print(
                f"Plutus: Web UI cannot bind 0.0.0.0:{cfg.ui_port} — port already in use.\n"
                f"   ({e})\n"
                "   Stop the other process, change UI_PORT in .env, or set UI_ENABLED=false for MCP-only.",
                file=sys.stderr,
                flush=True,
            )
        else:
            print(f"Plutus: Web UI failed: {e}", file=sys.stderr, flush=True)
        sys.exit(1)


async def _run_mcp_streamable_http() -> None:
    starlette_app = mcp.streamable_http_app()
    starlette_app.add_middleware(MCPBearerGateMiddleware)
    server = uvicorn.Server(
        uvicorn.Config(starlette_app, host=cfg.mcp_host, port=cfg.mcp_port, log_level="warning")
    )
    try:
        await server.serve()
    except OSError as e:
        if _is_address_in_use(e):
            print(
                f"Plutus: MCP cannot bind {cfg.mcp_host}:{cfg.mcp_port} — port already in use.\n"
                f"   ({e})\n"
                "   Stop the other process or set MCP_PORT in .env.",
                file=sys.stderr,
                flush=True,
            )
        raise


def _run_mcp_main() -> None:
    try:
        asyncio.run(_run_mcp_streamable_http())
    except OSError:
        sys.exit(1)


def _wait_for_ui_start(proc: multiprocessing.Process, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{cfg.ui_port}/server/health"
    while time.time() < deadline:
        if proc.exitcode not in (None, 0):
            return False
        try:
            with urlopen(url, timeout=0.35) as resp:
                if 200 <= resp.status < 500:
                    return True
        except Exception:
            time.sleep(0.15)
    return proc.is_alive()


_shutting_down = False


def _sweep_stale_tmp() -> None:
    """Remove leftover *.tmp files from atomic writes interrupted by a crash."""
    for d in (ROOT, ROOT / "data"):
        try:
            for f in d.glob("*.tmp"):
                f.unlink(missing_ok=True)
        except OSError:
            pass


def _install_signal_handlers(ui_proc: "multiprocessing.Process | None") -> None:
    import signal

    def _terminate_ui() -> None:
        if ui_proc and ui_proc.is_alive():
            ui_proc.terminate()
            ui_proc.join(timeout=5)

    def _handler(signum, _frame):
        global _shutting_down
        _shutting_down = True
        _terminate_ui()
        sys.exit(0)

    import atexit
    atexit.register(_terminate_ui)
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):
            pass  # not in main thread / unsupported platform


def _start_ui_watchdog(ui_proc: "multiprocessing.Process") -> None:
    """If the UI child dies unexpectedly, exit the main process so Docker's
    restart policy recycles the whole container (otherwise the dashboard would
    be down while MCP keeps the container 'up')."""
    def _watch() -> None:
        ui_proc.join()
        if not _shutting_down:
            print("⚠️  Plutus: Web UI process exited unexpectedly — stopping for container restart.",
                  file=sys.stderr, flush=True)
            os._exit(1)

    threading.Thread(target=_watch, name="ui-watchdog", daemon=True).start()


if __name__ == "__main__":
    ensure_data_dir(ROOT)
    _sweep_stale_tmp()
    if not cfg.ui_password and not allow_empty_ui_password():
        print(
            "⚠️  UI_PASSWORD is empty in .env — /ui will return 503 until you set a password "
            "(or PLUTUS_ALLOW_EMPTY_UI_PASSWORD=1 for dev only).",
            flush=True,
        )
    ui_proc = None
    if cfg.ui_enabled:
        ui_proc = multiprocessing.Process(target=run_ui, daemon=True)
        ui_proc.start()
        if not _wait_for_ui_start(ui_proc):
            print(
                f"Plutus: Web UI process exited early (code {ui_proc.exitcode}) — "
                f"check port {cfg.ui_port}.",
                flush=True,
            )
        else:
            _start_ui_watchdog(ui_proc)
    _install_signal_handlers(ui_proc)
    print("🚀 Plutus MCP v5", flush=True)
    print(f"   MCP:    http://0.0.0.0:{cfg.mcp_port}/mcp", flush=True)
    if cfg.mcp_require_bearer:
        print("   MCP auth: Bearer token required (MCP_REQUIRE_BEARER=true)", flush=True)
    if cfg.ui_enabled:
        print(f"   Web UI: http://0.0.0.0:{cfg.ui_port}/ui", flush=True)
        if os.getenv("UI_PASSWORD") is None:
            print(
                f"   ℹ️  Login: username `{cfg.ui_username}`, password `{DEFAULT_UI_PASSWORD}` "
                "(set UI_PASSWORD in .env to change)",
                flush=True,
            )
    else:
        print("   Web UI: off (UI_ENABLED=false) — MCP API only, lower RAM", flush=True)
    _run_mcp_main()
