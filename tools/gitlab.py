"""
tools/gitlab.py — GitLab read-only research tools (real calls, REST v4).

Search projects, inspect a project, and list its open issues / merge requests.
Targets GITLAB_URL (default https://gitlab.com); works unauthenticated for
public projects, an optional GITLAB_TOKEN adds private access + higher limits.
Read-only. Follows the service contract: real HTTP, structured, graceful errors.
"""
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field
from mcp.server.fastmcp import FastMCP

from config import cfg
from client import TIMEOUT, _handle_error


def _base() -> str:
    return (getattr(cfg, "gitlab_url", "") or "https://gitlab.com").rstrip("/")


def _fmt_count(v) -> str:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return str(v or "—")
    for unit, size in (("M", 1_000_000), ("K", 1_000)):
        if n >= size:
            return f"{n / size:.1f}{unit}".replace(".0", "")
    return str(n)


async def _get(path: str, params: dict | None = None) -> object:
    headers = {"User-Agent": "PlutusMCP/1.0"}
    token = (getattr(cfg, "gitlab_token", "") or "").strip()
    if token:
        headers["PRIVATE-TOKEN"] = token
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        r = await client.get(f"{_base()}/api/v4{path}", params=params or {}, headers=headers)
        r.raise_for_status()
        return r.json()


def register_gitlab_tools(mcp: FastMCP, *, allow: "set[str] | None" = None):
    from core.profiles import tool_filter
    mcp = tool_filter(mcp, allow)

    class SearchInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        query: str = Field(..., description="Search terms", min_length=1, max_length=200)
        limit: int = Field(default=10, description="Max results (1–25)", ge=1, le=25)

    @mcp.tool(name="gitlab_search_projects", annotations={"readOnlyHint": True})
    async def gitlab_search_projects(params: SearchInput) -> str:
        """Search GitLab projects by keyword, most-starred first."""
        try:
            data = await _get("/projects", {"search": params.query, "order_by": "star_count",
                                            "sort": "desc", "per_page": params.limit})
            items = data if isinstance(data, list) else []
            if not items:
                return f"## GitLab projects: '{params.query}'\n\nNo projects found."
            lines = [f"## GitLab projects: '{params.query}'\n"]
            for p in items:
                lines.append(
                    f"- **{p.get('path_with_namespace', '')}** — ★{_fmt_count(p.get('star_count'))}\n"
                    f"  {(p.get('description') or '').strip()[:120]}\n  {p.get('web_url', '')}"
                )
            return "\n".join(lines)
        except Exception as e:
            return _handle_error(e, "GitLab search")

    class ProjectInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        project: str = Field(..., description="namespace/project path or numeric ID", min_length=1, max_length=200)

    def _pid(project: str) -> str:
        p = project.strip().strip("/")
        return p if p.isdigit() else quote(p, safe="")

    @mcp.tool(name="gitlab_project_info", annotations={"readOnlyHint": True})
    async def gitlab_project_info(params: ProjectInput) -> str:
        """Project details — stars, forks, open issues, last activity."""
        try:
            p = await _get(f"/projects/{_pid(params.project)}")
            if not isinstance(p, dict) or not p.get("path_with_namespace"):
                return f"No project found for '{params.project}'."
            return (
                f"## {p.get('path_with_namespace')}\n\n"
                f"{(p.get('description') or '').strip()}\n\n"
                f"**Stars:** {_fmt_count(p.get('star_count'))}\n"
                f"**Forks:** {_fmt_count(p.get('forks_count'))}\n"
                f"**Open issues:** {_fmt_count(p.get('open_issues_count'))}\n"
                f"**Default branch:** {p.get('default_branch') or '—'}\n"
                f"**Last activity:** {(p.get('last_activity_at') or '')[:10]}\n"
                f"**Link:** {p.get('web_url', '')}"
            )
        except Exception as e:
            return _handle_error(e, "GitLab project")

    class ListInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        project: str = Field(..., description="namespace/project path or numeric ID", min_length=1, max_length=200)
        state: str = Field(default="opened", description="opened | closed | all")
        limit: int = Field(default=10, description="Max results (1–25)", ge=1, le=25)

    @mcp.tool(name="gitlab_list_issues", annotations={"readOnlyHint": True})
    async def gitlab_list_issues(params: ListInput) -> str:
        """List a project's issues."""
        try:
            data = await _get(f"/projects/{_pid(params.project)}/issues",
                              {"state": params.state, "per_page": params.limit})
            items = data if isinstance(data, list) else []
            if not items:
                return f"## Issues in {params.project} ({params.state})\n\nNone."
            lines = [f"## Issues in {params.project} ({params.state})\n"]
            for i in items:
                lines.append(f"- #{i.get('iid')} **{i.get('title', '')}** — {(i.get('author') or {}).get('username', '')}\n  {i.get('web_url', '')}")
            return "\n".join(lines)
        except Exception as e:
            return _handle_error(e, "GitLab issues")

    @mcp.tool(name="gitlab_list_merge_requests", annotations={"readOnlyHint": True})
    async def gitlab_list_merge_requests(params: ListInput) -> str:
        """List a project's merge requests."""
        try:
            data = await _get(f"/projects/{_pid(params.project)}/merge_requests",
                              {"state": params.state, "per_page": params.limit})
            items = data if isinstance(data, list) else []
            if not items:
                return f"## Merge requests in {params.project} ({params.state})\n\nNone."
            lines = [f"## Merge requests in {params.project} ({params.state})\n"]
            for m in items:
                lines.append(f"- !{m.get('iid')} **{m.get('title', '')}** — {(m.get('author') or {}).get('username', '')}\n  {m.get('web_url', '')}")
            return "\n".join(lines)
        except Exception as e:
            return _handle_error(e, "GitLab merge requests")
