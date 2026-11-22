#!/usr/bin/env python3
"""workers/workflow_manager.py — the Workflow Manager's daily check. No AI session.

Pulls the workflows repo, runs its own consistency and link check, lists open
pull requests, and compares the lab's ComfyUI with the newest release. What it
finds goes to the Overview (worker `workflow_manager`) and the activity log.
A new ComfyUI release is reported so re-tests can be lined up.
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("lab", HERE.parent / "lab" / "lab.py")
lab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lab)


def comfy_versions(cfg: dict) -> tuple[str, str]:
    """(installed, newest release) of ComfyUI; '' where it cannot be read."""
    installed = ""
    try:
        text = (Path(cfg["comfy_dir"]) / "comfyui_version.py").read_text()
        installed = re.search(r'"([\d.]+)"', text).group(1)
    except (OSError, AttributeError):
        pass
    newest = ""
    try:
        req = urllib.request.Request("https://api.github.com/repos/comfyanonymous/ComfyUI/releases/latest",
                                     headers={"User-Agent": "workflow-manager"})
        with urllib.request.urlopen(req, timeout=20) as r:
            newest = json.loads(r.read().decode()).get("tag_name", "").lstrip("v")
    except Exception:
        pass
    return installed, newest


def newer(a: str, b: str) -> bool:
    """Is version a newer than b?"""
    num = lambda v: [int(x) for x in re.findall(r"\d+", v)]
    return bool(a and b) and num(a) > num(b)


def own_copy(cfg: dict) -> Path:
    """This worker's own checkout of the workflows repo.

    Never the lab's working folder: the lab may be in the middle of a model on a
    branch with unsaved work, and switching branches under it would lose that.
    """
    repo = Path(cfg["state_dir"]) / "workers" / "workflow_manager" / "repo"
    if not (repo / ".git").is_dir():
        repo.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "-q", "https://" + cfg["workflows_remote"], str(repo)], check=True)
    lab.git(repo, "checkout", "-q", "main")
    lab.git(repo, "pull", "-q", "--ff-only")
    return repo


def check(cfg: dict) -> dict:
    out: dict = {"problems": [], "prs": [], "comfy": {}}
    try:
        repo = own_copy(cfg)
    except (RuntimeError, subprocess.CalledProcessError) as e:
        out["problems"].append(f"could not update the repo: {e}")
        return out
    r = subprocess.run([sys.executable, "scripts/check_repo.py", "--links"], cwd=repo,
                       capture_output=True, text=True, timeout=1800)
    if r.returncode:
        lines = [l.strip() for l in r.stdout.splitlines() if l.strip()]
        out["problems"] += [l for l in lines if not l.lower().startswith(("checked", "ok"))][-25:]
    try:
        prs = json.loads(lab.gh(repo, "pr", "list", "--state", "open", "--json", "number,title,isDraft,createdAt,url"))
        out["prs"] = [{"n": p["number"], "title": p["title"], "draft": p["isDraft"], "url": p["url"]} for p in prs]
    except Exception as e:
        out["problems"].append(f"could not list pull requests: {e}")
    installed, newest = comfy_versions(cfg)
    out["comfy"] = {"installed": installed, "newest": newest, "update": newer(newest, installed)}
    return out


def main() -> int:
    cfg = lab.load_config()

    def say(text: str, busy: bool) -> None:
        try:
            lab.plutus(cfg, "worker_report", {"params": {"worker": "workflow_manager", "text": text[:290], "busy": busy}},
                       timeout=20)
        except Exception as e:
            lab.log(f"could not report: {e}")

    say("Checking links, pull requests and the ComfyUI version", True)
    res = check(cfg)
    state = Path(cfg["state_dir"]) / "workers" / "workflow_manager"
    state.mkdir(parents=True, exist_ok=True)
    (state / "last_check.json").write_text(json.dumps(res, indent=1))
    parts = []
    parts.append(f"{len(res['problems'])} problem(s) in the repo" if res["problems"] else "repo and links are fine")
    if res["prs"]:
        parts.append(f"{len(res['prs'])} open pull request(s)")
    c = res["comfy"]
    if c.get("update"):
        parts.append(f"ComfyUI {c['newest']} is out (lab runs {c['installed']}): re-tests needed")
    line = "Workflow check: " + "; ".join(parts) + "."
    if res["problems"]:
        line += " First: " + res["problems"][0][:120]
    say(line, False)
    try:
        lab.plutus(cfg, "jobs_log", {"params": {"text": line[:480], "who": "workflow manager"}})
        if res["problems"] or c.get("update"):
            body = "\n".join(["## Problems"] + [f"- {p}" for p in res["problems"]] +
                             ["", "## Open pull requests"] + [f"- #{p['n']} {p['title']} {p['url']}" for p in res["prs"]] +
                             ["", f"## ComfyUI\ninstalled {c.get('installed')}, newest {c.get('newest')}"])
            lab.plutus(cfg, "db_write_note", {"params": {"title": "Workflow check " + lab.dt.date.today().isoformat(),
                                                         "body": body[:20000], "tags": "workflows,check"}})
    except Exception as e:
        lab.log(f"could not log: {e}")
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
