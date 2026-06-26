"""
core/mcp_export.py — Build downloadable MCP connection config files.

Given the live Plutus MCP endpoint (LAN or public HTTPS) and an optional bearer
token, this produces ready-to-use connection snippets for every common MCP
client (Claude Desktop, Claude Code, Cursor, VS Code, Cline, Windsurf,
ChatGPT/OpenAI, LM Studio, Open WebUI, n8n, and a generic fallback).

The UI calls build_connection_exports() and renders a small "Connection
Manager" that lets you download each file in its native format and drop it
into the client — connecting to Plutus without going through the Plutus UI
afterwards.
"""
from __future__ import annotations

import json
from typing import Optional


def _pretty(obj: object) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)


def _remote_args(url: str, *, is_http: bool, token: str) -> list[str]:
    """npx mcp-remote argv used by stdio-only clients (Claude Desktop)."""
    args = ["mcp-remote", url]
    if is_http:
        args.append("--allow-http")
    if token:
        args += ["--header", f"Authorization: Bearer {token}"]
    return args


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"} if token else {}


def build_connection_exports(
    *,
    mcp_url: str,
    sse_url: str,
    is_http: bool,
    token: str = "",
    server_name: str = "plutus",
) -> dict:
    """Return {"server_name", "mcp_url", "sse_url", "has_token", "clients": [...]}.

    Each client entry: id, label, group, download_name, target_path, mime,
    format, content, instructions, docs.
    """
    token = (token or "").strip()
    has_token = bool(token)
    hdr = _headers(token)

    clients: list[dict] = []

    def add(**kw) -> None:
        kw.setdefault("mime", "application/json")
        kw.setdefault("format", "json")
        kw.setdefault("docs", "")
        clients.append(kw)

    # ── Claude Desktop ────────────────────────────────────────────────────────
    add(
        id="claude_desktop",
        label="Claude Desktop",
        group="Claude",
        download_name="claude_desktop_config.json",
        target_path="claude_desktop_config.json (Settings → Developer → Edit Config)",
        content=_pretty({
            "mcpServers": {
                server_name: {
                    "command": "npx",
                    "args": _remote_args(mcp_url, is_http=is_http, token=token),
                }
            }
        }),
        instructions=(
            "Claude Desktop talks to local (stdio) servers, so it bridges to "
            "Plutus via the `mcp-remote` npx helper (needs Node.js). Open "
            "Settings → Developer → Edit Config, merge this into the file, then "
            "fully quit and reopen Claude Desktop."
        ),
        docs="https://modelcontextprotocol.io/quickstart/user",
    )

    # ── Claude Code (CLI) ─────────────────────────────────────────────────────
    cc_server: dict = {"type": "http", "url": mcp_url}
    if hdr:
        cc_server["headers"] = hdr
    cli = f'claude mcp add --transport http {server_name} {mcp_url}'
    if token:
        cli += f' --header "Authorization: Bearer {token}"'
    add(
        id="claude_code",
        label="Claude Code",
        group="Claude",
        download_name=".mcp.json",
        target_path=".mcp.json in your project root (or run the CLI command below)",
        content=_pretty({"mcpServers": {server_name: cc_server}}),
        instructions=(
            "Claude Code speaks remote HTTP natively. Either drop this as "
            "`.mcp.json` in your project root (project-scoped, prompts on first "
            "use), or run the one-liner:\n\n  " + cli
        ),
        docs="https://docs.claude.com/en/docs/claude-code/mcp",
    )

    # ── Cursor ────────────────────────────────────────────────────────────────
    cur_server: dict = {"url": mcp_url}
    if hdr:
        cur_server["headers"] = hdr
    add(
        id="cursor",
        label="Cursor",
        group="Editors",
        download_name="cursor-mcp.json",
        target_path="~/.cursor/mcp.json (global) or .cursor/mcp.json (per project)",
        content=_pretty({"mcpServers": {server_name: cur_server}}),
        instructions=(
            "Save as mcp.json under ~/.cursor/ (all projects) or .cursor/ in a "
            "project. Cursor connects to the remote URL directly — no npx needed. "
            "Reload Cursor and enable the server in Settings → MCP."
        ),
        docs="https://docs.cursor.com/context/model-context-protocol",
    )

    # ── VS Code (GitHub Copilot agent) ────────────────────────────────────────
    vsc_server: dict = {"type": "http", "url": mcp_url}
    if hdr:
        vsc_server["headers"] = hdr
    add(
        id="vscode",
        label="VS Code (Copilot)",
        group="Editors",
        download_name="vscode-mcp.json",
        target_path=".vscode/mcp.json (workspace) — note VS Code uses the `servers` key",
        content=_pretty({"servers": {server_name: vsc_server}}),
        instructions=(
            "VS Code's Copilot agent mode uses a `servers` key (not "
            "`mcpServers`). Save as .vscode/mcp.json in your workspace, then "
            "click Start on the server in the MCP view. Requires a recent VS "
            "Code with MCP support enabled."
        ),
        docs="https://code.visualstudio.com/docs/copilot/chat/mcp-servers",
    )

    # ── Cline (VS Code extension) ─────────────────────────────────────────────
    cline_server: dict = {"url": mcp_url, "disabled": False, "autoApprove": []}
    if hdr:
        cline_server["headers"] = hdr
    add(
        id="cline",
        label="Cline",
        group="Editors",
        download_name="cline-mcp-settings.json",
        target_path="cline_mcp_settings.json (Cline → MCP Servers → Configure)",
        content=_pretty({"mcpServers": {server_name: cline_server}}),
        instructions=(
            "Open Cline → MCP Servers → Configure MCP Servers and merge this "
            "into cline_mcp_settings.json. Cline connects to the remote URL "
            "directly."
        ),
        docs="https://docs.cline.bot/mcp-servers/connecting-to-a-remote-server",
    )

    # ── Windsurf ──────────────────────────────────────────────────────────────
    wind_server: dict = {"serverUrl": sse_url}
    if hdr:
        wind_server["headers"] = hdr
    add(
        id="windsurf",
        label="Windsurf",
        group="Editors",
        download_name="windsurf-mcp_config.json",
        target_path="~/.codeium/windsurf/mcp_config.json",
        content=_pretty({"mcpServers": {server_name: wind_server}}),
        instructions=(
            "Save as ~/.codeium/windsurf/mcp_config.json. Windsurf uses the "
            "`serverUrl` key and an SSE endpoint. Press the refresh button in "
            "the Cascade MCP panel after saving."
        ),
        docs="https://docs.windsurf.com/windsurf/cascade/mcp",
    )

    # ── ChatGPT / OpenAI ──────────────────────────────────────────────────────
    openai_tool: dict = {
        "type": "mcp",
        "server_label": server_name,
        "server_url": mcp_url,
        "require_approval": "never",
    }
    if hdr:
        openai_tool["headers"] = hdr
    add(
        id="openai",
        label="ChatGPT / OpenAI",
        group="Other",
        download_name="openai-mcp-tool.json",
        target_path="OpenAI Responses API `tools` array — or paste the URL into a ChatGPT connector",
        content=_pretty(openai_tool),
        instructions=(
            "Two ways to connect:\n"
            "• ChatGPT app (Pro/Business/Enterprise): Settings → Connectors → "
            "Create, and paste the MCP URL above (a public HTTPS URL is required "
            "— LAN http won't reach OpenAI).\n"
            "• OpenAI API: drop this object into the `tools` array of a Responses "
            "API call."
        ),
        docs="https://platform.openai.com/docs/guides/tools-remote-mcp",
    )

    # ── LM Studio ─────────────────────────────────────────────────────────────
    lms_server: dict = {"url": mcp_url}
    if hdr:
        lms_server["headers"] = hdr
    add(
        id="lmstudio",
        label="LM Studio",
        group="Other",
        download_name="lmstudio-mcp.json",
        target_path="mcp.json (LM Studio → Program → Edit mcp.json)",
        content=_pretty({"mcpServers": {server_name: lms_server}}),
        instructions=(
            "In LM Studio open the Program tab → Edit mcp.json and merge this in. "
            "Requires LM Studio 0.3.17+ with MCP support."
        ),
        docs="https://lmstudio.ai/docs/app/plugins/mcp",
    )

    # ── Open WebUI (via mcpo proxy) ───────────────────────────────────────────
    mcpo_server: dict = {"url": mcp_url}
    if hdr:
        mcpo_server["headers"] = hdr
    add(
        id="openwebui",
        label="Open WebUI (mcpo)",
        group="Other",
        download_name="mcpo-config.json",
        target_path="config.json for `mcpo --config config.json`, then add the OpenAPI URL as a Tool in Open WebUI",
        content=_pretty({"mcpServers": {server_name: mcpo_server}}),
        instructions=(
            "Open WebUI consumes OpenAPI tool servers, so bridge Plutus with "
            "mcpo:\n\n  uvx mcpo --config mcpo-config.json --port 8000\n\n"
            "Then in Open WebUI → Settings → Tools add http://<host>:8000/" +
            server_name + " as an OpenAPI server."
        ),
        docs="https://docs.openwebui.com/openapi-servers/mcp",
    )

    # ── n8n AI Agent ──────────────────────────────────────────────────────────
    add(
        id="n8n",
        label="n8n AI Agent",
        group="Other",
        download_name="n8n-plutus.txt",
        target_path="MCP Client Tool node → SSE/HTTP endpoint",
        mime="text/plain",
        format="text",
        content=(
            "n8n — MCP Client Tool node\n"
            "===========================\n"
            f"SSE endpoint:  {sse_url}\n"
            f"HTTP endpoint: {mcp_url}\n"
            + (f"\nAuthentication: Header Auth\n"
               f"  Name:  Authorization\n"
               f"  Value: Bearer {token}\n" if token else
               "\nAuthentication: None\n")
        ),
        instructions=(
            "Add an 'MCP Client Tool' node, choose the SSE (or Streamable HTTP) "
            "transport, paste the endpoint, and set Header Auth if a token is "
            "shown above."
        ),
        docs="https://docs.n8n.io/integrations/builtin/cluster-nodes/sub-nodes/n8n-nodes-langchain.toolmcp/",
    )

    # ── Generic MCP client ────────────────────────────────────────────────────
    gen_server: dict = {"type": "http", "url": mcp_url}
    if hdr:
        gen_server["headers"] = hdr
    add(
        id="generic",
        label="Generic MCP client",
        group="Other",
        download_name="mcp.json",
        target_path="any MCP-compatible client",
        content=_pretty({"mcpServers": {server_name: gen_server}}),
        instructions=(
            "Standard shape most MCP clients accept: a Streamable HTTP server "
            "with an optional Authorization header. Adjust the wrapper key if "
            "your client expects `servers` or `serverUrl`."
        ),
        docs="https://modelcontextprotocol.io/clients",
    )

    return {
        "server_name": server_name,
        "mcp_url": mcp_url,
        "sse_url": sse_url,
        "is_http": is_http,
        "has_token": has_token,
        "clients": clients,
    }
