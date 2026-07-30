"""A small synchronous MCP client over streamable HTTP.

Plutus is an MCP *server*; this is the piece that lets Plutus's own agents be MCP
*clients* of it. Two callers, one implementation on purpose:

- ``tools/mcp_stdio_bridge.py`` — Codex spawns it as a stdio MCP server and it
  relays to Plutus's HTTP endpoint, because ``codex exec`` has no equivalent of
  Claude's ``--mcp-config`` for an HTTP server we can rely on across versions.
- ``core/agent_runner._execute_api`` — Gemini has no MCP support at all, so its
  function-calling loop reads the tool list and calls tools through here.

The alternative was invoking Plutus's tool callables in-process. That would have
skipped the bearer gate, the profile scoping and the tool-exposure rules, and
given each provider a *different* tool surface from the one Claude sees through
``--mcp-config``. Going through the real endpoint means all three agree.

Synchronous by design: both callers are already on their own thread (the agent
queue worker, or a subprocess), and the MCP SDK's async client would drag an
event loop into places that do not have one.

Streamable HTTP, per the spec: POST JSON-RPC, accept either a JSON body or an SSE
stream back, and echo the session id the server hands out at initialize.
"""
from __future__ import annotations

import json
from typing import Any, Iterator

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "plutus-agent", "version": "1.0"}

# Tool results can be large (a directory listing, a container log). Clip before
# they reach a model's context rather than after.
MAX_RESULT_CHARS = 8000


class McpError(RuntimeError):
    """The server answered, but with an error — distinct from transport failure."""


def _sse_messages(text: str) -> Iterator[dict]:
    """JSON-RPC messages out of an SSE body.

    Only ``data:`` lines carry payload; ``event:``/``id:``/comments are framing.
    A single logical message may span several ``data:`` lines, which are joined
    with newlines before parsing.
    """
    buf: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        if line.startswith("data:"):
            buf.append(line[5:].lstrip())
            continue
        if line == "":                      # blank line terminates one event
            if buf:
                try:
                    yield json.loads("\n".join(buf))
                except json.JSONDecodeError:
                    pass
                buf = []
    if buf:
        try:
            yield json.loads("\n".join(buf))
        except json.JSONDecodeError:
            pass


class McpHttpClient:
    """One connection to an MCP server over streamable HTTP."""

    def __init__(self, url: str, token: str = "", *, timeout: float = 120.0):
        import httpx

        self.url = url
        self.timeout = timeout
        headers = {"Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._http = httpx.Client(timeout=timeout, headers=headers)
        self._id = 0
        self._session_id = ""
        self._initialized = False

    # ── transport ────────────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {}
        if self._session_id:
            h["Mcp-Session-Id"] = self._session_id
        if self._initialized:
            # Required from the second request onward by servers implementing
            # 2025-06-18 and later; harmless to older ones.
            h["MCP-Protocol-Version"] = PROTOCOL_VERSION
        return h

    def send(self, message: dict) -> list[dict]:
        """POST one JSON-RPC message; return whatever came back (may be empty)."""
        r = self._http.post(self.url, json=message, headers=self._headers())
        sid = r.headers.get("mcp-session-id") or r.headers.get("Mcp-Session-Id")
        if sid:
            self._session_id = sid
        if r.status_code == 202:            # accepted notification, no body
            return []
        if r.status_code >= 400:
            raise McpError(f"MCP server returned HTTP {r.status_code}: {r.text[:300]}")
        ctype = (r.headers.get("content-type") or "").lower()
        if "text/event-stream" in ctype:
            return list(_sse_messages(r.text))
        if not r.content:
            return []
        body = r.json()
        return body if isinstance(body, list) else [body]

    def proxy(self, message: dict) -> list[dict]:
        """Relay another client's raw JSON-RPC message, tracking handshake state.

        Used by the stdio bridge, which must not interpret the traffic it carries
        — Codex owns that conversation. The one thing it has to notice is the
        initialize, after which the protocol header becomes required.
        """
        out = self.send(message)
        if message.get("method") == "initialize":
            self._initialized = True
        return out

    def _request(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        rid = self._id
        msgs = self.send({"jsonrpc": "2.0", "id": rid, "method": method,
                          "params": params or {}})
        for m in msgs:
            if m.get("id") != rid:
                continue                    # a notification riding the same stream
            if "error" in m:
                err = m["error"] or {}
                raise McpError(f"{method} failed: {err.get('message') or err}")
            return m.get("result") or {}
        raise McpError(f"{method}: no response from the MCP server")

    def notify(self, method: str, params: dict | None = None) -> None:
        self.send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    # ── protocol ─────────────────────────────────────────────────────────────

    def initialize(self) -> dict:
        result = self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": CLIENT_INFO,
        })
        self._initialized = True
        # The spec requires this acknowledgement; some servers refuse tool calls
        # until they have seen it.
        self.notify("notifications/initialized")
        return result

    def list_tools(self) -> list[dict]:
        """Every tool, following pagination. [{name, description, inputSchema}]."""
        if not self._initialized:
            self.initialize()
        out: list[dict] = []
        cursor: str | None = None
        for _ in range(50):                 # bounded: a broken cursor must not spin
            params = {"cursor": cursor} if cursor else {}
            res = self._request("tools/list", params)
            out.extend(t for t in (res.get("tools") or []) if isinstance(t, dict))
            cursor = res.get("nextCursor")
            if not cursor:
                break
        return out

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        """{"text", "is_error"} — a tool's own error is data, not an exception."""
        if not self._initialized:
            self.initialize()
        try:
            res = self._request("tools/call", {"name": name,
                                               "arguments": arguments or {}})
        except McpError as e:
            return {"text": str(e), "is_error": True}
        return {"text": _result_text(res), "is_error": bool(res.get("isError"))}

    def close(self) -> None:
        try:
            self._http.close()
        except Exception:
            pass

    def __enter__(self) -> "McpHttpClient":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()


def _result_text(res: dict) -> str:
    """Flatten a tools/call result into text a model can read."""
    parts: list[str] = []
    for block in res.get("content") or []:
        if not isinstance(block, dict):
            continue
        if isinstance(block.get("text"), str):
            parts.append(block["text"])
        elif block.get("type") == "resource":
            r = block.get("resource") or {}
            parts.append(r.get("text") or r.get("uri") or "")
        else:
            parts.append(json.dumps(block, ensure_ascii=False, default=str))
    text = "\n".join(p for p in parts if p).strip()
    if not text and res.get("structuredContent") is not None:
        text = json.dumps(res["structuredContent"], ensure_ascii=False, default=str)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + f"\n… [{len(text) - MAX_RESULT_CHARS} more chars]"
    return text
