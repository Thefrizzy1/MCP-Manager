"""
tools/jobs.py — the job board over MCP.

One job per video folder on the NAS (see core/jobs.py). These tools let any
session find out where every video stands without being told in chat, and record
the facts files cannot show: a stage finished, a publish URL, a deadline, and
what work was done (which feeds the morning page).
"""
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from core import jobs

_ROOT = Path(__file__).resolve().parents[1]
_SYM = {"todo": "·", "active": "▶", "done": "✓"}


def _line(j: dict) -> str:
    bar = "".join(_SYM[s["status"]] for s in j["stages"])
    extra = []
    if j["deadline"]:
        extra.append(f"deadline {j['deadline']}")
    if j["idle_days"] is not None:
        extra.append(f"idle {j['idle_days']}d")
    if j["sponsored"]:
        extra.append("sponsored")
    return f"- `{j['id']}` [{bar}] at **{j['current']}**" + (f" ({', '.join(extra)})" if extra else "")


def register_job_tools(mcp: FastMCP, *, allow: "set[str] | None" = None):
    from core.profiles import tool_filter
    mcp = tool_filter(mcp, allow)

    class BoardInput(BaseModel):
        model_config = ConfigDict(extra="forbid")
        include_archived: bool = Field(default=False, description="Also list archived jobs")

    @mcp.tool(name="jobs_board", annotations={"readOnlyHint": True})
    async def jobs_board(params: BoardInput) -> str:
        """The job board: every active video and the stage it is at.

        Stages in order: research, script, film, edit, packaging, publish, shorts.
        The seven marks are those stages (✓ done, ▶ in progress, · not started).
        Call this first when asked what is being worked on.
        """
        data = jobs.list_jobs(_ROOT)
        if not data["mounted"]:
            return f"Error: the YouTube folder is not mounted at {data['root']} (set YOUTUBE_ROOT)."
        rows = [j for j in data["jobs"] if params.include_archived or not j["archived"]]
        hidden = len(data["jobs"]) - len(rows)
        out = [f"## Job board — {len(rows)} job(s)", ""] + [_line(j) for j in rows]
        if hidden:
            out += ["", f"{hidden} archived job(s) not shown."]
        return "\n".join(out)

    class JobInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        job: str = Field(..., description="Job id = the folder name, e.g. 20261111_Local Relight")
        folder: str = Field(default="", description="Subfolder to list, relative to the job folder")

    @mcp.tool(name="jobs_get", annotations={"readOnlyHint": True})
    async def jobs_get(params: JobInput) -> str:
        """One job in full: every stage with its evidence, key files, notes and a folder listing."""
        j = jobs.get_job(_ROOT, params.job)
        if not j:
            return f"Error: no job folder named {params.job!r}. Use jobs_board for ids."
        out = [f"## {j['title']}", f"- id: `{j['id']}`", f"- at stage: **{j['current']}**",
               f"- archived: {j['archived']} · deadline: {j['deadline'] or '-'}",
               f"- script: {j['script'] or '-'} · final export: {j['final_export'] or '-'}",
               f"- publish url: {j['publish_url'] or '-'}", f"- note: {j['note'] or '-'}", "", "### Stages"]
        for s in j["stages"]:
            ev = ", ".join(s["evidence"]) or "no files"
            out.append(f"- {_SYM[s['status']]} {s['label']}: {s['status']}"
                       f"{' (set by hand)' if s['manual'] else ''} — {ev}")
        files = jobs.list_folder(params.job, params.folder) or []
        out += ["", f"### Files in /{params.folder}"] + [
            f"- {'📁' if f['dir'] else '📄'} {f['rel']}" for f in files[:120]]
        acts = jobs.recent_activity(_ROOT, job_id=params.job, limit=10)
        if acts:
            out += ["", "### Recent work"] + [f"- {a['text']}" for a in acts]
        return "\n".join(out)

    class UpdateInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        job: str = Field(..., description="Job id = the folder name")
        stage: Optional[str] = Field(default=None, description="Stage to set: research, script, film, edit, packaging, publish or shorts")
        status: Optional[str] = Field(default=None, description="todo, active, done, or auto (back to what the files say)")
        deadline: Optional[str] = Field(default=None, description="YYYY-MM-DD, or empty string to clear")
        note: Optional[str] = Field(default=None, description="Short note shown on the board", max_length=4000)
        publish_url: Optional[str] = Field(default=None, description="YouTube URL once the video is live")
        archived: Optional[bool] = Field(default=None, description="Hide from / return to the active board")

    @mcp.tool(name="jobs_update", annotations={"readOnlyHint": False, "destructiveHint": False})
    async def jobs_update(params: UpdateInput) -> str:
        """Record a fact about a job: a stage's status, deadline, note, publish URL or archive.

        Only writes Plutus's own state file; never touches the video folder.
        """
        changes: dict = {}
        if params.stage:
            changes["stages"] = {params.stage: params.status or "done"}
        for key in ("deadline", "note", "publish_url", "archived"):
            val = getattr(params, key)
            if val is not None:
                changes[key] = val
        if not changes:
            return "Error: nothing to change."
        try:
            j = jobs.update_job(_ROOT, params.job, changes)
        except (KeyError, ValueError) as e:
            return f"Error: {e}"
        return "✓ Saved and read back.\n" + _line(j)

    class LogInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        text: str = Field(..., description="One sentence: what was finished", min_length=3, max_length=500)
        job: str = Field(default="", description="Job id it belongs to, if any")
        who: str = Field(default="", description="Which session or role did it", max_length=60)

    @mcp.tool(name="jobs_log", annotations={"readOnlyHint": False, "destructiveHint": False})
    async def jobs_log(params: LogInput) -> str:
        """Log one finished piece of work. Shown on the board and the morning page.

        Call it when a real deliverable lands (script draft saved, workflow pushed,
        thumbnails rendered) — not for every small step.
        """
        if params.job and not jobs.job_folder(params.job):
            return f"Error: no job folder named {params.job!r}."
        row = jobs.log_activity(_ROOT, params.text, params.job, params.who)
        return f"✓ Logged: {row['text']}"

    # ── Permissions: read and request only. Granting happens in the dashboard. ──
    from core import permissions

    @mcp.tool(name="studio_permissions", annotations={"readOnlyHint": True})
    async def studio_permissions() -> str:
        """What the automation may do: each action is allow, ask or never, plus open requests.

        Check this before any outward action (pushing, publishing, sending,
        deleting). ``ask`` means prepare the work and call studio_request_approval;
        ``never`` means do not do it and do not ask.
        """
        ov = permissions.overview(_ROOT)
        out = ["## Permissions"] + [f"- `{a['id']}`: **{a['value']}** — {a['label']}" for a in ov["actions"]]
        open_rows = [r for r in ov["requests"] if r["status"] in ("pending", "approved")]
        if open_rows:
            out += ["", "## Open requests"] + [
                f"- #{r['id']} `{r['action']}` {r['status']}: {r['summary']} {r['link']}" for r in open_rows]
        return "\n".join(out)

    class RequestInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        action: str = Field(..., description="Action id from studio_permissions, e.g. lab.merge_pr")
        summary: str = Field(..., description="One or two sentences: what will happen if approved", min_length=5, max_length=600)
        link: str = Field(default="", description="Where to look at it (PR, draft, file)")
        ref: str = Field(default="", description="Stable reference (e.g. the PR number) so re-runs do not duplicate")

    @mcp.tool(name="studio_request_approval", annotations={"readOnlyHint": False, "destructiveHint": False})
    async def studio_request_approval(params: RequestInput) -> str:
        """Ask for a go-ahead on an ``ask`` action. Returns the request and its status.

        ``approved`` straight away means the action is set to allow. ``pending``
        means stop here: it shows up in the dashboard and is carried out on a
        later run once approved.
        """
        try:
            r = permissions.request(_ROOT, params.action, params.summary, params.link, params.ref)
        except PermissionError as e:
            return f"Refused: {e}. Do not do this."
        return f"Request #{r['id']} for `{r['action']}` is **{r['status']}**."

    class DoneInput(BaseModel):
        model_config = ConfigDict(extra="forbid")
        request_id: int = Field(..., description="Id of an approved request that has now been carried out")

    @mcp.tool(name="studio_approval_done", annotations={"readOnlyHint": False, "destructiveHint": False})
    async def studio_approval_done(params: DoneInput) -> str:
        """Report that an approved request was carried out, so it leaves the open list."""
        try:
            r = permissions.mark_done(_ROOT, params.request_id)
        except (KeyError, ValueError) as e:
            return f"Error: {e}"
        return f"✓ Request #{r['id']} marked done."
