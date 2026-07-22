"""Headless agent runner — drives Claude Code (`claude -p`) as a subprocess and,
crucially, hands it Plutus's *own* MCP endpoint so the agent can operate every
homelab tool Plutus exposes (media, Docker, Home Assistant, files, …).

Pattern adapted from the "Model Radar" control panel: spawn `claude` with
`--output-format stream-json`, parse the event stream into a live line buffer for
an SSE console, track cost/turns, and persist one JSON record per run.

Requirements at runtime (see docs/AGENTS.md):
- Node.js + Claude Code (`@anthropic-ai/claude-code`) installed in the container.
- Claude Code logged in once (mount `~/.claude`) OR `ANTHROPIC_API_KEY` set.

The pure helpers (`build_agent_cmd`, `handle_event`, run storage) are unit-tested
offline; the subprocess call itself is exercised in the live container.
"""
from __future__ import annotations

import datetime
import glob
import json
import os
import subprocess
import threading
import uuid
from pathlib import Path

# ── shared live state (single run at a time) ─────────────────────────────────
_LOCK = threading.Lock()
_current: dict = {"running": False, "id": None, "started": None, "label": None}
LIVE: dict = {"id": None, "lines": [], "done": True}

_MAX_LIVE_LINES = 800


def _now() -> datetime.datetime:
    return datetime.datetime.now().astimezone()


def _runs_dir(root: Path) -> Path:
    d = root / "data" / "agent_runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _emit(line: str) -> None:
    LIVE["lines"].append(line)
    if len(LIVE["lines"]) > _MAX_LIVE_LINES:
        LIVE["lines"] = LIVE["lines"][-_MAX_LIVE_LINES:]


# ── config ───────────────────────────────────────────────────────────────────
DEFAULT_AGENT_CONFIG = {
    "model": "",                      # "" = Claude Code default
    "allowed_tools": ["mcp__plutus", "Read", "Write", "WebSearch", "WebFetch"],
    "skip_permissions": True,         # headless: --dangerously-skip-permissions
    "give_plutus_tools": True,        # expose Plutus's own MCP tools to the agent
    "timeout_min": 20,
    "max_cost_usd": 2.0,
    "library": "research",            # {{LIBRARY}} folder playbooks read/write (Obsidian folder or path)
}


def load_agent_config(root: Path) -> dict:
    p = root / "data" / "agent_config.json"
    cfg = dict(DEFAULT_AGENT_CONFIG)
    if p.exists():
        try:
            cfg.update(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            pass
    return cfg


def save_agent_config(root: Path, updates: dict) -> dict:
    cfg = load_agent_config(root)
    for k in DEFAULT_AGENT_CONFIG:
        if k in updates and updates[k] is not None:
            cfg[k] = updates[k]
    p = root / "data" / "agent_config.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)
    return cfg


def write_plutus_mcp_config(root: Path, *, mcp_url: str, token: str = "") -> str:
    """Write an mcp.json that points Claude Code at Plutus's own MCP endpoint."""
    server: dict = {"type": "http", "url": mcp_url}
    if token:
        server["headers"] = {"Authorization": f"Bearer {token}"}
    conf = {"mcpServers": {"plutus": server}}
    path = _runs_dir(root).parent / "agent_mcp.json"
    path.write_text(json.dumps(conf), encoding="utf-8")
    return str(path)


# ── command + event parsing (pure, testable) ─────────────────────────────────
def build_agent_cmd(prompt: str, cfg: dict, *, mcp_config_path: str | None = None) -> list[str]:
    cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose"]
    if cfg.get("skip_permissions", True):
        cmd.append("--dangerously-skip-permissions")
    tools = list(cfg.get("allowed_tools") or [])
    if tools:
        cmd += ["--allowedTools", ",".join(tools)]
    if mcp_config_path:
        cmd += ["--mcp-config", mcp_config_path]
    if cfg.get("model"):
        cmd += ["--model", cfg["model"]]
    cmd.append(prompt)
    return cmd


def handle_event(ev: dict, label: str = "") -> dict:
    """Turn a stream-json event into a console line + optional result fields.

    Returns {"line": str|None, "result": {cost_usd, turns, text, ok}|None}.
    """
    pre = f"[{label}] " if label else ""
    t = ev.get("type")
    if t == "system" and ev.get("subtype") == "init":
        return {"line": pre + "session started", "result": None}
    if t == "assistant":
        lines = []
        for b in ev.get("message", {}).get("content", []):
            if b.get("type") == "text" and b.get("text", "").strip():
                lines.append(pre + "- " + b["text"].strip().replace("\n", " ")[:160])
            elif b.get("type") == "tool_use":
                inp = b.get("input", {}) or {}
                hint = inp.get("query") or inp.get("url") or inp.get("path") or inp.get("pattern") or ""
                lines.append(pre + f"-> {b.get('name', 'tool')}: {str(hint)[:110]}")
        return {"line": "\n".join(lines) if lines else None, "result": None}
    if t == "result":
        return {
            "line": pre + f"finished - ${ev.get('total_cost_usd', '?')} - {ev.get('num_turns', '?')} turns",
            "result": {
                "cost_usd": ev.get("total_cost_usd"),
                "turns": ev.get("num_turns"),
                "text": (ev.get("result") or "")[-3000:],
                "ok": ev.get("subtype", "success") == "success" and not ev.get("is_error"),
            },
        }
    return {"line": None, "result": None}


# ── run storage ──────────────────────────────────────────────────────────────
def save_run(root: Path, rec: dict) -> None:
    (_runs_dir(root) / (rec["id"] + ".json")).write_text(
        json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def list_runs(root: Path, limit: int = 30) -> list[dict]:
    files = sorted(glob.glob(str(_runs_dir(root) / "*.json")), reverse=True)
    out = []
    for fp in files[:limit]:
        try:
            out.append(json.loads(Path(fp).read_text(encoding="utf-8")))
        except Exception:
            pass
    return out


def total_cost(root: Path) -> float:
    return round(sum((r.get("cost_usd") or 0) for r in list_runs(root, 9999)), 4)


def status(root: Path) -> dict:
    return {
        "running": _current["running"],
        "current_id": _current["id"],
        "current_label": _current["label"],
        "started": _current["started"],
        "total_cost_usd": total_cost(root),
        "last_run": (list_runs(root, 1) or [None])[0],
    }


# ── the run ──────────────────────────────────────────────────────────────────
def run_agent(
    root: Path,
    prompt: str,
    *,
    label: str = "agent",
    mcp_url: str = "http://127.0.0.1:8765/mcp",
    bearer_token: str = "",
    cwd: str | None = None,
) -> dict:
    """Run one headless Claude Code agent call. Blocking; call in a thread."""
    with _LOCK:
        if _current["running"]:
            return {"ok": False, "error": "An agent run is already in progress."}
        rid = _now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
        _current.update(running=True, id=rid, started=_now().isoformat(), label=label)
        LIVE.update(id=rid, lines=[], done=False)

    cfg = load_agent_config(root)
    rec = {
        "id": rid, "label": label, "prompt": prompt[:2000],
        "started": _now().isoformat(), "finished": None,
        "ok": False, "cost_usd": 0.0, "turns": None, "result": "",
        "over_budget": False, "error": None, "log": [],
    }
    _emit(f"agent '{label}' starting")

    mcp_config_path = None
    if cfg.get("give_plutus_tools", True):
        try:
            mcp_config_path = write_plutus_mcp_config(root, mcp_url=mcp_url, token=bearer_token)
        except Exception as e:
            _emit(f"warn: could not write MCP config ({e})")

    cmd = build_agent_cmd(prompt, cfg, mcp_config_path=mcp_config_path)
    try:
        proc = subprocess.Popen(
            cmd, cwd=cwd or str(root), env=dict(os.environ), text=True,
            bufsize=1, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        timer = threading.Timer(int(cfg.get("timeout_min", 20)) * 60, proc.kill)
        timer.start()
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    _emit(line[:160])
                    continue
                parsed = handle_event(ev, label)
                if parsed["line"]:
                    for sub in parsed["line"].split("\n"):
                        _emit(sub)
                if parsed["result"]:
                    res = parsed["result"]
                    rec["cost_usd"] = res["cost_usd"] or 0.0
                    rec["turns"] = res["turns"]
                    rec["result"] = res["text"]
                    rec["ok"] = res["ok"]
            proc.wait()
        finally:
            timer.cancel()
        if proc.returncode not in (0, None) and not rec["result"]:
            err = (proc.stderr.read() or "")[-400:] if proc.stderr else ""
            rec["error"] = f"claude exited {proc.returncode}. {err}".strip()
            _emit(rec["error"])
    except FileNotFoundError:
        rec["error"] = "`claude` not found. Install Claude Code in the container and log in (see docs/AGENTS.md)."
        _emit(rec["error"])
    except Exception as e:
        rec["error"] = str(e)
        _emit("error: " + str(e))
    finally:
        rec["cost_usd"] = round(rec["cost_usd"] or 0.0, 5)
        if rec["cost_usd"] > float(cfg.get("max_cost_usd", 99)):
            rec["over_budget"] = True
            _emit(f"! over cost guard (${cfg.get('max_cost_usd')})")
        rec["finished"] = _now().isoformat()
        rec["log"] = list(LIVE["lines"])
        save_run(root, rec)
        LIVE["done"] = True
        with _LOCK:
            _current.update(running=False, id=None, started=None, label=None)
    return rec
