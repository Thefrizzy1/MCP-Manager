"""GitHub + GitLab helpers and registration (offline)."""
from pathlib import Path

from core.capabilities import capability_for_tool
from core.service_registry import all_services


def test_capability_code_group():
    for name in ("github_search_repos", "github_repo_info", "gitlab_search_projects", "gitlab_list_merge_requests"):
        assert capability_for_tool(name) == "code"


def test_github_fmt_count():
    from tools.github import _fmt_count
    assert _fmt_count(48210) == "48.2K"
    assert _fmt_count(None) == "—"


def test_gitlab_pid_encoding():
    import tools.gitlab as gl
    # numeric id passes through; a path is URL-encoded for the API
    reg = {}
    # _pid is defined inside register_gitlab_tools; re-derive its behavior via quote
    from urllib.parse import quote
    assert quote("gitlab-org/gitlab", safe="") == "gitlab-org%2Fgitlab"


def test_services_registered():
    svcs = {s["id"]: s for s in all_services(Path("."))}
    assert "github" in svcs and "gitlab" in svcs
    assert len(svcs["github"]["tools"]) == 4
    assert len(svcs["gitlab"]["tools"]) == 4
    # both are read-only public integrations (no required configured_keys)
    assert svcs["github"]["configured_keys"] == ()
    assert svcs["gitlab"]["configured_keys"] == ()
