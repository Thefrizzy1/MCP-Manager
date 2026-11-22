"""
tools/jobs.py — the job board over MCP.

One job per video folder on the NAS (see core/jobs.py). These tools let any
session find out where every video stands without being told in chat, and record
the facts files cannot show: a stage finished, a publish URL, a deadline, and
what work was done (which feeds the morning page).
"""
import time
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from core import jobs

_ROOT = Path(__file__).resolve().parents[1]
_SYM = {"todo": "·", "active": "▶", "done": "✓"}


UPLOADS_MAX_AGE = 30 * 60


async def sync_uploads(root: Path = _ROOT, *, force: bool = False) -> dict:
    """Refresh the list of the channel's uploads that jobs are matched against.

    The channel is the Trend Radar's ``my_channel``. Skipped while the stored
    list is fresh; a failure keeps the old list (a job never un-publishes
    because YouTube was unreachable).
    """
    cur = jobs.load_uploads(root)
    if not force and time.time() - cur["ts"] < UPLOADS_MAX_AGE:
        return cur
    from core import trend_radar
    from tools.youtube import collect_channel
    channel = (trend_radar.load_config(root).get("my_channel") or "").strip()
    if not channel:
        return cur
    try:
        data = await collect_channel(channel, max_videos=60, max_shorts=0)
    except Exception:
        return cur
    return jobs.save_uploads(root, channel, data.get("uploads") or [])


def _line(j: dict) -> str:
    bar = "".join(_SYM[s["status"]] for s in j["stages"])
    extra = []
    if j.get("label"):
        extra.append(f"label {j['label']}")
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

        Stages in order: research, script, test, film, edit, packaging, publish, shorts.
        The eight marks are those stages (✓ done, ▶ in progress, · not started).
        Call this first when asked what is being worked on.
        """
        await sync_uploads()
        data = jobs.list_jobs(_ROOT)
        if not data["mounted"]:
            return f"Error: the YouTube folder is not mounted at {data['root']} (set YOUTUBE_ROOT)."
        rows = [j for j in data["jobs"] if params.include_archived or not j["archived"]]
        hidden = len(data["jobs"]) - len(rows)
        out = [f"## Job board — {len(rows)} job(s)",
               "Labels: " + ", ".join(f"{x['id']} ({x['name']})" for x in data["labels"]), ""] + [_line(j) for j in rows]
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
               f"- publish url: {j['publish_url'] or '-'}"
               + (f" (matched to the upload “{j['published']['title']}”, {j['published']['date']})"
                  if j.get("published") else ""),
               f"- note: {j['note'] or '-'}", "", "### Stages"]
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
        stage: Optional[str] = Field(default=None, description="Stage to set: research, script, test, film, edit, packaging, publish or shorts")
        status: Optional[str] = Field(default=None, description="todo, active, done, or auto (back to what the files say)")
        deadline: Optional[str] = Field(default=None, description="YYYY-MM-DD, or empty string to clear")
        note: Optional[str] = Field(default=None, description="Short note shown on the board", max_length=4000)
        publish_url: Optional[str] = Field(default=None, description="YouTube URL once the video is live")
        archived: Optional[bool] = Field(default=None, description="Hide from / return to the active board")
        label: Optional[str] = Field(default=None, description="Colour label id (jobs_board lists them), or empty string to clear")
        publish_match: Optional[bool] = Field(default=None, description="false = the automatically matched upload is the wrong video; true = allow matching again")

    @mcp.tool(name="jobs_update", annotations={"readOnlyHint": False, "destructiveHint": False})
    async def jobs_update(params: UpdateInput) -> str:
        """Record a fact about a job: a stage's status, deadline, note, publish URL or archive.

        Only writes Plutus's own state file; never touches the video folder.
        """
        changes: dict = {}
        if params.stage:
            changes["stages"] = {params.stage: params.status or "done"}
        for key in ("deadline", "note", "publish_url", "archived", "publish_match", "label"):
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

    class OrganiseInput(BaseModel):
        model_config = ConfigDict(extra="forbid")
        dry_run: bool = Field(default=True, description="true = only list what would move; false = move what is allowed")

    @mcp.tool(name="jobs_archive_finished", annotations={"readOnlyHint": False, "destructiveHint": False})
    async def jobs_archive_finished(params: OrganiseInput) -> str:
        """Move videos that are live into the archive (02_Archive/<year>/<month>).

        A video is due once it is published and its Shorts are done, or it has
        been live for two weeks. Runs by itself every half hour; call it to see
        what is due or to run it now. Obeys the nas.archive_videos permission.
        """
        import asyncio
        from core import organiser
        await sync_uploads()
        rows = await asyncio.to_thread(organiser.run, _ROOT, dry_run=params.dry_run)
        if not rows:
            return "Nothing is due for the archive."
        return "\n".join(f"- {r['title']}: {r['result']} ({r['why']})" for r in rows)

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

    # ── Model lab board: the lab asks what to test and reports how it goes. ──
    import json as _json

    from core import lab_board

    @mcp.tool(name="lab_next", annotations={"readOnlyHint": True})
    async def lab_next() -> str:
        """The next model the lab should work on, as JSON ({"slug", "name", "note"}), or {} when the list is empty."""
        row = lab_board.next_queued(_ROOT)
        return _json.dumps({k: row.get(k, "") for k in ("slug", "name", "note", "status")} if row else {})

    @mcp.tool(name="lab_list", annotations={"readOnlyHint": True})
    async def lab_list() -> str:
        """Every model on the lab's list as JSON: slug, name, status, pr."""
        return _json.dumps([{k: r.get(k, "") for k in ("slug", "name", "status", "pr")}
                            for r in lab_board.entries(_ROOT).values()])

    class LabReportInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        slug: str = Field(default="", description="Model slug the report is about; empty for a general sign of life")
        text: str = Field(default="", description="One line of progress, shown on the Models page", max_length=400)
        status: str = Field(default="", description="New status: queued, testing, built, failed, waiting, published or skipped")
        pr: str = Field(default="", description="Pull request URL once there is one")
        name: str = Field(default="", description="Display name, when the model is new to the list")
        gpu3060: str = Field(default="", description="State of the second test on the RTX 3060: queued, testing, done or failed (text = the result)")
        server_test: str = Field(default="", description="State of the test on the GPU server, when it is reported separately: queued, testing, done or failed")
        update_pr: str = Field(default="", description="Pull request that adds the second machine's results to an already published workflow")
        review_json: str = Field(default="", description="JSON for the review panel: repo, branch, folder, samples, title, tested, summary, critique", max_length=6000)

    @mcp.tool(name="lab_report", annotations={"readOnlyHint": False, "destructiveHint": False})
    async def lab_report(params: LabReportInput) -> str:
        """Report model-lab progress so it shows on the Models page.

        Call it at each real step (downloading, first clean run, critique result,
        pull request opened) with one plain sentence.
        """
        try:
            if not params.slug:
                lab_board.beat(_ROOT, params.text)
                return "✓ Noted."
            try:
                review = _json.loads(params.review_json) if params.review_json else None
            except ValueError:
                review = None                    # a broken review must not lose the status report
            row = lab_board.report(_ROOT, params.slug, params.text, params.status, params.pr,
                                   params.name, review if isinstance(review, dict) else None,
                                   params.gpu3060, params.update_pr, params.server_test)
        except ValueError as e:
            return f"Error: {e}"
        return f"✓ {row['slug']}: {row['status']}"

    class LabQueueInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        slug: str = Field(..., description="Model slug, e.g. ltx-2.5 (the Trend Radar's slug when it has one)")
        name: str = Field(default="", description="Display name")
        note: str = Field(default="", description="Why, or what to focus on", max_length=300)

    @mcp.tool(name="lab_queue", annotations={"readOnlyHint": False, "destructiveHint": False})
    async def lab_queue(params: LabQueueInput) -> str:
        """Put a model on the model lab's list to be built and tested."""
        try:
            row = lab_board.queue(_ROOT, params.slug, params.name, params.note)
        except ValueError as e:
            return f"Error: {e}"
        return f"✓ Queued {row['slug']}."

    # ── Machines, workers and the calendar: how the platform sees its own fleet. ──
    from core import calendar_feed, fleet

    class MachineInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        machine: str = Field(..., description="Short id of the machine, e.g. gpu-server, windows-pc")
        specs_json: str = Field(default="", description="JSON resources: cores, ram_gb, gpu, vram_gb, gpu_util, vram_used_gb, disk_free_gb, disk_total_gb, os, load", max_length=2000)
        doing: str = Field(default="", description="What it is doing right now, in a few words; empty = idle", max_length=200)
        next_up: str = Field(default="", description="What it is scheduled to do next", max_length=200)
        name: str = Field(default="", description="Display name (first report or to rename)")
        role: str = Field(default="", description="What the machine is for, one sentence")
        can_json: str = Field(default="", description="JSON list of what it can do, when describing the machine", max_length=2000)
        form: str = Field(default="", description="How to draw it: server or laptop")

    @mcp.tool(name="machine_report", annotations={"readOnlyHint": False, "destructiveHint": False})
    async def machine_report(params: MachineInput) -> str:
        """A machine reports in: its resources, what it is doing and what is next.

        Shown on the Overview page. Send it every few minutes; a machine that
        stops reporting shows as offline. With name/role/can_json it also
        describes the machine.
        """
        try:
            if params.name or params.role or params.can_json or params.form:
                can = _json.loads(params.can_json) if params.can_json else None
                fleet.register_machine(_ROOT, params.machine, params.name, params.role,
                                       can if isinstance(can, list) else None, params.form)
            if params.specs_json or params.doing or params.next_up:
                specs = _json.loads(params.specs_json) if params.specs_json else None
                fleet.report_machine(_ROOT, params.machine, specs if isinstance(specs, dict) else None,
                                     params.doing, params.next_up)
        except ValueError as e:
            return f"Error: {e}"
        return f"✓ {params.machine} noted."

    @mcp.tool(name="worker_settings", annotations={"readOnlyHint": True})
    async def worker_settings() -> str:
        """Every worker as JSON: id, label, model, home machine. A worker's runner reads its model here."""
        return _json.dumps([{k: w[k] for k in ("id", "label", "model", "home")} for w in fleet.workers(_ROOT)])

    class WorkerInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        worker: str = Field(..., description="researcher, workflow_maker, workflow_manager, editor, manager or publisher")
        text: str = Field(..., description="What it is doing or just finished, one sentence", max_length=300)
        busy: bool = Field(default=True, description="true while working, false when reporting it is done")
        machine: str = Field(default="", description="Machine it is working on")

    @mcp.tool(name="worker_report", annotations={"readOnlyHint": False, "destructiveHint": False})
    async def worker_report(params: WorkerInput) -> str:
        """A worker says what it is working on, for the Overview page."""
        try:
            fleet.report_worker(_ROOT, params.worker, params.text, params.busy)
            from core import studio
            studio.mark_round(_ROOT, params.worker, params.busy, params.text, params.machine)
        except KeyError as e:
            return f"Error: {e}"
        return "✓ Noted."

    class EventInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        date: str = Field(..., description="Day of the entry, YYYY-MM-DD")
        title: str = Field(..., description="Short title as it should read in the calendar", min_length=2, max_length=200)
        description: str = Field(default="", description="Details: what is due, who it is for, what to prepare", max_length=3000)
        link: str = Field(default="", description="Link to open from the entry (mail thread, document, video)")
        kind: str = Field(default="other", description="publish, deadline, review, film, sponsor, workflow or other")
        job: str = Field(default="", description="Video folder it belongs to, if any")
        ref: str = Field(default="", description="Stable id so the same entry is updated rather than duplicated")

    @mcp.tool(name="calendar_add_event", annotations={"readOnlyHint": False, "destructiveHint": False})
    async def calendar_add_event(params: EventInput) -> str:
        """Put a dated entry in the channel calendar (planned publish, script review, sponsor date).

        Published videos, video deadlines and published workflows appear on
        their own; add only what those do not cover. Reuse ``ref`` to move or
        reword an entry.
        """
        try:
            e = calendar_feed.add_event(_ROOT, params.date, params.title, params.description,
                                        params.link, params.job, params.kind, params.ref)
        except ValueError as ex:
            return f"Error: {ex}"
        return f"✓ {e['date']} {e['title']} (id {e['id']})"

    class EventRemoveInput(BaseModel):
        model_config = ConfigDict(extra="forbid")
        event_id: str = Field(..., description="Id of an entry added with calendar_add_event")

    @mcp.tool(name="calendar_remove_event", annotations={"readOnlyHint": False, "destructiveHint": False})
    async def calendar_remove_event(params: EventRemoveInput) -> str:
        """Remove an entry that was added by hand or by a worker."""
        return "✓ Removed." if calendar_feed.remove_event(_ROOT, params.event_id) else "Error: no such entry."

    @mcp.tool(name="calendar_events", annotations={"readOnlyHint": True})
    async def calendar_events() -> str:
        """The entries added to the channel calendar by hand or by workers, oldest first."""
        rows = calendar_feed.custom_events(_ROOT)
        return "\n".join(f"- {e['date']} [{e['kind']}] {e['title']} (id {e['id']})" for e in rows) or "No added entries."

    # ── Nodes: the small program on each machine asks for work and reports back. ──
    from core import node_jobs

    class NodeNextInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        machine: str = Field(..., description="The node's machine id, e.g. windows-pc")
        kinds_json: str = Field(default="", description="JSON list of job kinds this node can do; empty = all")
        boot_ts: int = Field(default=0, description="Unix time this machine was switched on")

    @mcp.tool(name="node_next_job", annotations={"readOnlyHint": False, "destructiveHint": False})
    async def node_next_job(params: NodeNextInput) -> str:
        """A node asks what to do. Returns JSON: {"job": {...}} or {"job": null, "waiting": n, "may_power_off": bool}.

        Jobs are only handed out inside the machine's work hours, or when the
        server itself switched the machine on.
        """
        kinds = _json.loads(params.kinds_json) if params.kinds_json else None
        return _json.dumps(node_jobs.next_for(_ROOT, params.machine, kinds if isinstance(kinds, list) else None,
                                              params.boot_ts))

    class NodeDoneInput(BaseModel):
        model_config = ConfigDict(extra="forbid")
        job_id: int = Field(..., description="Id of the job that was handed out")
        ok: bool = Field(..., description="true if it worked")
        result_json: str = Field(default="", description="JSON result of the job", max_length=20000)

    @mcp.tool(name="node_job_done", annotations={"readOnlyHint": False, "destructiveHint": False})
    async def node_job_done(params: NodeDoneInput) -> str:
        """A node reports a finished job. A finished test on the second machine shows on the model's Test arrow."""
        try:
            result = _json.loads(params.result_json) if params.result_json else {}
            job = node_jobs.finish(_ROOT, params.job_id, params.ok, result if isinstance(result, dict) else {})
        except (KeyError, ValueError) as e:
            return f"Error: {e}"
        if job["kind"] == "comfy_test" and job["payload"].get("slug"):
            runs = job["result"].get("runs") or []
            good = [r for r in runs if r.get("ok")]
            if params.ok and good:
                text = (f"{len(good)} run(s) on {job['result'].get('gpu', 'the second card')}: slowest "
                        f"{max(r.get('seconds', 0) for r in good):.0f} s, peak "
                        f"{max(r.get('peak_vram_gb', 0) for r in good):.1f} GB")
            else:
                text = "Failed: " + str(job["result"].get("error") or next(
                    (r.get("error") for r in runs if r.get("error")), "no run finished"))[:300]
            lab_board.report(_ROOT, job["payload"]["slug"], text, gpu3060="done" if params.ok and good else "failed")
        return "✓ Noted."

    class NodeQueueInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        machine: str = Field(..., description="Machine that should do it, e.g. windows-pc")
        kind: str = Field(..., description="comfy_test (run prompts on that machine's ComfyUI and measure) or wake")
        payload_json: str = Field(default="", description="JSON payload of the job", max_length=400000)
        ref: str = Field(default="", description="Stable reference so the same job is not queued twice")

    @mcp.tool(name="node_queue_job", annotations={"readOnlyHint": False, "destructiveHint": False})
    async def node_queue_job(params: NodeQueueInput) -> str:
        """Queue work for a machine. It is done the next time that machine is on inside its work hours."""
        try:
            payload = _json.loads(params.payload_json) if params.payload_json else {}
            job = node_jobs.add(_ROOT, params.machine, params.kind, payload if isinstance(payload, dict) else {}, params.ref)
        except ValueError as e:
            return f"Error: {e}"
        if job["kind"] == "comfy_test" and job["payload"].get("slug"):
            lab_board.report(_ROOT, job["payload"]["slug"],
                             "Optional second test on the 12 GB card is lined up for when that PC is on. "
                             "You can publish without it.", gpu3060="queued")
        return f"✓ Queued job {job['id']} for {job['machine']}."
