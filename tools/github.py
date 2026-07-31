"""
tools/github.py — GitHub as the authenticated account, not a search client.

Without a token this is public research: search repositories, inspect one, list
its issues and pull requests, at 60 requests an hour.

With ``GITHUB_TOKEN`` it becomes the account's own working identity — private
repos, code search, Actions runs, notifications, and the ability to *change*
things: commit a file, open an issue or a pull request, comment, close, branch.
Everything is bounded by the token's own scopes, which is the right place for
that boundary to live: GitHub already enforces it, and an operator who wants an
agent that can only read simply issues a read-only token.

Write tools are registered in core.tool_registry.TOOL_SAFETY_LEVELS so the smoke
runner does not fire them at a real repository.
"""
import base64

import httpx
from pydantic import BaseModel, ConfigDict, Field
from mcp.server.fastmcp import FastMCP

from config import cfg
from client import TIMEOUT, _handle_error

API = "https://api.github.com"

# Scope names that appear in a 403 when the token is simply not allowed to do
# what was asked. Worth naming, because "403" alone sends people to the wrong
# place — usually to check the repo rather than the token.
_SCOPE_HINT = ("The token is missing the scope this needs. A classic token wants "
               "`repo` (and `workflow` for Actions); a fine-grained one wants "
               "Contents/Issues/Pull requests read-write on the repository.")


def _fmt_count(v) -> str:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return str(v or "—")
    for unit, size in (("B", 1_000_000_000), ("M", 1_000_000), ("K", 1_000)):
        if n >= size:
            return f"{n / size:.1f}{unit}".replace(".0", "")
    return str(n)


def github_token() -> str:
    return (getattr(cfg, "github_token", "") or "").strip()


def github_authenticated() -> bool:
    return bool(github_token())


NEEDS_TOKEN = ("Error: this needs a GitHub token. Add GITHUB_TOKEN in "
               "Settings → GitHub (a classic token with `repo`, or a fine-grained "
               "token scoped to the repositories the agent should reach).")


def _headers() -> dict:
    headers = {"User-Agent": "PlutusMCP/1.0", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    token = github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def _request(method: str, path: str, *, params: dict | None = None,
                   json: dict | None = None) -> object:
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        r = await client.request(method, f"{API}{path}", params=params or {},
                                 json=json, headers=_headers())
        r.raise_for_status()
        if r.status_code == 204 or not r.content:
            return {}
        return r.json()


async def _get(path: str, params: dict | None = None) -> object:
    return await _request("GET", path, params=params)


async def _raw(method: str, path: str, *, params: dict | None = None,
               json: dict | None = None) -> httpx.Response:
    """Like _request but hands back the response, for headers and status codes."""
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        return await client.request(method, f"{API}{path}", params=params or {},
                                    json=json, headers=_headers())


def _explain(e: Exception, what: str) -> str:
    """GitHub's own words, plus the thing the status code usually means here."""
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        if code == 401:
            return ("Error: GitHub rejected the token (401). It is expired, revoked, "
                    "or mistyped — reissue it in Settings → GitHub.")
        if code == 403:
            body = (e.response.text or "").lower()
            if "rate limit" in body:
                return ("Error: GitHub rate limit reached. Unauthenticated is 60/hr; "
                        "adding a token raises it to 5000/hr."
                        if not github_authenticated() else
                        "Error: GitHub rate limit reached — try again shortly.")
            return f"Error: GitHub refused this (403). {_SCOPE_HINT}"
        if code == 404 and github_authenticated():
            # GitHub answers 404 rather than 403 for a private repo the token
            # cannot see, so "not found" is genuinely ambiguous here.
            return (f"Error: {what} not found — or the token cannot see it. "
                    "A private repository looks identical to a missing one.")
        if code == 422:
            return f"Error: GitHub rejected the request (422): {e.response.text[:200]}"
    return _handle_error(e, f"GitHub {what}")


def _repo(value: str) -> str:
    return (value or "").strip().strip("/")


def register_github_tools(mcp: FastMCP, *, allow: "set[str] | None" = None):
    from core.profiles import tool_filter
    mcp = tool_filter(mcp, allow)

    class SearchInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        query: str = Field(..., description="Search terms (GitHub search syntax ok)", min_length=1, max_length=256)
        sort: str = Field(default="stars", description="stars | forks | updated")
        limit: int = Field(default=10, description="Max results (1–25)", ge=1, le=25)

    @mcp.tool(name="github_search_repos", annotations={"readOnlyHint": True})
    async def github_search_repos(params: SearchInput) -> str:
        """Search GitHub repositories by keyword, sorted by stars/forks/updated."""
        try:
            data = await _get("/search/repositories", {"q": params.query, "sort": params.sort, "per_page": params.limit})
            items = (data or {}).get("items") or [] if isinstance(data, dict) else []
            if not items:
                return f"## GitHub repos: '{params.query}'\n\nNo repositories found."
            lines = [f"## GitHub repos: '{params.query}'\n"]
            for r in items:
                lines.append(
                    f"- **{r.get('full_name', '')}** — ★{_fmt_count(r.get('stargazers_count'))} "
                    f"· {r.get('language') or '—'}\n  {(r.get('description') or '').strip()[:120]}\n  {r.get('html_url', '')}"
                )
            return "\n".join(lines)
        except Exception as e:
            return _handle_error(e, "GitHub search")

    class RepoInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        repo: str = Field(..., description="owner/repo, e.g. comfyanonymous/ComfyUI", min_length=3, max_length=140)

    @mcp.tool(name="github_repo_info", annotations={"readOnlyHint": True})
    async def github_repo_info(params: RepoInput) -> str:
        """Repository details — stars, forks, open issues, language, last push."""
        try:
            r = await _get(f"/repos/{params.repo.strip('/')}")
            if not isinstance(r, dict) or not r.get("full_name"):
                return f"No repo found for '{params.repo}'."
            return (
                f"## {r.get('full_name')}\n\n"
                f"{(r.get('description') or '').strip()}\n\n"
                f"**Stars:** {_fmt_count(r.get('stargazers_count'))}\n"
                f"**Forks:** {_fmt_count(r.get('forks_count'))}\n"
                f"**Open issues:** {_fmt_count(r.get('open_issues_count'))}\n"
                f"**Language:** {r.get('language') or '—'}\n"
                f"**License:** {(r.get('license') or {}).get('spdx_id') or '—'}\n"
                f"**Updated:** {(r.get('pushed_at') or '')[:10]}\n"
                f"**Link:** {r.get('html_url', '')}"
            )
        except Exception as e:
            return _handle_error(e, "GitHub repo")

    class IssuesInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        repo: str = Field(..., description="owner/repo", min_length=3, max_length=140)
        state: str = Field(default="open", description="open | closed | all")
        limit: int = Field(default=10, description="Max results (1–25)", ge=1, le=25)

    @mcp.tool(name="github_list_issues", annotations={"readOnlyHint": True})
    async def github_list_issues(params: IssuesInput) -> str:
        """List a repo's issues (pull requests excluded)."""
        try:
            data = await _get(f"/repos/{params.repo.strip('/')}/issues",
                              {"state": params.state, "per_page": params.limit})
            items = [i for i in (data or []) if isinstance(i, dict) and "pull_request" not in i]
            if not items:
                return f"## Issues in {params.repo} ({params.state})\n\nNone."
            lines = [f"## Issues in {params.repo} ({params.state})\n"]
            for i in items:
                lines.append(f"- #{i.get('number')} **{i.get('title', '')}** — {(i.get('user') or {}).get('login', '')}\n  {i.get('html_url', '')}")
            return "\n".join(lines)
        except Exception as e:
            return _handle_error(e, "GitHub issues")

    @mcp.tool(name="github_list_pulls", annotations={"readOnlyHint": True})
    async def github_list_pulls(params: IssuesInput) -> str:
        """List a repo's pull requests."""
        try:
            data = await _get(f"/repos/{params.repo.strip('/')}/pulls",
                              {"state": params.state, "per_page": params.limit})
            items = [p for p in (data or []) if isinstance(p, dict)]
            if not items:
                return f"## Pull requests in {params.repo} ({params.state})\n\nNone."
            lines = [f"## Pull requests in {params.repo} ({params.state})\n"]
            for p in items:
                lines.append(f"- #{p.get('number')} **{p.get('title', '')}** — {(p.get('user') or {}).get('login', '')}\n  {p.get('html_url', '')}")
            return "\n".join(lines)
        except Exception as e:
            return _handle_error(e, "GitHub pulls")

    # ─── THE AUTHENTICATED ACCOUNT ───────────────────────────────────────────
    #
    # Everything below needs a token. These are the account's own view and the
    # account's own actions — what makes this a GitHub client rather than a
    # search box over public data.

    class NoInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    @mcp.tool(name="github_me", annotations={"readOnlyHint": True})
    async def github_me() -> str:
        """Who Plutus is on GitHub, which scopes the token carries, and the rate limit.

        Worth running first: it answers "can this agent actually change anything"
        without touching a repository to find out.
        """
        if not github_authenticated():
            return NEEDS_TOKEN
        try:
            r = await _raw("GET", "/user")
            r.raise_for_status()
            me = r.json()
            # Classic tokens advertise their scopes in a header; fine-grained ones
            # do not, so absence means "unknown", never "none".
            scopes = (r.headers.get("x-oauth-scopes") or "").strip()
            limits = await _get("/rate_limit")
        except Exception as e:
            return _explain(e, "account")
        core = ((limits or {}).get("resources") or {}).get("core") or {}
        return "\n".join([
            "## GitHub account", "",
            f"**{me.get('login', '?')}**" + (f" — {me.get('name')}" if me.get("name") else ""),
            f"- Public repos: {_fmt_count(me.get('public_repos'))}"
            f" · Followers: {_fmt_count(me.get('followers'))}",
            f"- Token scopes: {scopes or 'not reported (fine-grained token)'}",
            f"- Rate limit: {core.get('remaining', '?')}/{core.get('limit', '?')} remaining",
            f"- Profile: {me.get('html_url', '')}",
        ])

    class MyReposInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        limit: int = Field(default=20, ge=1, le=100)
        sort: str = Field(default="pushed", description="pushed | updated | created | full_name")
        visibility: str = Field(default="all", description="all | public | private")

    @mcp.tool(name="github_my_repos", annotations={"readOnlyHint": True})
    async def github_my_repos(params: MyReposInput) -> str:
        """Repositories this token can reach, including private ones."""
        if not github_authenticated():
            return NEEDS_TOKEN
        try:
            data = await _get("/user/repos", {
                "per_page": params.limit, "sort": params.sort,
                "visibility": params.visibility,
                "affiliation": "owner,collaborator,organization_member"})
        except Exception as e:
            return _explain(e, "repositories")
        items = [r for r in (data or []) if isinstance(r, dict)]
        if not items:
            return "No repositories visible to this token."
        lines = [f"## Repositories ({len(items)})", ""]
        for r in items:
            mark = "🔒" if r.get("private") else "🌐"
            lines.append(f"- {mark} **{r.get('full_name')}** — ★{_fmt_count(r.get('stargazers_count'))}"
                         f" · {r.get('language') or '—'} · {(r.get('pushed_at') or '')[:10]}")
            if r.get("description"):
                lines.append(f"  {r['description'].strip()[:120]}")
        return "\n".join(lines)

    class BranchesInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        repo: str = Field(..., description="owner/repo", min_length=3, max_length=140)
        limit: int = Field(default=30, ge=1, le=100)

    @mcp.tool(name="github_list_branches", annotations={"readOnlyHint": True})
    async def github_list_branches(params: BranchesInput) -> str:
        """List a repository's branches."""
        try:
            data = await _get(f"/repos/{_repo(params.repo)}/branches",
                              {"per_page": params.limit})
        except Exception as e:
            return _explain(e, params.repo)
        items = [b for b in (data or []) if isinstance(b, dict)]
        if not items:
            return f"No branches in {params.repo}."
        lines = [f"## Branches in {params.repo}", ""]
        for b in items:
            lock = " 🔒protected" if b.get("protected") else ""
            lines.append(f"- `{b.get('name')}` — {(b.get('commit') or {}).get('sha', '')[:7]}{lock}")
        return "\n".join(lines)

    class CommitsInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        repo: str = Field(..., description="owner/repo", min_length=3, max_length=140)
        branch: str = Field(default="", description="Branch or ref (default: the repo's default)")
        path: str = Field(default="", description="Only commits touching this path")
        limit: int = Field(default=15, ge=1, le=50)

    @mcp.tool(name="github_list_commits", annotations={"readOnlyHint": True})
    async def github_list_commits(params: CommitsInput) -> str:
        """Recent commits on a branch, optionally filtered to one path."""
        q: dict = {"per_page": params.limit}
        if params.branch:
            q["sha"] = params.branch
        if params.path:
            q["path"] = params.path
        try:
            data = await _get(f"/repos/{_repo(params.repo)}/commits", q)
        except Exception as e:
            return _explain(e, params.repo)
        items = [c for c in (data or []) if isinstance(c, dict)]
        if not items:
            return f"No commits found in {params.repo}."
        lines = [f"## Commits in {params.repo}", ""]
        for c in items:
            commit = c.get("commit") or {}
            author = (commit.get("author") or {}).get("name") or "?"
            when = ((commit.get("author") or {}).get("date") or "")[:10]
            first = (commit.get("message") or "").splitlines()[0][:100]
            lines.append(f"- `{(c.get('sha') or '')[:7]}` {first}\n  {author} · {when}")
        return "\n".join(lines)

    class ReadFileInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        repo: str = Field(..., description="owner/repo", min_length=3, max_length=140)
        path: str = Field(..., description="Path in the repo, e.g. src/main.py",
                          min_length=1, max_length=400)
        ref: str = Field(default="", description="Branch, tag or commit (default: default branch)")

    @mcp.tool(name="github_read_file", annotations={"readOnlyHint": True})
    async def github_read_file(params: ReadFileInput) -> str:
        """Read a file's contents from a repository."""
        q = {"ref": params.ref} if params.ref else {}
        try:
            data = await _get(f"/repos/{_repo(params.repo)}/contents/"
                              f"{params.path.strip('/')}", q)
        except Exception as e:
            return _explain(e, f"{params.repo}/{params.path}")
        if isinstance(data, list):
            names = "\n".join(f"- {'📁' if d.get('type') == 'dir' else '📄'} {d.get('name')}"
                              for d in data if isinstance(d, dict))
            return f"## {params.repo}/{params.path} (directory)\n\n{names}"
        if not isinstance(data, dict) or data.get("encoding") != "base64":
            return f"Error: '{params.path}' is not a readable text file."
        try:
            text = base64.b64decode(data.get("content") or "").decode("utf-8", "replace")
        except Exception:
            return f"Error: could not decode '{params.path}' — it looks binary."
        size = data.get("size") or len(text)
        clipped = text[:20000]
        more = "" if len(text) <= 20000 else f"\n… [{len(text) - 20000} more chars]"
        return (f"## {params.repo}/{params.path}\n\n"
                f"_{_fmt_count(size)} bytes · {data.get('sha', '')[:7]}_\n\n"
                f"```\n{clipped}{more}\n```")

    class CodeSearchInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        query: str = Field(..., description="Code search, e.g. 'addEventListener repo:me/app'",
                           min_length=1, max_length=256)
        limit: int = Field(default=10, ge=1, le=25)

    @mcp.tool(name="github_search_code", annotations={"readOnlyHint": True})
    async def github_search_code(params: CodeSearchInput) -> str:
        """Search code across GitHub, or inside one repo with `repo:owner/name`.

        Needs a token: GitHub's code search is authenticated-only.
        """
        if not github_authenticated():
            return NEEDS_TOKEN
        try:
            data = await _get("/search/code", {"q": params.query, "per_page": params.limit})
        except Exception as e:
            return _explain(e, "code search")
        items = (data or {}).get("items") or [] if isinstance(data, dict) else []
        if not items:
            return f"No code matches for '{params.query}'."
        lines = [f"## Code matches: '{params.query}'", ""]
        for i in items:
            lines.append(f"- **{(i.get('repository') or {}).get('full_name', '')}** "
                         f"— `{i.get('path', '')}`\n  {i.get('html_url', '')}")
        return "\n".join(lines)

    class RunsInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        repo: str = Field(..., description="owner/repo", min_length=3, max_length=140)
        limit: int = Field(default=10, ge=1, le=30)
        branch: str = Field(default="", description="Only runs on this branch")

    @mcp.tool(name="github_workflow_runs", annotations={"readOnlyHint": True})
    async def github_workflow_runs(params: RunsInput) -> str:
        """Recent GitHub Actions runs — status, conclusion and when."""
        q: dict = {"per_page": params.limit}
        if params.branch:
            q["branch"] = params.branch
        try:
            data = await _get(f"/repos/{_repo(params.repo)}/actions/runs", q)
        except Exception as e:
            return _explain(e, f"{params.repo} Actions")
        runs = (data or {}).get("workflow_runs") or [] if isinstance(data, dict) else []
        if not runs:
            return f"No workflow runs in {params.repo}."
        icons = {"success": "✅", "failure": "❌", "cancelled": "⚪", "skipped": "⏭"}
        lines = [f"## Actions runs in {params.repo}", ""]
        for r in runs:
            concl = r.get("conclusion") or r.get("status") or "?"
            lines.append(f"- {icons.get(concl, '🔄')} **{r.get('name', '?')}** "
                         f"#{r.get('run_number')} — {concl} on `{r.get('head_branch')}`"
                         f" · {(r.get('created_at') or '')[:10]}")
            if r.get("html_url"):
                lines.append(f"  {r['html_url']}")
        return "\n".join(lines)

    class NotificationsInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        limit: int = Field(default=20, ge=1, le=50)
        all_notifications: bool = Field(default=False, description="Include ones already read")

    @mcp.tool(name="github_notifications", annotations={"readOnlyHint": True})
    async def github_notifications(params: NotificationsInput) -> str:
        """This account's GitHub notifications."""
        if not github_authenticated():
            return NEEDS_TOKEN
        try:
            data = await _get("/notifications", {"per_page": params.limit,
                                                 "all": str(params.all_notifications).lower()})
        except Exception as e:
            return _explain(e, "notifications")
        items = [n for n in (data or []) if isinstance(n, dict)]
        if not items:
            return "No notifications."
        lines = [f"## GitHub notifications ({len(items)})", ""]
        for n in items:
            subject = n.get("subject") or {}
            lines.append(f"- **{subject.get('title', '')}** "
                         f"({subject.get('type', '')}) — "
                         f"{(n.get('repository') or {}).get('full_name', '')}"
                         f" · {n.get('reason', '')}")
        return "\n".join(lines)

    # ─── ACTING AS THE ACCOUNT ───────────────────────────────────────────────
    #
    # These change things on GitHub, under the token's own scopes. That boundary
    # is deliberately GitHub's rather than ours: an operator who wants an agent
    # that can only read issues a read-only token, and no code here has to be
    # trusted to honour it.
    #
    # Every commit and comment made here is attributed to the token's account, so
    # it is visible and revertible in the normal way.

    class WriteFileInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        repo: str = Field(..., description="owner/repo", min_length=3, max_length=140)
        path: str = Field(..., description="Path in the repo", min_length=1, max_length=400)
        content: str = Field(..., description="The file's full new text", max_length=500_000)
        message: str = Field(..., description="Commit message", min_length=1, max_length=500)
        branch: str = Field(default="", description="Branch (default: the repo's default)")

    @mcp.tool(name="github_write_file",
              annotations={"readOnlyHint": False, "destructiveHint": False})
    async def github_write_file(params: WriteFileInput) -> str:
        """Create or update a file in a repository, as one commit.

        The whole file is replaced, so read it first if you mean to edit rather
        than overwrite.
        """
        if not github_authenticated():
            return NEEDS_TOKEN
        repo, path = _repo(params.repo), params.path.strip("/")
        body: dict = {
            "message": params.message,
            "content": base64.b64encode(params.content.encode("utf-8")).decode("ascii"),
        }
        if params.branch:
            body["branch"] = params.branch
        try:
            # Updating requires the current blob sha; creating must not send one.
            # Asking first is the only way to know which case this is.
            probe = await _raw("GET", f"/repos/{repo}/contents/{path}",
                               params={"ref": params.branch} if params.branch else None)
            if probe.status_code == 200:
                existing = probe.json()
                if isinstance(existing, dict) and existing.get("sha"):
                    body["sha"] = existing["sha"]
            elif probe.status_code not in (404,):
                probe.raise_for_status()
            data = await _request("PUT", f"/repos/{repo}/contents/{path}", json=body)
        except Exception as e:
            return _explain(e, f"{params.repo}/{params.path}")
        commit = (data or {}).get("commit") or {}
        verb = "Updated" if "sha" in body else "Created"
        return (f"✓ {verb} `{path}` in {repo}\n\n"
                f"- Commit: `{(commit.get('sha') or '')[:7]}` — {params.message}\n"
                f"- {commit.get('html_url', '')}")

    class CreateIssueInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        repo: str = Field(..., description="owner/repo", min_length=3, max_length=140)
        title: str = Field(..., description="Issue title", min_length=1, max_length=250)
        body: str = Field(default="", description="Issue body (Markdown)", max_length=60_000)
        labels: str = Field(default="", description="Comma-separated labels")

    @mcp.tool(name="github_create_issue",
              annotations={"readOnlyHint": False, "destructiveHint": False})
    async def github_create_issue(params: CreateIssueInput) -> str:
        """Open an issue on a repository, as the authenticated account."""
        if not github_authenticated():
            return NEEDS_TOKEN
        payload: dict = {"title": params.title, "body": params.body}
        labels = [x.strip() for x in params.labels.split(",") if x.strip()]
        if labels:
            payload["labels"] = labels
        try:
            data = await _request("POST", f"/repos/{_repo(params.repo)}/issues", json=payload)
        except Exception as e:
            return _explain(e, params.repo)
        return (f"✓ Opened issue #{(data or {}).get('number')} in {params.repo}\n\n"
                f"**{params.title}**\n{(data or {}).get('html_url', '')}")

    class CommentInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        repo: str = Field(..., description="owner/repo", min_length=3, max_length=140)
        number: int = Field(..., description="Issue or pull request number", ge=1)
        body: str = Field(..., description="Comment text (Markdown)", min_length=1,
                          max_length=60_000)

    @mcp.tool(name="github_comment",
              annotations={"readOnlyHint": False, "destructiveHint": False})
    async def github_comment(params: CommentInput) -> str:
        """Comment on an issue or pull request. Pull requests are issues here."""
        if not github_authenticated():
            return NEEDS_TOKEN
        try:
            data = await _request(
                "POST", f"/repos/{_repo(params.repo)}/issues/{params.number}/comments",
                json={"body": params.body})
        except Exception as e:
            return _explain(e, f"{params.repo}#{params.number}")
        return (f"✓ Commented on {params.repo}#{params.number}\n\n"
                f"{(data or {}).get('html_url', '')}")

    class IssueStateInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        repo: str = Field(..., description="owner/repo", min_length=3, max_length=140)
        number: int = Field(..., description="Issue number", ge=1)
        state: str = Field(default="closed", description="closed | open")
        reason: str = Field(default="", description="completed | not_planned (when closing)")

    @mcp.tool(name="github_set_issue_state",
              annotations={"readOnlyHint": False, "destructiveHint": True})
    async def github_set_issue_state(params: IssueStateInput) -> str:
        """Close or reopen an issue."""
        if not github_authenticated():
            return NEEDS_TOKEN
        payload: dict = {"state": "open" if params.state == "open" else "closed"}
        if params.reason in ("completed", "not_planned"):
            payload["state_reason"] = params.reason
        try:
            data = await _request("PATCH",
                                  f"/repos/{_repo(params.repo)}/issues/{params.number}",
                                  json=payload)
        except Exception as e:
            return _explain(e, f"{params.repo}#{params.number}")
        return (f"✓ {params.repo}#{params.number} is now {payload['state']}\n"
                f"{(data or {}).get('html_url', '')}")

    class BranchInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        repo: str = Field(..., description="owner/repo", min_length=3, max_length=140)
        name: str = Field(..., description="New branch name", min_length=1, max_length=200)
        base: str = Field(default="", description="Branch to start from (default: the repo's default)")

    @mcp.tool(name="github_create_branch",
              annotations={"readOnlyHint": False, "destructiveHint": False})
    async def github_create_branch(params: BranchInput) -> str:
        """Create a branch from another branch's current head."""
        if not github_authenticated():
            return NEEDS_TOKEN
        repo = _repo(params.repo)
        try:
            base = params.base
            if not base:
                info = await _get(f"/repos/{repo}")
                base = (info or {}).get("default_branch") or "main"
            ref = await _get(f"/repos/{repo}/git/ref/heads/{base}")
            sha = ((ref or {}).get("object") or {}).get("sha")
            if not sha:
                return f"Error: could not resolve the head of '{base}'."
            await _request("POST", f"/repos/{repo}/git/refs",
                           json={"ref": f"refs/heads/{params.name}", "sha": sha})
        except Exception as e:
            return _explain(e, params.repo)
        return f"✓ Created branch `{params.name}` in {repo} from `{base}` (`{sha[:7]}`)"

    class CreateRepoInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        name: str = Field(..., description="Repository name", min_length=1, max_length=100)
        description: str = Field(default="", max_length=350)
        private: bool = Field(default=True, description="Private (the safer default)")
        org: str = Field(default="", description="Create under this organisation instead of your account")
        auto_init: bool = Field(
            default=True,
            description="Start with a README so the repo has a default branch")

    @mcp.tool(name="github_create_repo",
              annotations={"readOnlyHint": False, "destructiveHint": False})
    async def github_create_repo(params: CreateRepoInput) -> str:
        """Create a repository, on the account or one of its organisations.

        Private by default: a repo created by an agent should not be public
        because nobody said otherwise.

        `auto_init` matters more than it looks — an empty repo has no default
        branch, so committing a file or opening a pull request against it fails
        until something is in it.
        """
        if not github_authenticated():
            return NEEDS_TOKEN
        path = f"/orgs/{params.org.strip('/')}/repos" if params.org else "/user/repos"
        try:
            data = await _request("POST", path, json={
                "name": params.name, "description": params.description,
                "private": params.private, "auto_init": params.auto_init})
        except Exception as e:
            if isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 422:
                return (f"Error: GitHub refused to create '{params.name}' (422) — "
                        "the name is already taken, or invalid.")
            return _explain(e, params.org or "your account")
        r = data or {}
        return (f"✓ Created {'private' if r.get('private') else 'public'} repository "
                f"**{r.get('full_name', params.name)}**\n\n"
                f"- Default branch: `{r.get('default_branch') or '(none — repo is empty)'}`\n"
                f"- {r.get('html_url', '')}\n"
                f"- Clone: `{r.get('clone_url', '')}`")

    class PullInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        repo: str = Field(..., description="owner/repo", min_length=3, max_length=140)
        title: str = Field(..., description="Pull request title", min_length=1, max_length=250)
        head: str = Field(..., description="Branch with the changes", min_length=1, max_length=200)
        base: str = Field(default="", description="Branch to merge into (default: the repo's default)")
        body: str = Field(default="", description="Description (Markdown)", max_length=60_000)
        draft: bool = Field(default=False, description="Open as a draft")

    @mcp.tool(name="github_create_pull",
              annotations={"readOnlyHint": False, "destructiveHint": False})
    async def github_create_pull(params: PullInput) -> str:
        """Open a pull request from one branch into another."""
        if not github_authenticated():
            return NEEDS_TOKEN
        repo = _repo(params.repo)
        try:
            base = params.base
            if not base:
                info = await _get(f"/repos/{repo}")
                base = (info or {}).get("default_branch") or "main"
            data = await _request("POST", f"/repos/{repo}/pulls", json={
                "title": params.title, "head": params.head, "base": base,
                "body": params.body, "draft": params.draft})
        except Exception as e:
            return _explain(e, params.repo)
        return (f"✓ Opened pull request #{(data or {}).get('number')} in {repo}\n\n"
                f"**{params.title}** — `{params.head}` → `{base}`\n"
                f"{(data or {}).get('html_url', '')}")
