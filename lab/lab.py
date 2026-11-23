#!/usr/bin/env python3
"""lab.py — the nightly model lab runner. Standard library only.

Runs on the GPU server. One night = one task: build (or re-check) one ComfyUI
workflow in the house style, test it, have a second fresh session critique it,
open a draft pull request, then delete the models it downloaded.

The runner — not the model — owns every step that reaches outside:

  * pushing and merging use a token that is never in the model's environment
  * merging happens only when Plutus says ``lab.merge_pr`` is allowed, or after
    an approval in the dashboard
  * deleting touches only files inside the lab's own models folder that were
    not there when the run started

    python3 lab.py preflight     check the machine is ready, change nothing
    python3 lab.py nightly       the whole night (what the timer runs)
    python3 lab.py approvals     carry out requests approved in the dashboard
    python3 lab.py beat          report this machine's resources and activity (every few minutes)
    python3 lab.py cleanup DATE  delete what a run downloaded (also runs itself)
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULTS = {
    "plutus_mcp": "http://localhost:8765/mcp",
    "workflows_repo": "/srv/lab/workflows",
    "workflows_remote": "",
    "comfy_dir": "/srv/lab/ComfyUI",
    "comfy_python": "/srv/lab/venv/bin/python",
    "comfy_port": 8198,
    "comfy_args": ["--listen", "127.0.0.1", "--reserve-vram", "12"],
    "models_dir": "/srv/lab/models",
    "inputs_dir": "/srv/lab/inputs",
    "state_dir": "/srv/lab/state",
    "min_free_gb": 60,
    "task_timeout_min": 150,
    "critique_timeout_min": 25,
    "fix_rounds": 2,
    "shot_python": "",          # python with playwright, for real screenshots of the graph; "" = none            # how often a failed critique may send the work back
    "night_hours": 7,           # stop starting new models after this long
    "max_tasks": 20,
    "machine_id": "gpu-server",
    "second_test_machine": "",   # node that repeats the test on the smaller card, e.g. "windows-pc"; "" = none  # how this machine is named on the Overview page
    "hf_repo": "",              # Hugging Face mirror, e.g. "you/your-workflows"; "" = none
    "gpu_lock": ["gpu-lock", "lab"],
    # Succeeds only when the GPU is free; a busy GPU skips the night. [] = no check.
    "gpu_free_check": [],
    "claude_bin": "claude",
    "claude_model": "",
}
# What the model may not run, on top of the unix user's own limits. The real
# boundary is the dedicated `lab` user and the token it never sees.
DENY = ["Bash(sudo:*)", "Bash(systemctl:*)", "Bash(git push:*)", "Bash(gh pr merge:*)",
        "Bash(gh pr ready:*)", "Bash(gh repo:*)", "Bash(shutdown:*)", "Bash(reboot:*)"]
# Plutus tools a build session may call: research and progress only. Everything
# else on the server (mail, posting, files, containers) stays out of reach.
PLUTUS_TOOLS = ("lab_report trend_radar_list trend_radar_candidate trend_radar_signals trend_radar_done "
                "huggingface_search_models huggingface_trending_models huggingface_model_info "
                "github_search_repos github_repo_info github_list_issues github_list_pulls "
                "github_read_file github_search_code reddit_search reddit_subreddit_posts "
                "reddit_post_comments web_search web_fetch web_read exa_search youtube_search").split()
ALLOW = ["Bash", "Read", "Write", "Edit", "Glob", "Grep", "WebFetch", "WebSearch",
         *(f"mcp__plutus__{t}" for t in PLUTUS_TOOLS)]
READONLY = ["Read", "Glob", "Grep", "Bash(ls:*)", "Bash(python3:*)", "Bash(ffprobe:*)",
            "Bash(ffmpeg:*)", "Bash(git diff:*)", "Bash(git log:*)", "Write"]


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    path = Path(os.getenv("LAB_CONFIG", HERE / "config.json"))
    if path.is_file():
        cfg.update(json.loads(path.read_text()))
    return cfg


def log(msg: str) -> None:
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


# ── Plutus over MCP (streamable HTTP), enough to call one tool ───────────────
def plutus(cfg: dict, tool: str, args: dict | None = None, timeout: int = 60) -> str:
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    token = os.getenv("PLUTUS_MCP_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    def post(body: dict, sid: str = ""):
        h = dict(headers, **({"Mcp-Session-Id": sid} if sid else {}))
        req = urllib.request.Request(cfg["plutus_mcp"], json.dumps(body).encode(), h)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = r.read().decode()
            m = re.search(r"\{.*\}", text, re.S)
            return r.headers.get("Mcp-Session-Id", sid), (json.loads(m.group(0)) if m else {})

    sid, _ = post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-03-26", "capabilities": {},
        "clientInfo": {"name": "model-lab", "version": "1"}}})
    post({"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)
    _, res = post({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                   "params": {"name": tool, "arguments": args or {}}}, sid)
    if "error" in res:
        raise RuntimeError(f"{tool}: {res['error']}")
    text = "\n".join(c.get("text", "") for c in res.get("result", {}).get("content", []))
    # A tool that refused its input answers normally but flags the result as an
    # error. Treat that as a failure too, or a rejected report looks like a sent one.
    if res.get("result", {}).get("isError"):
        raise RuntimeError(f"{tool}: {text[:300]}")
    return text


def permission(cfg: dict, action: str) -> str:
    """allow / ask / never, read from Plutus.

    The last answer is cached on disk. With Plutus unreachable the cached setting
    is used, so a network blip neither grants anything new nor stops the lab
    deleting its own downloads (a full disk on a shared server is the worse
    failure). With no cache at all, everything is ``ask``.
    """
    cache = Path(cfg.get("state_dir", ".")) / "permissions_cache.json"
    try:
        text = plutus(cfg, "studio_permissions", timeout=20)
        known = dict(re.findall(r"`([\w.]+)`: \*\*(\w+)\*\*", text))
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(known))
        except OSError:
            pass
        return known.get(action, "never")
    except Exception as e:
        try:
            known = json.loads(cache.read_text())
        except (OSError, ValueError):
            known = {}
        setting = known.get(action, "ask")
        # Never widen on stale data: a cached allow still holds only for clean-up.
        if setting == "allow" and action != "lab.delete_own_models":
            setting = "ask"
        log(f"Plutus unreachable ({e}); treating {action} as {setting}")
        return setting


def tell(cfg: dict, text: str, slug: str = "", status: str = "", pr: str = "", name: str = "",
         review: dict | None = None, server: str = "") -> None:
    """Progress for the Workflow Maker page. Never lets a reporting failure stop the night.

    A report that changes a model's status is the one thing the owner acts on,
    so it is retried for a few minutes: Plutus may just be restarting.
    """
    if review:                                   # keep each field short; never cut the JSON itself
        review = {k: (v[:700] if isinstance(v, str) else v) for k, v in review.items()}
    params = {"slug": slug, "text": text[:390], "status": status, "pr": pr, "name": name,
              "server_test": server if slug else "", "review_json": json.dumps(review) if review else ""}
    tries = 6 if status else 1
    for attempt in range(tries):
        try:
            plutus(cfg, "lab_report", {"params": params}, timeout=20)
            return
        except Exception as e:
            log(f"could not report progress ({attempt + 1}/{tries}): {e}")
            if attempt + 1 < tries:
                time.sleep(30)


def next_assignment(cfg: dict, state: Path) -> dict:
    """What to work on: the Workflow Maker list first, then the local queue file, else auto."""
    try:
        row = json.loads(plutus(cfg, "lab_next", timeout=20) or "{}")
    except Exception as e:
        log(f"could not read the lab list: {e}")
        row = {}
    if row.get("slug"):
        return {"task": "build", "slug": row["slug"], "model": row.get("name") or row["slug"],
                "note": row.get("note", "")}
    queue = read_json(state / "queue.json", [])
    return queue[0] if queue else {"task": "auto"}


# ── models folder: snapshot before, delete only what is new after ────────────
def snapshot(models_dir: Path) -> list[str]:
    if not models_dir.is_dir():
        return []
    return sorted(str(p.relative_to(models_dir)) for p in models_dir.rglob("*") if p.is_file())


def new_files(models_dir: Path, before: list[str]) -> list[Path]:
    """Files under models_dir that were not in the snapshot. Symlinks to shared
    model folders are never followed out of the lab's own tree."""
    base = models_dir.resolve()
    seen = set(before)
    out = []
    for p in models_dir.rglob("*"):
        if not p.is_file() or p.is_symlink():
            continue
        if str(p.relative_to(models_dir)) in seen:
            continue
        if base in p.resolve().parents:
            out.append(p)
    return out


def cleanup(cfg: dict, run_dir: Path) -> dict:
    models = Path(cfg["models_dir"])
    snap = run_dir / "models_before.json"
    if not snap.is_file():
        return {"deleted": 0, "gb": 0.0, "note": "no snapshot, nothing deleted"}
    if permission(cfg, "lab.delete_own_models") != "allow":
        return {"deleted": 0, "gb": 0.0, "note": "deleting is not allowed; files kept"}
    files = new_files(models, json.loads(snap.read_text()))
    size = sum(f.stat().st_size for f in files)
    for f in files:
        f.unlink()
    return {"deleted": len(files), "gb": round(size / 1e9, 1), "note": ""}


# ── checks ───────────────────────────────────────────────────────────────────
def preflight(cfg: dict) -> list[tuple[str, bool, str]]:
    rows: list[tuple[str, bool, str]] = []
    models = Path(cfg["models_dir"])
    probe = models if models.exists() else models.parent if models.parent.exists() else Path("/")
    free = shutil.disk_usage(probe).free / 1e9
    rows.append(("disk", free >= cfg["min_free_gb"],
                 f"{free:.0f} GB free at {probe}, need {cfg['min_free_gb']}"))
    for tool in (cfg["claude_bin"], "git", "gh", "ffmpeg"):
        rows.append((tool, shutil.which(tool) is not None, shutil.which(tool) or "not on PATH"))
    lock = cfg["gpu_lock"][0] if cfg["gpu_lock"] else ""
    if lock:
        rows.append(("gpu lock", shutil.which(lock) is not None, shutil.which(lock) or "not on PATH"))
    rows.append(("workflows_remote", bool(cfg["workflows_remote"]),
                 cfg["workflows_remote"] or "set workflows_remote, e.g. github.com/you/workflows.git"))
    if cfg["gpu_free_check"]:
        try:
            free_now = subprocess.run(cfg["gpu_free_check"], capture_output=True, timeout=30).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            free_now = False
        rows.append(("gpu free", free_now, "free" if free_now else "busy with other work; skipping tonight"))
    for key in ("comfy_dir", "workflows_repo", "inputs_dir"):
        rows.append((key, Path(cfg[key]).is_dir(), cfg[key]))
    manifest = json.loads((HERE / "inputs" / "manifest.json").read_text())
    missing = [i["file"] for i in manifest["inputs"]
               if not (Path(cfg["inputs_dir"]) / i["file"]).is_file()]
    rows.append(("standard inputs", not missing, "all present" if not missing else "missing: " + ", ".join(missing)))
    rows.append(("push token", bool(os.getenv("LAB_GH_TOKEN")), "LAB_GH_TOKEN set" if os.getenv("LAB_GH_TOKEN") else "LAB_GH_TOKEN not set"))
    try:
        plutus(cfg, "studio_permissions", timeout=15)
        rows.append(("plutus", True, cfg["plutus_mcp"]))
    except Exception as e:
        rows.append(("plutus", False, f"{cfg['plutus_mcp']}: {e}"))
    return rows


def print_preflight(rows) -> bool:
    for name, ok, detail in rows:
        print(f"  {'ok  ' if ok else 'FAIL'} {name:<16} {detail}")
    # Missing inputs only limit which models can be tested; everything else blocks.
    return all(ok for name, ok, _ in rows if name != "standard inputs")


# ── sessions ─────────────────────────────────────────────────────────────────
def render(name: str, **values: str) -> str:
    text = (HERE / "prompts" / name).read_text()
    for key, val in values.items():
        text = text.replace("{{" + key + "}}", str(val))
    left = re.findall(r"\{\{(\w+)\}\}", text)
    if left:
        raise ValueError(f"{name}: unfilled {left}")
    return text


def model_env() -> dict:
    """The environment a session runs in: everything except the secrets."""
    return {k: v for k, v in os.environ.items()
            if k not in ("LAB_GH_TOKEN", "GH_TOKEN", "GITHUB_TOKEN", "PLUTUS_MCP_TOKEN")}


class UsageLimit(Exception):
    """The Claude plan's usage window is used up. Not the model's fault: wait, then retry."""

    def __init__(self, message: str):
        super().__init__(message)
        self.resume_at = parse_reset(message)


def parse_reset(message: str, now: dt.datetime | None = None) -> dt.datetime | None:
    """When a 'resets 12:40am' message says work can continue (local time), or None."""
    m = re.search(r"resets?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)", message or "", re.I)
    if not m:
        return None
    now = now or dt.datetime.now()
    hour = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "pm" else 0)
    at = now.replace(hour=hour, minute=int(m.group(2) or 0), second=0, microsecond=0)
    return at if at > now else at + dt.timedelta(days=1)


def session_outcome(out: Path) -> dict:
    """The JSON result a session printed (it may follow a warning line), or {}."""
    try:
        text = out.read_text()
    except OSError:
        return {}
    for start in (0, text.find("\n{") + 1):
        try:
            data = json.loads(text[start:])
            return data if isinstance(data, dict) else {}
        except ValueError:
            continue
    return {}


def check_usage(out: Path) -> None:
    res = session_outcome(out)
    msg = str(res.get("result") or "")
    if res.get("is_error") and (res.get("api_error_status") == 429 or "limit" in msg.lower()):
        raise UsageLimit(msg or "usage limit reached")


def run_session(cfg: dict, prompt: str, cwd: Path, out: Path, *, allowed: list[str],
                timeout_min: int, mcp: Path | None) -> int:
    cmd = [cfg["claude_bin"], "-p", prompt, "--output-format", "json",
           "--permission-mode", "acceptEdits",
           "--allowedTools", ",".join(allowed), "--disallowedTools", ",".join(DENY)]
    if mcp:
        cmd += ["--mcp-config", str(mcp), "--strict-mcp-config"]
    if cfg["claude_model"]:
        cmd += ["--model", cfg["claude_model"]]
    with out.open("w") as fh:
        try:
            return subprocess.run(cmd, cwd=cwd, env=model_env(), stdout=fh,
                                  stderr=subprocess.STDOUT, timeout=timeout_min * 60).returncode
        except subprocess.TimeoutExpired:
            fh.write(f"\n[lab] stopped after {timeout_min} minutes\n")
            return 124


def write_mcp_config(cfg: dict, run_dir: Path) -> Path:
    server: dict = {"type": "http", "url": cfg["plutus_mcp"]}
    if os.getenv("PLUTUS_MCP_TOKEN"):
        server["headers"] = {"Authorization": "Bearer " + os.environ["PLUTUS_MCP_TOKEN"]}
    path = run_dir / "mcp.json"
    path.write_text(json.dumps({"mcpServers": {"plutus": server}}))
    path.chmod(0o600)
    return path


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def git(repo: Path, *args: str, check: bool = True) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()[:300]}")
    return r.stdout.strip()


def gh(repo: Path, *args: str) -> str:
    env = dict(os.environ, GH_TOKEN=os.environ.get("LAB_GH_TOKEN", ""))
    r = subprocess.run(["gh", *args], cwd=repo, env=env, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"gh {' '.join(args[:3])}: {r.stderr.strip()[:300]}")
    return r.stdout.strip()


def start_comfy(cfg: dict, run_dir: Path) -> subprocess.Popen:
    """The lab's own ComfyUI, held under the GPU lock for the length of the run."""
    cmd = [*cfg["gpu_lock"], cfg["comfy_python"], "main.py", "--port", str(cfg["comfy_port"]),
           *cfg["comfy_args"]]
    fh = (run_dir / "comfyui.log").open("w")
    proc = subprocess.Popen(cmd, cwd=cfg["comfy_dir"], stdout=fh, stderr=subprocess.STDOUT,
                            env=model_env(), start_new_session=True)
    # Wait until it answers. Stopping it while the lock wrapper is still setting up
    # would end the wrapper early and leave the GPU marked as taken.
    url = f"http://127.0.0.1:{cfg['comfy_port']}/system_stats"
    for _ in range(150):
        if proc.poll() is not None:
            break
        try:
            urllib.request.urlopen(url, timeout=2)
            break
        except OSError:
            time.sleep(2)
    return proc


def stop_comfy(proc: subprocess.Popen) -> None:
    """Stop ComfyUI but let the lock wrapper finish, so it releases the GPU lock.

    Killing the wrapper itself would orphan ComfyUI with the lock still held:
    the GPU would look busy to everything else until someone noticed.
    """
    import signal
    subprocess.run(["pkill", "-TERM", "-P", str(proc.pid)], capture_output=True)   # the wrapper's child
    try:
        proc.wait(timeout=90)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)       # last resort: the whole group
    except ProcessLookupError:
        pass
    proc.wait(timeout=30)


def union_conflicts(text: str) -> str:
    """Resolve merge conflicts in an index file (the README's tables and tree) by
    keeping the lines of both sides. A row both sides changed (same link) keeps
    this branch's version, which is the newer one."""
    out, i, lines = [], 0, text.split("\n")
    while i < len(lines):
        if not lines[i].startswith("<<<<<<<"):
            out.append(lines[i])
            i += 1
            continue
        mine, theirs = [], []
        i += 1
        side = "mine"
        while i < len(lines) and not lines[i].startswith(">>>>>>>"):
            if lines[i].startswith("======="):
                side = "theirs"
            else:
                (mine if side == "mine" else theirs).append(lines[i])
            i += 1
        i += 1
        key = lambda l: (re.search(r"\]\(([^)]+)\)", l) or re.search(r"(\S+/)\s", l) or [None, l.strip()])[1]
        mine_keys = {key(l) for l in mine}
        out += mine + [l for l in theirs if key(l) not in mine_keys and l not in mine]
    return "\n".join(out)


def merge_main_into(repo: Path, branch: str, token: str, remote: str) -> bool:
    """Bring a pull request branch up to date with main. Conflicts limited to the
    README are resolved by keeping both sides; anything else is left to a person.
    Returns whether the branch was updated and pushed."""
    git(repo, "fetch", "-q", "origin")
    git(repo, "checkout", "-q", "-B", f"merge-{branch}", f"origin/{branch}")
    r = subprocess.run(["git", "-C", str(repo), "-c", "user.name=model-lab", "-c", "user.email=lab@users.noreply.github.com",
                        "merge", "-q", "--no-edit", "origin/main"], capture_output=True, text=True)
    if r.returncode:
        conflicted = git(repo, "diff", "--name-only", "--diff-filter=U", check=False).split()
        if conflicted != ["README.md"]:
            git(repo, "merge", "--abort", check=False)
            git(repo, "checkout", "-q", "main", check=False)
            return False
        readme = repo / "README.md"
        text = union_conflicts(readme.read_text())
        count = sum(1 for _ in repo.glob("workflows/*/*/models.json"))
        text = re.sub(r"badge/workflows-\d+-", f"badge/workflows-{count}-", text)
        readme.write_text(text)
        git(repo, "add", "README.md")
        git(repo, "-c", "user.name=model-lab", "-c", "user.email=lab@users.noreply.github.com",
            "commit", "-q", "--no-edit")
    git(repo, "push", "-q", f"https://x-access-token:{token}@{remote}", f"HEAD:{branch}")
    git(repo, "checkout", "-q", "main", check=False)
    return True


def process_approvals(cfg: dict) -> list[str]:
    """Merge pull requests whose request was approved in the dashboard."""
    done = []
    try:
        text = plutus(cfg, "studio_permissions")
    except Exception as e:
        log(f"approvals skipped: {e}")
        return done
    repo = Path(cfg["workflows_repo"])
    for rid, link in re.findall(r"#(\d+) `lab\.merge_pr` approved: .*?(https://github\.com/\S+/pull/\d+)", text):
        try:
            gh(repo, "pr", "ready", link)
            try:
                gh(repo, "pr", "merge", link, "--squash", "--delete-branch")
            except RuntimeError as e:
                if "conflict" not in str(e).lower():
                    raise
                # Another workflow landed first and both touched the README: bring the
                # branch up to date and try once more.
                branch = json.loads(gh(repo, "pr", "view", link, "--json", "headRefName"))["headRefName"]
                if not merge_main_into(repo, branch, os.environ.get("LAB_GH_TOKEN", ""), cfg["workflows_remote"]):
                    raise RuntimeError(f"{link} conflicts outside the README; it needs a person") from e
                gh(repo, "pr", "merge", link, "--squash", "--delete-branch")
            plutus(cfg, "studio_approval_done", {"params": {"request_id": int(rid)}})
            done.append(link)
            log(f"merged {link}")
            mark_published(cfg, link)
            try:
                where = mirror_to_hf(cfg, repo)
                if where:
                    log(f"mirrored to Hugging Face: {where}")
            except Exception as e:
                log(str(e))
        except Exception as e:
            log(f"could not merge {link}: {e}")
    return done


def template_names(cfg: dict) -> list[str]:
    """Names, titles and model tags of ComfyUI's built-in templates (local ones)."""
    out: list[str] = []
    # Not resolve(): a venv's python is a symlink to the system one, and the
    # templates live in the venv.
    pattern = str(Path(cfg["comfy_python"]).parent.parent / "lib" / "python*" / "site-packages"
                  / "comfyui_workflow_templates*" / "**" / "index.json")
    import glob
    for path in glob.glob(pattern, recursive=True)[:1]:
        try:
            cats = json.loads(Path(path).read_text())
        except (OSError, ValueError):
            continue
        for cat in cats:
            for t in cat.get("templates", []):
                if str(t.get("name", "")).startswith("api_"):
                    continue          # hosted partner models, not something to run locally
                out += [str(t.get("name", "")), str(t.get("title", "")), *map(str, t.get("models") or [])]
    return out


def has_template(names: list[str], assignment: dict) -> bool:
    """Does any built-in template mention this model? Unknown (no index) counts as yes."""
    if not names:
        return True
    squash = lambda x: re.sub(r"[^a-z0-9]", "", x.lower())
    hay = [squash(n) for n in names]
    for cand in (assignment.get("slug", ""), assignment.get("model", "")):
        key = squash(cand)
        for k in (key, re.sub(r"\d+$", "", key)):          # also without a trailing version
            if len(k) >= 4 and any(k in h for h in hay):
                return True
    return False


def run_task(cfg: dict, assignment: dict, run_dir: Path, branch: str) -> dict:
    """One model, start to finish: build, critique, one fix round, PR, clean-up."""
    state = Path(cfg["state_dir"])
    repo = Path(cfg["workflows_repo"])
    run_dir.mkdir(parents=True, exist_ok=True)
    out: dict = {"assignment": assignment, "status": "started"}
    git(repo, "checkout", "-q", "main")
    git(repo, "pull", "-q", "--ff-only")
    git(repo, "checkout", "-q", "-B", branch)
    (run_dir / "models_before.json").write_text(json.dumps(snapshot(Path(cfg["models_dir"]))))
    slug = assignment.get("slug", "")
    (state / "current.json").write_text(json.dumps({"slug": slug, "model": assignment.get("model", "")}))
    tell(cfg, "Lab started on this model", slug, "testing" if slug else "", name=assignment.get("model", ""),
         server="testing")
    (state / "LESSONS.md").touch()
    mcp = write_mcp_config(cfg, run_dir)
    values = dict(
        DATE=dt.date.today().isoformat(), RUN_DIR=run_dir, REPO=repo, MODELS_DIR=cfg["models_dir"],
        INPUTS_DIR=cfg["inputs_dir"], COMFY_URL=f"http://127.0.0.1:{cfg['comfy_port']}",
        COMFY_DIR=cfg["comfy_dir"], STYLE=HERE / "STYLE.md", LESSONS=state / "LESSONS.md",
        MANIFEST=HERE / "inputs" / "manifest.json", ASSIGNMENT=json.dumps(assignment),
        MAX_GB=int(shutil.disk_usage(cfg["models_dir"]).free / 1e9 - 20), ISSUES="none",
        SLUG=slug or "(choose one: lowercase-with-dashes, the Trend Radar slug when it has one)",
        SHOT=(f"{cfg['shot_python']} {HERE / 'shot.py'} http://127.0.0.1:{cfg['comfy_port']}"
              if cfg["shot_python"] else "(no screenshot tool on this machine: say so in not_verified)"),
    )
    comfy = start_comfy(cfg, run_dir)
    try:
        verdict: dict = {}
        for attempt in range(1, int(cfg["fix_rounds"]) + 2):   # build, critique, then the fix rounds
            log(f"build session, attempt {attempt}")
            run_session(cfg, render("build.md", **values), repo, run_dir / f"build{attempt}.log",
                        allowed=ALLOW, timeout_min=cfg["task_timeout_min"], mcp=mcp)
            check_usage(run_dir / f"build{attempt}.log")
            result = read_json(run_dir / "result.json", {})
            if result.get("status") != "built":
                break
            log("critique session")
            # Still under test until the critique agrees, whatever the build session reported.
            tell(cfg, "Built. Now being checked by a second session.", slug or result.get("slug", ""),
                 "testing", server="testing")
            (run_dir / "review.json").unlink(missing_ok=True)
            run_session(cfg, render("critique.md", **values), repo, run_dir / f"critique{attempt}.log",
                        allowed=READONLY, timeout_min=cfg["critique_timeout_min"], mcp=None)
            check_usage(run_dir / f"critique{attempt}.log")
            verdict = read_json(run_dir / "review.json", {"verdict": "fail", "issues": ["no review written"]})
            slug = slug or result.get("slug", "")
            tell(cfg, f"Critique {attempt}: {verdict.get('verdict', 'fail')}. {verdict.get('summary', '')}", slug)
            if verdict.get("verdict") == "pass":
                break
            values["ISSUES"] = json.dumps(verdict.get("issues", []))
            tell(cfg, "Fix round: working through the critique's issues.", slug, "testing", server="testing")
        result = read_json(run_dir / "result.json", {"status": "no_result"})
        out.update(result=result, review=verdict)
        slug = slug or result.get("slug", "")
        out["slug"] = slug

        check = subprocess.run([sys.executable, "scripts/check_repo.py"], cwd=repo,
                               capture_output=True, text=True)
        out["repo_check"] = "ok" if check.returncode == 0 else check.stdout[-1500:]
        changed = bool(git(repo, "status", "--porcelain"))
        good = (result.get("status") == "built" and verdict.get("verdict") == "pass"
                and check.returncode == 0 and changed)
        if good and permission(cfg, "lab.open_pr") == "allow":
            out["pr"] = publish(cfg, repo, branch, result, verdict)
            out["status"] = "pr_opened"
            folder = str(result.get("folder") or "").strip("/")
            samples = sorted(p.name for p in (repo / folder / "samples").glob("*")
                             if p.suffix.lower() in (".webp", ".png", ".jpg", ".mp4", ".webm")) if folder else []
            review = {"repo": cfg["workflows_remote"].removeprefix("github.com/").removesuffix(".git"),
                      "branch": branch, "folder": folder, "samples": samples[:8],
                      "title": result.get("title", ""), "tested": result.get("tested", ""),
                      "summary": result.get("summary", ""), "critique": verdict.get("summary", ""),
                      "not_verified": "; ".join(result.get("not_verified") or [])[:400]}
            tell(cfg, "Workflow built and reviewed. Ready for you to check and publish.", slug, "built",
                 out["pr"], review=review, server="done")
            note = queue_second_test(cfg, run_dir, repo, result, slug)
            if note:
                log(note)
                out["second_test"] = note
            try:
                if json.loads(gh(repo, "pr", "view", out["pr"], "--json", "state"))["state"] == "MERGED":
                    out["status"] = "published"
                    tell(cfg, "Published on GitHub" + (f" and mirrored to {cfg['hf_repo']}" if cfg["hf_repo"] else ""),
                         slug, "published", out["pr"])
            except Exception as e:
                log(f"could not read the PR state: {e}")
        else:
            out["status"] = "nothing_to_publish" if not changed else "failed_review"
            waiting = result.get("status") in ("waiting_for_core", "needs_input")
            why = (result.get("reason") or (check.returncode and "repo check failed: " + out["repo_check"][-200:])
                   or verdict.get("summary") or result.get("summary") or "No workflow came out of this run.")
            tell(cfg, str(why), slug, "waiting" if waiting else "failed", server="" if waiting else "failed")
            git(repo, "stash", "-u", "-m", f"lab {branch} unpublished", check=False)
    except UsageLimit:
        git(repo, "stash", "-u", "-m", f"lab {branch} paused at the usage limit", check=False)
        raise
    finally:
        (state / "current.json").unlink(missing_ok=True)
        stop_comfy(comfy)
        out["cleanup"] = cleanup(cfg, run_dir)
        git(repo, "checkout", "-q", "main", check=False)
    return out


def machine_specs(cfg: dict) -> dict:
    """This machine's resources, read with standard tools. Missing tools just leave gaps."""
    specs: dict = {"cores": os.cpu_count() or 0}
    try:
        with open("/proc/meminfo") as fh:
            specs["ram_gb"] = round(int(next(l for l in fh if l.startswith("MemTotal:")).split()[1]) / 1e6)
    except (OSError, StopIteration, ValueError):
        pass
    try:
        du = shutil.disk_usage(cfg["models_dir"] if Path(cfg["models_dir"]).exists() else "/")
        specs.update(disk_free_gb=round(du.free / 1e9), disk_total_gb=round(du.total / 1e9))
    except OSError:
        pass
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used,utilization.gpu",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=15).stdout
        name, total, used, util = [x.strip() for x in out.strip().splitlines()[0].split(",")]
        specs.update(gpu=name.replace("NVIDIA ", ""), vram_gb=round(int(total) / 1024),
                     vram_used_gb=round(int(used) / 1024, 1), gpu_util=int(util))
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        pass
    return specs


def beat(cfg: dict) -> None:
    """Report this machine to the Overview page: resources, current work, what is next."""
    state = Path(cfg["state_dir"])
    cur = read_json(state / "current.json", {})
    running = subprocess.run(["pgrep", "-f", "[l]ab.py nightly"], capture_output=True).returncode == 0
    doing = f"Model lab: {cur.get('model') or cur.get('slug') or 'working'}" if running and cur else \
        "Model lab: between models" if running else ""
    nxt = ""
    try:
        rows = json.loads(plutus(cfg, "lab_list", timeout=20) or "[]")
        queued = sum(1 for r in rows if r.get("status") == "queued")
        nxt = f"{queued} model(s) queued for the lab" if queued else "Nothing queued"
    except Exception:
        pass
    plutus(cfg, "machine_report", {"params": {"machine": cfg["machine_id"],
                                              "specs_json": json.dumps(machine_specs(cfg)),
                                              "doing": doing or " ", "next_up": nxt}}, timeout=20)


def queue_second_test(cfg: dict, run_dir: Path, repo: Path, result: dict, slug: str) -> str:
    """Hand the same test to the second machine's node. Returns a note for the log."""
    if not cfg["second_test_machine"] or not slug:
        return ""
    prompts = []
    for f in sorted((run_dir / "final").glob("*.api.json"))[:6]:
        try:
            prompts.append({"name": f.name.removesuffix(".api.json"), "prompt": json.loads(f.read_text())})
        except (OSError, ValueError):
            continue
    if not prompts:
        return "no final/*.api.json prompts were saved, so the second test was not queued"
    models = read_json(repo / str(result.get("folder") or "") / "models.json", {}).get("files") or []
    blob = json.dumps(prompts)
    try:
        names = [p.name for p in Path(cfg["inputs_dir"]).iterdir() if p.is_file()]
    except OSError:
        names = []
    inputs = [n for n in names if json.dumps(n)[1:-1] in blob]
    payload = {"slug": slug, "prompts": prompts, "inputs": inputs,
               "models": [{k: m.get(k) for k in ("name", "url", "dest", "required")} for m in models if m.get("url")]}
    try:
        plutus(cfg, "node_queue_job", {"params": {"machine": cfg["second_test_machine"], "kind": "comfy_test",
                                                  "payload_json": json.dumps(payload), "ref": f"test-{slug}"}},
               timeout=60)
    except Exception as e:
        return f"could not queue the second test: {e}"
    return f"second test queued for {cfg['second_test_machine']} ({len(prompts)} prompt(s))"


def mark_published(cfg: dict, pr: str) -> None:
    """Tell the board which model a merged pull request belonged to."""
    try:
        rows = json.loads(plutus(cfg, "lab_list", timeout=20) or "[]")
    except Exception as e:
        log(f"could not read the lab list: {e}")
        return
    for row in rows:
        if row.get("pr") == pr:
            tell(cfg, "Published on GitHub" + (f" and mirrored to {cfg['hf_repo']}" if cfg["hf_repo"] else ""),
                 row["slug"], "published", pr)


def nightly(cfg: dict) -> int:
    """Work through the list until it is empty, the night is over, or the machine is not ready."""
    today = dt.date.today().isoformat()
    state = Path(cfg["state_dir"])
    night_dir = state / "runs" / today
    night_dir.mkdir(parents=True, exist_ok=True)
    deadline = dt.datetime.now() + dt.timedelta(hours=cfg["night_hours"])
    report: dict = {"date": today, "status": "started", "merged": process_approvals(cfg), "tasks": []}
    seen: set[str] = set()
    templates = template_names(cfg)
    built = lambda: sum(1 for t in report["tasks"] if t.get("status") != "no_template")
    while built() < cfg["max_tasks"] and len(report["tasks"]) < 500 and dt.datetime.now() < deadline:
        if report["tasks"]:                      # between models: carry out anything approved meanwhile
            report["merged"] += process_approvals(cfg)
        rows = preflight(cfg)
        report["preflight"] = [{"check": n, "ok": ok, "detail": d} for n, ok, d in rows]
        if not print_preflight(rows):
            if not report["tasks"]:
                report["status"] = "blocked"
            tell(cfg, "Stopped: " + "; ".join(d for n, ok, d in rows if not ok and n != "standard inputs"))
            break
        assignment = next_assignment(cfg, state)
        key = assignment.get("slug") or assignment.get("model") or "auto"
        if key in seen:
            # The same model came back, so its outcome never reached the board. Say it
            # again and ask once more before giving up, rather than ending the night.
            last = next((t for t in reversed(report["tasks"]) if t.get("slug") == key), None)
            if last and not last.get("resent"):
                last["resent"] = True
                status = {"pr_opened": "built", "published": "published"}.get(last.get("status"), "failed")
                tell(cfg, "Result of the last run, sent again.", key, status, last.get("pr", ""),
                     server="done" if status != "failed" else "failed")
                seen.discard(key)
                continue
            break
        seen.add(key)
        if assignment.get("slug") and not assignment.get("note", "").lower().startswith("resume") \
                and not has_template(templates, assignment):
            # Not something this ComfyUI can run yet (or not an image/video/audio model at all).
            tell(cfg, "No built-in ComfyUI template mentions this model yet, so there is nothing to build on.",
                 assignment["slug"], "waiting")
            report["tasks"].append({"assignment": assignment, "status": "no_template", "slug": assignment["slug"]})
            continue
        n = len(report["tasks"]) + 1
        safe = re.sub(r"[^a-z0-9.-]+", "-", key.lower()).strip("-") or "auto"
        log(f"task {n}: {key}")
        try:
            task = run_task(cfg, assignment, night_dir / f"{n:02d}-{safe}", f"lab/{today}-{safe}")
        except UsageLimit as e:
            # The plan's usage window is empty. The model goes back on the list untouched;
            # wait for the reset if the night is long enough, otherwise stop here.
            slug = assignment.get("slug", "")
            resume = e.resume_at
            can_wait = resume is not None and resume + dt.timedelta(minutes=3) < deadline
            when = f"{resume:%H:%M}" if resume else "later"
            tell(cfg, f"Paused: Claude usage limit reached, continues {when}." if can_wait
                 else f"Stopped for tonight: Claude usage limit reached (resets {when}).",
                 slug, "queued" if slug else "", server="queued" if slug else "")
            log(f"usage limit: {e}")
            report["usage_limit"] = str(e)[:200]
            if not can_wait:
                break
            seen.discard(key)
            time.sleep(max(60, (resume - dt.datetime.now()).total_seconds() + 180))
            continue
        except Exception as e:     # one broken model must not end the night
            task = {"assignment": assignment, "status": "crashed", "error": str(e)[:300]}
            tell(cfg, f"Crashed: {e}", assignment.get("slug", ""), "failed" if assignment.get("slug") else "")
        report["tasks"].append(task)
        if not assignment.get("slug"):
            queue = read_json(state / "queue.json", [])
            if queue and assignment == queue[0]:
                (state / "queue.json").write_text(json.dumps(queue[1:], indent=1))
            elif assignment.get("task") == "auto":
                break              # nothing was asked for: one pick of its own per night
    if report["status"] != "blocked":
        report["status"] = "done"
    return finish(cfg, night_dir, report)


def publish(cfg: dict, repo: Path, branch: str, result: dict, verdict: dict) -> str:
    title = result.get("title") or f"Lab {branch}"
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=model-lab", "-c", "user.email=lab@users.noreply.github.com",
        "commit", "-q", "-m", f"lab: {title}")
    token = os.environ["LAB_GH_TOKEN"]
    git(repo, "push", "-q", "--force", f"https://x-access-token:{token}@{cfg['workflows_remote']}",
        f"{branch}:{branch}")
    body = (f"{result.get('summary', '')}\n\n**Tested:** {result.get('tested', 'see run log')}\n\n"
            f"**Critique:** {verdict.get('summary', 'passed')}\n\n"
            "Built by the nightly model lab. Draft until approved in Plutus → Permissions.")
    url = gh(repo, "pr", "create", "--draft", "--head", branch, "--base", "main",
             "--title", f"lab: {title}", "--body", body)
    try:
        answer = plutus(cfg, "studio_request_approval", {"params": {
            "action": "lab.merge_pr", "summary": f"Publish workflow: {title}", "link": url, "ref": url}})
        if "**approved**" in answer:
            process_approvals(cfg)
    except Exception as e:
        log(f"approval request failed: {e}")
    return url


def mirror_to_hf(cfg: dict, repo: Path) -> str:
    """Copy the published workflows folder to the Hugging Face mirror. '' when not configured."""
    if not cfg["hf_repo"]:
        return ""
    git(repo, "checkout", "-q", "main")
    git(repo, "pull", "-q", "--ff-only")
    code = ("import sys; from huggingface_hub import HfApi; "
            "HfApi().upload_folder(repo_id=sys.argv[1], folder_path='workflows', path_in_repo='workflows', "
            "repo_type='model', commit_message='Sync workflows from GitHub')")
    env = dict(os.environ)
    if os.getenv("LAB_HF_TOKEN"):
        env["HF_TOKEN"] = os.environ["LAB_HF_TOKEN"]
    r = subprocess.run([cfg["comfy_python"], "-c", code, cfg["hf_repo"]], cwd=repo, env=env,
                       capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError("Hugging Face mirror: " + r.stderr.strip()[-300:])
    return cfg["hf_repo"]


def finish(cfg: dict, run_dir: Path, report: dict) -> int:
    (run_dir / "report.json").write_text(json.dumps(report, indent=1, default=str))
    tasks = report.get("tasks", [])
    opened = [t for t in tasks if t.get("status") in ("pr_opened", "published")]
    if report["status"] == "blocked":
        line = "Model lab did not run: " + "; ".join(
            r["detail"] for r in report.get("preflight", []) if not r["ok"] and r["check"] != "standard inputs")
    else:
        line = (f"Model lab: {len(tasks)} model(s) worked on, {len(opened)} workflow(s) built"
                + (f", {len(report.get('merged') or [])} published" if report.get("merged") else ""))
    log(line)
    try:
        plutus(cfg, "jobs_log", {"params": {"text": line[:480], "who": "model lab"}})
    except Exception as e:
        log(f"could not log to Plutus: {e}")
    return 0 if report["status"] == "done" else 1


def main() -> int:
    cfg = load_config()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "preflight"
    if cmd == "preflight":
        return 0 if print_preflight(preflight(cfg)) else 1
    if cmd == "nightly":
        return nightly(cfg)
    if cmd == "beat":
        beat(cfg)
        return 0
    if cmd == "approvals":
        print(process_approvals(cfg))
        return 0
    if cmd == "cleanup" and len(sys.argv) > 2:
        print(cleanup(cfg, Path(cfg["state_dir"]) / "runs" / sys.argv[2]))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
