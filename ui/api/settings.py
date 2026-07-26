"""Settings surface: env writes, bearer token, CA upload, client export, SSH/SMB,
beta tool-cache prefs, update check, factory reset."""
from __future__ import annotations

import json
import os
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request

from config import cfg
from core.custom_integrations import save_raw
from core.tool_cache import (
    DEFAULT_PREFS as BETA_CACHE_DEFAULT_PREFS,
    load_entries,
    load_prefs,
    refresh_all_cached_tools,
    save_prefs,
)
from core.updates_github import check_github_release
from pydantic import BaseModel, Field

from ui.api.deps import verify_auth
from ui import runtime
from ui.runtime import ROOT, _services_live, load_env, log, save_env, tools

router = APIRouter(dependencies=[Depends(verify_auth)])


# ─── env-backed JSON list helpers (SSH hosts, SMB shares) ─────────────────────

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


# ─── SSH host manager ─────────────────────────────────────────────────────────

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


@router.get("/api/v1/ssh/hosts")
async def api_v1_ssh_hosts():
    return {"hosts": _read_json_env("SSH_HOSTS")}


@router.post("/api/v1/ssh/hosts")
async def api_v1_ssh_hosts_add(body: SSHHostBody):
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


@router.post("/api/v1/ssh/hosts/remove")
async def api_v1_ssh_hosts_remove(body: HostNameBody):
    return {"ok": True, "hosts": _remove_named_env_item("SSH_HOSTS", "Host", body.name)}


# ─── SMB share manager ────────────────────────────────────────────────────────

class SMBShareBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    server: str = Field(..., min_length=1, max_length=253)
    share: str = Field(..., min_length=1, max_length=128)
    user: str = "guest"
    password: str = ""
    mount: str = Field(..., min_length=1, max_length=512)


@router.get("/api/v1/smb/shares")
async def api_v1_smb_shares():
    return {"shares": _read_json_env("SMB_SHARES")}


@router.post("/api/v1/smb/shares")
async def api_v1_smb_shares_add(body: SMBShareBody):
    entry = {
        "name": body.name,
        "server": body.server,
        "share": body.share,
        "user": body.user or "guest",
        "password": body.password or "",
        "mount": body.mount,
    }
    return {"ok": True, "shares": _append_named_env_item("SMB_SHARES", "Share", entry)}


@router.post("/api/v1/smb/shares/remove")
async def api_v1_smb_shares_remove(body: HostNameBody):
    return {"ok": True, "shares": _remove_named_env_item("SMB_SHARES", "Share", body.name)}


# ─── Updates ──────────────────────────────────────────────────────────────────

@router.post("/api/v1/settings/check-updates")
async def api_settings_check_updates():
    return await check_github_release(os.getenv("PLUTUS_UPDATES_REPO", ""))


# ─── MCP client export ────────────────────────────────────────────────────────

@router.get("/api/v1/mcp/connections")
async def api_v1_mcp_connections(request: Request):
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


# ─── Env writes / reset / token / cert ────────────────────────────────────────

class SettingsResetBody(BaseModel):
    scopes: list[str] = Field(default_factory=lambda: ["urls"])


@router.post("/env/save")
async def env_save(request: Request):
    try:
        data = await request.json()
        if not isinstance(data, dict):
            raise HTTPException(400, "Expected a JSON object")
        save_env(data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@router.post("/api/v1/settings/reset")
async def api_v1_settings_reset(body: SettingsResetBody):
    """Reset selected settings to shipped defaults (does not remove service API keys)."""
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
    async with runtime._health_lock:
        runtime._health_cache, runtime._health_ts = {}, 0.0
    return {"ok": True, "scopes": sorted(scopes)}


@router.post("/settings/generate-token")
async def generate_token():
    token = secrets.token_hex(32)
    try:
        save_env({"MCP_BEARER_TOKEN": token})
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"token": token}


_MAX_CA_PEM_BYTES = 512_000


@router.post("/settings/upload-cert")
async def upload_cert(request: Request):
    content = await request.body()
    if len(content) > _MAX_CA_PEM_BYTES:
        raise HTTPException(400, f"CA bundle too large (max {_MAX_CA_PEM_BYTES} bytes)")
    os.makedirs(ROOT / "data", exist_ok=True)
    with open(ROOT / "data" / "ca.pem", "wb") as f:
        f.write(content)
    return {"ok": True}


# ─── Beta tool-output cache ───────────────────────────────────────────────────

@router.get("/api/v1/beta/cache/prefs")
async def api_beta_cache_prefs_get():
    return load_prefs(ROOT)


class BetaCachePrefsBody(BaseModel):
    enabled: bool | None = None
    refresh_hours: float | None = None
    refresh_scope: str | None = None
    disabled_service_ids: list[str] | None = None
    disabled_tool_names: list[str] | None = None


@router.post("/api/v1/beta/cache/prefs")
async def api_beta_cache_prefs_post(body: BetaCachePrefsBody):
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


@router.get("/api/v1/beta/cache/entries")
async def api_beta_cache_entries_get():
    return load_entries(ROOT)


@router.post("/api/v1/beta/cache/refresh")
async def api_beta_cache_refresh():
    rep = await refresh_all_cached_tools(ROOT, tools.raw_manager, _services_live())
    return {"ok": True, **rep}
