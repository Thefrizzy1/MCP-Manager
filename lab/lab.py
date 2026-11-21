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
ALLOW = ["Bash", "Read", "Write", "Edit", "Glob", "Grep", "WebFetch", "WebSearch", "mcp__plutus"]
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
    return "\n".join(c.get("text", "") for c in res.get("result", {}).get("content", []))


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
    return subprocess.Popen(cmd, cwd=cfg["comfy_dir"], stdout=fh, stderr=subprocess.STDOUT,
                            env=model_env())


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
            gh(repo, "pr", "merge", link, "--squash", "--delete-branch")
            plutus(cfg, "studio_approval_done", {"params": {"request_id": int(rid)}})
            done.append(link)
            log(f"merged {link}")
        except Exception as e:
            log(f"could not merge {link}: {e}")
    return done


def nightly(cfg: dict) -> int:
    today = dt.date.today().isoformat()
    state = Path(cfg["state_dir"])
    run_dir = state / "runs" / today
    run_dir.mkdir(parents=True, exist_ok=True)
    repo = Path(cfg["workflows_repo"])
    report: dict = {"date": today, "status": "started", "merged": process_approvals(cfg)}

    rows = preflight(cfg)
    report["preflight"] = [{"check": n, "ok": ok, "detail": d} for n, ok, d in rows]
    if not print_preflight(rows):
        report["status"] = "blocked"
        return finish(cfg, run_dir, report)

    branch = f"lab/{today}"
    git(repo, "checkout", "-q", "main")
    git(repo, "pull", "-q", "--ff-only")
    git(repo, "checkout", "-q", "-B", branch)
    (run_dir / "models_before.json").write_text(json.dumps(snapshot(Path(cfg["models_dir"]))))
    queue = read_json(state / "queue.json", [])
    assignment = queue[0] if queue else {"task": "auto"}
    (state / "LESSONS.md").touch()
    mcp = write_mcp_config(cfg, run_dir)
    values = dict(
        DATE=today, RUN_DIR=run_dir, REPO=repo, MODELS_DIR=cfg["models_dir"],
        INPUTS_DIR=cfg["inputs_dir"], COMFY_URL=f"http://127.0.0.1:{cfg['comfy_port']}",
        COMFY_DIR=cfg["comfy_dir"], STYLE=HERE / "STYLE.md", LESSONS=state / "LESSONS.md",
        MANIFEST=HERE / "inputs" / "manifest.json", ASSIGNMENT=json.dumps(assignment),
        MAX_GB=int(shutil.disk_usage(cfg["models_dir"]).free / 1e9 - 20), ISSUES="none",
    )
    comfy = start_comfy(cfg, run_dir)
    try:
        verdict: dict = {}
        for attempt in (1, 2):                     # build, critique, one fix round
            log(f"build session, attempt {attempt}")
            run_session(cfg, render("build.md", **values), repo, run_dir / f"build{attempt}.log",
                        allowed=ALLOW, timeout_min=cfg["task_timeout_min"], mcp=mcp)
            result = read_json(run_dir / "result.json", {})
            if result.get("status") != "built":
                break
            log("critique session")
            (run_dir / "review.json").unlink(missing_ok=True)
            run_session(cfg, render("critique.md", **values), repo, run_dir / f"critique{attempt}.log",
                        allowed=READONLY, timeout_min=cfg["critique_timeout_min"], mcp=None)
            verdict = read_json(run_dir / "review.json", {"verdict": "fail", "issues": ["no review written"]})
            if verdict.get("verdict") == "pass":
                break
            values["ISSUES"] = json.dumps(verdict.get("issues", []))
        result = read_json(run_dir / "result.json", {"status": "no_result"})
        report.update(result=result, review=verdict)

        check = subprocess.run([sys.executable, "scripts/check_repo.py"], cwd=repo,
                               capture_output=True, text=True)
        report["repo_check"] = "ok" if check.returncode == 0 else check.stdout[-1500:]
        changed = bool(git(repo, "status", "--porcelain"))
        good = (result.get("status") == "built" and verdict.get("verdict") == "pass"
                and check.returncode == 0 and changed)
        if good and permission(cfg, "lab.open_pr") == "allow":
            report["pr"] = publish(cfg, repo, branch, result, verdict)
            if queue:
                (state / "queue.json").write_text(json.dumps(queue[1:], indent=1))
            report["status"] = "pr_opened"
        else:
            report["status"] = "nothing_to_publish" if not changed else "failed_review"
            git(repo, "stash", "-u", "-m", f"lab {today} unpublished", check=False)
    finally:
        comfy.terminate()
        try:
            comfy.wait(timeout=60)
        except subprocess.TimeoutExpired:
            comfy.kill()
        report["cleanup"] = cleanup(cfg, run_dir)
        git(repo, "checkout", "-q", "main", check=False)
    return finish(cfg, run_dir, report)


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


def finish(cfg: dict, run_dir: Path, report: dict) -> int:
    (run_dir / "report.json").write_text(json.dumps(report, indent=1, default=str))
    title = (report.get("result") or {}).get("title", "")
    line = {"pr_opened": f"Model lab opened a PR: {title} {report.get('pr', '')}",
            "blocked": "Model lab did not run: " + "; ".join(
                r["detail"] for r in report.get("preflight", []) if not r["ok"] and r["check"] != "standard inputs"),
            "failed_review": f"Model lab built {title or 'a workflow'} but it failed review",
            "nothing_to_publish": "Model lab ran and found nothing to publish"}.get(report["status"], report["status"])
    log(line)
    try:
        plutus(cfg, "jobs_log", {"params": {"text": line[:480], "who": "model lab"}})
    except Exception as e:
        log(f"could not log to Plutus: {e}")
    return 0 if report["status"] in ("pr_opened", "nothing_to_publish") else 1


def main() -> int:
    cfg = load_config()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "preflight"
    if cmd == "preflight":
        return 0 if print_preflight(preflight(cfg)) else 1
    if cmd == "nightly":
        return nightly(cfg)
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
