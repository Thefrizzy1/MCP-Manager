"""A synchronous MCP client for a server that speaks stdio — the other half of
``core/mcp_client.py``, which only speaks streamable HTTP.

Why Plutus needs one: some MCP servers are only published as a command to run
(Comfy-Org's ``comfy-mcp`` is one), and some cannot share Plutus's Python at all
(``comfy-mcp`` requires mcp 2.x; Plutus is on 1.x). Running such a server as a
child process and talking JSON-RPC over its pipes sidesteps both: it lives in its
own environment, and Plutus re-serves its tools behind its own auth, profiles and
write/publish switches.

One long-lived child per client. Requests may overlap — each carries an id, and a
reader thread routes every answer to the caller waiting on that id — so a
five-minute job poll does not hold up a quick status call.

The server may ask *us* things. ``ping`` is answered. Anything asking for a human
decision (``elicitation/create``) is **declined**: nobody is sitting at this pipe,
and a server that fails closed on consent (``comfy-mcp`` does) then refuses the
gated action instead of taking it. That is the safe answer, and the only honest
one.

stderr goes to a log file, never back into the protocol stream.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "plutus", "version": "1.0"}


class McpStdioError(RuntimeError):
    """The server could not be started, died, timed out, or answered an error."""


class StdioMcpClient:
    def __init__(self, cmd: list[str], *, env: dict[str, str] | None = None,
                 cwd: str | None = None, stderr_path: Path | None = None,
                 start_timeout: float = 60.0):
        self.cmd = list(cmd)
        self.env = env
        self.cwd = cwd
        self.stderr_path = stderr_path
        self.start_timeout = start_timeout
        self.server_info: dict = {}
        self.instructions = ""
        self._proc: subprocess.Popen | None = None
        self._write_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._start_lock = threading.Lock()
        self._ready = False
        self._reading = False
        self._pending: dict[int, queue.Queue] = {}
        self._next_id = 0
        self._stderr = None

    # ── lifecycle ────────────────────────────────────────────────────────────
    @property
    def alive(self) -> bool:
        """Running *and* still talking — a closed stdout means it is on its way out."""
        return self._proc is not None and self._proc.poll() is None and self._reading

    def ensure_started(self) -> None:
        """Spawn and initialize once; callers racing in wait for the same handshake."""
        with self._start_lock:
            if self.alive and self._ready:
                return
            if self._proc is not None:
                self.close()
            self._ready = False
            self._spawn()
            try:
                res = self.request("initialize", {"protocolVersion": PROTOCOL_VERSION,
                                                  "capabilities": {}, "clientInfo": CLIENT_INFO},
                                   timeout=self.start_timeout)
                self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            except Exception:
                self.close()
                raise
            self.server_info = res.get("serverInfo") or {}
            self.instructions = res.get("instructions") or ""
            self._ready = True

    def _spawn(self) -> None:
        if self.stderr_path:
            self.stderr_path.parent.mkdir(parents=True, exist_ok=True)
            self._stderr = open(self.stderr_path, "ab")
        try:
            self._proc = subprocess.Popen(
                self.cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=self._stderr or subprocess.DEVNULL, env=self.env, cwd=self.cwd,
                bufsize=0)
        except OSError as e:
            if self._stderr:
                self._stderr.close()
                self._stderr = None
            raise McpStdioError(f"could not start {self.cmd[0]!r}: {e}") from e
        self._pending.clear()
        self._reading = True
        threading.Thread(target=self._read_loop, args=(self._proc,), daemon=True,
                         name="mcp-stdio-reader").start()

    def close(self) -> None:
        self._ready = False
        proc, self._proc = self._proc, None
        if proc and proc.poll() is None:
            try:
                proc.stdin.close()
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        if self._stderr:
            try:
                self._stderr.close()
            except OSError:
                pass
            self._stderr = None
        for q in list(self._pending.values()):
            q.put({"error": {"code": -32000, "message": "the MCP server was stopped"}})
        self._pending.clear()

    # ── wire ─────────────────────────────────────────────────────────────────
    def _send(self, msg: dict) -> None:
        proc = self._proc
        if not proc or proc.poll() is not None:
            raise McpStdioError("the MCP server is not running")
        data = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        with self._write_lock:
            try:
                proc.stdin.write(data)
                proc.stdin.flush()
            except OSError as e:
                raise McpStdioError(f"the MCP server stopped reading: {e}") from e

    def _read_loop(self, proc: subprocess.Popen) -> None:
        for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue          # a stray print from the server; not ours to parse
            if not isinstance(msg, dict):
                continue
            if "method" in msg and "id" in msg:
                self._answer_server(msg)
            elif "id" in msg:
                q = self._pending.pop(msg["id"], None)
                if q:
                    q.put(msg)
            # notifications (logging, progress) are dropped
        # A reader outliving a restart must not fail the *new* process's calls.
        if proc is not self._proc:
            return
        self._reading = False          # before waking callers, so they see it dead
        self._ready = False
        for q in list(self._pending.values()):
            q.put({"error": {"code": -32000, "message": "the MCP server exited"}})
        self._pending.clear()

    def _answer_server(self, msg: dict) -> None:
        method = msg.get("method")
        if method == "ping":
            reply = {"jsonrpc": "2.0", "id": msg["id"], "result": {}}
        elif method == "elicitation/create":
            reply = {"jsonrpc": "2.0", "id": msg["id"], "result": {"action": "decline"}}
        else:
            reply = {"jsonrpc": "2.0", "id": msg["id"],
                     "error": {"code": -32601, "message": f"{method} is not supported by Plutus"}}
        try:
            self._send(reply)
        except McpStdioError:
            pass

    def request(self, method: str, params: dict | None = None, *, timeout: float = 120.0) -> dict:
        with self._state_lock:
            self._next_id += 1
            rid = self._next_id
        q: queue.Queue = queue.Queue(maxsize=1)
        self._pending[rid] = q
        msg: dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        try:
            self._send(msg)
            reply = q.get(timeout=timeout)
        except queue.Empty:
            raise McpStdioError(f"{method} got no answer within {timeout:.0f}s") from None
        finally:
            self._pending.pop(rid, None)
        if "error" in reply:
            err = reply["error"] or {}
            raise McpStdioError(err.get("message") or f"{method} failed")
        return reply.get("result") or {}

    # ── MCP ──────────────────────────────────────────────────────────────────
    def list_tools(self, *, timeout: float = 60.0) -> list[dict]:
        self.ensure_started()
        tools, cursor = [], None
        for _ in range(50):
            res = self.request("tools/list", {"cursor": cursor} if cursor else {}, timeout=timeout)
            tools += res.get("tools") or []
            cursor = res.get("nextCursor")
            if not cursor:
                break
        return tools

    def call_tool(self, name: str, arguments: dict, *, timeout: float = 600.0) -> dict:
        self.ensure_started()
        return self.request("tools/call", {"name": name, "arguments": arguments}, timeout=timeout)


def result_text(result: dict, *, limit: int = 30000) -> str:
    """A tools/call result as the text a model reads. Images are named, not inlined."""
    parts: list[str] = []
    for c in result.get("content") or []:
        kind = c.get("type")
        if kind == "text":
            parts.append(c.get("text", ""))
        elif kind == "image":
            parts.append(f"[image: {c.get('mimeType', '?')}, {len(c.get('data') or '') * 3 // 4:,} bytes]")
        elif kind == "resource":
            r = c.get("resource") or {}
            parts.append(r.get("text") or f"[resource: {r.get('uri', '')}]")
        elif kind == "resource_link":
            parts.append(f"[{c.get('name') or 'resource'}: {c.get('uri', '')}]")
    if not any(p.strip() for p in parts) and result.get("structuredContent") is not None:
        parts.append(json.dumps(result["structuredContent"], ensure_ascii=False, indent=2))
    text = "\n".join(parts).strip() or "(no output)"
    if len(text) > limit:
        text = text[:limit] + f"\n\n[… {len(text) - limit:,} more characters]"
    return ("Error: " + text) if result.get("isError") else text


# What a child process gets from Plutus's environment: what an OS and a Python
# program need to run, reach the network through a proxy and find their config —
# and nothing else. Plutus's own environment is full of service credentials
# (.env is loaded into it), and none of them are a child server's business.
_ENV_KEEP = (
    "PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_", "TERM", "TZ",
    "TMP", "TEMP", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC",
    "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES", "COMMONPROGRAMFILES",
    "NUMBER_OF_PROCESSORS", "PROCESSOR_", "XDG_",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY",
    "SSL_CERT_", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
)


def base_env(extra: dict[str, str] | None = None, *, keep: tuple[str, ...] = (),
             drop: tuple[str, ...] = ()) -> dict[str, str]:
    """A child's environment: the OS essentials, variables starting with ``keep``,
    minus ``drop``, plus ``extra`` (empty values in ``extra`` are left out)."""
    prefixes = _ENV_KEEP + tuple(keep)
    env = {k: v for k, v in os.environ.items()
           if k.upper().startswith(prefixes) and k.upper() not in drop}
    env.update({k: v for k, v in (extra or {}).items() if v})
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env
