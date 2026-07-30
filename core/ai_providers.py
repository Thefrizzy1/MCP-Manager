"""AI providers, with multiple authenticated accounts each.

A provider is one of two *kinds*:

``cli``
    Plutus drives the vendor's CLI as a subprocess (`claude -p …`, `codex exec
    …`). The unit of authentication is a *credentials directory on disk*, not a
    bearer token in .env — and that is what makes multi-account work: the CLI
    honours a config-dir env var, so every account simply gets its own directory.

        data/providers/claude/<account-id>/.credentials.json

    Selecting an account when launching an agent just means pointing
    ``CLAUDE_CONFIG_DIR`` (or ``CODEX_HOME``) at that directory. No token
    juggling, no shared global state, and logging out of one account cannot
    disturb another.

``api``
    Plutus calls the vendor's HTTP API directly with a per-account key. There is
    no CLI to install, no interactive login, and no shared config directory —
    the key is the whole credential, so accounts are isolated by construction.
    Gemini is this kind: Google's free AI Studio tier issues a key, and the
    Gemini CLI has no config-dir override at all, which made its per-account
    story a log-in/adopt/log-out dance for no benefit.

Why the ``cli`` kind replaces the old single ``CLAUDE_CODE_OAUTH_TOKEN``: that
token was injected into every run's environment, where it *overrode* any real CLI
login. A stale token therefore broke runs that would otherwise have worked, and
produced a bare "401 Invalid bearer token" with no indication of the cause. The
token path is kept as a per-account fallback, but a real login on disk wins.
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

# How Plutus talks to the provider. This is not cosmetic: it decides whether an
# account needs a CLI on disk and an interactive login, or just a pasted key.
KIND_CLI = "cli"
KIND_API = "api"

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


# Model menus.
#
# These are *suggestions*, not a whitelist: every picker also accepts a typed
# model id, because a CLI's available models change under us with each vendor
# release and a hardcoded list that silently omits the model you pay for is worse
# than no list. An empty id means "whatever the account defaults to", which is the
# right answer often enough to be first in every menu.
#
# Gemini is the exception: being an HTTP provider, its real model list can be
# fetched from the API with the account's own key (see list_models), so the
# entries here are only the offline fallback.
MODELS: dict[str, tuple[tuple[str, str], ...]] = {
    "claude": (
        ("", "Account default"),
        ("opus", "Opus — most capable"),
        ("sonnet", "Sonnet — balanced"),
        ("haiku", "Haiku — fastest"),
    ),
    "codex": (
        ("", "Account default"),
        ("gpt-5.1-codex", "GPT-5.1 Codex — coding"),
        ("gpt-5.1-codex-mini", "GPT-5.1 Codex mini — cheaper"),
        ("gpt-5.1", "GPT-5.1 — general"),
        ("gpt-5", "GPT-5"),
        ("o3", "o3 — reasoning"),
    ),
    "gemini": (
        ("", "Account default"),
        ("gemini-2.5-flash", "Gemini 2.5 Flash — fast, free tier"),
        ("gemini-2.5-pro", "Gemini 2.5 Pro — most capable"),
        ("gemini-2.5-flash-lite", "Gemini 2.5 Flash-Lite — cheapest"),
    ),
}


PROVIDERS: dict[str, dict[str, Any]] = {
    "claude": {
        "label": "Claude Code",
        "kind": KIND_CLI,
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
        "kind": KIND_CLI,
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
        "label": "Gemini",
        # HTTP, not a CLI. The Gemini CLI has NO config-dir override — it reads
        # ~/.gemini and that is that — so per-account isolation meant logging in,
        # adopting the credential file, logging out, and logging in again as the
        # next identity. A key from Google's free tier does the same job with one
        # paste, isolates accounts by construction, and needs nothing installed.
        "kind": KIND_API,
        "cli": "",
        "install_hint": "",
        "config_dir_env": None,
        "default_home": "~/.gemini",
        "credential_files": (),
        "login_cmd": None,
        "token_cmd": None,
        "token_env": "GEMINI_API_KEY",
        "key_hint": "Free key from https://aistudio.google.com/apikey",
        "api_base": "https://generativelanguage.googleapis.com/v1beta",
        "exec": None,
        "test_model": "gemini-2.5-flash",
        "default_model": "gemini-2.5-flash",
        "role": ROLE_RESEARCH,
        "runnable": True,
    },
}

for _pid, _spec in PROVIDERS.items():
    _spec.setdefault("kind", KIND_CLI)
    _spec.setdefault("models", MODELS.get(_pid, (("", "Account default"),)))
    _spec.setdefault("default_model", "")
del _pid, _spec


def provider_ids() -> list[str]:
    return list(PROVIDERS)


def _spec(provider: str) -> dict[str, Any]:
    spec = PROVIDERS.get((provider or "").strip())
    if not spec:
        raise ValueError(f"unknown provider: {provider!r}")
    return spec


def kind(provider: str) -> str:
    return _spec(provider).get("kind", KIND_CLI)


def is_api(provider: str) -> bool:
    """True when Plutus talks HTTP to this provider instead of driving a CLI."""
    return kind(provider) == KIND_API


def default_model(provider: str) -> str:
    return _spec(provider).get("default_model", "") or ""


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
    """Drop stored credentials but keep the account entry, so it can be re-linked.

    Both credential kinds go: a stored key left behind after "Logout" would keep
    the account reading as linked and keep being injected into every run, which
    is the opposite of what the button says it does.
    """
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
    return clear_token(root, provider, account_id) or removed


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
    """Whether the provider's CLI can be found, and its version.

    An API provider has no CLI to find, so it reports installed with an empty
    path: nothing to install, nothing that can go missing after an image update.
    Callers that care about the difference read ``kind``.
    """
    spec = _spec(provider)
    if spec.get("kind") == KIND_API:
        return {"installed": True, "path": "", "version": "HTTP API",
                "install_hint": "", "kind": KIND_API}
    now = time.time()
    if not fresh:
        hit = _cli_info_cache.get(provider)
        if hit and hit[0] > now:
            return dict(hit[1])

    path = resolve_cli(spec["cli"])
    info = {"installed": bool(path), "path": path or "", "version": "",
            "install_hint": spec["install_hint"], "kind": KIND_CLI}
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
    if spec.get("kind") == KIND_API:
        return None          # no CLI, so no stray login to find
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
    if spec.get("kind") == KIND_API:
        return {"ok": False, "copied": [],
                "error": f"{spec['label']} authenticates with an API key — there is no "
                         "CLI login to adopt. Paste a key instead."}
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

    An API provider returns "" — there is nothing to run. The UI shows the key
    field instead, rather than a command that would do nothing.
    """
    spec = _spec(provider)
    if spec.get("kind") == KIND_API or not spec.get("login_cmd"):
        return ""
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
    api = spec.get("kind") == KIND_API
    # An unauthenticated account's next step differs by kind, and saying
    # "login required" to someone who only ever needs to paste a key sends them
    # looking for a command that does not exist.
    if cred or has_key:
        state = "connected"
    elif pending:
        state = "adoptable"
    else:
        state = "key_required" if api else "login_required"
    return {
        **account,
        "provider": provider,
        "provider_label": spec["label"],
        "kind": spec.get("kind", KIND_CLI),
        "role": spec.get("role", ROLE_GENERAL),
        "role_label": ROLE_LABEL.get(spec.get("role", ROLE_GENERAL), ""),
        "config_dir": "" if api else str(d),
        "isolated": supports_isolation(provider) or bool(spec.get("token_env")),
        # An API key counts as linked: it is injected per invocation, so the
        # account is authenticated and isolated without any shared directory.
        "authenticated": cred is not None or has_key,
        "auth_kind": "api_key" if has_key else ("cli_login" if cred else ""),
        "accepts_key": bool(spec.get("token_env")),
        "key_hint": spec.get("key_hint", ""),
        "credentials_path": str(cred) if cred else "",
        "linked_at": int(cred.stat().st_mtime) if cred else None,
        "state": state,
        "adoptable": pending is not None,
        "adoptable_from": str(default_home(provider)) if pending else "",
        "login_command": login_command(root, provider, aid),
    }


def provider_status(root: Path, provider: str) -> dict:
    spec = _spec(provider)
    api = spec.get("kind") == KIND_API
    cli = cli_info(provider)
    accounts = [account_status(root, provider, a)
                for a in load_accounts(root).get(provider, [])]
    if not api and not cli["installed"]:
        state = "cli_missing"
    elif not accounts:
        state = "no_accounts"
    elif any(a["authenticated"] for a in accounts):
        state = "connected"
    elif any(a["adoptable"] for a in accounts):
        state = "adoptable"
    else:
        state = "key_required" if api else "login_required"
    return {
        "id": provider,
        "label": spec["label"],
        "kind": spec.get("kind", KIND_CLI),
        "isolated": supports_isolation(provider),
        "runnable": bool(spec.get("runnable")),
        "role": spec.get("role", ROLE_GENERAL),
        "role_label": ROLE_LABEL.get(spec.get("role", ROLE_GENERAL), ""),
        "cli": cli,
        "accounts": accounts,
        "state": state,
        # The offline menu. A live list (Gemini) is fetched separately, per
        # account, because it needs that account's own key.
        "models": [{"id": m, "label": lbl} for m, lbl in spec.get("models", ())],
        "default_model": spec.get("default_model", ""),
        "accepts_key": bool(spec.get("token_env")),
        "key_hint": spec.get("key_hint", ""),
        # Kept for callers that want a provider-level hint; each account also
        # carries its own, which is what the UI renders.
        "login_command": accounts[0]["login_command"] if accounts else "",
    }


def all_status(root: Path) -> list[dict]:
    return [provider_status(root, p) for p in PROVIDERS]


# ── HTTP providers (Gemini) ──────────────────────────────────────────────────
#
# One place that speaks to the Generative Language API, used by both the
# capability test and the agent runner so a run and a test cannot diverge.
#
# The two functions below are the only network calls in this module, which is
# what makes them the seam tests stub.

def _http(method: str, url: str, key: str, *, payload: dict | None = None,
          timeout: int = 60) -> dict:
    """{"code", "json", "error"} — never raises, so callers stay linear."""
    import httpx

    try:
        r = httpx.request(method, url, json=payload, timeout=timeout,
                          headers={"x-goog-api-key": key,
                                   "Content-Type": "application/json"})
    except Exception as e:                       # network, DNS, TLS, timeout
        return {"code": 0, "json": {}, "error": str(e)}
    try:
        body = r.json()
    except ValueError:
        body = {}
    err = ""
    if r.status_code >= 400:
        err = (body.get("error", {}) or {}).get("message") or r.text[:300] or f"HTTP {r.status_code}"
    return {"code": r.status_code, "json": body, "error": err}


def api_generate(root: Path, provider: str, account_id: str, prompt: str, *,
                 model: str = "", timeout: int = 120, search: bool = True) -> dict:
    """One completion from an HTTP provider. {"ok", "text", "error", "model"}.

    Google Search grounding is requested by default: a research runtime with no
    way to look anything up is not a research runtime. It is not universally
    available (model and tier dependent), so a rejection of the *tool* retries
    the same prompt without it rather than failing the whole run — losing
    grounding is a degradation, not an error.
    """
    spec = _spec(provider)
    if spec.get("kind") != KIND_API:
        return {"ok": False, "text": "", "error": f"{spec['label']} is not an HTTP provider",
                "model": ""}
    key = stored_token(root, provider, account_id)
    if not key:
        return {"ok": False, "text": "", "error":
                f"no API key stored for this account. {spec.get('key_hint', '')}".strip(),
                "model": ""}

    chosen = (model or spec.get("default_model") or "").strip()
    if not chosen:
        return {"ok": False, "text": "", "error": "no model selected", "model": ""}
    url = f"{spec['api_base']}/models/{chosen}:generateContent"
    body: dict[str, Any] = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
    if search:
        body["tools"] = [{"google_search": {}}]

    res = _http("POST", url, key, payload=body, timeout=timeout)
    if res["error"] and search and _rejects_the_search_tool(res["error"]):
        body.pop("tools", None)
        res = _http("POST", url, key, payload=body, timeout=timeout)
    if res["error"]:
        return {"ok": False, "text": "", "error": _explain_api(res), "model": chosen}

    text = _api_text(res["json"])
    if not text:
        return {"ok": False, "text": "", "error": _empty_reason(res["json"]), "model": chosen}
    return {"ok": True, "text": text, "error": "", "model": chosen}


def _rejects_the_search_tool(err: str) -> bool:
    low = err.lower()
    return ("search" in low or "tool" in low or "grounding" in low) and \
           ("not supported" in low or "unsupported" in low or "invalid" in low
            or "unknown name" in low)


def _api_text(body: dict) -> str:
    parts = (((body.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
    return "\n".join(p["text"] for p in parts
                     if isinstance(p, dict) and isinstance(p.get("text"), str)).strip()


def _empty_reason(body: dict) -> str:
    """Why a 200 came back with no text — otherwise this reads as a silent success."""
    blocked = (body.get("promptFeedback") or {}).get("blockReason")
    if blocked:
        return f"the prompt was blocked by a safety filter ({blocked})"
    finish = ((body.get("candidates") or [{}])[0]).get("finishReason")
    if finish and finish != "STOP":
        return f"the model stopped early ({finish})"
    return "the model returned no text"


def _explain_api(res: dict) -> str:
    """Turn an API failure into something actionable, the way _explain does for CLIs."""
    msg = res["error"] or ""
    low = msg.lower()
    if res["code"] in (401, 403) or "api key not valid" in low or "api_key_invalid" in low:
        return "the stored API key was rejected — paste a fresh one from https://aistudio.google.com/apikey"
    if res["code"] == 429 or "quota" in low or "rate limit" in low:
        return "the free tier's rate limit or daily quota is exhausted — try again later"
    if res["code"] == 404 and "model" in low:
        return f"{msg} — pick a different model"
    if res["code"] == 0:
        return f"could not reach the API: {msg}"
    return msg[:300]


# Model menus change with every vendor release, so the live list is worth the
# call — but not on every keystroke.
_MODELS_TTL = 300.0
_models_cache: dict[str, tuple[float, list[dict]]] = {}


def list_models(root: Path, provider: str, account_id: str = "") -> dict:
    """{"models": [{"id","label"}], "source": "live"|"static", "allow_custom": True}.

    Live for an HTTP provider with a key stored — that is the only way the menu
    can name the models the account can actually reach, rather than the ones that
    existed when this file was written. Everything else falls back to the static
    menu, and *every* menu accepts a typed id, because being unable to select a
    model the CLI supports is worse than an incomplete list.
    """
    spec = _spec(provider)
    static = [{"id": m, "label": lbl} for m, lbl in spec.get("models", ())]
    if spec.get("kind") != KIND_API or not account_id:
        return {"models": static, "source": "static", "allow_custom": True}

    key = stored_token(root, provider, account_id)
    if not key:
        return {"models": static, "source": "static", "allow_custom": True}

    ck = f"{provider}/{account_id}"
    hit = _models_cache.get(ck)
    if hit and hit[0] > time.time():
        return {"models": list(hit[1]), "source": "live", "allow_custom": True}

    res = _http("GET", f"{spec['api_base']}/models?pageSize=200", key, timeout=20)
    if res["error"]:
        return {"models": static, "source": "static", "allow_custom": True,
                "error": _explain_api(res)}

    live: list[dict] = [{"id": "", "label": "Account default"}]
    for m in res["json"].get("models") or []:
        if "generateContent" not in (m.get("supportedGenerationMethods") or []):
            continue          # embedding/token-count models cannot answer a prompt
        mid = str(m.get("name") or "").split("models/")[-1]
        if not mid:
            continue
        live.append({"id": mid, "label": m.get("displayName") or mid})
    if len(live) == 1:                       # nothing usable came back
        return {"models": static, "source": "static", "allow_custom": True}
    _models_cache[ck] = (time.time() + _MODELS_TTL, list(live))
    return {"models": live, "source": "live", "allow_custom": True}


def forget_models(provider: str | None = None) -> None:
    if provider is None:
        _models_cache.clear()
    else:
        for k in [k for k in _models_cache if k.startswith(f"{provider}/")]:
            _models_cache.pop(k, None)


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

    An HTTP provider has no CLI and no credentials directory, so it runs its own
    two checks: a key is stored, and a real completion comes back.
    """
    spec = _spec(provider)
    checks: list[dict] = []

    if spec.get("kind") == KIND_API:
        return _api_capability_test(root, provider, account_id, spec,
                                    model=model, timeout=timeout)

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


def _api_capability_test(root: Path, provider: str, account_id: str, spec: dict, *,
                         model: str | None = None, timeout: int = 120) -> dict:
    key = stored_token(root, provider, account_id)
    checks = [_check("API key stored", bool(key),
                     "key present for this account" if key
                     else f"paste a key to link this account. {spec.get('key_hint', '')}".strip())]
    if not key:
        return {"ok": False, "checks": checks}

    sentinel = "PLUTUS_OK"
    res = api_generate(root, provider, account_id,
                       f"Reply with exactly this word and nothing else: {sentinel}",
                       model=spec.get("test_model", "") if model is None else (model or ""),
                       timeout=timeout)
    got = sentinel in (res["text"] or "")
    checks.append(_check("Can execute prompt", got,
                         f"{res['model']} answered" if got else (res["error"] or res["text"][:200])))
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
