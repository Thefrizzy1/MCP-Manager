"""Workers: what each may touch, and the workflow manager's version logic."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


run = _load("workers_run", "workers/run.py")
wm = _load("workflow_manager", "workers/workflow_manager.py")


def test_every_worker_tool_exists_and_none_can_send_or_delete():
    from ui.runtime import tools
    served = set(tools.tool_names())
    for worker, names in run.TOOLS.items():
        assert set(names) <= served, (worker, sorted(set(names) - served))
        assert (ROOT / "workers" / "prompts" / f"{worker}.md").is_file()
    mail = [t for t in run.TOOLS["manager"] if t.startswith("proton_") or t == "send_email"]
    assert mail and all(("read" in t or "list" in t or "search" in t or "status" in t) for t in mail)
    # Nothing that posts, sends, deletes or reaches the machines themselves.
    forbidden_names = {"send_email", "proton_send_draft", "reddit_comment", "reddit_submit", "github_comment",
                       "github_write_file", "db_delete_note"}
    forbidden_parts = ("delete", "ssh_", "docker_", "fs_", "smb_", "move_messages", "set_flags")
    for worker, names in run.TOOLS.items():
        bad = [t for t in names if t in forbidden_names or any(f in t for f in forbidden_parts)]
        assert bad == [], (worker, bad)


def test_version_comparison():
    assert wm.newer("0.46.0", "0.45.3") and wm.newer("0.45.10", "0.45.3")
    assert not wm.newer("0.45.3", "0.45.3") and not wm.newer("", "0.45.3") and not wm.newer("0.45.3", "")


def test_the_workflow_manager_never_works_in_the_labs_folder(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(wm.lab, "git", lambda repo, *a, **k: calls.append((str(repo), a)) or "")
    monkeypatch.setattr(wm.subprocess, "run", lambda *a, **k: calls.append(("clone", a[0])) or None)
    cfg = {"state_dir": str(tmp_path / "state"), "workflows_repo": str(tmp_path / "lab-working-folder"),
           "workflows_remote": "github.com/o/r.git"}
    repo = wm.own_copy(cfg)
    assert str(repo).startswith(str(tmp_path / "state")) and "lab-working-folder" not in str(repo)
    assert all("lab-working-folder" not in c[0] for c in calls)
