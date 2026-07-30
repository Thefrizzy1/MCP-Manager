"""
tools/github.py — GitHub read-only research tools (real calls, public API).

Search repositories, inspect a repo, and list its open issues / pull requests.
Hits https://api.github.com — works unauthenticated (60 req/hr); an optional
GITHUB_TOKEN raises the limit to 5000/hr and allows private repos. Read-only.
Follows the service contract: real HTTP, structured output, graceful errors.
"""
import httpx
from pydantic import BaseModel, ConfigDict, Field
from mcp.server.fastmcp import FastMCP

from config import cfg
from client import TIMEOUT, _handle_error

API = "https://api.github.com"


def _fmt_count(v) -> str:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return str(v or "—")
    for unit, size in (("B", 1_000_000_000), ("M", 1_000_000), ("K", 1_000)):
        if n >= size:
            return f"{n / size:.1f}{unit}".replace(".0", "")
    return str(n)


async def _get(path: str, params: dict | None = None) -> object:
    headers = {"User-Agent": "PlutusMCP/1.0", "Accept": "application/vnd.github+json"}
    token = (getattr(cfg, "github_token", "") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        r = await client.get(f"{API}{path}", params=params or {}, headers=headers)
        r.raise_for_status()
        return r.json()


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
