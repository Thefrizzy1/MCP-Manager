"""stdio MCP server that relays to Plutus's own HTTP MCP endpoint.

Codex spawns this as a subprocess and talks MCP to it over stdin/stdout; it
forwards everything to ``http://…/mcp`` and writes the answers back. That is how
a Codex agent reaches the same ~209 homelab tools a Claude agent reaches through
``--mcp-config``.

Why a bridge rather than pointing Codex straight at the URL: stdio servers
(``command``/``args``/``env``) are the one MCP transport every Codex release has
supported. HTTP server support has moved between experimental flags and config
keys across versions, and a config key the installed Codex does not understand
fails the *whole* run, not just the tool wiring. A stdio entry is the shape that
does not break on upgrade.

It also enforces the run's scope. ``codex exec`` has no ``--disallowedTools``, so
the connection picker's decision is applied here: denied tools are removed from
``tools/list`` and refused on ``tools/call``. Filtering at the bridge is stricter
than a CLI flag anyway — the tool is never even named to the model.

Configured entirely by environment (see agent_runner.write_codex_mcp_config):

    PLUTUS_MCP_URL    the endpoint, e.g. http://127.0.0.1:8765/mcp
    PLUTUS_MCP_TOKEN  bearer token, when the gate is on
    PLUTUS_MCP_DENY   comma-separated tool names this run may not use

Nothing but JSON-RPC may ever reach stdout — a stray print corrupts the stream
and the client drops the connection. Diagnostics go to stderr, which Codex logs.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Spawned by Codex with an unknown working directory, so make the package
# importable from this file's location rather than hoping cwd is /app.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.mcp_client import McpHttpClient  # noqa: E402

MCP_PREFIX = "mcp__plutus__"


def log(msg: str) -> None:
    print(f"[plutus-bridge] {msg}", file=sys.stderr, flush=True)


def write(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def denied_tools() -> set[str]:
    raw = os.environ.get("PLUTUS_MCP_DENY", "")
    out = set()
    for part in raw.split(","):
        name = part.strip()
        if name.startswith(MCP_PREFIX):
            name = name[len(MCP_PREFIX):]
        if name:
            out.add(name)
    return out


def error(rid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def main() -> int:
    url = os.environ.get("PLUTUS_MCP_URL", "").strip()
    if not url:
        log("PLUTUS_MCP_URL is not set — nothing to bridge to")
        return 1
    deny = denied_tools()
    client = McpHttpClient(url, os.environ.get("PLUTUS_MCP_TOKEN", "").strip())
    log(f"bridging to {url}" + (f", {len(deny)} tools denied" if deny else ""))

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            log(f"ignoring non-JSON input: {line[:120]}")
            continue
        if not isinstance(msg, dict):
            continue

        rid = msg.get("id")
        method = msg.get("method")

        # Refuse a denied tool here rather than forwarding it: the point of the
        # connection picker is that the tool is out of reach for this run.
        if method == "tools/call":
            name = ((msg.get("params") or {}).get("name") or "")
            if name in deny:
                log(f"refused out-of-scope tool: {name}")
                if rid is not None:
                    write({"jsonrpc": "2.0", "id": rid, "result": {
                        "isError": True,
                        "content": [{"type": "text", "text":
                                     f"'{name}' is not available to this agent. Its "
                                     "connection was not selected for this run."}],
                    }})
                continue

        try:
            replies = client.proxy(msg)
        except Exception as exc:                 # transport died, server gone, …
            log(f"{method} failed: {exc}")
            if rid is not None:
                write(error(rid, -32603, f"Plutus MCP unreachable: {exc}"))
            continue

        for reply in replies:
            if deny and method == "tools/list":
                result = reply.get("result")
                if isinstance(result, dict) and isinstance(result.get("tools"), list):
                    result["tools"] = [t for t in result["tools"]
                                       if t.get("name") not in deny]
            write(reply)

    client.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
    except BrokenPipeError:                      # the client went away first
        sys.exit(0)
