"""GitHub as the authenticated account, not a search client.

Without a token this is public research at 60 requests an hour. With one it is the
account's own client and can *change* things, so what these cover is the switch
and the request shapes — a wrong verb or a missing sha does not fail loudly, it
writes the wrong thing to somebody's real repository.
"""
from __future__ import annotations

import asyncio
import base64
import json as _json

import httpx
import pytest

import tools.github as GH
from core.invoke_tool import invoke_mcp_tool_fn

WRITE_TOOLS = ["github_write_file", "github_create_issue", "github_comment",
               "github_set_issue_state", "github_create_branch", "github_create_pull",
               "github_create_repo"]


def _tool(name):
    from mcp.server.fastmcp import FastMCP

    m = FastMCP("t")
    GH.register_github_tools(m)
    return {t.name: t.fn for t in m._tool_manager.list_tools()}[name]


def _run(name, payload):
    return str(asyncio.run(invoke_mcp_tool_fn(_tool(name), payload=payload)))


class _Resp:
    def __init__(self, payload=None, status=200, headers=None, text=""):
        self._payload = payload if payload is not None else {}
        self.status_code = status
        self.headers = headers or {}
        self.text = text or _json.dumps(self._payload)
        self.content = self.text.encode()

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=self)


def _fake_api(monkeypatch, routes, *, token="ghp_test"):
    """Record every call; answer from `routes` keyed by "METHOD /path-prefix"."""
    seen: list[dict] = []
    monkeypatch.setattr(GH.cfg, "github_token", token, raising=False)

    class C:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, method, url, **kw):
            path = url.replace(GH.API, "")
            seen.append({"method": method, "path": path, "json": kw.get("json"),
                         "params": kw.get("params"),
                         "headers": kw.get("headers") or {}})
            for key, resp in routes.items():
                verb, _, prefix = key.partition(" ")
                if method == verb and path.startswith(prefix):
                    return resp
            return _Resp({}, 404)

    monkeypatch.setattr(GH.httpx, "AsyncClient", C)
    return seen


# ── the token gate ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,payload", [
    ("github_me", {}),
    ("github_my_repos", {"limit": 5}),
    ("github_search_code", {"query": "x", "limit": 5}),
    ("github_notifications", {"limit": 5}),
    ("github_write_file", {"repo": "a/b", "path": "f", "content": "c", "message": "m"}),
    ("github_create_issue", {"repo": "a/b", "title": "t"}),
    ("github_comment", {"repo": "a/b", "number": 1, "body": "hi"}),
    ("github_set_issue_state", {"repo": "a/b", "number": 1}),
    ("github_create_branch", {"repo": "a/b", "name": "x"}),
    ("github_create_pull", {"repo": "a/b", "title": "t", "head": "x"}),
    ("github_create_repo", {"name": "r"}),
])
def test_token_only_tools_say_so_instead_of_failing(monkeypatch, name, payload):
    """Without a token these cannot work, so they explain rather than letting
    GitHub answer 401/404 and leaving the agent to guess what went wrong."""
    monkeypatch.setattr(GH.cfg, "github_token", "", raising=False)
    monkeypatch.setattr(
        GH.httpx, "AsyncClient",
        lambda *a, **k: pytest.fail(f"{name} called the API with no token"))
    out = _run(name, payload)
    assert "needs a GitHub token" in out and "Settings" in out


def test_public_reads_still_work_without_a_token(monkeypatch):
    seen = _fake_api(monkeypatch, {"GET /repos/a/b/branches": _Resp(
        [{"name": "main", "commit": {"sha": "abc1234567"}, "protected": True}])},
        token="")
    out = _run("github_list_branches", {"repo": "a/b", "limit": 5})
    assert "`main`" in out and "abc1234" in out and "protected" in out
    assert "Authorization" not in seen[0]["headers"]


def test_a_token_is_sent_as_a_bearer(monkeypatch):
    seen = _fake_api(monkeypatch, {"GET /repos/a/b/branches": _Resp([])})
    _run("github_list_branches", {"repo": "a/b"})
    assert seen[0]["headers"]["Authorization"] == "Bearer ghp_test"


# ── writes send the right request ────────────────────────────────────────────

def test_creating_a_file_sends_no_sha(monkeypatch):
    """A create must not carry a sha — GitHub rejects one that names nothing."""
    seen = _fake_api(monkeypatch, {
        "GET /repos/a/b/contents/new.md": _Resp({}, 404),
        "PUT /repos/a/b/contents/new.md": _Resp(
            {"commit": {"sha": "deadbeef123", "html_url": "https://gh/c"}}),
    })
    out = _run("github_write_file", {"repo": "a/b", "path": "new.md",
                                     "content": "hello", "message": "add"})
    put = [c for c in seen if c["method"] == "PUT"][0]
    assert "sha" not in put["json"]
    assert base64.b64decode(put["json"]["content"]).decode() == "hello"
    assert put["json"]["message"] == "add"
    assert "Created" in out and "deadbee" in out


def test_updating_a_file_carries_the_current_sha(monkeypatch):
    """Without it GitHub answers 409 and the write silently never happens."""
    seen = _fake_api(monkeypatch, {
        "GET /repos/a/b/contents/x.md": _Resp({"sha": "oldsha99", "size": 5}),
        "PUT /repos/a/b/contents/x.md": _Resp({"commit": {"sha": "newsha11"}}),
    })
    out = _run("github_write_file", {"repo": "a/b", "path": "x.md", "content": "v2",
                                     "message": "edit", "branch": "feature"})
    put = [c for c in seen if c["method"] == "PUT"][0]
    assert put["json"]["sha"] == "oldsha99"
    assert put["json"]["branch"] == "feature"
    assert "Updated" in out


def test_an_issue_is_opened_with_labels(monkeypatch):
    seen = _fake_api(monkeypatch, {"POST /repos/a/b/issues": _Resp(
        {"number": 42, "html_url": "https://gh/i/42"})})
    out = _run("github_create_issue", {"repo": "a/b", "title": "Bug",
                                       "body": "detail", "labels": "bug, p1"})
    assert seen[0]["method"] == "POST" and seen[0]["path"] == "/repos/a/b/issues"
    assert seen[0]["json"]["labels"] == ["bug", "p1"]
    assert "#42" in out


def test_a_comment_goes_to_the_issues_endpoint_for_prs_too(monkeypatch):
    """GitHub treats pull requests as issues for comments; the pulls endpoint
    would need a review id nobody has."""
    seen = _fake_api(monkeypatch, {"POST /repos/a/b/issues/7/comments": _Resp(
        {"html_url": "https://gh/c/1"})})
    _run("github_comment", {"repo": "a/b", "number": 7, "body": "looks good"})
    assert seen[0]["path"] == "/repos/a/b/issues/7/comments"
    assert seen[0]["json"] == {"body": "looks good"}


def test_closing_an_issue_uses_patch_with_a_reason(monkeypatch):
    seen = _fake_api(monkeypatch, {"PATCH /repos/a/b/issues/3": _Resp({"html_url": "u"})})
    out = _run("github_set_issue_state", {"repo": "a/b", "number": 3,
                                          "state": "closed", "reason": "not_planned"})
    assert seen[0]["method"] == "PATCH"
    assert seen[0]["json"] == {"state": "closed", "state_reason": "not_planned"}
    assert "now closed" in out


def test_a_branch_starts_from_the_default_when_none_is_given(monkeypatch):
    seen = _fake_api(monkeypatch, {
        "GET /repos/a/b/git/ref/heads/trunk": _Resp({"object": {"sha": "s" * 40}}),
        "GET /repos/a/b": _Resp({"default_branch": "trunk"}),
        "POST /repos/a/b/git/refs": _Resp({}),
    })
    out = _run("github_create_branch", {"repo": "a/b", "name": "feature/x"})
    post = [c for c in seen if c["method"] == "POST"][0]
    assert post["json"]["ref"] == "refs/heads/feature/x"
    assert post["json"]["sha"] == "s" * 40
    assert "from `trunk`" in out


def test_a_pull_request_targets_the_default_branch_by_default(monkeypatch):
    seen = _fake_api(monkeypatch, {
        "GET /repos/a/b": _Resp({"default_branch": "trunk"}),
        "POST /repos/a/b/pulls": _Resp({"number": 9, "html_url": "https://gh/p/9"}),
    })
    out = _run("github_create_pull", {"repo": "a/b", "title": "Fix",
                                      "head": "feature/x", "body": "why"})
    post = [c for c in seen if c["method"] == "POST"][0]
    assert post["json"]["base"] == "trunk" and post["json"]["head"] == "feature/x"
    assert "#9" in out


# ── reading ──────────────────────────────────────────────────────────────────

def test_a_file_is_decoded_from_base64(monkeypatch):
    _fake_api(monkeypatch, {"GET /repos/a/b/contents/r.txt": _Resp({
        "encoding": "base64", "size": 11, "sha": "abc1234def",
        "content": base64.b64encode(b"hello world").decode()})})
    out = _run("github_read_file", {"repo": "a/b", "path": "r.txt"})
    assert "hello world" in out and "abc1234" in out


def test_reading_a_directory_lists_it(monkeypatch):
    _fake_api(monkeypatch, {"GET /repos/a/b/contents/src": _Resp([
        {"name": "main.py", "type": "file"}, {"name": "lib", "type": "dir"}])})
    out = _run("github_read_file", {"repo": "a/b", "path": "src"})
    assert "main.py" in out and "lib" in out and "directory" in out


def test_the_identity_reports_scopes_and_rate_limit(monkeypatch):
    _fake_api(monkeypatch, {
        "GET /user": _Resp({"login": "friso", "name": "F", "public_repos": 12,
                            "followers": 3, "html_url": "u"},
                           headers={"x-oauth-scopes": "repo, workflow"}),
        "GET /rate_limit": _Resp({"resources": {"core": {"remaining": 4999,
                                                         "limit": 5000}}}),
    })
    out = _run("github_me", {})
    assert "friso" in out and "repo, workflow" in out and "4999/5000" in out


def test_a_fine_grained_token_reports_unknown_scopes_not_none(monkeypatch):
    """Fine-grained tokens do not send the header; "none" would be a lie that
    makes a perfectly capable token look useless."""
    _fake_api(monkeypatch, {
        "GET /user": _Resp({"login": "friso"}),
        "GET /rate_limit": _Resp({"resources": {"core": {}}}),
    })
    out = _run("github_me", {})
    assert "not reported" in out and "fine-grained" in out


# ── failures point at the right thing ────────────────────────────────────────

def test_a_403_names_the_scope_not_the_repo(monkeypatch):
    _fake_api(monkeypatch, {"POST /repos/a/b/issues": _Resp({}, 403, text="Forbidden")})
    out = _run("github_create_issue", {"repo": "a/b", "title": "t"})
    assert "scope" in out and "fine-grained" in out


def test_a_401_says_the_token_is_bad(monkeypatch):
    _fake_api(monkeypatch, {"GET /user": _Resp({}, 401)})
    out = _run("github_me", {})
    assert "rejected the token" in out and "reissue" in out


def test_a_404_with_a_token_admits_it_might_be_permissions(monkeypatch):
    """GitHub answers 404, not 403, for a private repo a token cannot see — so
    "not found" is genuinely ambiguous and claiming otherwise misleads."""
    _fake_api(monkeypatch, {"GET /repos/a/b/branches": _Resp({}, 404)})
    out = _run("github_list_branches", {"repo": "a/b"})
    assert "cannot see it" in out and "private repository" in out


def test_rate_limiting_without_a_token_suggests_adding_one(monkeypatch):
    _fake_api(monkeypatch, {"GET /repos/a/b/branches": _Resp(
        {}, 403, text="API rate limit exceeded")}, token="")
    out = _run("github_list_branches", {"repo": "a/b"})
    assert "rate limit" in out and "5000/hr" in out


# ── annotations and safety ───────────────────────────────────────────────────

def test_write_tools_are_annotated_and_smoke_excluded():
    """The agent blast-radius rules and the smoke runner both read these, so a
    write tool claiming to be read-only would be fired at a real repository."""
    from mcp.server.fastmcp import FastMCP

    from core.tool_registry import tool_safety_level
    from tools.github import register_github_tools

    m = FastMCP("t")
    register_github_tools(m)
    tools = {t.name: t for t in m._tool_manager.list_tools()}

    for name in WRITE_TOOLS:
        assert tools[name].annotations.readOnlyHint is False, name
        assert tool_safety_level(name) == 2, name

    for name, t in tools.items():
        if name not in WRITE_TOOLS:
            assert t.annotations.readOnlyHint is True, name
            assert tool_safety_level(name) == 0, name


# ── creating repositories ────────────────────────────────────────────────────

def test_a_new_repo_is_private_by_default(monkeypatch):
    """A repo an agent creates because it was asked to should not be public
    because nobody said otherwise."""
    seen = _fake_api(monkeypatch, {"POST /user/repos": _Resp(
        {"full_name": "friso/notes", "private": True, "default_branch": "main",
         "html_url": "https://gh/r", "clone_url": "https://gh/r.git"})})
    out = _run("github_create_repo", {"name": "notes"})

    assert seen[0]["path"] == "/user/repos"
    assert seen[0]["json"]["private"] is True
    assert seen[0]["json"]["auto_init"] is True
    assert "private repository" in out and "friso/notes" in out


def test_an_org_repo_goes_to_the_org_endpoint(monkeypatch):
    seen = _fake_api(monkeypatch, {"POST /orgs/acme/repos": _Resp(
        {"full_name": "acme/svc", "private": False, "default_branch": "main"})})
    out = _run("github_create_repo", {"name": "svc", "org": "acme", "private": False})
    assert seen[0]["path"] == "/orgs/acme/repos"
    assert "public repository" in out


def test_auto_init_is_reported_because_an_empty_repo_has_no_branch(monkeypatch):
    """Without a default branch, committing a file or opening a PR against the
    new repo fails — so the answer says which case this is."""
    _fake_api(monkeypatch, {"POST /user/repos": _Resp(
        {"full_name": "friso/empty", "private": True, "default_branch": None})})
    out = _run("github_create_repo", {"name": "empty", "auto_init": False})
    assert "none — repo is empty" in out


def test_a_taken_name_says_so(monkeypatch):
    _fake_api(monkeypatch, {"POST /user/repos": _Resp({}, 422, text="name already exists")})
    out = _run("github_create_repo", {"name": "notes"})
    assert "already taken" in out
