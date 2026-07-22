"""
tools/monitoring.py — n8n and Uptime Kuma tools
"""

import httpx
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict
from mcp.server.fastmcp import FastMCP

from config import cfg
from client import TIMEOUT, _handle_error


def register_monitoring_tools(mcp: FastMCP):

    # ─── N8N ──────────────────────────────────────────────────────────────────

    def _n8n_headers() -> dict:
        return {"X-N8N-API-KEY": cfg.n8n_api_key}

    @mcp.tool(name="n8n_list_workflows", annotations={"readOnlyHint": True})
    async def n8n_list_workflows() -> str:
        """List all n8n workflows with their active/inactive status."""
        if not cfg.is_configured("n8n_url", "n8n_api_key"):
            return "Error: n8n not configured. Set N8N_URL and N8N_API_KEY in .env"
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.get(
                    f"{cfg.n8n_url}/api/v1/workflows",
                    headers=_n8n_headers()
                )
                r.raise_for_status()
                data = r.json()

            workflows = data.get("data", [])
            if not workflows:
                return "No workflows found in n8n."

            active = [w for w in workflows if w.get("active")]
            inactive = [w for w in workflows if not w.get("active")]

            result = f"## n8n Workflows ({len(workflows)} total)\n\n"
            result += f"### Active ({len(active)})\n"
            for w in active:
                result += f"✅ **{w.get('name')}** (ID: {w.get('id')})\n"

            if inactive:
                result += f"\n### Inactive ({len(inactive)})\n"
                for w in inactive:
                    result += f"⏸ {w.get('name')} (ID: {w.get('id')})\n"

            return result
        except Exception as e:
            return _handle_error(e, "n8n")

    @mcp.tool(name="n8n_get_executions", annotations={"readOnlyHint": True})
    async def n8n_get_executions() -> str:
        """Get recent n8n workflow execution history."""
        if not cfg.is_configured("n8n_url", "n8n_api_key"):
            return "Error: n8n not configured."
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.get(
                    f"{cfg.n8n_url}/api/v1/executions",
                    headers=_n8n_headers(),
                    params={"limit": 20}
                )
                r.raise_for_status()
                data = r.json()

            executions = data.get("data", [])
            if not executions:
                return "No recent executions found."

            result = f"## n8n Recent Executions ({len(executions)})\n\n"
            for ex in executions:
                status = ex.get("status", "unknown")
                icon = "✅" if status == "success" else ("❌" if status == "error" else "⏳")
                name = ex.get("workflowData", {}).get("name", "Unknown")
                started = ex.get("startedAt", "")[:19]
                result += f"{icon} **{name}** — {status} — {started}\n"

            return result
        except Exception as e:
            return _handle_error(e, "n8n")

    class N8NTriggerInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        webhook_path: str = Field(..., description="Webhook path (the part after /webhook/) e.g. 'morning-brief'", min_length=1)
        data: Optional[dict] = Field(default=None, description="Optional JSON data to send to the webhook")

    @mcp.tool(name="n8n_trigger_webhook", annotations={"readOnlyHint": False})
    async def n8n_trigger_webhook(params: N8NTriggerInput) -> str:
        """Trigger an n8n workflow via webhook URL.

        The webhook_path is the path after /webhook/ in your n8n webhook URL.
        """
        if not cfg.is_configured("n8n_url"):
            return "Error: n8n not configured. Set N8N_URL in .env"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                # Try the production webhook path first; n8n only registers
                # `/webhook/<path>` once the workflow is *active*. If the user
                # only has it on for testing, the production path 404s and
                # we transparently retry against `/webhook-test/<path>`.
                r = await client.post(
                    f"{cfg.n8n_url}/webhook/{params.webhook_path}",
                    json=params.data or {},
                )
                if r.status_code == 404:
                    r2 = await client.post(
                        f"{cfg.n8n_url}/webhook-test/{params.webhook_path}",
                        json=params.data or {},
                    )
                    if r2.status_code != 404:
                        r = r2
                        used_path = "/webhook-test/"
                    else:
                        return (
                            f"Error: n8n returned 404 for both /webhook/{params.webhook_path} "
                            f"and /webhook-test/{params.webhook_path}. "
                            "Check the workflow is active or the test webhook is listening."
                        )
                else:
                    used_path = "/webhook/"
                r.raise_for_status()
                try:
                    resp = r.json()
                    return f"✓ Webhook triggered ({used_path}). Response: {str(resp)[:200]}"
                except Exception:
                    return f"✓ Webhook triggered ({used_path}). Status: {r.status_code}"
        except Exception as e:
            return _handle_error(e, "n8n webhook")

    # ─── UPTIME KUMA ──────────────────────────────────────────────────────────

    @mcp.tool(name="uptime_status", annotations={"readOnlyHint": True})
    async def uptime_status() -> str:
        """Get all Uptime Kuma monitor statuses — which services are up/down."""
        if not cfg.is_configured("uptime_kuma_url"):
            return "Error: Uptime Kuma not configured. Set UPTIME_KUMA_URL in .env"
        try:
            # Uptime Kuma has a public status page API
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.get(f"{cfg.uptime_kuma_url}/api/status-page/default")
                if r.status_code == 404:
                    # Fall back to the Prometheus /metrics endpoint. Format is:
                    #   monitor_status{monitor_name="My Site",monitor_type="http",...} 1
                    # The previous parser used line.split('"')[3] which silently
                    # returns the wrong label (e.g. monitor_type) whenever
                    # monitor_name isn't the first quoted label. Use a regex
                    # that names what it's looking for.
                    import re
                    r2 = await client.get(f"{cfg.uptime_kuma_url}/metrics")
                    if r2.status_code != 200:
                        return "Error: Could not access Uptime Kuma. Check URL and that metrics are enabled."
                    name_re = re.compile(r'monitor_name="([^"]+)"')
                    val_re = re.compile(r"\}\s+([0-9.eE+\-]+)\s*$")
                    rows: list[tuple[str, str]] = []
                    for line in r2.text.splitlines():
                        if not line.startswith("monitor_status"):
                            continue
                        nm = name_re.search(line)
                        vm = val_re.search(line)
                        if not nm or not vm:
                            continue
                        rows.append((nm.group(1), vm.group(1).strip()))
                    if not rows:
                        return "No monitor_status metrics returned (no monitors configured?)."
                    result = "## Uptime Kuma Monitors\n\n"
                    for name, status in rows:
                        # Prometheus exporter encodes UP=1, DOWN=0, PENDING=2, MAINT=3.
                        try:
                            sv = int(float(status))
                        except ValueError:
                            sv = -1
                        icon = {1: "✅", 0: "❌", 2: "🟡", 3: "🛠️"}.get(sv, "❓")
                        result += f"{icon} {name}\n"
                    return result
                r.raise_for_status()
                data = r.json()

            monitors = data.get("publicGroupList", [])
            result = "## Uptime Kuma Status\n\n"
            for group in monitors:
                result += f"### {group.get('name', 'Group')}\n"
                for monitor in group.get("monitorList", []):
                    status = monitor.get("status", 0)
                    icon = "✅" if status == 1 else ("🟡" if status == 2 else "❌")
                    name = monitor.get("name", "Unknown")
                    uptime = monitor.get("uptime", {}).get("day", "?")
                    result += f"{icon} **{name}** — {uptime}% uptime (24h)\n"
                result += "\n"
            return result
        except Exception as e:
            return _handle_error(e, "Uptime Kuma")
