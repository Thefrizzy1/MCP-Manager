"""
tools/studio.py — the production studio over MCP.

What the dispatcher and the agents' sessions use: take a task, finish it,
delegate work, read and write a video's documents, read the shared library,
and talk in a video's chat thread.
"""
import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from core import jobs, studio

_ROOT = Path(__file__).resolve().parents[1]


def register_studio_tools(mcp: FastMCP, *, allow: "set[str] | None" = None):
    from core.profiles import tool_filter
    mcp = tool_filter(mcp, allow)

    class NextInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        agents_json: str = Field(..., description="JSON list of agent ids this dispatcher can run")
        machine: str = Field(default="", description="Machine id the work will run on")

    @mcp.tool(name="studio_next_task", annotations={"readOnlyHint": False, "destructiveHint": False})
    async def studio_next_task(params: NextInput) -> str:
        """Dispatcher: the next task one of these agents can start, as JSON ({} when there is none). It is marked running."""
        agents = json.loads(params.agents_json)
        from core import fleet
        off = {w["id"] for w in fleet.workers(_ROOT) if not w["enabled"]}
        task = studio.next_task(_ROOT, [a for a in agents if a not in off], params.machine)
        if task:
            task["thread"] = studio.thread(_ROOT, task["job"], limit=14) if task["kind"] == "chat" else []
        return json.dumps(task)

    class FinishInput(BaseModel):
        model_config = ConfigDict(extra="forbid")
        task_id: int = Field(..., description="Id of the task")
        status: str = Field(..., description="done, failed, not_required, or queued to hand it back untouched")
        result: str = Field(default="", description="What was produced, in a few sentences", max_length=20000)
        reason: str = Field(default="", description="Why it failed, was not required, or was handed back", max_length=1000)
        say: bool = Field(default=True, description="Also post the result in the video's chat thread")

    @mcp.tool(name="studio_finish_task", annotations={"readOnlyHint": False, "destructiveHint": False})
    async def studio_finish_task(params: FinishInput) -> str:
        """Dispatcher: close a task with its outcome."""
        try:
            t = studio.finish_task(_ROOT, params.task_id, params.status, params.result, params.reason)
        except (KeyError, ValueError) as e:
            return f"Error: {e}"
        if params.say and params.status != "queued" and (params.result or params.reason):
            text = params.result if params.status == "done" else f"{params.status.replace('_', ' ')}: {params.reason or params.result}"
            studio.post(_ROOT, t["job"], "agent", text, t["agent"], t["id"])
        jobs._cache["ts"] = 0.0
        return f"✓ task {t['id']} is {t['status']}"

    class DelegateInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        job: str = Field(..., description="Video folder name the work is for")
        kind: str = Field(..., description="research, script, thumbnail, edit_plan, motion, shorts or upload_pack")
        instruction: str = Field(default="", description="What exactly is wanted, with everything the agent needs to know", max_length=8000)
        after_task: int = Field(default=0, description="Id of a task that must end well first (0 = start when free)")
        not_required: bool = Field(default=False, description="true = record that this step is not needed for this video")
        reason: str = Field(default="", description="Why it is not required", max_length=600)

    @mcp.tool(name="studio_delegate", annotations={"readOnlyHint": False, "destructiveHint": False})
    async def studio_delegate(params: DelegateInput) -> str:
        """Channel Manager: hand work to the agent that does it, or record a step as not required.

        One open task per kind and video: asking twice returns the existing one.
        A step marked not required shows crossed out and does not hold the video up.
        """
        if not jobs.job_folder(params.job):
            return f"Error: no video folder named {params.job!r}"
        if params.kind in ("chat", "intake"):
            return "Error: delegate production work, not chat"
        try:
            t = studio.add_task(_ROOT, params.job, params.kind, params.instruction, params.after_task or None,
                                "not_required" if params.not_required else "queued", params.reason)
        except ValueError as e:
            return f"Error: {e}"
        jobs._cache["ts"] = 0.0
        return f"✓ task {t['id']}: {t['kind']} for {t['agent']} is {t['status']}"

    class JobInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        job: str = Field(..., description="Video folder name, or 'channel'")

    @mcp.tool(name="studio_tasks", annotations={"readOnlyHint": True})
    async def studio_tasks(params: JobInput) -> str:
        """The tasks of a video: who is doing what, what is waiting, what was skipped and why."""
        rows = studio.tasks(_ROOT, params.job, limit=40)
        return "\n".join(
            f"- #{t['id']} {t['kind']} ({t['agent']}): {'blocked' if t.get('blocked') else t['status']}"
            + (f" — {t['reason'] or t['result'][:160]}" if t['reason'] or t['result'] else "") for t in rows) or "No tasks."

    @mcp.tool(name="studio_thread", annotations={"readOnlyHint": True})
    async def studio_thread(params: JobInput) -> str:
        """The recent chat about a video (or the channel)."""
        return "\n".join(f"{m['agent'] or m['role']}: {m['text']}" for m in studio.thread(_ROOT, params.job, 30)) or "Empty."

    class DocInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        job: str = Field(..., description="Video folder name")
        name: str = Field(..., description="File name, optionally under research/, upload/, shorts/ or edit/. e.g. SCRIPT.md, research/RESEARCH.md")

    @mcp.tool(name="jobs_read_doc", annotations={"readOnlyHint": True})
    async def jobs_read_doc(params: DocInput) -> str:
        """Read a text document from a video folder (script, research, plans)."""
        try:
            return jobs.read_doc(params.job, params.name)
        except (KeyError, ValueError, FileNotFoundError) as e:
            return f"Error: {e}"

    class WriteDocInput(DocInput):
        text: str = Field(..., description="The full document", min_length=1, max_length=400000)

    @mcp.tool(name="jobs_write_doc", annotations={"readOnlyHint": False, "destructiveHint": False})
    async def jobs_write_doc(params: WriteDocInput) -> str:
        """Write a document into a video folder. An existing file of that name is kept in _old first, never overwritten."""
        try:
            r = jobs.write_doc(params.job, params.name, params.text)
        except (KeyError, ValueError, PermissionError) as e:
            return f"Error: {e}"
        return f"✓ wrote {r['written']} ({r['bytes']} bytes)" + (f"; the previous version is {r['previous_kept_as']}" if r["previous_kept_as"] else "")

    class LibInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        path: str = Field(default="", description="Path inside the shared library, '' for its top level")

    @mcp.tool(name="library_list", annotations={"readOnlyHint": True})
    async def library_list(params: LibInput) -> str:
        """List a folder of the shared library: channel bible, music, sounds, stingers, thumbnails, graphics."""
        try:
            rows = jobs.library_list(params.path)
        except (ValueError, FileNotFoundError) as e:
            return f"Error: {e}"
        return "\n".join(f"- {'📁' if r['dir'] else '📄'} {r['rel']}" for r in rows) or "Empty."

    @mcp.tool(name="library_read", annotations={"readOnlyHint": True})
    async def library_read(params: LibInput) -> str:
        """Read a text file from the shared library (style guides, the channel bible, agent knowledge)."""
        try:
            return jobs.library_read(params.path)
        except (ValueError, FileNotFoundError) as e:
            return f"Error: {e}"
