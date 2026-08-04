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
import re
import subprocess
import sys
import threading
import time
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
    # "" = the app's own research library (core/library.py). It used to default to
    # the host path /data/library, which exists on nobody's install and was not in
    # FILESYSTEM_ALLOWED_PATHS either — so "write this up" was refused by the
    # product's own default directory.
    "fs_library_path": "",
    # Optional ntfy notification after each run:
    "notify_enabled": False,
    "notify_on": "all",               # "all" | "error"
}


def resolve_library(cfg: dict) -> tuple[str, str]:
    """Return ({{LIBRARY}} folder, {{OUTPUT_HINT}}) for the configured destination."""
    if cfg.get("output_mode") == "filesystem":
        from core.library import ensure_library

        lib = (cfg.get("fs_library_path") or "").rstrip("/") or str(ensure_library())
        hint = (
            "Persist notes as Markdown files under this path using the filesystem tools "
            "(fs_write_file, fs_read_file, fs_list_directory, fs_search_files). Create "
            "subfolders freely — this is the app's own research library, it is always "
            "writable, and everything in it appears in the Files page ready to download."
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


def _claude_uses_http_mcp() -> bool:
    """True when the escape hatch points Claude straight at /mcp, no bridge.

    One reader for the flag, because two things depend on it and they must agree:
    which transport the config names, and whether the disallow list still has to
    be repeated on the command line.
    """
    return str(os.getenv("PLUTUS_CLAUDE_MCP_HTTP", "")).strip().lower() in ("1", "true", "yes")


def write_plutus_mcp_config(root: Path, *, mcp_url: str, token: str = "",
                            disallowed: list[str] | None = None) -> str:
    """Write an mcp.json that points Claude Code at Plutus's own MCP tools.

    Routed through the stdio bridge (as Codex already is) rather than straight at
    the URL, because the bridge is what makes the connection picker *cheap* as
    well as enforced. Pointed at the endpoint directly, Claude received all ~260
    tool schemas — about 39k tokens — on every single request, and the picker's
    decision arrived separately as ``--disallowedTools``: the out-of-scope tools
    were still described to the model in full, so restricting a run cost more
    tokens than not restricting it. The bridge removes them from ``tools/list``,
    so a run scoped to three connections is charged for three connections.

    Set ``PLUTUS_CLAUDE_MCP_HTTP=1`` to go back to the direct HTTP entry. It is
    the same tool surface either way — only the transport and the token bill
    differ — but a transport change deserves a way back that is not a redeploy.
    """
    if _claude_uses_http_mcp():
        server: dict = {"type": "http", "url": mcp_url}
        if token:
            server["headers"] = {"Authorization": f"Bearer {token}"}
    else:
        env = {"PLUTUS_MCP_URL": mcp_url}
        if token:
            env["PLUTUS_MCP_TOKEN"] = token
        if disallowed:
            env["PLUTUS_MCP_DENY"] = ",".join(sorted(disallowed))
        # "type" stated rather than inferred from the presence of "command":
        # the HTTP branch names its transport, so this one should too, and a
        # config that says what it is survives a stricter schema.
        server = {"type": "stdio", "command": sys.executable or "python3",
                  "args": [str(MCP_BRIDGE)], "env": env}
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


MCP_BRIDGE = Path(__file__).resolve().parent.parent / "tools" / "mcp_stdio_bridge.py"


def _toml_str(value: str) -> str:
    """A TOML basic string. Windows paths are full of backslashes; unescaped they
    become escape sequences and Codex reads a path that does not exist."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


# A TOML table header: the first non-blank character of a line is '['. Matching
# on the *character* instead — which an earlier version did, with `[^\[]*` — stops
# at the '[' inside `args = [...]`, cuts the block mid-assignment, and leaves the
# value orphaned at the start of a line. TOML then reads that value as a table
# header, and the next write adds another one:
#
#     Error loading config.toml:7:2: duplicate key
#     7 | ["/app/tools/mcp_stdio_bridge.py"]
#
# which stopped Codex from launching at all.
_TOML_HEADER = re.compile(r"^\s*\[")
_PLUTUS_HEADER = re.compile(r"^\s*\[mcp_servers\.plutus\]\s*$")


def strip_plutus_block(text: str) -> str:
    """Everything except our own [mcp_servers.plutus] table.

    Line-based and header-aware: from our header, drop lines until the next line
    that opens a table. Anything else in the file is preserved verbatim, comments
    included.
    """
    out: list[str] = []
    skipping = False
    for line in (text or "").splitlines():
        if _PLUTUS_HEADER.match(line):
            skipping = True
            continue
        if skipping:
            if _TOML_HEADER.match(line):
                skipping = False          # a new table starts; keep it
            else:
                continue
        out.append(line)
    return "\n".join(out).strip()


def valid_toml(text: str) -> bool:
    import tomllib

    try:
        tomllib.loads(text)
        return True
    except (tomllib.TOMLDecodeError, ValueError):
        return False


def write_codex_mcp_config(root: Path, account_id: str, *, mcp_url: str,
                           token: str = "", disallowed: list[str] | None = None) -> str:
    """Register Plutus's MCP server in one Codex account's config.toml.

    Codex reads ``$CODEX_HOME/config.toml``, and each account already has its own
    ``CODEX_HOME`` (that is how multi-account works), so this is per account and
    cannot leak one run's scope into another's.

    A **stdio** entry, not a URL: ``command``/``args``/``env`` is the one MCP
    transport shape every Codex release has accepted. HTTP support has moved
    between an experimental flag and different config keys across versions, and a
    key the installed Codex rejects fails the entire run rather than just the tool
    wiring.

    Only our own block is rewritten — the rest of the file is preserved verbatim,
    so anything the operator put there survives.
    """
    from core import ai_providers

    d = ai_providers.account_dir(root, "codex", account_id)
    d.mkdir(parents=True, exist_ok=True)
    path = d / "config.toml"

    env_pairs = [f"PLUTUS_MCP_URL = {_toml_str(mcp_url)}"]
    if token:
        env_pairs.append(f"PLUTUS_MCP_TOKEN = {_toml_str(token)}")
    if disallowed:
        env_pairs.append(f"PLUTUS_MCP_DENY = {_toml_str(','.join(sorted(disallowed)))}")

    block = (
        "[mcp_servers.plutus]\n"
        "# Written by Plutus before each run — edits here are overwritten.\n"
        f"command = {_toml_str(sys.executable or 'python3')}\n"
        f"args = [{_toml_str(str(MCP_BRIDGE))}]\n"
        "env = { " + ", ".join(env_pairs) + " }\n"
    )

    existing = ""
    try:
        existing = path.read_text(encoding="utf-8")
    except OSError:
        pass

    kept = strip_plutus_block(existing)
    # Self-healing: a file the old generator mangled cannot be repaired by
    # stripping our table, because the damage is an orphaned value masquerading as
    # somebody else's table. Preserving it would keep Codex broken forever, so a
    # config that will not parse is discarded rather than carried forward.
    if kept and not valid_toml(kept):
        _emit("note: rewrote a corrupt Codex config.toml (previous content did not parse)")
        kept = ""

    text = (kept + "\n\n" + block) if kept else block
    # Never hand Codex something that will not load: it exits 1 on a bad config,
    # which fails the whole run rather than just the tool wiring. If merging with
    # the existing file produced something unparseable, our own block alone is
    # always valid — keeping the agent runnable matters more than preserving
    # settings we could not parse anyway.
    if not valid_toml(text):
        _emit("note: could not merge into the existing Codex config.toml — "
              "wrote just the Plutus block so the run can proceed")
        text = block
    if not valid_toml(text):
        raise ValueError("generated Codex config.toml is not valid TOML")

    # 0600: the block carries the MCP bearer token.
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        path.write_text(text, encoding="utf-8")
    return str(path)


def cli_credentials_file() -> "Path | None":
    """The mounted CLI login file, if there is one."""
    home = Path(os.path.expanduser("~/.claude"))
    for name in (".credentials.json", "credentials.json"):
        f = home / name
        if f.exists():
            return f
    return None


def cli_logged_in() -> bool:
    """True if the Claude Code CLI has a real login in ~/.claude (mounted). Only
    credential files count — a bare settings.json is not proof of a session."""
    return cli_credentials_file() is not None


def legacy_credential_source() -> tuple[str, str]:
    """Which credential a run *without* a provider account will use, and why.

    A mounted ~/.claude login and a saved session token can **both** be stale, so
    neither may win unconditionally — and each fixed order fails in one direction:

      - always prefer the file  -> a freshly pasted token is silently discarded,
        so the user updates the token, nothing changes, and the run still 401s
        against dead credentials with no hint why. (This was the live bug.)
      - always prefer the token -> a stale token shadows a working CLI login,
        which is the failure the previous fix was written to stop.

    So the most recently *established* credential wins: that is the one the user
    just acted on. Returns ("cli"|"token"|"none", human explanation) — the
    explanation is written into the run log so the choice is visible rather than
    guessed at.
    """
    from core import agent_login

    cred = cli_credentials_file()
    try:
        tok = (agent_login.read_env().get(agent_login.TOKEN_KEY, "") or "").strip()
        saved_at = agent_login.token_saved_at()
    except Exception:
        tok, saved_at = "", 0

    if cred and tok:
        try:
            cred_at = int(cred.stat().st_mtime)
        except OSError:
            cred_at = 0
        if saved_at > cred_at:
            return "token", ("saved session token (newer than the mounted CLI login, "
                             "so it takes precedence)")
        return "cli", (f"mounted CLI login {cred} (newer than the saved token, "
                       "so it takes precedence)")
    if cred:
        return "cli", f"mounted CLI login {cred}"
    if tok:
        return "token", "saved session token"
    return "none", "no Claude credentials found"


def _subprocess_env(root: Path | None = None, provider: str = "",
                    account_id: str = "") -> dict:
    """Environment for the headless `claude` run.

    With a provider account selected, that account's credentials directory is the
    whole story (see core/ai_providers). Without one, the legacy behaviour applies.

    A real CLI session (mounted ~/.claude login) is authoritative: when one
    exists we use it and strip any CLAUDE_CODE_OAUTH_TOKEN / ANTHROPIC_API_KEY
    from the child env, so a stale saved token or key can never override the
    login (that produced "401 Invalid bearer token" on runs that would otherwise
    work). Only when there is no CLI login do we fall back to a saved session
    token.

    We also set IS_SANDBOX=1: Claude Code refuses ``--dangerously-skip-permissions``
    when it detects it is running as root, unless the environment is flagged as a
    sandbox. A container IS a sandbox, so this is the intended, safe mechanism — it
    does not elevate anything, it just tells Claude Code the isolation already exists.
    """
    env = dict(os.environ)
    env.setdefault("IS_SANDBOX", "1")
    # Claude Code auto-updates into ~/.local/bin, which the container's ENV PATH
    # does not include — so give children the widened search path too.
    try:
        from core.ai_providers import cli_search_path
        env["PATH"] = cli_search_path()
    except Exception:
        pass

    # A named provider account is authoritative when one is selected: point the
    # CLI at that account's own config dir and strip every ambient credential, so
    # accounts cannot bleed into each other and a stale global token cannot
    # shadow the account's login.
    if root is not None and provider and account_id:
        from core import ai_providers
        try:
            # Ambient credentials out first, then the account's own in. Reversed,
            # this popped the account's stored API key back out again.
            #
            # *Every* provider's credential is stripped, not just this one's. The
            # run that prompted this fix selected a Codex account and inherited a
            # stale CLAUDE_CODE_OAUTH_TOKEN — harmless to Codex, but it meant the
            # environment carried a credential belonging to a different identity,
            # and any code path that reached for it got the wrong one.
            env.pop("ANTHROPIC_API_KEY", None)
            for spec in ai_providers.PROVIDERS.values():
                if spec.get("token_env"):
                    env.pop(spec["token_env"], None)
            env.update(ai_providers.account_env(root, provider, account_id))
        except ValueError:
            pass
        else:
            return env

    # Legacy single-login path: whichever credential was established most recently.
    source, _why = legacy_credential_source()
    if source == "cli":
        # Let the CLI read its own login; nothing in the env may shadow it.
        env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
        env.pop("ANTHROPIC_API_KEY", None)
    elif source == "token":
        try:
            from core.env_store import read_env
            env["CLAUDE_CODE_OAUTH_TOKEN"] = (read_env().get("CLAUDE_CODE_OAUTH_TOKEN", "") or "").strip()
        except Exception:
            pass
        env.pop("ANTHROPIC_API_KEY", None)
    # source == "none": leave the env alone so a deliberately configured
    # ANTHROPIC_API_KEY (compose-only opt-in) still works.
    return env


def runs_today(root: Path) -> int:
    today = _now().strftime("%Y%m%d")
    return sum(1 for rid in _index(root) if str(rid).startswith(today))


def busy() -> bool:
    """True while a run holds the single execution slot."""
    with _LOCK:
        return bool(_current.get("running"))


def wait_for_slot(timeout: float = 1800.0, poll: float = 1.0) -> bool:
    """Block until no run is in flight. False if the wait timed out.

    Rooms need this. A room's seats call run_agent directly, and run_agent
    *refuses* — instantly, not queueing — while another run is active. So a room
    launched while the agent queue happened to be busy died on its first seat with
    "An agent run is already in progress", which reads like a bug in the room
    rather than a slot that was two seconds from being free.
    """
    deadline = time.monotonic() + max(0.0, timeout)
    while busy():
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll)
    return True


def cancel() -> dict:
    """Kill the in-flight agent run, if any.

    An HTTP-provider run has no process to kill — the request is already in
    flight and blocking a worker thread. Flagging it still matters: the run is
    recorded as cancelled and its answer discarded, and Stop reporting "no run in
    progress" while one was plainly running would be worse than either.
    """
    with _LOCK:
        if not _current.get("running"):
            return {"ok": False, "error": "No run in progress."}
        proc = _current.get("proc")
        _current["cancelled"] = True
    if not proc:
        _emit("run cancelled by user — the provider request is already in flight, "
              "so its answer will be discarded when it returns")
        return {"ok": True, "killed": False}
    try:
        proc.kill()
        _emit("run cancelled by user")
        return {"ok": True, "killed": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── command + event parsing (pure, testable) ─────────────────────────────────
def build_agent_cmd(prompt: str, cfg: dict, *, mcp_config_path: str | None = None,
                    disallowed_tools: list[str] | None = None, model: str | None = None,
                    system_prompt: str = "") -> list[str]:
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
        names = list(disallowed_tools)
        if not _claude_uses_http_mcp():
            # Going through the bridge, which already removed every out-of-scope
            # Plutus tool from tools/list and refuses it on tools/call. Repeating
            # those names here enforces nothing, and there are hundreds of them:
            # on Windows the argv blew the ~32k command-line limit and the run
            # died with "The command line is too long" before the model saw the
            # prompt. Claude's own built-ins are not bridge-covered, so anything
            # that is not a Plutus tool still has to be named.
            #
            # Only safe *because* the bridge is in the path. With the HTTP escape
            # hatch on there is no bridge, and this list is the sole enforcement.
            names = [t for t in names if not t.startswith("mcp__plutus__")]
        if names:
            cmd += ["--disallowedTools", ",".join(names)]
    if mcp_config_path:
        cmd += ["--mcp-config", mcp_config_path]
    chosen_model = model or cfg.get("model")
    if chosen_model:
        cmd += ["--model", chosen_model]
    # The agent's operating manual (library location, fallbacks, how to work).
    if system_prompt:
        cmd += ["--append-system-prompt", system_prompt]
    cmd += ["--", prompt]
    return cmd


def build_provider_cmd(provider: str, prompt: str, *, model: str = "") -> list[str]:
    """Argv for a non-Claude CLI provider, from that provider's own exec builder.

    Claude keeps its own builder above because only Claude is driven in
    ``stream-json`` mode with an MCP config attached. Everything else is a plain
    text invocation, which is why one line is enough here.

    This function existing at all is the fix for the live bug: ``run_agent`` used
    to build a ``claude`` command no matter which account was selected. Choosing a
    Codex account therefore ran Claude Code against a Codex config directory,
    which of course failed — and failed as "401 Invalid bearer token", because
    Claude fell back to whatever ambient token was lying around.
    """
    from core import ai_providers

    spec = ai_providers.PROVIDERS.get(provider)
    if not spec or not callable(spec.get("exec")):
        raise ValueError(f"{provider!r} has no command builder")
    cli = ai_providers.resolve_cli(spec["cli"]) or spec["cli"]
    return [cli, *spec["exec"](prompt, model or "")]


# Codex's `exec` prints a header (version, workdir, model, sandbox…) and a
# trailing token count around the actual answer. Keeping the whole thing as the
# run's result buries the answer in boilerplate, so pull out the last assistant
# block when the markers are there — and fall back to the full output when they
# are not, because the format is the vendor's to change.
_CODEX_TURN = re.compile(r"^\[[^\]]+\]\s*codex\s*$", re.MULTILINE)
_CODEX_TAIL = re.compile(r"^\[[^\]]+\]\s*tokens used:.*$", re.MULTILINE)


def codex_answer(out: str) -> str:
    text = out or ""
    turns = list(_CODEX_TURN.finditer(text))
    if not turns:
        return text.strip()
    tail = text[turns[-1].end():]
    cut = _CODEX_TAIL.search(tail)
    if cut:
        tail = tail[:cut.start()]
    return tail.strip() or text.strip()


_MAX_ENTRY_CHARS = 6000
_MAX_TRANSCRIPT_ENTRIES = 600


def _clip(text, limit: int = _MAX_ENTRY_CHARS) -> str:
    s = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False, default=str)
    return s if len(s) <= limit else s[:limit] + f"\n… [{len(s) - limit} more chars]"


def _tool_result_text(content) -> str:
    """Flatten a tool_result's content, which is either a string or content blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                parts.append(b.get("text") or json.dumps(b, ensure_ascii=False, default=str))
            else:
                parts.append(str(b))
        return "\n".join(parts)
    return "" if content is None else str(content)


def transcript_entries(ev: dict) -> list[dict]:
    """Structured transcript rows for one stream-json event.

    handle_event() produces one-line console summaries — assistant text clipped to
    160 chars, tool arguments to 110 — which is fine live and useless afterwards:
    you cannot tell which tools ran, with what arguments, or what came back. So a
    run could report success while you had no way to find the note it claimed to
    write. These rows keep the detail, and are stored beside the run.
    """
    t = ev.get("type")
    out: list[dict] = []
    if t == "system" and ev.get("subtype") == "init":
        out.append({"kind": "session", "model": ev.get("model") or "",
                    "cwd": ev.get("cwd") or "",
                    "tools": ev.get("tools") or [],
                    "mcp_servers": ev.get("mcp_servers") or []})
    elif t == "assistant":
        for b in (ev.get("message", {}) or {}).get("content", []) or []:
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if bt == "text" and (b.get("text") or "").strip():
                out.append({"kind": "assistant", "text": _clip(b["text"])})
            elif bt == "thinking" and (b.get("thinking") or "").strip():
                out.append({"kind": "thinking", "text": _clip(b["thinking"])})
            elif bt == "tool_use":
                out.append({"kind": "tool_call", "name": b.get("name") or "tool",
                            "id": b.get("id") or "", "input": _clip(b.get("input") or {})})
    elif t == "user":
        for b in (ev.get("message", {}) or {}).get("content", []) or []:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                out.append({"kind": "tool_result", "id": b.get("tool_use_id") or "",
                            "is_error": bool(b.get("is_error")),
                            "text": _clip(_tool_result_text(b.get("content")))})
    elif t == "result":
        out.append({"kind": "final", "cost_usd": ev.get("total_cost_usd"),
                    "turns": ev.get("num_turns"), "text": _clip(ev.get("result") or "")})
    return out


def _transcripts_dir(root: Path) -> Path:
    """Transcripts live in their own directory, NOT beside the run records.

    list_runs() globs `data/agent_runs/*.json`, so a `<id>.transcript.json` sidecar
    in there matches — it showed up as a phantom run and made _index_rebuild raise
    AttributeError on a JSON list, breaking total_cost()/runs_today().
    """
    d = root / "data" / "agent_transcripts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _transcript_path(root: Path, run_id: str) -> Path:
    return _transcripts_dir(root) / f"{run_id}.json"


def save_transcript(root: Path, run_id: str, entries: list[dict]) -> None:
    p = _transcript_path(root, run_id)
    tmp = p.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass


def get_transcript(root: Path, run_id: str) -> list[dict] | None:
    if not run_id or "/" in run_id or "\\" in run_id or run_id.startswith("."):
        return None
    p = _transcript_path(root, run_id)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else None
    except (OSError, json.JSONDecodeError):
        return None


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
    from core.ai_providers import resolve_cli
    cmd = [resolve_cli("claude") or "claude", "-p", "--output-format", "text"]
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
            rec = json.loads(Path(fp).read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(rec, dict):      # ignore anything that isn't a run record
            out.append(rec)
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
        if not isinstance(r, dict):    # not a run record — skip, don't crash
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
    for fp in glob.glob(str(_transcripts_dir(root) / "*.json")):
        try:
            Path(fp).unlink()      # clearing history must not orphan transcripts
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
    logged_in = cli_logged_in()
    # A real CLI login wins (see _subprocess_env), so report it first — otherwise
    # the UI would claim "session token" while the run actually uses the login.
    if logged_in:
        mode = "subscription"     # interactive ~/.claude login -> your plan
    elif session_token:
        mode = "session_token"    # OAuth token from the dashboard -> your plan
    elif api_key:
        mode = "api_key"          # bills the Anthropic API per token
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


# ── execution strategies ─────────────────────────────────────────────────────
#
# One per runtime. Each fills the shared run record in place; run_agent owns the
# lock, the record, the transcript sidecar and the error funnel, so the only thing
# that varies between providers is how the work actually gets done.

def _timeout_min(cfg: dict) -> int:
    try:
        return int(cfg.get("timeout_min", 20) or 20)
    except (TypeError, ValueError):
        return 20


def _spawn(cmd: list[str], *, root: Path, cwd: str | None, env: dict) -> subprocess.Popen:
    # stderr is merged into stdout on purpose. Keeping it on its own pipe and
    # only draining it after proc.wait() deadlocks the moment the CLI writes
    # more than the ~64 KB pipe buffer: the child blocks on the stderr write,
    # stops producing stdout, and both sides wait until the timeout fires.
    proc = subprocess.Popen(
        cmd, cwd=cwd or str(root), env=env, text=True,
        # No inherited stdin: a CLI that decides to read from it (Codex does,
        # even with a positional prompt) would otherwise block forever waiting
        # for input that is never coming.
        stdin=subprocess.DEVNULL,
        bufsize=1, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    with _LOCK:
        _current["proc"] = proc
    return proc


def _arm_timeout(proc: subprocess.Popen, minutes: int) -> tuple[threading.Timer, threading.Event]:
    # A bare proc.kill() as the timer target is indistinguishable from a crash
    # downstream ("claude exited -9"), so record *why* we killed it.
    fired = threading.Event()

    def _on_timeout() -> None:
        fired.set()
        try:
            proc.kill()
        except Exception:
            pass

    timer = threading.Timer(minutes * 60, _on_timeout)
    timer.start()
    return timer, fired


def _account_credential(root: Path, provider: str, account_id: str) -> tuple[bool, str]:
    """(has one, what to do about it) for a selected provider account."""
    from core import ai_providers

    try:
        cred = ai_providers.credentials_file(root, provider, account_id)
        key = ai_providers.stored_token(root, provider, account_id)
        spec = ai_providers.PROVIDERS[provider]
    except (ValueError, KeyError):
        return False, f"unknown provider account {provider!r}/{account_id!r}"
    if cred or key:
        return True, ""
    if spec.get("kind") == ai_providers.KIND_API:
        return False, (f"No API key stored for this {spec['label']} account. "
                       f"Settings → AI providers → paste a key. {spec.get('key_hint', '')}".strip())
    cmd = ai_providers.login_command(root, provider, account_id)
    return False, (f"This {spec['label']} account is not linked. Run:\n  {cmd}\n"
                   "then press Test in Settings → AI providers.")


def _execute_claude(root: Path, rec: dict, prompt: str, cfg: dict, *, label: str,
                    cwd: str | None, mcp_config_path: str | None,
                    disallowed_tools: list[str] | None, model: str | None,
                    provider: str, account_id: str, cred_source: str,
                    transcript: list[dict]) -> None:
    """Claude Code in stream-json mode — the only runtime with MCP tools and cost."""
    skill = ""
    if cfg.get("inject_skill", True):
        try:
            from core.agent_skill import render_skill
            skill = render_skill(cfg)
        except Exception:
            skill = ""
    cmd = build_agent_cmd(prompt, cfg, mcp_config_path=mcp_config_path,
                          disallowed_tools=disallowed_tools, model=model, system_prompt=skill)
    # Spawn by absolute path. A bare "claude" is resolved from the child's PATH,
    # which misses the auto-updated native install and fails with a bare
    # FileNotFoundError even though the CLI is plainly installed.
    from core.ai_providers import CLI_SEARCH_DIRS, resolve_cli
    resolved = resolve_cli("claude")
    if resolved:
        cmd[0] = resolved
    if not resolved:
        looked = ", ".join(["$PATH", *CLI_SEARCH_DIRS])
        raise FileNotFoundError(
            "`claude` was not found. Looked in: " + looked +
            ". If `docker exec -it plutus-mcp claude` works, the CLI has "
            "auto-updated somewhere Plutus cannot see — run "
            "`docker exec plutus-mcp sh -lc 'command -v claude'` and report the path."
        )
    if cred_source == "none":
        # Fail before spending a run that can only come back 401.
        raise RuntimeError(
            "No Claude credentials. Either log the CLI in once — "
            "`docker exec -it plutus-mcp claude` — or paste a session token "
            "in Settings (Connect Claude account)."
        )

    proc = _spawn(cmd, root=root, cwd=cwd,
                  env=_subprocess_env(root, provider, account_id))
    minutes = _timeout_min(cfg)
    timer, timed_out = _arm_timeout(proc, minutes)
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
            if len(transcript) < _MAX_TRANSCRIPT_ENTRIES:
                transcript.extend(transcript_entries(ev))
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
        rec["error"] = f"Timed out after {minutes} min (Settings → Agent → timeout)."
        _emit(rec["error"])
    elif proc.returncode not in (0, None) and not rec["result"]:
        low = err.lower()
        if "root" in low and "permission" in low:
            rec["error"] = ("Claude Code refused to run as root. Plutus now sets IS_SANDBOX=1 "
                            "for the container — rebuild the image so this fix is present, or run "
                            "the container as a non-root user.")
        elif "invalid bearer" in low or "401" in low:
            # A real ~/.claude CLI login is now used automatically when present
            # (it overrides any saved token). A 401 means there is no valid
            # session at all — either no login and a bad/expired saved token.
            if account_id:
                rec["error"] = (
                    f"Claude Code rejected this account's credentials (401). Re-link the "
                    f"'{account_id}' account in Settings → AI providers (Logout, then run "
                    "its link command again)."
                )
            elif cli_logged_in():
                rec["error"] = (
                    "Claude Code rejected the credentials (401). Your mounted ~/.claude login "
                    "looks expired — re-run `docker exec -it plutus-mcp claude` to log in again."
                )
            else:
                rec["error"] = (
                    "Claude Code isn't authenticated (401). For a CLI *session* (your plan, no "
                    "API key), log in once with `docker exec -it plutus-mcp claude` — that's used "
                    "automatically. Or Settings → Connect Claude account to paste a fresh "
                    "`claude setup-token` token."
                )
        elif ("not logged in" in low or "authenticat" in low or "unauthorized" in low
              or "credit balance" in low or ("out of" in low and "usage" in low)):
            rec["error"] = ("Claude Code isn't authenticated (or the plan is out of usage). "
                            "Settings → Connect Claude account.")
        else:
            rec["error"] = f"claude exited {proc.returncode}. {err}".strip()
        _emit(rec["error"])


def _execute_cli(root: Path, rec: dict, prompt: str, provider: str, account_id: str,
                 model: str, cfg: dict, cwd: str | None, transcript: list[dict]) -> None:
    """A non-Claude CLI (Codex): its own non-interactive command, plain text out.

    No stream-json, so there is no per-tool detail and no cost figure — the CLI
    does not report one. Reporting $0 is honest here; inventing a number would
    not be.
    """
    from core import ai_providers

    spec = ai_providers.PROVIDERS[provider]
    ok, why = _account_credential(root, provider, account_id)
    if not ok:
        raise RuntimeError(why)
    if not ai_providers.resolve_cli(spec["cli"]):
        raise FileNotFoundError(
            f"`{spec['cli']}` was not found. Looked in: " +
            ", ".join(["$PATH", *ai_providers.CLI_SEARCH_DIRS]) +
            f". Install it with: {spec['install_hint']}"
        )

    cmd = build_provider_cmd(provider, prompt, model=model)
    proc = _spawn(cmd, root=root, cwd=cwd,
                  env=_subprocess_env(root, provider, account_id))
    minutes = _timeout_min(cfg)
    timer, timed_out = _arm_timeout(proc, minutes)
    out: list[str] = []
    try:
        for line in proc.stdout:
            line = line.rstrip()
            out.append(line)
            if line.strip():
                _emit(line[:200])
        proc.wait()
    finally:
        timer.cancel()

    blob = "\n".join(out).strip()
    answer = codex_answer(blob) if provider == "codex" else blob
    if answer:
        transcript.append({"kind": "assistant", "text": _clip(answer)})
        transcript.append({"kind": "final", "cost_usd": None, "turns": None,
                           "text": _clip(answer)})
    rec["result"] = answer[-3000:]

    if _current.get("cancelled"):
        rec["cancelled"] = True
        rec["error"] = "Cancelled by user."
    elif timed_out.is_set():
        rec["error"] = f"Timed out after {minutes} min (Settings → Agent → timeout)."
        _emit(rec["error"])
    elif proc.returncode not in (0, None):
        rec["error"] = _explain_cli_failure(spec, provider, account_id, proc.returncode, blob)
        _emit(rec["error"])
    elif not answer:
        rec["error"] = f"{spec['label']} exited cleanly but produced no output."
        _emit(rec["error"])
    else:
        rec["ok"] = True


def _explain_cli_failure(spec: dict, provider: str, account_id: str,
                         code: int | None, blob: str) -> str:
    """A CLI's own words, translated into the action that fixes it.

    "401 Invalid bearer token" is the specific string this exists for: it is what
    the user saw for every failed run, and it told them to go looking for an API
    key they had never configured.
    """
    low = blob.lower()
    head = blob.strip()[:400]
    if "invalid bearer" in low or "401" in low or "unauthorized" in low or "not logged in" in low:
        return (f"{spec['label']} rejected this account's login (401). Re-link the "
                f"'{account_id}' account in Settings → AI providers, then press Test.")
    if "usage limit" in low or "rate limit" in low or "quota" in low or "429" in low:
        return f"{spec['label']} is rate limited or out of quota right now."
    if "skip-git-repo-check" in low or "trusted directory" in low:
        return (f"{spec['label']} refused to run outside a git repository. This should "
                "already be handled — rebuild the image so the fix is present.")
    return f"{spec['cli']} exited {code}. {head}".strip()


# How many times a Gemini run may call tools and come back. A loop is the whole
# point of function calling, but a model that keeps re-calling the same failing
# tool would otherwise burn the run's budget without ever answering.
MAX_TOOL_TURNS = 12


def _plutus_declarations(mcp_url: str, token: str, disallowed: list[str] | None,
                         dialect: str = "gemini") -> tuple[list[dict], object]:
    """(function declarations, live MCP client) for a run that gets Plutus tools.

    The MCP list comes from a real ``tools/list`` against Plutus's own endpoint,
    so a Gemini agent is offered exactly what a Claude agent is offered.

    The **library tools are always added**, and do not come from MCP at all. An
    agent that cannot write up what it found is not much use, and whether it can
    must not depend on what a particular endpoint happens to expose — a run
    against a read-only profile ended with the agent asking the user to create the
    file by hand. Losing the MCP endpoint costs the homelab tools; it does not
    cost the ability to produce output.
    """
    from core import agent_tools
    from core.mcp_client import McpHttpClient

    builtin = agent_tools.library_tools_for(disallowed)
    builtin_decls, _ = agent_tools.tool_declarations(
        builtin, None, limit=len(builtin) or 1, dialect=dialect)

    if not mcp_url:
        _emit(f"tools: {len(builtin_decls)} built-in library tools (Plutus MCP not attached)")
        return builtin_decls, None

    client = McpHttpClient(mcp_url, token)
    try:
        tools = client.list_tools()
    except Exception as e:
        client.close()
        _emit(f"warn: could not read Plutus MCP tools ({e}) — continuing with "
              f"{len(builtin_decls)} built-in library tools")
        return builtin_decls, None

    # The cap counts the built-ins, which must never be the ones dropped.
    room = max(1, agent_tools.MAX_DECLARATIONS - len(builtin_decls))
    decls, dropped = agent_tools.tool_declarations(tools, disallowed, limit=room,
                                                   dialect=dialect)
    if dropped:
        _emit(f"note: {dropped} MCP tools left out (cap {agent_tools.MAX_DECLARATIONS}) — "
              "narrow the connections for this run to choose which")
    _emit(f"tools: {len(decls)} from Plutus MCP + {len(builtin_decls)} built-in library")
    return builtin_decls + decls, client


def _spare_account(root: Path, provider: str, used: "str | set[str]") -> str:
    """Another linked account on the same provider, or "".

    Same provider only: a model id chosen for one provider means nothing to
    another, and silently switching provider would change what the run costs and
    what it can do.

    ``used`` is every account this run has already burned, not just the current
    one. With two accounts those are the same thing; with three they are not —
    excluding only the current account made A→B→A→B ping-pong between the first
    two while C, sitting there unlimited, was never tried.
    """
    from core import ai_providers

    spent = {used} if isinstance(used, str) else set(used)
    for account in ai_providers.load_accounts(root).get(provider, []):
        aid = account.get("id") or ""
        if aid and aid not in spent:
            if ai_providers.account_status(root, provider, account)["authenticated"]:
                return aid
    return ""


def _execute_api(root: Path, rec: dict, prompt: str, provider: str, account_id: str,
                 model: str, cfg: dict, transcript: list[dict], *,
                 mcp_url: str = "", bearer_token: str = "",
                 disallowed: list[str] | None = None,
                 smart_fallback: bool = True) -> None:
    """An HTTP provider (Gemini), with Plutus's tools attached as functions.

    Gemini has no MCP support, so the equivalent is its function-calling loop:
    declare Plutus's tools, and every time the model asks for one, actually call
    it through the MCP endpoint and hand the result back. Same tools, same scope,
    same transcript rows as a Claude run — the wire format is the only difference.
    """
    import json as _json

    from core import agent_tools, ai_providers

    spec = ai_providers.PROVIDERS[provider]
    ok, why = _account_credential(root, provider, account_id)
    if not ok:
        raise RuntimeError(why)

    dialect = ai_providers.api_dialect(provider).name
    decls, client = _plutus_declarations(mcp_url, bearer_token, disallowed, dialect)
    if decls and ai_providers.model_capabilities(
            root, provider, account_id, model).get("tools") is False:
        _emit(f"note: {model} does not support tool calling — this run has no tools. "
              "Pick a model tagged 'tools' to give it any.")
    contents: list[dict] = [ai_providers.api_user_message(provider, prompt)]
    deadline = time.monotonic() + _timeout_min(cfg) * 60
    turns = 0
    # Every account this run has hit a limit on. A rate limit lasts minutes, so
    # an account that was exhausted on turn three is still exhausted on turn six.
    spent: set[str] = set()
    try:
        for _ in range(MAX_TOOL_TURNS):
            if _current.get("cancelled"):
                rec["cancelled"] = True
                rec["error"] = "Cancelled by user."
                return
            left = deadline - time.monotonic()
            if left <= 0:
                rec["error"] = (f"Timed out after {_timeout_min(cfg)} min "
                                "(Settings → Agent → timeout).")
                _emit(rec["error"])
                return

            turns += 1
            res = ai_providers.api_turn(root, provider, account_id, contents=contents,
                                        declarations=decls, model=model,
                                        timeout=int(left),
                                        search=not decls)
            rec["model"] = res.get("model") or model
            if not res["ok"] and smart_fallback and ai_providers.is_rate_limited(res["error"]):
                # api_turn already backed off and retried. Still limited means
                # this account is done for now, so move to another one rather
                # than throwing away the work done so far.
                spent.add(account_id)
                spare = _spare_account(root, provider, spent)
                if spare:
                    _emit(f"note: {account_id} is rate limited — switching to {spare}")
                    account_id = spare
                    rec["account_id"] = spare
                    res = ai_providers.api_turn(root, provider, account_id,
                                                contents=contents, declarations=decls,
                                                model=model, timeout=int(left),
                                                search=not decls)
            if not res["ok"]:
                rec["error"] = f"{spec['label']}: {res['error']}"
                _emit(rec["error"])
                return

            if res["text"]:
                transcript.append({"kind": "assistant", "text": _clip(res["text"])})
            if not res["calls"]:
                rec["result"] = res["text"][-3000:]
                rec["turns"] = turns
                rec["ok"] = True
                transcript.append({"kind": "final", "cost_usd": None, "turns": turns,
                                   "text": _clip(res["text"])})
                return

            # The model's own message has to go back verbatim, or the API rejects
            # the tool responses as unsolicited. Opaque here on purpose: its shape
            # is the dialect's business, not the loop's.
            contents.append(res["raw_message"])
            replies = []
            for call in res["calls"]:
                name = str(call.get("name") or "")
                args = call.get("args") if isinstance(call.get("args"), dict) else {}
                _emit(f"-> {name}: {_json.dumps(args, default=str)[:110]}")
                transcript.append({"kind": "tool_call", "name": name, "id": "",
                                   "input": _clip(args)})
                if agent_tools.is_library_tool(name):
                    # In-process: writing to the app's own library never depends
                    # on the MCP endpoint being reachable.
                    out = agent_tools.call_library_tool(name, args, root=root)
                elif client is None:
                    out = {"text": "Plutus MCP tools are not available to this run.",
                           "is_error": True}
                else:
                    try:
                        out = client.call_tool(name, args)
                    except Exception as e:
                        out = {"text": f"tool call failed: {e}", "is_error": True}
                transcript.append({"kind": "tool_result", "id": call.get("id") or "",
                                   "is_error": out["is_error"],
                                   "text": _clip(out["text"])})
                # The id matters for an OpenAI-compatible provider, which rejects
                # a result that does not name the call it answers.
                replies.append({"id": call.get("id") or "", "name": name,
                                "text": out["text"], "is_error": out["is_error"]})
            contents.extend(ai_providers.api_tool_results_message(provider, replies))

        rec["turns"] = turns
        rec["error"] = (f"Stopped after {MAX_TOOL_TURNS} tool rounds without a final "
                        "answer. Narrow the task or the connections.")
        _emit(rec["error"])
    finally:
        if client is not None:
            client.close()


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
    provider: str = "",
    account_id: str = "",
    smart_fallback: bool = True,
) -> dict:
    """Run one headless agent call on the selected provider. Blocking; thread it.

    Three runtimes, chosen by the provider of the selected account:

    - **Claude Code** — ``stream-json`` with Plutus's MCP config attached. Full
      transcript, live tool calls, real cost accounting.
    - **another CLI** (Codex) — the provider's own non-interactive command, plain
      text out. No MCP config: ``--mcp-config`` is Claude's flag, and the other
      CLIs configure servers through their own files.
    - **an HTTP provider** (Gemini) — a single API call with the account's key.

    With no account selected this is the legacy Claude path, unchanged.
    """
    with _LOCK:
        if _current["running"]:
            return {"ok": False, "error": "An agent run is already in progress."}
        rid = _now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
        _current.update(running=True, id=rid, started=_now().isoformat(), label=label,
                        proc=None, cancelled=False)
        LIVE.update(id=rid, lines=[], done=False)

    from core import ai_providers

    cfg = load_agent_config(root)
    prov = (provider or "").strip()
    aid = (account_id or "").strip()
    # An account is what selects a runtime; a provider id with no account is not
    # enough to point at a credential, so it falls back to the legacy path.
    spec = ai_providers.PROVIDERS.get(prov) if (prov and aid) else None
    runtime = ("claude" if spec is None or prov == "claude"
               else ("api" if spec.get("kind") == ai_providers.KIND_API else "cli"))
    chosen_model = (model if model is not None else cfg.get("model")) or ""

    rec = {
        # Store the prompt at the API's own cap, not a display-sized slice — a
        # truncated prompt cannot be re-run faithfully.
        "id": rid, "label": label, "prompt": prompt[:20000],
        # Which connections the run was scoped to, so "Run again" reproduces it
        # instead of silently widening to every tool.
        "mcp_services": mcp_services,
        # Which provider account executed it — so a failure can be traced to the
        # right login, and "Run again" reuses the same one.
        "provider": prov, "account_id": aid, "model": chosen_model,
        "started": _now().isoformat(), "finished": None,
        "ok": False, "cost_usd": 0.0, "turns": None, "result": "",
        "over_budget": False, "cancelled": False, "error": None, "log": [],
    }
    _emit(f"agent '{label}' starting")
    if spec is not None:
        _emit(f"runtime: {spec['label']}" + (f" · model {chosen_model}" if chosen_model else ""))

    # Say which credential this run uses. Without this line an auth failure was
    # indistinguishable from a bad token, a stale mounted login, or no login at
    # all — every case surfaced as the same opaque "401 Invalid bearer token".
    if spec is not None:
        cred_source, cred_why = "account", f"{prov} account '{aid}'"
    else:
        cred_source, cred_why = legacy_credential_source()
    _emit(f"auth: {cred_why}")
    rec["auth_source"] = cred_source

    # Every runtime reaches the same tools, by three different roads: Claude's
    # --mcp-config, a stdio bridge for Codex, and function calling for Gemini
    # (which has no MCP support at all). See docs/AGENTS.md §2b.
    give_tools = bool(cfg.get("give_plutus_tools", True))
    mcp_config_path = None
    if give_tools:
        try:
            if runtime == "claude":
                mcp_config_path = write_plutus_mcp_config(root, mcp_url=mcp_url,
                                                          token=bearer_token,
                                                          disallowed=disallowed_tools)
                if disallowed_tools:
                    _emit(f"tool scope: {len(disallowed_tools)} tools withheld from this "
                          f"run's manifest, not just blocked")
            elif prov == "codex":
                write_codex_mcp_config(root, aid, mcp_url=mcp_url, token=bearer_token,
                                       disallowed=disallowed_tools)
                _emit("Plutus MCP tools wired into Codex via the stdio bridge")
        except Exception as e:
            # Losing tools is bad; failing the run over it is worse.
            _emit(f"warn: could not wire Plutus MCP tools ({e})")

    transcript: list[dict] = []    # full detail, persisted beside the run
    try:
        if runtime == "api":
            _execute_api(root, rec, prompt, prov, aid, chosen_model, cfg, transcript,
                         mcp_url=mcp_url if give_tools else "",
                         bearer_token=bearer_token, disallowed=disallowed_tools,
                         smart_fallback=smart_fallback)
        elif runtime == "cli":
            _execute_cli(root, rec, prompt, prov, aid, chosen_model, cfg, cwd, transcript)
        else:
            _execute_claude(root, rec, prompt, cfg, label=label, cwd=cwd,
                            mcp_config_path=mcp_config_path,
                            disallowed_tools=disallowed_tools, model=model,
                            provider=prov, account_id=aid,
                            cred_source=cred_source, transcript=transcript)
    except FileNotFoundError as e:
        # Keep the resolver's detail (where it looked) rather than replacing it
        # with a generic "install it" line for a CLI that is already installed.
        rec["error"] = str(e) or "`claude` not found — see docs/AGENTS.md."
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
        # Sidecar file: the transcript can be large, and keeping it out of the
        # run record leaves list_runs() cheap.
        try:
            if transcript:
                save_transcript(root, rid, transcript)
                rec["transcript_entries"] = len(transcript)
        except Exception:
            pass
        save_run(root, rec)
        LIVE["done"] = True
        with _LOCK:
            _current.update(running=False, id=None, started=None, label=None, proc=None, cancelled=False)
    return rec
