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
_current: dict = {"running": False, "id": None, "started": None, "label": None,
                  "proc": None, "cancelled": False}
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
    "timeout_min": 20,                # baked in — not a per-launch choice
    "max_cost_usd": 2.0,
    "max_runs_per_day": 20,           # scheduled/queued runs refused past this
    # Where playbooks read/write their knowledge library:
    "output_mode": "obsidian",        # "obsidian" | "filesystem"
    "obsidian_folder": "research",     # vault-relative folder when output_mode=obsidian
    "fs_library_path": "/data/library",  # host-mounted path when output_mode=filesystem
    # Optional ntfy notification after each run:
    "notify_enabled": False,
    "notify_on": "all",               # "all" | "error"
}


def resolve_library(cfg: dict) -> tuple[str, str]:
    """Return ({{LIBRARY}} folder, {{OUTPUT_HINT}}) for the configured destination."""
    if cfg.get("output_mode") == "filesystem":
        lib = (cfg.get("fs_library_path") or "/data/library").rstrip("/")
        hint = (
            "Persist notes as Markdown files under this path using the filesystem tools "
            "(fs_write_file, fs_read_file, fs_list_directory, fs_search_files). The path "
            "must be inside FILESYSTEM_ALLOWED_PATHS."
        )
    else:
        lib = (cfg.get("obsidian_folder") or "research").strip("/")
        hint = (
            "Persist notes in Obsidian using the obsidian tools (obsidian_write_note, "
            "obsidian_append_to_note, obsidian_get_note, obsidian_search, obsidian_list_directory). "
            "Treat the folder above as a vault-relative path."
        )
    return lib, hint


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
    # Create with 0600 rather than chmod-ing after writing — the old order left the
    # bearer token world-readable for the duration of the write.
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(conf))
    except OSError:
        path.write_text(json.dumps(conf), encoding="utf-8")
    try:
        os.chmod(path, 0o600)  # re-assert for a pre-existing file (no-op on Windows)
    except OSError:
        pass
    return str(path)


def _subprocess_env() -> dict:
    """Process env plus the session OAuth token from .env, so a web login applies
    without a restart. Session token only — never an API key here.

    We also set IS_SANDBOX=1: Claude Code refuses ``--dangerously-skip-permissions``
    when it detects it is running as root, unless the environment is flagged as a
    sandbox. A container IS a sandbox, so this is the intended, safe mechanism — it
    does not elevate anything, it just tells Claude Code the isolation already exists.
    """
    env = dict(os.environ)
    env.setdefault("IS_SANDBOX", "1")
    try:
        from core.env_store import read_env
        tok = (read_env().get("CLAUDE_CODE_OAUTH_TOKEN", "") or "").strip()
        if tok:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = tok
    except Exception:
        pass
    return env


def runs_today(root: Path) -> int:
    today = _now().strftime("%Y%m%d")
    return sum(1 for rid in _index(root) if str(rid).startswith(today))


def cancel() -> dict:
    """Kill the in-flight agent run, if any."""
    with _LOCK:
        proc = _current.get("proc")
        if not (_current.get("running") and proc):
            return {"ok": False, "error": "No run in progress."}
        _current["cancelled"] = True
    try:
        proc.kill()
        _emit("run cancelled by user")
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── command + event parsing (pure, testable) ─────────────────────────────────
def build_agent_cmd(prompt: str, cfg: dict, *, mcp_config_path: str | None = None,
                    disallowed_tools: list[str] | None = None, model: str | None = None) -> list[str]:
    """Argv for one headless `claude -p` run.

    The trailing ``--`` is load-bearing. Several Claude Code options are variadic
    (`--mcp-config <configs...>`, `--allowedTools <tools...>`,
    `--disallowedTools <tools...>`), so a bare positional prompt straight after
    one of them gets swallowed as another value:

        claude -p --mcp-config /app/data/agent_mcp.json "research X"
        -> Error: Invalid MCP configuration:
           MCP config file not found: /app/research X

    The run then had no prompt at all and exited 1, with the prompt text showing
    up inside the error. `--` ends option parsing so the prompt stays positional.
    """
    cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose"]
    if cfg.get("skip_permissions", True):
        cmd.append("--dangerously-skip-permissions")
    tools = list(cfg.get("allowed_tools") or [])
    if tools:
        cmd += ["--allowedTools", ",".join(tools)]
    if disallowed_tools:
        cmd += ["--disallowedTools", ",".join(disallowed_tools)]
    if mcp_config_path:
        cmd += ["--mcp-config", mcp_config_path]
    chosen_model = model or cfg.get("model")
    if chosen_model:
        cmd += ["--model", chosen_model]
    cmd += ["--", prompt]
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
def build_text(root: Path, prompt: str, *, timeout_min: int = 3) -> dict:
    """One quick, non-streaming `claude -p` call that returns plain text.

    Used by the 'build with Claude' playbook generator. Refuses while an agent run
    is active so we never spawn two `claude` processes at once.
    """
    with _LOCK:
        if _current.get("running"):
            return {"ok": False, "text": "", "error": "Agent is busy — try again when the current run finishes."}
    cfg = load_agent_config(root)
    cmd = ["claude", "-p", "--output-format", "text"]
    if cfg.get("skip_permissions", True):
        cmd.append("--dangerously-skip-permissions")
    if cfg.get("model"):
        cmd += ["--model", cfg["model"]]
    cmd += ["--", prompt]     # see build_agent_cmd: variadic options eat a bare prompt
    try:
        proc = subprocess.run(
            cmd, cwd=str(root), env=_subprocess_env(), text=True,
            capture_output=True, timeout=timeout_min * 60,
        )
        if proc.returncode != 0:
            return {"ok": False, "text": "", "error": (proc.stderr or "claude failed")[-400:]}
        return {"ok": True, "text": (proc.stdout or "").strip(), "error": None}
    except FileNotFoundError:
        return {"ok": False, "text": "", "error": "`claude` not found. Install Claude Code and log in (docs/AGENTS.md)."}
    except subprocess.TimeoutExpired:
        return {"ok": False, "text": "", "error": f"Timed out after {timeout_min} min."}
    except Exception as e:
        return {"ok": False, "text": "", "error": str(e)}


def save_run(root: Path, rec: dict) -> None:
    p = _runs_dir(root) / (rec["id"] + ".json")
    tmp = p.with_suffix(".json.tmp")
    # Atomic like every other writer in the codebase. A torn write here produces a
    # file that list_runs() silently skips, so the run just vanishes.
    tmp.write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)
    _index_add(root, rec)


def list_runs(root: Path, limit: int = 30) -> list[dict]:
    files = sorted(glob.glob(str(_runs_dir(root) / "*.json")), reverse=True)
    out = []
    for fp in files[:limit]:
        try:
            out.append(json.loads(Path(fp).read_text(encoding="utf-8")))
        except Exception:
            pass
    return out


def get_run(root: Path, run_id: str) -> dict | None:
    """One run by id — a direct read instead of parsing every run file."""
    if not run_id or "/" in run_id or "\\" in run_id or run_id.startswith("."):
        return None
    p = _runs_dir(root) / f"{run_id}.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


# ── run index ────────────────────────────────────────────────────────────────
# runs_today() and total_cost() used to read and JSON-parse *every* run file, and
# total_cost() sits inside status(), which the dashboard polls. After a few
# hundred runs that is hundreds of file reads per poll. The index keeps just the
# fields those two need; it lives outside the runs dir so list_runs' glob is
# unaffected, and is rebuilt whenever it disagrees with the file count.

def _index_path(root: Path) -> Path:
    return root / "data" / "agent_runs_index.json"


def _index_write(root: Path, idx: dict) -> None:
    p = _index_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(idx), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass


def _index_rebuild(root: Path, files: list[str]) -> dict:
    idx: dict[str, dict] = {}
    for fp in files:
        try:
            r = json.loads(Path(fp).read_text(encoding="utf-8"))
        except Exception:
            continue
        rid = str(r.get("id") or Path(fp).stem)
        idx[rid] = {"cost_usd": float(r.get("cost_usd") or 0.0), "ok": bool(r.get("ok"))}
    _index_write(root, idx)
    return idx


def _index_stored(root: Path) -> dict:
    try:
        idx = json.loads(_index_path(root).read_text(encoding="utf-8"))
        return idx if isinstance(idx, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _index(root: Path) -> dict:
    idx = _index_stored(root)
    files = glob.glob(str(_runs_dir(root) / "*.json"))
    # One cheap glob (no parsing) detects drift from runs deleted out of band.
    if len(idx) != len(files):
        idx = _index_rebuild(root, files)
    return idx


def _index_add(root: Path, rec: dict) -> None:
    # Deliberately skips the drift check: save_run has just added a file, so the
    # counts legitimately differ for a moment and checking here would trigger a
    # full rebuild on every single save.
    idx = _index_stored(root)
    idx[str(rec.get("id"))] = {"cost_usd": float(rec.get("cost_usd") or 0.0), "ok": bool(rec.get("ok"))}
    _index_write(root, idx)


def total_cost(root: Path) -> float:
    return round(sum(float(v.get("cost_usd") or 0) for v in _index(root).values()), 4)


def clear_runs(root: Path) -> int:
    """Delete every persisted agent run record. Returns how many were removed.

    Used by the "Clear history" action — old runs (e.g. failures from before a
    rebuild) persist as JSON files and would otherwise linger in the dashboard
    and keep counting toward the all-time cost."""
    n = 0
    for fp in glob.glob(str(_runs_dir(root) / "*.json")):
        try:
            Path(fp).unlink()
            n += 1
        except OSError:
            pass
    _index_write(root, {})
    return n


def auth_info() -> dict:
    """Which billing mode the agent will use.

    - ANTHROPIC_API_KEY set  -> pay-per-token API billing.
    - otherwise, a Claude Code login (~/.claude) -> your subscription plan's usage.
    """
    api_key = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
    session_token = False
    try:
        from core.env_store import read_env
        session_token = bool((read_env().get("CLAUDE_CODE_OAUTH_TOKEN", "") or "").strip())
    except Exception:
        pass
    claude_home = Path(os.path.expanduser("~/.claude"))
    # Only credential files count. Treating any *.json in ~/.claude as proof of
    # login reported "subscription" whenever a bare settings.json existed, so the
    # UI claimed the agent was authenticated when it would fail on first run.
    logged_in = any(
        (claude_home / name).exists()
        for name in (".credentials.json", "credentials.json")
    )
    if session_token:
        mode = "session_token"    # OAuth token from the dashboard -> your plan
    elif api_key:
        mode = "api_key"          # bills the Anthropic API per token
    elif logged_in:
        mode = "subscription"     # interactive ~/.claude login -> your plan
    else:
        mode = "none"             # not authenticated yet
    return {"mode": mode, "api_key": api_key, "session_token": session_token, "logged_in": logged_in}


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
    disallowed_tools: list[str] | None = None,
    model: str | None = None,
    mcp_services: list[str] | None = None,
) -> dict:
    """Run one headless Claude Code agent call. Blocking; call in a thread."""
    with _LOCK:
        if _current["running"]:
            return {"ok": False, "error": "An agent run is already in progress."}
        rid = _now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
        _current.update(running=True, id=rid, started=_now().isoformat(), label=label,
                        proc=None, cancelled=False)
        LIVE.update(id=rid, lines=[], done=False)

    cfg = load_agent_config(root)
    rec = {
        # Store the prompt at the API's own cap, not a display-sized slice — a
        # truncated prompt cannot be re-run faithfully.
        "id": rid, "label": label, "prompt": prompt[:20000],
        # Which connections the run was scoped to, so "Run again" reproduces it
        # instead of silently widening to every tool.
        "mcp_services": mcp_services,
        "started": _now().isoformat(), "finished": None,
        "ok": False, "cost_usd": 0.0, "turns": None, "result": "",
        "over_budget": False, "cancelled": False, "error": None, "log": [],
    }
    _emit(f"agent '{label}' starting")

    mcp_config_path = None
    if cfg.get("give_plutus_tools", True):
        try:
            mcp_config_path = write_plutus_mcp_config(root, mcp_url=mcp_url, token=bearer_token)
        except Exception as e:
            _emit(f"warn: could not write MCP config ({e})")

    cmd = build_agent_cmd(prompt, cfg, mcp_config_path=mcp_config_path,
                          disallowed_tools=disallowed_tools, model=model)
    try:
        # stderr is merged into stdout on purpose. Keeping it on its own pipe and
        # only draining it after proc.wait() deadlocks the moment `claude` writes
        # more than the ~64 KB pipe buffer: the child blocks on the stderr write,
        # stops producing stdout, and both sides wait until the timeout fires.
        proc = subprocess.Popen(
            cmd, cwd=cwd or str(root), env=_subprocess_env(), text=True,
            bufsize=1, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        with _LOCK:
            _current["proc"] = proc

        try:
            timeout_min = int(cfg.get("timeout_min", 20) or 20)
        except (TypeError, ValueError):
            timeout_min = 20
        # A bare proc.kill() as the timer target is indistinguishable from a crash
        # downstream ("claude exited -9"), so record *why* we killed it.
        timed_out = threading.Event()

        def _on_timeout() -> None:
            timed_out.set()
            try:
                proc.kill()
            except Exception:
                pass

        timer = threading.Timer(timeout_min * 60, _on_timeout)
        timer.start()
        max_cost = float(cfg.get("max_cost_usd", 99) or 99)
        noise: list[str] = []          # non-JSON output, kept for error classification
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    noise.append(line)
                    del noise[:-40]
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
                    # Enforce the budget *while* the run is live. Checking it after
                    # the loop (as this used to) only labelled the overspend after
                    # the money was gone — a guard that never guarded.
                    if rec["cost_usd"] > max_cost:
                        rec["over_budget"] = True
                        rec["ok"] = False
                        _emit(f"! cost guard hit (${rec['cost_usd']} > ${max_cost}) — stopping run")
                        try:
                            proc.kill()
                        except Exception:
                            pass
                        break
            proc.wait()
        finally:
            timer.cancel()
        # Keep the HEAD of the output, not the tail. CLI errors lead with the
        # useful line ("Error: Invalid MCP configuration:") and trail off into
        # echoed arguments, so tailing showed a slice of the prompt and hid the
        # cause entirely.
        _noise = "\n".join(noise).strip()
        err = _noise[:400] + (" …" if len(_noise) > 400 else "")
        if _current.get("cancelled"):
            rec["cancelled"] = True
            rec["error"] = "Cancelled by user."
        elif rec["over_budget"]:
            rec["error"] = (f"Stopped: cost reached ${rec['cost_usd']}, over the "
                            f"${max_cost} cap (Settings → Agent → max cost).")
            _emit(rec["error"])
        elif timed_out.is_set():
            rec["error"] = f"Timed out after {timeout_min} min (Settings → Agent → timeout)."
            _emit(rec["error"])
        elif proc.returncode not in (0, None) and not rec["result"]:
            low = err.lower()
            if "root" in low and "permission" in low:
                rec["error"] = ("Claude Code refused to run as root. Plutus now sets IS_SANDBOX=1 "
                                "for the container — rebuild the image so this fix is present, or run "
                                "the container as a non-root user.")
            elif "invalid bearer" in low or "401" in low:
                # Plutus injects CLAUDE_CODE_OAUTH_TOKEN from .env into the agent's
                # environment, where it overrides a mounted ~/.claude login. A stale
                # token therefore breaks runs that would otherwise work.
                rec["error"] = (
                    "Claude Code rejected the credentials (401). The saved session token has "
                    "expired: Settings → Connect Claude account → paste a fresh token, or clear "
                    "CLAUDE_CODE_OAUTH_TOKEN so the mounted ~/.claude login is used instead."
                )
            elif ("not logged in" in low or "authenticat" in low or "unauthorized" in low
                  or "credit balance" in low or ("out of" in low and "usage" in low)):
                rec["error"] = ("Claude Code isn't authenticated (or the plan is out of usage). "
                                "Settings → Connect Claude account.")
            else:
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
        # Backstop for a final cost that arrived without us seeing a result event
        # (e.g. the process died right after billing). The live check above is what
        # actually stops a run.
        if not rec["over_budget"] and rec["cost_usd"] > float(cfg.get("max_cost_usd", 99) or 99):
            rec["over_budget"] = True
            _emit(f"! over cost guard (${cfg.get('max_cost_usd')})")
        rec["finished"] = _now().isoformat()
        rec["log"] = list(LIVE["lines"])
        save_run(root, rec)
        LIVE["done"] = True
        with _LOCK:
            _current.update(running=False, id=None, started=None, label=None, proc=None, cancelled=False)
    return rec
