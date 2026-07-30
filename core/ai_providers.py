"""AI providers as CLI runtimes, with multiple authenticated accounts each.

Plutus drives Claude Code as a subprocess (`claude -p …`), not as an HTTP client.
The unit of authentication is therefore a *credentials directory on disk*, not a
bearer token in .env — and that is what makes multi-account work: Claude Code
honours ``CLAUDE_CONFIG_DIR``, so every account simply gets its own directory.

    data/providers/claude/<account-id>/.credentials.json

Selecting an account when launching an agent just means pointing
``CLAUDE_CONFIG_DIR`` at that directory. No token juggling, no shared global
state, and logging out of one account cannot disturb another.

Why this replaces the old single ``CLAUDE_CODE_OAUTH_TOKEN``: that token was
injected into every run's environment, where it *overrode* any real CLI login. A
stale token therefore broke runs that would otherwise have worked, and produced a
bare "401 Invalid bearer token" with no indication of the cause. The token path
is kept as a per-account fallback, but a real login on disk always wins.

Codex is declared here so the surface is provider-generic, but only detection is
implemented: its non-interactive invocation flags are not verified, and inventing
them would produce a provider that reports healthy and fails on first use.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

ACCOUNTS_FILE = "ai_accounts.json"

# ── provider registry ────────────────────────────────────────────────────────

# Roles are a routing *label*, not a sandbox. Codex is a coding agent and makes a
# poor research runtime; Gemini is the reverse. The launch picker groups accounts
# by role so the sensible choice is the obvious one — nothing here stops an
# operator deliberately picking the other.
ROLE_GENERAL = "general"
ROLE_CODING = "coding"
ROLE_RESEARCH = "research"

ROLE_LABEL = {
    ROLE_GENERAL: "general purpose",
    ROLE_CODING: "coding",
    ROLE_RESEARCH: "research",
}


def _claude_exec(prompt: str, model: str) -> list[str]:
    # `--` terminates option parsing: several Claude Code options are variadic and
    # would otherwise swallow a bare positional prompt (see build_agent_cmd).
    args = ["-p", "--output-format", "text"]
    if model:
        args += ["--model", model]
    return args + ["--", prompt]


def _codex_exec(prompt: str, model: str) -> list[str]:
    # `codex exec <prompt>` is Codex's non-interactive mode; the prompt is
    # positional and --model takes exactly one value.
    #
    # --skip-git-repo-check is required, not optional: Codex refuses to run
    # outside a git repository, and the agent's working directory (/app in the
    # container) is not one. Its own error names the flag —
    # "Not inside a trusted directory and --skip-git-repo-check was not specified".
    args = ["exec", "--skip-git-repo-check"]
    if model:
        args += ["--model", model]
    return args + [prompt]


def _gemini_exec(prompt: str, model: str) -> list[str]:
    # Gemini CLI takes the prompt as the *value* of -p, so no option can eat it.
    args: list[str] = []
    if model:
        args += ["-m", model]
    return args + ["-p", prompt]


PROVIDERS: dict[str, dict[str, Any]] = {
    "claude": {
        "label": "Claude Code",
        "cli": "claude",
        # Runnable as-is: the CLI has to exist *inside the container*, so the hint
        # is the docker exec form rather than a bare npm line that would install it
        # on the host where nothing can reach it.
        "install_hint": "docker exec plutus-mcp npm install -g @anthropic-ai/claude-code",
        # Env var that relocates the CLI's whole config/credential directory.
        "config_dir_env": "CLAUDE_CONFIG_DIR",
        "default_home": "~/.claude",
        # Files that prove a completed login inside a config directory.
        "credential_files": (".credentials.json", "credentials.json"),
        # Interactive login command (run once per account); `setup-token` prints a
        # long-lived token instead, for the paste-a-token path.
        "login_cmd": ("claude",),
        "token_cmd": ("claude", "setup-token"),
        "token_env": "CLAUDE_CODE_OAUTH_TOKEN",
        "key_hint": "Session token from `claude setup-token`",
        "exec": _claude_exec,
        "test_model": "haiku",
        "role": ROLE_GENERAL,
        "runnable": True,
    },
    "codex": {
        "label": "Codex",
        "cli": "codex",
        "install_hint": "docker exec plutus-mcp npm install -g @openai/codex",
        # Real and documented: Codex reads $CODEX_HOME/config.toml and writes
        # $CODEX_HOME/auth.json, defaulting to ~/.codex.
        "config_dir_env": "CODEX_HOME",
        "default_home": "~/.codex",
        "credential_files": ("auth.json", ".credentials.json"),
        # `codex` on its own opens the interactive TUI and completes the browser
        # sign-in from there; `codex login` also exists but the plain command is
        # what actually worked in the container.
        "login_cmd": ("codex",),
        "token_cmd": None,
        "token_env": None,
        "exec": _codex_exec,
        "test_model": "",           # use whatever the account defaults to
        "role": ROLE_CODING,
        "runnable": True,
    },
    "gemini": {
        "label": "Gemini CLI",
        "cli": "gemini",
        "install_hint": "docker exec plutus-mcp npm install -g @google/gemini-cli",
        # Gemini CLI has NO config-dir override: it reads ~/.gemini and that is
        # that. Handing the user `-e GEMINI_CONFIG_DIR=…` (which this once did) is
        # worse than handing them nothing — the CLI ignores it, the login lands in
        # ~/.gemini, and the account stays "not linked" with no clue why. Accounts
        # for such providers are linked by adopting the login instead; see
        # adopt_login().
        "config_dir_env": None,
        "default_home": "~/.gemini",
        "credential_files": ("oauth_creds.json", "google_accounts.json", ".credentials.json"),
        "login_cmd": ("gemini",),
        "token_cmd": None,
        "token_env": "GEMINI_API_KEY",
        # Gemini's free tier issues a key from AI Studio, which is the simplest
        # way to run it headlessly *and* the only one that isolates accounts,
        # since the CLI shares one ~/.gemini for OAuth logins.
        "key_hint": "Free key from https://aistudio.google.com/apikey",
        "exec": _gemini_exec,
        "test_model": "",
        "role": ROLE_RESEARCH,
        "runnable": True,
    },
}


def provider_ids() -> list[str]:
    return list(PROVIDERS)


def _spec(provider: str) -> dict[str, Any]:
    spec = PROVIDERS.get((provider or "").strip())
    if not spec:
        raise ValueError(f"unknown provider: {provider!r}")
    return spec


# ── account store ────────────────────────────────────────────────────────────

def _accounts_path(root: Path) -> Path:
    return Path(root) / "data" / ACCOUNTS_FILE


def load_accounts(root: Path) -> dict[str, list[dict]]:
    """{provider_id: [account, …]} — always has a key per known provider."""
    out: dict[str, list[dict]] = {p: [] for p in PROVIDERS}
    try:
        raw = json.loads(_accounts_path(root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return out
    if not isinstance(raw, dict):
        return out
    for pid in out:
        rows = raw.get(pid)
        if isinstance(rows, list):
            out[pid] = [r for r in rows if isinstance(r, dict) and r.get("id")]
    return out


def _save_accounts(root: Path, data: dict) -> None:
    p = _accounts_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, p)


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(label: str) -> str:
    s = _SLUG_RE.sub("-", (label or "").strip().lower()).strip("-")
    return s[:32] or "account"


def account_dir(root: Path, provider: str, account_id: str) -> Path:
    """The account's private config/credential directory.

    ``account_id`` is generated here and never taken raw from a request, but the
    guard stays: this value becomes a filesystem path.
    """
    _spec(provider)
    aid = (account_id or "").strip()
    if not aid or "/" in aid or "\\" in aid or aid.startswith("."):
        raise ValueError(f"invalid account id: {account_id!r}")
    return Path(root) / "data" / "providers" / provider / aid


def add_account(root: Path, provider: str, label: str) -> dict:
    _spec(provider)
    label = (label or "").strip()[:60]
    if not label:
        raise ValueError("account label is required")
    data = load_accounts(root)
    if any(a.get("label") == label for a in data[provider]):
        raise ValueError(f"an account called {label!r} already exists")
    aid = f"{_slug(label)}-{uuid.uuid4().hex[:6]}"
    rec = {"id": aid, "label": label, "created_at": int(time.time())}
    account_dir(root, provider, aid).mkdir(parents=True, exist_ok=True)
    data[provider].append(rec)
    _save_accounts(root, data)
    return rec


def get_account(root: Path, provider: str, account_id: str) -> dict | None:
    return next((a for a in load_accounts(root).get(provider, [])
                 if a.get("id") == account_id), None)


def remove_account(root: Path, provider: str, account_id: str) -> bool:
    """Forget an account and delete its credentials directory (this is logout)."""
    data = load_accounts(root)
    before = len(data.get(provider, []))
    data[provider] = [a for a in data.get(provider, []) if a.get("id") != account_id]
    if len(data[provider]) == before:
        return False
    _save_accounts(root, data)
    try:
        shutil.rmtree(account_dir(root, provider, account_id), ignore_errors=True)
    except (OSError, ValueError):
        pass
    return True


def logout_account(root: Path, provider: str, account_id: str) -> bool:
    """Drop stored credentials but keep the account entry, so it can be re-linked."""
    spec = _spec(provider)
    d = account_dir(root, provider, account_id)
    removed = False
    for name in spec["credential_files"]:
        f = d / name
        if f.exists():
            try:
                f.unlink()
                removed = True
            except OSError:
                pass
    return removed


# ── CLI detection ────────────────────────────────────────────────────────────

# Where a provider CLI can live besides PATH.
#
# Claude Code auto-updates itself into a *native* install under ~/.local/bin,
# superseding the npm global the image created at build time. An interactive
# `docker exec … claude` finds it because the login shell's profile adds that
# directory; a subprocess spawned from the app does not, because the container's
# ENV PATH never included it. The symptom is "`claude` not found" from an app that
# you could run by hand in the same container seconds earlier — so resolve
# explicitly rather than trusting PATH.
CLI_SEARCH_DIRS: tuple[str, ...] = (
    "~/.local/bin",
    "~/.claude/local",
    "/usr/local/bin",
    "/usr/bin",
    "/opt/homebrew/bin",
)


def resolve_cli(name: str) -> str | None:
    """Absolute path to a provider CLI, searching beyond PATH."""
    found = shutil.which(name)
    if found:
        return found
    for d in CLI_SEARCH_DIRS:
        base = Path(os.path.expanduser(d)) / name
        for cand in (base, base.with_suffix(".cmd"), base.with_suffix(".exe")):
            if cand.is_file() and os.access(cand, os.X_OK):
                return str(cand)
    return None


def cli_search_path() -> str:
    """PATH with the extra CLI directories prepended, for child processes."""
    extra = [str(Path(os.path.expanduser(d))) for d in CLI_SEARCH_DIRS]
    current = os.environ.get("PATH", "")
    seen, parts = set(), []
    for p in extra + current.split(os.pathsep):
        if p and p not in seen:
            seen.add(p)
            parts.append(p)
    return os.pathsep.join(parts)


# `--version` shells out, so memoise briefly: the providers card polls to notice a
# CLI installed from a terminal, and that must not mean three subprocesses every
# few seconds. Short enough that an install shows up promptly.
_CLI_INFO_TTL = 15.0
_cli_info_cache: dict[str, tuple[float, dict]] = {}


def forget_cli_info(provider: str | None = None) -> None:
    """Drop the cached probe so the next read re-detects immediately."""
    if provider is None:
        _cli_info_cache.clear()
    else:
        _cli_info_cache.pop(provider, None)


def cli_info(provider: str, *, fresh: bool = False) -> dict:
    """Whether the provider's CLI can be found, and its version."""
    spec = _spec(provider)
    now = time.time()
    if not fresh:
        hit = _cli_info_cache.get(provider)
        if hit and hit[0] > now:
            return dict(hit[1])

    path = resolve_cli(spec["cli"])
    info = {"installed": bool(path), "path": path or "", "version": "",
            "install_hint": spec["install_hint"]}
    if not path:
        _cli_info_cache[provider] = (now + _CLI_INFO_TTL, dict(info))
        return info
    try:
        r = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=20)
        info["version"] = (r.stdout or r.stderr or "").strip().splitlines()[0][:80] if (r.stdout or r.stderr) else ""
    except (OSError, subprocess.SubprocessError):
        pass
    _cli_info_cache[provider] = (now + _CLI_INFO_TTL, dict(info))
    return info


# A per-account API key, written 0600 by the providers API.
#
# This is the cleanest auth path for a CLI with no config-dir override: a key is
# injected per invocation, so two accounts are genuinely isolated without either
# touching a shared directory. Gemini's free tier makes it the practical default
# there; Claude accepts a session token the same way.
TOKEN_FILE = "plutus_token"


def stored_token(root: Path, provider: str, account_id: str) -> str:
    _spec(provider)
    try:
        return account_dir(root, provider, account_id).joinpath(TOKEN_FILE).read_text(
            encoding="utf-8").strip()
    except (OSError, ValueError):
        return ""


def save_token(root: Path, provider: str, account_id: str, token: str) -> None:
    spec = _spec(provider)
    if not spec.get("token_env"):
        raise ValueError(f"{spec['label']} does not support key/token auth")
    tok = (token or "").strip().strip("'\"").strip()
    if not tok:
        raise ValueError("token is empty")
    d = account_dir(root, provider, account_id)
    d.mkdir(parents=True, exist_ok=True)
    f = d / TOKEN_FILE
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(tok)


def clear_token(root: Path, provider: str, account_id: str) -> bool:
    try:
        account_dir(root, provider, account_id).joinpath(TOKEN_FILE).unlink()
        return True
    except (OSError, ValueError):
        return False


def credentials_file(root: Path, provider: str, account_id: str) -> Path | None:
    spec = _spec(provider)
    d = account_dir(root, provider, account_id)
    for name in spec["credential_files"]:
        f = d / name
        if f.is_file():
            return f
    return None


def default_home(provider: str) -> Path:
    """Where the CLI keeps its config when nothing redirects it."""
    return Path(os.path.expanduser(_spec(provider)["default_home"]))


def supports_isolation(provider: str) -> bool:
    """True when the CLI can be pointed at a per-account directory."""
    return bool(_spec(provider).get("config_dir_env"))


def account_env(root: Path, provider: str, account_id: str,
                *, token: str = "") -> dict[str, str]:
    """Environment fragment that points the CLI at this account.

    Returned as a fragment (not a full env) so callers stay explicit about what
    they are overriding. Empty of a config dir when the CLI has no override — we
    must not set a variable it ignores and then behave as though it took effect.
    """
    spec = _spec(provider)
    env: dict[str, str] = {}
    if spec.get("config_dir_env"):
        env[spec["config_dir_env"]] = str(account_dir(root, provider, account_id))
    # The account's own key, read here so every caller gets it without having to
    # remember to look it up.
    tok = token or stored_token(root, provider, account_id)
    if tok and spec.get("token_env"):
        env[spec["token_env"]] = tok
    return env


def shared_credentials_file(provider: str) -> Path | None:
    """A login sitting in the CLI's default home, not yet claimed by an account."""
    spec = _spec(provider)
    home = default_home(provider)
    for name in spec["credential_files"]:
        f = home / name
        if f.is_file():
            return f
    return None


def adopt_login(root: Path, provider: str, account_id: str) -> dict:
    """Copy the CLI's current login out of its default home into this account.

    This is how multi-account works for a CLI with no config-dir override: log in
    as one identity, adopt it here, log out of the CLI, log in as the next, adopt
    that into another account. Each account then holds its own credential copy.
    """
    import shutil as _shutil

    spec = _spec(provider)
    home = default_home(provider)
    dest = account_dir(root, provider, account_id)
    dest.mkdir(parents=True, exist_ok=True)

    copied: list[str] = []
    for name in spec["credential_files"]:
        src = home / name
        if src.is_file():
            try:
                _shutil.copy2(src, dest / name)
                copied.append(name)
            except OSError as e:
                return {"ok": False, "copied": copied, "error": f"could not copy {name}: {e}"}
    if not copied:
        return {
            "ok": False, "copied": [],
            "error": (f"No login found in {home}. Run the link command first. Note that "
                      "some CLIs store credentials in the OS keyring rather than a file, "
                      "in which case there is nothing for Plutus to adopt and the account "
                      "shares the CLI's single login."),
        }
    return {"ok": True, "copied": copied, "from": str(home)}


def login_command(root: Path, provider: str, account_id: str) -> str:
    """The exact `docker exec` one-liner that links *this* account.

    Per-account and per-provider on purpose: the UI used to hardcode the Claude
    form, so a Codex account was told to run `CLAUDE_CONFIG_DIR=… plutus-mcp
    claude` — the wrong env var and the wrong binary.

    When the CLI has no config-dir override the command carries no env var at all.
    Passing one the CLI silently ignores is actively misleading: the login appears
    to succeed, lands in the default home, and the account still reads "not
    linked".
    """
    spec = _spec(provider)
    cmd = " ".join(spec["login_cmd"])
    if not spec.get("config_dir_env"):
        return f"docker exec -it plutus-mcp {cmd}"
    d = account_dir(root, provider, account_id)
    return f"docker exec -it -e {spec['config_dir_env']}={d} plutus-mcp {cmd}"


def account_status(root: Path, provider: str, account: dict) -> dict:
    """Auth state for one account, without running the CLI."""
    spec = _spec(provider)
    aid = account.get("id", "")
    cred = credentials_file(root, provider, aid)
    has_key = bool(stored_token(root, provider, aid))
    d = account_dir(root, provider, aid)
    # A login the CLI left in its default home: not owned by this account yet, but
    # visible, so the UI can offer to adopt it instead of reporting a dead end.
    pending = None if (cred or has_key) else shared_credentials_file(provider)
    return {
        **account,
        "provider": provider,
        "provider_label": spec["label"],
        "role": spec.get("role", ROLE_GENERAL),
        "role_label": ROLE_LABEL.get(spec.get("role", ROLE_GENERAL), ""),
        "config_dir": str(d),
        "isolated": supports_isolation(provider) or bool(spec.get("token_env")),
        # An API key counts as linked: it is injected per invocation, so the
        # account is authenticated and isolated without any shared directory.
        "authenticated": cred is not None or has_key,
        "auth_kind": "api_key" if has_key else ("cli_login" if cred else ""),
        "accepts_key": bool(spec.get("token_env")),
        "key_hint": spec.get("key_hint", ""),
        "credentials_path": str(cred) if cred else "",
        "linked_at": int(cred.stat().st_mtime) if cred else None,
        "state": ("connected" if (cred or has_key)
                  else ("adoptable" if pending else "login_required")),
        "adoptable": pending is not None,
        "adoptable_from": str(default_home(provider)) if pending else "",
        "login_command": login_command(root, provider, aid),
    }


def provider_status(root: Path, provider: str) -> dict:
    spec = _spec(provider)
    cli = cli_info(provider)
    accounts = [account_status(root, provider, a)
                for a in load_accounts(root).get(provider, [])]
    if not cli["installed"]:
        state = "cli_missing"
    elif not accounts:
        state = "no_accounts"
    elif any(a["authenticated"] for a in accounts):
        state = "connected"
    elif any(a["adoptable"] for a in accounts):
        state = "adoptable"
    else:
        state = "login_required"
    return {
        "id": provider,
        "label": spec["label"],
        "isolated": supports_isolation(provider),
        "runnable": bool(spec.get("runnable")),
        "role": spec.get("role", ROLE_GENERAL),
        "role_label": ROLE_LABEL.get(spec.get("role", ROLE_GENERAL), ""),
        "cli": cli,
        "accounts": accounts,
        "state": state,
        # Kept for callers that want a provider-level hint; each account also
        # carries its own, which is what the UI renders.
        "login_command": accounts[0]["login_command"] if accounts else "",
    }


def all_status(root: Path) -> list[dict]:
    return [provider_status(root, p) for p in PROVIDERS]


# ── real capability tests ────────────────────────────────────────────────────

def _check(name: str, ok: bool, detail: str = "") -> dict:
    return {"name": name, "ok": bool(ok), "detail": detail}


def capability_test(root: Path, provider: str, account_id: str, *,
                    mcp_config_path: str | None = None,
                    model: str | None = None, timeout: int = 120) -> dict:
    """Actually exercise the provider instead of probing a port.

    Runs, in order: CLI present → credentials on disk → a real prompt round-trip
    → (optionally) the same round-trip with Plutus's MCP config attached. Stops at
    the first failure, because every later check depends on it.

    The invocation comes from the provider's own ``exec`` builder, so each CLI is
    driven with its real flags rather than Claude's — this used to hardcode
    ``-p --output-format text``, which no other CLI understands.
    """
    spec = _spec(provider)
    checks: list[dict] = []

    cli = cli_info(provider)
    checks.append(_check("CLI installed", cli["installed"],
                         cli["version"] or cli["install_hint"]))
    if not cli["installed"]:
        return {"ok": False, "checks": checks}

    if not spec.get("runnable"):
        checks.append(_check("Can execute prompt", False,
                             f"{spec['label']} is detected but Plutus cannot run agents "
                             "through it yet — its non-interactive flags are unverified."))
        return {"ok": False, "checks": checks}

    cred = credentials_file(root, provider, account_id)
    key = stored_token(root, provider, account_id)
    if cred is None and key:
        checks.append(_check("Session credentials present", True,
                             f"API key stored for this account ({spec['token_env']})"))
    elif cred is None:
        pending = shared_credentials_file(provider)
        detail = (f"a login exists in {default_home(provider)} but is not claimed by "
                  "this account yet — use Adopt login") if pending else \
                 (f"no login found for this account. Run the link command, then "
                  f"Adopt login if the CLI wrote to {default_home(provider)}.")
        checks.append(_check("Session credentials present", False, detail))
        return {"ok": False, "checks": checks}
    checks.append(_check("Session credentials present", True, str(cred)))

    # Strip ambient credentials FIRST, then overlay the account's own. Doing it the
    # other way round popped the account's stored API key straight back out and
    # left the run with no credential at all.
    env = {**os.environ, "IS_SANDBOX": "1"}
    env.pop("ANTHROPIC_API_KEY", None)
    if spec.get("token_env"):
        env.pop(spec["token_env"], None)
    env.update(account_env(root, provider, account_id))

    sentinel = "PLUTUS_OK"
    chosen = spec.get("test_model", "") if model is None else model
    build = spec["exec"]
    prompt = f"Reply with exactly this word and nothing else: {sentinel}"

    ran = _run_cli([cli["path"], *build(prompt, chosen)], env, timeout)
    checks.append(_check("Can execute prompt", sentinel in ran["out"],
                         _explain(ran, sentinel)))
    if sentinel not in ran["out"]:
        return {"ok": False, "checks": checks}

    # MCP wiring is Claude-specific for now (--mcp-config); the other CLIs
    # configure their servers through their own config files, so claiming to have
    # tested it there would be a lie.
    if mcp_config_path and provider == "claude":
        mcp_prompt = ("List the name of any one tool you can call from the 'plutus' MCP "
                      f"server, then the word {sentinel}. If you have no such tools, "
                      "reply NO_MCP.")
        mran = _run_cli(
            [cli["path"], "-p", "--output-format", "text", "--mcp-config", mcp_config_path,
             "--", mcp_prompt], env, timeout)
        got = sentinel in mran["out"] and "NO_MCP" not in mran["out"]
        checks.append(_check("MCP tools reachable", got,
                             mran["out"][:200] if mran["out"] else _explain(mran, sentinel)))

    return {"ok": all(c["ok"] for c in checks), "checks": checks}


def _run_cli(cmd: list[str], env: dict, timeout: int) -> dict:
    try:
        # DEVNULL, not inherited: Codex announces "Reading additional input from
        # stdin…" even with a positional prompt, and an inherited terminal would
        # leave it waiting for input nobody is going to type.
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           env=env, stdin=subprocess.DEVNULL)
        return {"code": r.returncode, "out": (r.stdout or "").strip(),
                "err": (r.stderr or "").strip(), "timeout": False}
    except subprocess.TimeoutExpired:
        return {"code": -1, "out": "", "err": "", "timeout": True}
    except OSError as e:
        return {"code": -1, "out": "", "err": str(e), "timeout": False}


def _explain(ran: dict, sentinel: str) -> str:
    """Turn CLI failure into something a human can act on.

    Explicitly does *not* pass through a bare "401 Invalid bearer token": for a
    CLI provider that means the stored session is stale, which is actionable,
    whereas the raw string sent people hunting for an API key they never set.
    """
    if ran["timeout"]:
        return "the CLI did not respond in time"
    blob = f"{ran['out']}\n{ran['err']}".strip()
    low = blob.lower()
    if "invalid bearer" in low or "401" in low:
        return ("the stored session was rejected — re-link this account "
                "(Logout, then Link account)")
    if "out of" in low and "usage" in low:
        return "authenticated, but this plan is out of usage right now"
    if "credit balance" in low:
        return "authenticated, but the account has no credit"
    if not blob:
        return f"the CLI exited {ran['code']} with no output"
    return blob[:300]
