"""The agent execution engine — the serial queue, its worker, and the scoping
helpers that turn a connection selection into a tool deny-list.

This used to live at module scope in ``ui/runtime.py``, which meant the engine
was welded to the UI process and could only be reached from there. Two problems
followed: tool modules that needed the same primitives reached *back* into
``ui.runtime`` (a tool importing the web UI is backwards), and the MCP-target
builder was copy-pasted in three places. Rooms, in turn, never got the
connection-scope deny-list at all, because the only code that computed it lived
here in the UI.

Now the engine is plain ``core`` with its dependencies injected. ``ui.runtime``
constructs one ``AgentOrchestrator`` and keeps thin wrappers for its own callers;
``tools/rooms.py``, ``tools/agents.py`` and ``core/workforce.py`` import the
module-level helpers directly, so nothing in ``core`` or ``tools`` depends on the
UI any more.
"""
from __future__ import annotations

import logging
import queue
import threading
from pathlib import Path

log = logging.getLogger("plutus")


# ── module-level helpers (no UI, no orchestrator instance needed) ─────────────

def mcp_target(cfg) -> tuple[str, str]:
    """(mcp_url, bearer_token) an agent uses to reach Plutus's own MCP tools.

    The one definition of "where do my tools live" — Claude, Codex/Gemini
    sub-agents, and room seats all resolve it through here instead of each
    re-deriving the loopback URL and re-reading the token.
    """
    url = f"http://127.0.0.1:{cfg.mcp_port}/mcp"
    token = ""
    if cfg.mcp_require_bearer:
        from core.env_store import read_env
        token = (read_env().get("MCP_BEARER_TOKEN", "") or "").strip()
    return url, token


def launch_room(root: Path, cfg, room_id: str, brief: str = "", *,
                block: bool = False) -> dict:
    """Start a room on a background thread. The one way a room gets run.

    There were three near-identical copies of this — the dashboard endpoint, the
    MCP ``room_run`` tool, and (nearly) the scheduler — each re-deriving the MCP
    target, the budget and the slot wait. They drifted: only one of them took the
    shared run lock. One definition instead.

    Returns ``{"ok": bool, "error": str}``; ``block=True`` waits for the room to
    finish, which only tests want — a caller on a request path must not hold a
    connection open for several agent runs.
    """
    import threading

    from core import agent_runner, workforce

    room = workforce.get_room(root, room_id)
    if not room:
        return {"ok": False, "error": f"no room with id '{room_id}'"}
    if not (room.get("seats") or []):
        return {"ok": False, "error": f"room '{room.get('label')}' has no agents in it yet"}
    if workforce.LIVE.get("running"):
        return {"ok": False, "error": "a room is already running"}

    acfg = agent_runner.load_agent_config(root)
    cap = float(acfg.get("max_cost_usd", 2.0) or 2.0) * 4   # a room is several runs
    slot_wait = max(60, agent_runner._timeout_min(acfg) * 60 + 120)
    outcome: dict = {"ok": True, "error": ""}

    def _work() -> None:
        # One lock for every entry point, or a scheduled room and a hand-started
        # one both believe they are the room that is running.
        with workforce.RUN_LOCK:
            try:
                url, token = mcp_target(cfg)

                def _run(r, prompt, **kw):
                    # "Refuses" is not "queues": run_agent returns an error the
                    # instant another run holds the slot, so a room launched
                    # while the queue was busy used to die on its first seat.
                    if not agent_runner.wait_for_slot(slot_wait):
                        return {"ok": False, "cost_usd": 0.0, "result": "",
                                "error": "another agent run held the runner for too long"}
                    return agent_runner.run_agent(r, prompt, mcp_url=url,
                                                  bearer_token=token, **kw)

                workforce.run_room(root, room_id, brief, run_agent=_run, max_cost_usd=cap)
            except Exception as exc:                 # the record carries the detail
                outcome.update(ok=False, error=str(exc))
                log.warning("room %s failed to run: %s", room_id, exc)

    t = threading.Thread(target=_work, name=f"room-{room_id}", daemon=True)
    t.start()
    if block:
        t.join()
    return outcome


def service_disallow(root: Path, selected: "list[str] | None") -> list[str]:
    """Per-connection ACL: deny every tool that belongs to a service the caller
    did NOT select. ``None`` = no restriction (back-compat for callers and older
    schedules that never stored a selection); ``[]`` = deny every service tool.

    Public services (web search/fetch, weather, maps, Wikipedia, …) are ordinary
    connections here — listed, and off unless ticked. Tools tied to no service
    (filesystem, utilities, the research library) are never touched, so a scoped
    agent or room can still read and write its own working files.
    """
    if selected is None:
        return []
    from core.dashboard_api import tool_to_service_map
    from core.service_registry import all_services

    sel = set(selected)
    conn_ids = {s["id"] for s in all_services(root)}
    tmap = tool_to_service_map()
    return sorted(
        f"mcp__plutus__{t}"
        for t, svc in tmap.items()
        if svc in conn_ids and svc not in sel
    )


# ── the execution engine ──────────────────────────────────────────────────────

class AgentOrchestrator:
    """Owns the single serial run queue and the one worker that drains it.

    Dependencies are injected so the engine is testable and UI-free:
      - ``root``          the data root (Path)
      - ``cfg``           the config singleton (for the MCP target)
      - ``tool_registry`` the tool adapter (``.raw_manager`` for notifications /
                          scheduled tool calls)
    """

    def __init__(self, root: Path, cfg, tool_registry, *, log: logging.Logger | None = None):
        self.root = root
        self.cfg = cfg
        self.tools = tool_registry
        self.log = log or logging.getLogger("plutus")
        # One job at a time; a small buffer so a burst of launches is not dropped.
        self.queue: "queue.Queue" = queue.Queue(maxsize=6)

    # -- scoping ---------------------------------------------------------------

    def mcp_target(self) -> tuple[str, str]:
        return mcp_target(self.cfg)

    def service_disallow(self, selected: "list[str] | None") -> list[str]:
        return service_disallow(self.root, selected)

    # -- notifications & bookkeeping -------------------------------------------

    def notify(self, rec: dict) -> None:
        """Fire the configured ntfy notification for a finished run, if enabled."""
        from core import agent_runner
        from core.scheduler import invoke_tool_sync

        acfg = agent_runner.load_agent_config(self.root)
        if not acfg.get("notify_enabled"):
            return
        if acfg.get("notify_on") == "error" and rec.get("ok"):
            return
        ok = "OK" if rec.get("ok") else "FAIL"
        msg = f"{ok} agent '{rec.get('label')}' — ${rec.get('cost_usd')}"
        if rec.get("error"):
            msg += f" — {rec['error'][:120]}"
        try:
            invoke_tool_sync(self.tools.raw_manager, "ntfy_send",
                             {"message": msg, "title": "Plutus agent"})
        except Exception as exc:
            self.log.warning("agent ntfy failed: %s", exc)

    def record_skipped(self, label: str, cap: int) -> None:
        """Leave a visible trace when the daily cap swallows a scheduled run.

        Recorded, not just logged: a scheduled run that vanishes because of the
        cap is otherwise indistinguishable from a scheduler that never fired — and
        the log is inside the container, where the person asking "did my job run?"
        is not looking.
        """
        import datetime
        import uuid as _uuid

        from core import agent_runner

        now = datetime.datetime.now().astimezone()
        try:
            agent_runner.save_run(self.root, {
                "id": now.strftime("%Y%m%d-%H%M%S") + "-" + _uuid.uuid4().hex[:4],
                "label": label, "prompt": "", "started": now.isoformat(),
                "finished": now.isoformat(), "ok": False, "cost_usd": 0.0,
                "skipped": True, "result": "",
                "error": f"Skipped: the daily cap of {cap} runs is already used. "
                         "Raise it in Settings → Agent, or wait for tomorrow.",
                "log": [],
            })
        except Exception as exc:
            self.log.warning("could not record a skipped run: %s", exc)

    # -- the queue -------------------------------------------------------------

    def enqueue(self, prompt: str, label: str, *,
                model: "str | None" = None, force: bool = False,
                extra_disallowed: "list[str] | None" = None,
                mcp_services: "list[str] | None" = None,
                provider: str = "", account_id: str = "",
                smart_fallback: bool = True) -> bool:
        """Queue one agent run. Returns False if the queue is full.

        The connection selection (folded into ``extra_disallowed`` by the caller)
        is the only tool gate: selecting a connection grants read and write on
        that service's tools.
        """
        disallowed = sorted(set(extra_disallowed or []))
        try:
            self.queue.put_nowait({
                "prompt": prompt, "label": label, "disallowed": disallowed,
                "model": model, "force": force, "mcp_services": mcp_services,
                "provider": provider, "account_id": account_id,
                "smart_fallback": smart_fallback,
            })
            return True
        except queue.Full:
            self.log.warning("Agent queue full — dropping '%s'", label)
            return False

    def start_worker(self) -> None:
        """Start the single serial worker thread (daemon)."""
        threading.Thread(target=self._worker, name="agent-queue", daemon=True).start()

    def _worker(self) -> None:
        """Run one agent job at a time, honouring the daily cap."""
        from core import agent_runner

        while True:
            job = self.queue.get()
            try:
                acfg = agent_runner.load_agent_config(self.root)
                cap = int(acfg.get("max_runs_per_day", 20) or 0)
                if cap and not job.get("force") and agent_runner.runs_today(self.root) >= cap:
                    self.log.info(
                        "Agent daily run cap (%s) reached — skipping scheduled '%s'",
                        cap, job.get("label"))
                    self.record_skipped(job.get("label", "agent"), cap)
                    continue
                url, token = self.mcp_target()
                rec = agent_runner.run_agent(
                    self.root, job["prompt"], label=job.get("label", "agent"),
                    mcp_url=url, bearer_token=token,
                    disallowed_tools=job.get("disallowed"), model=job.get("model") or None,
                    mcp_services=job.get("mcp_services"),
                    provider=job.get("provider") or "", account_id=job.get("account_id") or "",
                    smart_fallback=job.get("smart_fallback", True),
                )
                self.notify(rec)
            except Exception as exc:
                self.log.warning("Agent worker error: %s", exc)
            finally:
                self.queue.task_done()

    # -- scheduled tool calls --------------------------------------------------

    def run_tool_scheduled(self, tool_name: str, params: dict):
        from core.scheduler import invoke_tool_sync
        return invoke_tool_sync(self.tools.raw_manager, tool_name, params)
