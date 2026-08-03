"""APScheduler runtime that fires the schedules in core/schedule_store.py.

APScheduler is imported lazily so Plutus still runs (with scheduling disabled and
a clear message) if the dependency is missing — the store and UI keep working.
Jobs run in APScheduler's thread pool; tool jobs invoke the async MCP tool via a
short-lived event loop.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import Callable

from core.schedule_store import load_schedules

log = logging.getLogger("plutus.scheduler")

# Standard cron numbers the day-of-week field 0=Sunday … 6=Saturday, and that is
# what every crontab, every online cron helper, and Plutus's own UI mean by it.
# APScheduler's from_crontab does NOT: it numbers 0=Monday, so a schedule saved
# as "Friday" (5) fired on Saturday, and "Sunday" (0) fired on Monday. Every
# weekly schedule in the product was a day late, silently.
#
# Naming the days sidesteps the disagreement — APScheduler accepts these, and
# there is nothing left to misread.
_CRON_DOW = {"0": "sun", "1": "mon", "2": "tue", "3": "wed",
             "4": "thu", "5": "fri", "6": "sat", "7": "sun"}


def _dow_to_names(field: str) -> str:
    """Rewrite a standard-cron day-of-week field using day names.

    Handles the shapes a cron field actually takes — ``*``, ``5``, ``1-5``,
    ``0,6``, ``*/2``, ``1-5/2`` — and leaves alone any names the user typed.

    The step is deliberately not translated. A naive digit swap turns ``*/2``
    into ``*/tue``, which is not a schedule at all: the number after a slash is
    "every N", not a day.
    """
    def days(part: str) -> str:
        return "-".join(_CRON_DOW.get(x, x) for x in part.split("-"))

    out = []
    for item in (field or "").split(","):
        base, slash, step = item.partition("/")
        out.append(days(base) + slash + step)
    return ",".join(out)


def cron_trigger(expr: str, timezone: str):
    """A CronTrigger for a *standard* 5-field cron expression."""
    from apscheduler.triggers.cron import CronTrigger

    parts = (expr or "").split()
    if len(parts) != 5:
        # Let APScheduler raise its own error for anything that is not 5 fields.
        return CronTrigger.from_crontab(expr, timezone=timezone)
    minute, hour, day, month, dow = parts
    return CronTrigger(minute=minute, hour=hour, day=day, month=month,
                       day_of_week=_dow_to_names(dow), timezone=timezone)


class PlutusScheduler:
    def __init__(self, root: Path):
        self.root = root
        self._sched = None
        self._available = False
        self._run_agent: Callable[..., None] | None = None
        self._run_tool: Callable[[str, dict], object] | None = None
        self._run_task: Callable[[str], None] | None = None
        self._run_room: Callable[[str, str], None] | None = None

    # ── lifecycle ────────────────────────────────────────────────────────────
    def start(self, *, run_agent: Callable[..., None], run_tool: Callable[[str, dict], object],
              run_task: Callable[[str], None] | None = None,
              run_room: Callable[[str, str], None] | None = None) -> bool:
        self._run_agent = run_agent
        self._run_tool = run_tool
        self._run_task = run_task
        self._run_room = run_room
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
        except Exception as e:  # dependency missing
            log.warning("APScheduler unavailable (%s) — scheduling disabled. `pip install apscheduler`.", e)
            self._available = False
            return False
        self._sched = BackgroundScheduler()
        self._sched.start()
        self._available = True
        self.reschedule()
        return True

    def shutdown(self) -> None:
        if self._sched is not None:
            try:
                self._sched.shutdown(wait=False)
            except Exception:
                pass
        # Without this, a post-shutdown reschedule() would happily try to add jobs
        # to a dead scheduler.
        self._available = False
        _shutdown_tool_loop()

    @property
    def available(self) -> bool:
        return self._available

    # ── (re)build jobs from the store ────────────────────────────────────────
    def reschedule(self) -> None:
        if not self._available or self._sched is None:
            return
        for job in list(self._sched.get_jobs()):
            job.remove()
        for sc in load_schedules(self.root):
            if not sc.get("enabled"):
                continue
            try:
                trigger = cron_trigger(sc["cron"], sc.get("timezone") or "UTC")
            except Exception as e:
                log.warning("Skipping schedule %s — bad cron/timezone: %s", sc.get("id"), e)
                continue
            self._sched.add_job(
                self._make_job(sc), trigger, id=sc["id"],
                misfire_grace_time=3600, coalesce=True, max_instances=1,
            )

    def _make_job(self, sc: dict) -> Callable[[], None]:
        kind = sc.get("kind")
        payload = sc.get("payload") or {}
        name = sc.get("name") or sc.get("id")

        def _job() -> None:
            from core.schedule_store import record_run

            sid = sc.get("id") or ""
            try:
                if kind == "agent" and self._run_agent:
                    # `permission` in older stored payloads is ignored: the
                    # connection selection is now the only tool gate.
                    self._run_agent(payload.get("prompt", ""), f"sched:{name}",
                                    mcp_services=payload.get("mcp_services"),
                                    profile=payload.get("profile"),
                                    provider=payload.get("provider") or "",
                                    account_id=payload.get("account_id") or "",
                                    # Scheduled on one provider's model; without
                                    # this the run silently fell back to whatever
                                    # the global config last held.
                                    model=payload.get("model") or None,
                                    allow_write=payload.get("allow_write", True),
                                    allow_publish=payload.get("allow_publish", False),
                                    smart_fallback=payload.get("smart_fallback", True))
                elif kind == "task" and self._run_task:
                    self._run_task(payload.get("task_id", ""))
                elif kind == "room" and self._run_room:
                    # Everything the room needs — seats, their provider accounts,
                    # the room's connections, the chain — is stored on the room
                    # itself, so a scheduled room only names it. An empty brief
                    # means "use the room's own".
                    self._run_room(payload.get("room_id", ""), payload.get("brief", ""))
                elif kind == "tool" and self._run_tool:
                    self._run_tool(payload.get("tool", ""), payload.get("params", {}))
                else:
                    record_run(self.root, sid, "error",
                               f"nothing wired to run a '{kind}' schedule")
                    return
                # "queued", not "ok": an agent schedule hands off to the run queue
                # and returns immediately, so this records that the schedule fired
                # — the run's own outcome lands in the agent run history.
                record_run(self.root, sid, "queued" if kind != "tool" else "ok")
            except Exception as e:
                log.warning("Scheduled job %s failed: %s", sc.get("id"), e)
                record_run(self.root, sid, "error", str(e))

        return _job

    # ── introspection ────────────────────────────────────────────────────────
    def next_run_times(self) -> dict[str, str | None]:
        out: dict[str, str | None] = {}
        if not self._available or self._sched is None:
            return out
        for job in self._sched.get_jobs():
            out[job.id] = job.next_run_time.isoformat() if job.next_run_time else None
        return out

    def run_now(self, sc: dict) -> None:
        """Fire a schedule's action immediately (out of band), in a thread."""
        import threading
        threading.Thread(target=self._make_job(sc), daemon=True).start()


# One long-lived event loop for every scheduled tool call.
#
# asyncio.run() per invocation created and tore down a fresh loop each time. Any
# tool holding a loop-bound resource (httpx.AsyncClient, a connection pool) works
# over HTTP but breaks from a scheduled job, because the loop it was bound to is
# already closed. A single shared loop makes both paths behave the same.
_loop_lock = threading.Lock()
_tool_loop: "asyncio.AbstractEventLoop | None" = None
_tool_loop_thread: "threading.Thread | None" = None


def _ensure_tool_loop() -> "asyncio.AbstractEventLoop":
    global _tool_loop, _tool_loop_thread
    with _loop_lock:
        if _tool_loop is not None and not _tool_loop.is_closed():
            return _tool_loop
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, name="plutus-tool-loop", daemon=True)
        thread.start()
        _tool_loop, _tool_loop_thread = loop, thread
        return loop


def _shutdown_tool_loop() -> None:
    global _tool_loop, _tool_loop_thread
    with _loop_lock:
        loop, thread = _tool_loop, _tool_loop_thread
        _tool_loop, _tool_loop_thread = None, None
    if loop is None:
        return
    try:
        loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=5)
        loop.close()
    except Exception:
        pass


def invoke_tool_sync(tool_manager, tool_name: str, params: dict, timeout: float = 120.0):
    """Run an async MCP tool from a synchronous scheduler job thread."""
    from core.invoke_tool import invoke_mcp_tool_fn

    tool = tool_manager.get_tool(tool_name)
    if not tool:
        raise RuntimeError(f"Tool '{tool_name}' not available")

    async def _call():
        return await asyncio.wait_for(invoke_mcp_tool_fn(tool.fn, payload=params or {}), timeout=timeout)

    loop = _ensure_tool_loop()
    return asyncio.run_coroutine_threadsafe(_call(), loop).result(timeout + 30)
