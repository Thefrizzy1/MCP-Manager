"""APScheduler runtime that fires the schedules in core/schedule_store.py.

APScheduler is imported lazily so Plutus still runs (with scheduling disabled and
a clear message) if the dependency is missing — the store and UI keep working.
Jobs run in APScheduler's thread pool; tool jobs invoke the async MCP tool via a
short-lived event loop.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Callable

from core.schedule_store import load_schedules

log = logging.getLogger("plutus.scheduler")


class PlutusScheduler:
    def __init__(self, root: Path):
        self.root = root
        self._sched = None
        self._available = False
        self._run_agent: Callable[..., None] | None = None
        self._run_tool: Callable[[str, dict], object] | None = None
        self._run_task: Callable[[str], None] | None = None

    # ── lifecycle ────────────────────────────────────────────────────────────
    def start(self, *, run_agent: Callable[..., None], run_tool: Callable[[str, dict], object],
              run_task: Callable[[str], None] | None = None) -> bool:
        self._run_agent = run_agent
        self._run_tool = run_tool
        self._run_task = run_task
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

    @property
    def available(self) -> bool:
        return self._available

    # ── (re)build jobs from the store ────────────────────────────────────────
    def reschedule(self) -> None:
        if not self._available or self._sched is None:
            return
        from apscheduler.triggers.cron import CronTrigger

        for job in list(self._sched.get_jobs()):
            job.remove()
        for sc in load_schedules(self.root):
            if not sc.get("enabled"):
                continue
            try:
                trigger = CronTrigger.from_crontab(sc["cron"], timezone=sc.get("timezone") or "UTC")
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
            try:
                if kind == "agent" and self._run_agent:
                    self._run_agent(payload.get("prompt", ""), f"sched:{name}",
                                    permission=payload.get("permission") or None,
                                    mcp_services=payload.get("mcp_services"),
                                    profile=payload.get("profile"))
                elif kind == "task" and self._run_task:
                    self._run_task(payload.get("task_id", ""))
                elif kind == "tool" and self._run_tool:
                    self._run_tool(payload.get("tool", ""), payload.get("params", {}))
            except Exception as e:
                log.warning("Scheduled job %s failed: %s", sc.get("id"), e)

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


def invoke_tool_sync(tool_manager, tool_name: str, params: dict, timeout: float = 120.0):
    """Run an async MCP tool from a synchronous scheduler job thread."""
    from core.invoke_tool import invoke_mcp_tool_fn

    tool = tool_manager.get_tool(tool_name)
    if not tool:
        raise RuntimeError(f"Tool '{tool_name}' not available")

    async def _call():
        return await asyncio.wait_for(invoke_mcp_tool_fn(tool.fn, payload=params or {}), timeout=timeout)

    return asyncio.run(_call())
