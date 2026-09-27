"""
tools/comfy_mcp.py — Comfy-Org's ``comfy-mcp`` server, served through Plutus.

``comfy-mcp`` is the official ComfyUI MCP server: a thin wrapper over
``comfy-cli`` whose ~40 tools run workflows and templates, poll and fetch jobs,
search the live node/model catalog, validate and vary workflows, and manage a
local ComfyUI install. Plutus's own ``comfyui_*`` tools talk to ComfyUI's HTTP
API directly; this adds the rest without re-implementing any of it.

It cannot be imported: it requires mcp 2.x and Plutus is on 1.x. So it runs as a
child process (``core/mcp_stdio.py``) and every one of its tools is re-registered
here as ``comfy_<name>``. That puts them behind Plutus's own auth, profiles,
exposure switches and the agent write/publish axis — which a client connecting
to ``comfy-mcp`` directly would not have.

Three decisions worth knowing:

- **Descriptions are shortened to their first line.** comfy-mcp's own run to
  ~46K characters for 39 tools — about 12K tokens in every request that carries
  the manifest. ``comfy_mcp_status(tool=…)`` returns any tool's full guide on
  demand, and the tools stay in their own ``comfy`` category so a profile or the
  exposure page can drop all of them at once.
- **Read vs write is decided here**, by name, because comfy-mcp publishes no
  annotations. An unknown future tool is registered as a destructive write until
  it is classified — the safe default for the permission switches.
- **Where ComfyUI is** comes from Plutus's ``COMFYUI_URL``. A loopback address
  means "this machine" (comfy-cli's ``COMFY_LOCAL_URL``); anything else is a
  remote GPU box (comfy-mcp's ``COMFYUI_URL``), where runs, jobs, uploads and
  output fetches go to the remote and install/lifecycle tools stay local.

comfy-mcp asks for consent before installs, version switches and spending
credits. Plutus declines every such prompt (nobody is at the pipe), so those
fail closed unless pre-authorised the way comfy-mcp documents
(``COMFY_MCP_ASSUME_CONSENT``; credits only through comfy-cli's own consent).
"""
import json
import os
import shlex
import shutil
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import anyio
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from config import cfg
from core.mcp_stdio import McpStdioError, StdioMcpClient, base_env, result_text

_ROOT = Path(__file__).resolve().parents[1]
PREFIX = "comfy_"
CACHE_TTL = 24 * 3600

# Classified by hand: comfy-mcp publishes no annotations.
READ_ONLY = frozenset({
    "server_info", "auth_status", "list_partner_models", "partner_model_schema",
    "system_stats", "get_logs", "discover", "which", "search_templates", "get_template",
    "nodes", "node_dependencies", "workflow_deps", "search_models", "validate_workflow",
    "list_workflow_slots", "list_workflow_notes",
})
# Changes an install, stops something running, or spends money.
DESTRUCTIVE = frozenset({
    "stop_comfyui", "restart_comfyui", "update_comfyui", "switch_comfyui_version",
    "install_node", "partner_generate",
})
_INTERNET = frozenset({
    "auth_status", "auth_login", "list_partner_models", "partner_model_schema",
    "partner_generate", "search_templates", "get_template", "fetch_template",
    "run_template", "download_model", "update_comfyui", "install_node",
    "switch_comfyui_version",
})


def _dir() -> Path:
    d = _ROOT / "data" / "comfy-mcp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def command() -> list[str]:
    """How to start comfy-mcp: COMFY_MCP_COMMAND, else ``comfy-mcp`` on PATH.

    Empty when the program is not there — an image built without it, or a
    command naming a path that does not exist — so nothing tries to spawn it.
    """
    raw = (cfg.comfy_mcp_command or "").strip() or "comfy-mcp"
    if Path(raw).is_file():
        return [raw]
    parts = shlex.split(raw, posix=os.name != "nt")
    exe = shutil.which(parts[0]) if parts else None
    return [exe, *parts[1:]] if exe else []


def target_env() -> dict[str, str]:
    """The environment comfy-mcp needs, derived from Plutus's own settings."""
    project = _dir() / "project"
    project.mkdir(parents=True, exist_ok=True)
    env = {"COMFY_PROJECT": str(project), "COMFY_API_KEY": cfg.comfy_api_key or ""}
    url = (cfg.comfyui_url or "").strip()
    if url:
        u = urlparse(url if "://" in url else f"http://{url}")
        host, port = u.hostname or "", u.port or 8188
        if host:
            # comfy-mcp accepts plain http host:port only — no path, no TLS.
            addr = f"http://{'[' + host + ']' if ':' in host else host}:{port}"
            if host in ("127.0.0.1", "localhost", "::1"):
                env["COMFY_LOCAL_URL"] = addr          # this machine, maybe another port
            else:
                env["COMFYUI_URL"] = addr              # a GPU box elsewhere
    return env


# ── one child process per Plutus process ─────────────────────────────────────

_lock = threading.Lock()
_client: StdioMcpClient | None = None
_client_sig: tuple = ()


def client() -> StdioMcpClient:
    """The shared session, restarted if the command or ComfyUI address changed."""
    global _client, _client_sig
    cmd = command()
    if not cmd:
        raise McpStdioError(NOT_INSTALLED)
    extra = target_env()
    sig = (tuple(cmd), tuple(sorted(extra.items())))
    with _lock:
        if _client is None or sig != _client_sig:
            if _client is not None:
                _client.close()
            # COMFY* passes through (COMFY_BIN, COMFY_MCP_ASSUME_CONSENT, …) except
            # the address variables, which come only from target_env — Plutus's
            # own COMFYUI_URL means something different to comfy-mcp.
            env = base_env(extra, keep=("COMFY",),
                           drop=("COMFYUI_URL", "COMFYUI_HOST", "COMFYUI_PORT", "COMFY_LOCAL_URL"))
            _client = StdioMcpClient(cmd, env=env, cwd=extra["COMFY_PROJECT"],
                                     stderr_path=_dir() / "stderr.log")
            _client_sig = sig
        return _client


NOT_INSTALLED = (
    "comfy-mcp is not set up: install it (`pip install comfy-mcp comfy-cli` in its own "
    "environment — it needs mcp 2.x) and set COMFY_MCP_COMMAND to its `comfy-mcp` "
    "executable on the ComfyUI MCP card, or put it on PATH. The Plutus Docker image "
    "ships it already."
)


# ── the tool list, cached so a restart does not have to spawn comfy-mcp ──────

def _cache_path() -> Path:
    return _dir() / "tools.json"


def load_tools(*, refresh: bool = False, timeout: float = 60.0) -> dict:
    """{"tools", "server", "instructions", "fetched_at", "error"} for the configured
    comfy-mcp. Reads the day-old cache unless ``refresh``; never raises."""
    cmd = command()
    if not cmd:
        return {"tools": [], "error": NOT_INSTALLED}
    p = _cache_path()
    if not refresh:
        try:
            cached = json.loads(p.read_text(encoding="utf-8"))
            if cached.get("command") == cmd and time.time() - cached.get("fetched_at", 0) < CACHE_TTL:
                return cached
        except (OSError, ValueError):
            pass
    c = client()
    try:
        tools = c.list_tools(timeout=timeout)
        info = {"command": cmd, "fetched_at": int(time.time()), "tools": tools,
                "server": c.server_info, "instructions": c.instructions, "error": ""}
    except Exception as e:
        return {"tools": [], "error": f"comfy-mcp did not list its tools: {e}"}
    finally:
        # Listing happens at startup in both Plutus processes; only a real call
        # should keep a comfy-mcp child alive. The next call restarts it (~2 s).
        c.close()
    try:
        p.write_text(json.dumps(info), encoding="utf-8")
    except OSError:
        pass
    return info


def summary(desc: str, limit: int = 200) -> str:
    first = (desc or "").strip().split("\n\n")[0].replace("\n", " ")
    first = " ".join(first.split())
    return first if len(first) <= limit else first[: limit - 1].rstrip() + "…"


def wrapped_schema(schema: dict) -> dict:
    """comfy-mcp's input schema under Plutus's ``params`` convention.

    ``$defs`` move to the top so ``#/$defs/…`` references still resolve.
    """
    inner = {k: v for k, v in (schema or {"type": "object"}).items() if k != "$defs"}
    out = {"type": "object", "properties": {"params": inner}}
    if (schema or {}).get("$defs"):
        out["$defs"] = schema["$defs"]
    if inner.get("required"):
        out["required"] = ["params"]
    return out


def annotations_for(name: str) -> dict:
    read = name in READ_ONLY
    return {"readOnlyHint": read,
            "destructiveHint": (not read) and (name in DESTRUCTIVE or name not in _KNOWN_WRITES),
            "idempotentHint": read,
            "openWorldHint": name in _INTERNET}


# Writes that change files or start jobs but destroy nothing.
_KNOWN_WRITES = frozenset({
    "auth_login", "run_workflow", "generate_image", "emit_partner_workflow", "run_template",
    "job", "free_memory", "fetch_outputs", "launch_comfyui", "project", "fetch_template",
    "download_model", "download", "upload_file", "set_workflow_slot", "vary_workflow",
})


class ComfyParams(BaseModel):
    """Passed through untouched — comfy-mcp validates against its own schema."""
    model_config = ConfigDict(extra="allow")


async def _call(downstream: str, args: dict) -> str:
    def run() -> str:
        try:
            return result_text(client().call_tool(downstream, args, timeout=1800))
        except McpStdioError as e:
            return f"Error: comfy-mcp {downstream}: {e}"
    return await anyio.to_thread.run_sync(run)


def register_comfy_mcp_tools(mcp: FastMCP, *, allow: "set[str] | None" = None):
    from core.profiles import tool_filter
    raw = mcp
    mcp = tool_filter(mcp, allow)

    class StatusInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        tool: str = Field(default="", description="A comfy_* tool (or its comfy-mcp name) to show the full guide for", max_length=80)
        refresh: bool = Field(default=False, description="Ask comfy-mcp for its tool list again")

    @mcp.tool(name="comfy_mcp_status", annotations={"readOnlyHint": True})
    async def comfy_mcp_status(params: StatusInput) -> str:
        """ComfyUI via comfy-mcp: whether it runs, which ComfyUI it targets, its
        working rules, and the full guide for any comfy_* tool (pass `tool`). Read
        this before a first comfy_* call."""
        info = await anyio.to_thread.run_sync(lambda: load_tools(refresh=params.refresh))
        if info.get("error"):
            return info["error"]
        tools = {t["name"]: t for t in info["tools"]}
        if params.tool:
            name = params.tool.removeprefix(PREFIX)
            t = tools.get(name)
            if not t:
                return f"comfy-mcp has no tool '{name}'. It has: {', '.join(sorted(tools))}"
            return (f"## {PREFIX}{name}\n\n{t.get('description', '').strip()}\n\n"
                    f"### Parameters (inside `params`)\n```json\n"
                    f"{json.dumps(t.get('inputSchema') or {}, indent=1)}\n```")
        env = target_env()
        where = env.get("COMFYUI_URL") or env.get("COMFY_LOCAL_URL") or "http://127.0.0.1:8188 (default)"
        srv = info.get("server") or {}
        lines = [f"## comfy-mcp {srv.get('version', '')}".rstrip(),
                 f"**Command:** `{' '.join(command())}`",
                 f"**ComfyUI target:** {where}"
                 + (" (remote: runs, jobs, uploads and outputs go there; install/lifecycle stay local)"
                    if "COMFYUI_URL" in env else ""),
                 f"**Project dir (relative paths land here):** `{env['COMFY_PROJECT']}`",
                 f"**Tools:** {len(tools)} · list cached {time.strftime('%Y-%m-%d %H:%M', time.localtime(info.get('fetched_at', 0)))}"]
        if params.refresh:
            lines.append("_Newly added tools are registered on the next Plutus restart._")
        if info.get("instructions"):
            lines += ["", "### comfy-mcp's working rules", info["instructions"].strip()]
        lines += ["", "### Tools (read = no changes)"]
        lines += [f"- `{PREFIX}{n}` {'(read)' if n in READ_ONLY else ''} — {summary(t.get('description', ''), 110)}"
                  for n, t in sorted(tools.items())]
        return "\n".join(lines)

    info = load_tools()
    for t in info.get("tools") or []:
        name = t.get("name") or ""
        if not name or not name.replace("_", "").isalnum():
            continue
        full = PREFIX + name
        if allow is not None and full not in allow:
            continue

        def make(downstream: str):
            async def proxy(params: ComfyParams = ComfyParams()) -> str:
                return await _call(downstream, params.model_dump())
            return proxy

        desc = summary(t.get("description", "")) + f" Full guide: comfy_mcp_status(tool='{name}')."
        mcp.tool(name=full, description=desc, annotations=annotations_for(name))(make(name))
        registered = raw._tool_manager.get_tool(full) if hasattr(raw, "_tool_manager") else None
        if registered is not None:
            registered.parameters = wrapped_schema(t.get("inputSchema") or {})
