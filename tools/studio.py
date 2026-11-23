"""
tools/studio.py — the production studio over MCP.

What the dispatcher and the agents' sessions use: take a task, finish it,
delegate work, read and write a video's documents, read the shared library,
and talk in a video's chat thread.
"""
import asyncio
import base64
import io
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP, Image
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

    def _picture(path: Path):
        """An image small enough to look at: scaled down when Pillow is there, else as it is if not huge."""
        fmt = {".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".webp": "webp"}.get(path.suffix.lower())
        if not fmt:
            raise ValueError("not a picture (png, jpg or webp)")
        try:
            import io

            from PIL import Image as PILImage
            with PILImage.open(path) as im:
                im.thumbnail((1280, 1280))
                buf = io.BytesIO()
                im.convert("RGB").save(buf, "JPEG", quality=82)
                return Image(data=buf.getvalue(), format="jpeg")
        except ImportError:
            if path.stat().st_size > 4_000_000:
                raise ValueError("the picture is too large to show without Pillow installed")
            return Image(data=path.read_bytes(), format=fmt)

    class ViewInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        job: str = Field(default="", description="Video folder name; leave empty to look in the shared library")
        path: str = Field(..., description="Path of the picture inside the video folder (or inside the library)")

    @mcp.tool(name="studio_view_image", annotations={"readOnlyHint": True})
    async def studio_view_image(params: ViewInput):
        """Look at a picture: a thumbnail draft, a frame, a cutout, a sample, a reference from the library.

        Use jobs_get (folder=...) or library_list to find the path first.
        """
        try:
            if params.job:
                path = jobs.resolve_file(params.job, params.path)
                if not path:
                    return f"Error: no such file in {params.job}"
            else:
                path = jobs.library_path(params.path)
                if not path.is_file():
                    return "Error: no such file in the library"
            return _picture(path)
        except (ValueError, OSError) as e:
            return f"Error: {e}"

    # ── Real thumbnails: compose in HTML, render on a machine with a browser ──
    RENDER_DIR = _ROOT / "data" / "render"
    _ASSET = re.compile(r"""(src|href)=(["'])(asset|library):([^"']+)\2""")

    def _data_uri(path: Path) -> str:
        """An image as a data URI, scaled down to at most 1600 px (thumbnails are 1280 wide)."""
        mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
                ".svg": "image/svg+xml"}.get(path.suffix.lower())
        if not mime:
            raise ValueError(f"{path.name} is not an image")
        raw = path.read_bytes()
        if mime != "image/svg+xml":
            try:
                from PIL import Image as PILImage
                with PILImage.open(io.BytesIO(raw)) as im:
                    if max(im.size) > 1600:
                        im.thumbnail((1600, 1600))
                        buf = io.BytesIO()
                        keep_alpha = im.mode in ("RGBA", "LA", "P")
                        im.save(buf, "PNG" if keep_alpha else "JPEG", **({} if keep_alpha else {"quality": 90}))
                        raw, mime = buf.getvalue(), ("image/png" if keep_alpha else "image/jpeg")
            except Exception:                  # no Pillow, or a file it cannot read: send it as it is
                pass
        return f"data:{mime};base64," + base64.b64encode(raw).decode()

    def _inline(job: str, html: str) -> str:
        def sub(m):
            where, rel = m.group(3), m.group(4)
            if where == "asset":
                path = jobs.resolve_file(job, rel)
                if not path:
                    raise ValueError(f"asset:{rel} is not a file in {job}")
            else:
                path = jobs.library_path(rel)
                if not path.is_file():
                    raise ValueError(f"library:{rel} is not a file in the library")
            return f'{m.group(1)}={m.group(2)}{_data_uri(path)}{m.group(2)}'
        return _ASSET.sub(sub, html)

    class RenderInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        job: str = Field(..., description="Video folder name")
        name: str = Field(..., description="File name for the picture, e.g. 'concept A v1' (saved as upload/thumbs/<name>.png)", max_length=80)
        html: str = Field(..., description="A complete HTML page, 1280x720. Images: src=\"asset:<path in the video folder>\" or src=\"library:<path in 00_Library>\". Google Fonts links work.", min_length=20, max_length=200000)

    @mcp.tool(name="thumbnail_render", annotations={"readOnlyHint": False, "destructiveHint": False})
    async def thumbnail_render(params: RenderInput) -> str:
        """Render a thumbnail from HTML into a real 1280x720 PNG in the video's upload/thumbs folder.

        Waits up to about three minutes for the render machine. Look at the result
        with studio_view_image before calling it done. An existing picture of the
        same name is kept in _old.
        """
        from core import node_jobs
        if not jobs.job_folder(params.job):
            return f"Error: no video folder named {params.job!r}"
        name = re.sub(r"[^\w .()-]+", "", params.name).strip()[:80] or "thumbnail"
        try:
            html = _inline(params.job, params.html)
        except (ValueError, OSError) as e:
            return f"Error: {e}"
        RENDER_DIR.mkdir(parents=True, exist_ok=True)
        ref = f"{int(time.time() * 1000)}"
        (RENDER_DIR / f"{ref}.html").write_text(html, encoding="utf-8")
        machine = os.getenv("RENDER_MACHINE", "gpu-render")
        job = node_jobs.add(_ROOT, machine, "render_html",
                            {"ref": ref, "job": params.job, "out": f"upload/thumbs/{name}.png", "width": 1280, "height": 720})
        for _ in range(90):
            await asyncio.sleep(2)
            cur = next((j for j in node_jobs.jobs(_ROOT, machine) if j["id"] == job["id"]), None)
            if cur and cur["status"] in ("done", "failed"):
                (RENDER_DIR / f"{ref}.html").unlink(missing_ok=True)
                if cur["status"] == "failed":
                    return "Error: the render failed: " + str(cur["result"].get("error", "unknown"))[:400]
                return f"✓ rendered upload/thumbs/{name}.png. Look at it with studio_view_image."
        return (f"Error: the render machine ({machine}) did not pick the job up within three minutes. "
                "It is still queued and will be saved when it runs.")

    class FetchInput(BaseModel):
        model_config = ConfigDict(extra="forbid")
        job_id: int = Field(..., description="Node job id")

    @mcp.tool(name="node_fetch_render", annotations={"readOnlyHint": True})
    async def node_fetch_render(params: FetchInput) -> str:
        """Node: the HTML page of a render job, with its images inlined."""
        from core import node_jobs
        job = next((j for j in node_jobs.jobs(_ROOT) if j["id"] == params.job_id and j["kind"] == "render_html"), None)
        if not job:
            return "Error: no such render job"
        path = RENDER_DIR / f"{job['payload']['ref']}.html"
        return path.read_text(encoding="utf-8") if path.is_file() else "Error: the page is gone"

    class PutInput(BaseModel):
        model_config = ConfigDict(extra="forbid")
        job_id: int = Field(..., description="Node job id of a render job")
        data_b64: str = Field(..., description="The PNG, base64", max_length=30_000_000)

    @mcp.tool(name="node_put_render", annotations={"readOnlyHint": False, "destructiveHint": False})
    async def node_put_render(params: PutInput) -> str:
        """Node: deliver the rendered picture of a render job into its video folder."""
        from core import node_jobs
        job = next((j for j in node_jobs.jobs(_ROOT) if j["id"] == params.job_id and j["kind"] == "render_html"), None)
        if not job or job["status"] != "running":
            return "Error: no running render job with that id"
        raw = base64.b64decode(params.data_b64)
        if not raw.startswith(b"\x89PNG"):
            return "Error: not a PNG"
        folder = jobs.job_folder(job["payload"]["job"])
        if not folder:
            return "Error: the video folder is gone"
        out = folder / job["payload"]["out"]
        for d in (out.parent.parent, out.parent):
            if not d.is_dir():
                d.mkdir()
                jobs.inherit(d)
        if out.is_file():
            old = out.parent / "_old"
            if not old.is_dir():
                old.mkdir()
                jobs.inherit(old)
            out.rename(old / f"{out.stem}_old_{time.strftime('%Y%m%d_%H%M%S')}{out.suffix}")
        out.write_bytes(raw)
        jobs.inherit(out)
        jobs._cache["ts"] = 0.0
        return f"✓ saved {job['payload']['out']}"

    class FramesInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        job: str = Field(..., description="Video folder name")
        video: str = Field(..., description="Path of the video file inside the video folder")
        times_json: str = Field(default="", description="JSON list of seconds to grab, e.g. [3, 12.5, 40]. Empty = 12 frames spread over the clip")

    @mcp.tool(name="jobs_video_frames", annotations={"readOnlyHint": False, "destructiveHint": False})
    async def jobs_video_frames(params: FramesInput) -> str:
        """Grab still frames from a video in the folder into upload/frames, to look at or use in a thumbnail."""
        if not shutil.which("ffmpeg"):
            return "Error: ffmpeg is not installed where Plutus runs"
        src = jobs.resolve_file(params.job, params.video)
        if not src:
            return f"Error: no file {params.video!r} in {params.job}"
        try:
            probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(src)],
                                   capture_output=True, text=True, timeout=60)
            duration = float(probe.stdout.strip() or 0)
        except (ValueError, subprocess.TimeoutExpired):
            duration = 0.0
        try:
            times = [float(t) for t in json.loads(params.times_json)] if params.times_json else []
        except (ValueError, TypeError):
            return "Error: times_json must be a JSON list of numbers"
        if not times:
            if not duration:
                return "Error: could not read the clip's length; give times_json"
            times = [round(duration * (i + 0.5) / 12, 2) for i in range(12)]
        out_dir = jobs.job_folder(params.job) / "upload" / "frames"
        for d in (out_dir.parent, out_dir):
            if not d.is_dir():
                d.mkdir()
                jobs.inherit(d)
        stem = re.sub(r"[^\w-]+", "_", src.stem)[:40]
        saved = []
        for t in times[:24]:
            if duration and t > duration:
                continue
            out = out_dir / f"{stem}_{t:08.2f}s.jpg"
            r = await asyncio.to_thread(subprocess.run, ["ffmpeg", "-y", "-v", "error", "-ss", str(t), "-i", str(src),
                                                         "-frames:v", "1", "-q:v", "2", str(out)],
                                        capture_output=True, text=True, timeout=120)
            if r.returncode == 0 and out.is_file():
                jobs.inherit(out)
                saved.append(str(out.relative_to(jobs.job_folder(params.job))))
        return "✓ saved:\n" + "\n".join(saved) if saved else "Error: no frame could be grabbed"
