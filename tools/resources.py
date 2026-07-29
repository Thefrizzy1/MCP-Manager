"""Server data exposed as MCP resources (``plutus://`` scheme).

- ``plutus://connections``          JSON list of every known service.
- ``plutus://health/latest``        Markdown service-health report (freshly gathered).
- ``plutus://agent-runs/{run_id}``  Markdown summary of one agent run.
- ``plutus://library/{path}``       A file from the internal research library,
                                    path-guarded and secret-redacted.

A resource read is not less dangerous than a tool call: the library resource goes
through ``core.path_guard`` + ``core.redact``, exactly like the Files endpoint.
Resources are read-only server data, so they are registered on every instance
regardless of a profile's tool ``allow`` set.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from core import agent_runner

_ROOT = Path(__file__).resolve().parents[1]


def _library_base() -> str:
    acfg = agent_runner.load_agent_config(_ROOT)
    return str(acfg.get("fs_library_path") or "/data/library").rstrip("/")


def safe_library_path(base: str, rel: str) -> str | None:
    """Resolve ``rel`` under ``base`` and return the real path only if it stays
    inside ``base`` after symlink/.. resolution, else None.

    Pure and testable in isolation — this is the guard the library resource
    relies on, so the traversal tests hit it directly.
    """
    from core.path_guard import is_within_any
    if not base:
        return None
    candidate = os.path.realpath(os.path.join(base, rel or ""))
    if not is_within_any(candidate, [base]):
        return None
    return candidate


def register_resource_tools(mcp, *, allow: "set[str] | None" = None) -> None:
    @mcp.resource("plutus://connections", mime_type="application/json")
    def connections() -> str:
        from core.service_registry import all_services
        rows = [
            {"id": s.get("id"), "label": s.get("label"), "section": s.get("section"), "tag": s.get("tag")}
            for s in all_services(_ROOT)
        ]
        return json.dumps({"connections": rows}, ensure_ascii=False, indent=2)

    @mcp.resource("plutus://health/latest", mime_type="text/markdown")
    async def health_latest() -> str:
        from config import cfg
        from core.dashboard_health import build_health_report_markdown, gather_service_health
        from core.service_registry import all_services
        _, svc_rows = await gather_service_health(all_services(_ROOT), cfg)
        return build_health_report_markdown(svc_rows, [])

    @mcp.resource("plutus://agent-runs/{run_id}", mime_type="text/markdown")
    def agent_run(run_id: str) -> str:
        # Direct read by id — scanning and parsing every run file to find one was
        # O(all runs) per resource fetch.
        rec = agent_runner.get_run(_ROOT, str(run_id))
        if not rec:
            return f"# Agent run `{run_id}`\n\nNot found."
        lines = [
            f"# Agent run: {rec.get('label') or run_id}",
            "",
            f"- id: `{rec.get('id')}`",
            f"- started: {rec.get('started', '')}",
            f"- ok: {rec.get('ok')}",
            f"- cost: ${rec.get('cost_usd')}",
        ]
        if rec.get("error"):
            lines += ["", "## Error", "", str(rec["error"])]
        if rec.get("result"):
            lines += ["", "## Result", "", str(rec["result"])]
        return "\n".join(lines)

    @mcp.resource("plutus://library/{path}", mime_type="text/markdown")
    def library(path: str) -> str:
        from core.redact import redact_secrets
        safe = safe_library_path(_library_base(), path)
        if not safe:
            raise ValueError("Path is outside the research library.")
        if not os.path.isfile(safe):
            raise ValueError("Not a file.")
        text = Path(safe).read_text(encoding="utf-8", errors="replace")
        red, _ = redact_secrets(text)
        return red[:200000]
