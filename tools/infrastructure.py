"""
tools/infrastructure.py — Infrastructure & security tools
Covers: Syncthing, Tailscale, Fail2ban, Proton Bridge (plan), SSH exec, Test-All report
"""

import os
import re
import json
import asyncio
import subprocess
import httpx
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict
from mcp.server.fastmcp import FastMCP

from config import cfg
from client import TIMEOUT, _handle_error, fmt_size


async def _run_blocking(*args, **kwargs):
    """Run subprocess.run in a worker thread so the async event loop is not blocked.

    Long-running commands (ssh -o ConnectTimeout=10, tailscale status, fail2ban-client)
    would otherwise stall every other MCP request and dashboard probe for the duration.
    """
    return await asyncio.to_thread(subprocess.run, *args, **kwargs)


# ssh_exec builds a remote command string executed by the target's shell, so the
# host and arg must be charset-validated to prevent command/argument injection.
_SSH_HOST_RE = re.compile(r"[A-Za-z0-9._-]{1,253}")
_SSH_ARG_RE = re.compile(r"[A-Za-z0-9._/:-]{1,200}")


def _valid_ssh_host(host: str) -> bool:
    return bool(host) and not host.startswith("-") and _SSH_HOST_RE.fullmatch(host) is not None


def _valid_ssh_arg(arg: str) -> bool:
    return _SSH_ARG_RE.fullmatch(arg) is not None
from core.invoke_tool import invoke_mcp_tool_fn
from core.result_status import text_looks_successful
from core.tool_manager_adapter import ToolRegistryAdapter
from core.tool_registry import (
    ZERO_PARAM_HEALTH_TOOLS,
    is_tool_environment_ready,
    looks_like_missing_service_config,
    merged_tool_payload,
)


def register_infrastructure_tools(mcp: FastMCP):

    # ─── SYNCTHING ────────────────────────────────────────────────────────────

    def _st_headers() -> dict:
        return {"X-API-Key": cfg.syncthing_api_key}

    @mcp.tool(name="syncthing_status", annotations={"readOnlyHint": True})
    async def syncthing_status() -> str:
        """Get Syncthing system status — version, uptime, connections, CPU/RAM."""
        if not cfg.is_configured("syncthing_url", "syncthing_api_key"):
            return "Error: Syncthing not configured. Set SYNCTHING_URL and SYNCTHING_API_KEY in .env"
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                sys_r = await client.get(f"{cfg.syncthing_url}/rest/system/status", headers=_st_headers())
                ver_r = await client.get(f"{cfg.syncthing_url}/rest/system/version", headers=_st_headers())
                conn_r = await client.get(f"{cfg.syncthing_url}/rest/system/connections", headers=_st_headers())
                sys_r.raise_for_status()
                ver_r.raise_for_status()
                conn_r.raise_for_status()
                sys = sys_r.json()
                ver = ver_r.json()
                conns = conn_r.json().get("connections", {})

            connected = sum(1 for c in conns.values() if c.get("connected"))
            total = len(conns)

            result = "## Syncthing Status\n\n"
            result += f"**Version:** {ver.get('version', '?')}\n"
            result += f"**Uptime:** {sys.get('uptime', 0) // 3600}h {(sys.get('uptime', 0) % 3600) // 60}min\n"
            result += f"**Devices:** {connected}/{total} connected\n"
            result += f"**CPU:** {sys.get('cpuPercent', 0):.1f}%\n"
            result += f"**RAM:** {fmt_size(sys.get('alloc', 0))}\n"
            result += f"**My Device ID:** `{sys.get('myID', '?')}`\n"
            return result
        except Exception as e:
            return _handle_error(e, "Syncthing")

    @mcp.tool(name="syncthing_folders", annotations={"readOnlyHint": True})
    async def syncthing_folders() -> str:
        """List all Syncthing sync folders with their sync status."""
        if not cfg.is_configured("syncthing_url", "syncthing_api_key"):
            return "Error: Syncthing not configured."
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                cfg_r = await client.get(f"{cfg.syncthing_url}/rest/config/folders", headers=_st_headers())
                cfg_r.raise_for_status()
                folders = cfg_r.json()

            result = f"## Syncthing Folders ({len(folders)})\n\n"
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
                for folder in folders:
                    fid = folder.get("id", "?")
                    label = folder.get("label", fid)
                    path = folder.get("path", "?")
                    try:
                        stat_r = await client.get(
                            f"{cfg.syncthing_url}/rest/db/status",
                            headers=_st_headers(), params={"folder": fid}
                        )
                        stat = stat_r.json()
                        state = stat.get("state", "?")
                        need_files = stat.get("needFiles", 0)
                        in_sync = stat.get("inSyncFiles", 0)
                        total_files = stat.get("globalFiles", 0)
                        icon = "✅" if state == "idle" and need_files == 0 else "🔄"
                        result += f"{icon} **{label}** (`{fid}`)\n"
                        result += f"  Path: {path}\n"
                        result += f"  State: {state} | {in_sync}/{total_files} files synced\n"
                        if need_files > 0:
                            result += f"  ⚠️ {need_files} files need syncing\n"
                    except Exception:
                        result += f"❓ **{label}** — status unavailable\n"
                    result += "\n"
            return result
        except Exception as e:
            return _handle_error(e, "Syncthing")

    @mcp.tool(name="syncthing_devices", annotations={"readOnlyHint": True})
    async def syncthing_devices() -> str:
        """List all Syncthing devices and their connection status."""
        if not cfg.is_configured("syncthing_url", "syncthing_api_key"):
            return "Error: Syncthing not configured."
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                cfg_r = await client.get(f"{cfg.syncthing_url}/rest/config/devices", headers=_st_headers())
                conn_r = await client.get(f"{cfg.syncthing_url}/rest/system/connections", headers=_st_headers())
                cfg_r.raise_for_status()
                conn_r.raise_for_status()
                devices = cfg_r.json()
                connections = conn_r.json().get("connections", {})

            result = f"## Syncthing Devices ({len(devices)})\n\n"
            for dev in devices:
                dev_id = dev.get("deviceID", "?")
                name = dev.get("name", dev_id[:8])
                conn = connections.get(dev_id, {})
                connected = conn.get("connected", False)
                icon = "🟢" if connected else "⚫"
                result += f"{icon} **{name}**\n"
                result += f"  ID: `{dev_id[:16]}...`\n"
                if connected:
                    addr = conn.get("address", "?")
                    speed_in = conn.get("inBytesTotal", 0)
                    speed_out = conn.get("outBytesTotal", 0)
                    result += f"  Address: {addr}\n"
                    result += f"  Traffic: ↓{fmt_size(speed_in)} ↑{fmt_size(speed_out)}\n"
                result += "\n"
            return result
        except Exception as e:
            return _handle_error(e, "Syncthing")

    class SyncthingRescanInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        folder_id: str = Field(..., description="Folder ID to rescan (from syncthing_folders)", min_length=1)

    @mcp.tool(name="syncthing_rescan", annotations={"readOnlyHint": False})
    async def syncthing_rescan(params: SyncthingRescanInput) -> str:
        """Trigger a rescan of a Syncthing folder."""
        if not cfg.is_configured("syncthing_url", "syncthing_api_key"):
            return "Error: Syncthing not configured."
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.post(
                    f"{cfg.syncthing_url}/rest/db/scan",
                    headers=_st_headers(), params={"folder": params.folder_id}
                )
                r.raise_for_status()
            return f"✓ Rescan triggered for folder: `{params.folder_id}`"
        except Exception as e:
            return _handle_error(e, "Syncthing")

    # ─── TAILSCALE ────────────────────────────────────────────────────────────

    @mcp.tool(name="tailscale_status", annotations={"readOnlyHint": True})
    async def tailscale_status() -> str:
        """Get Tailscale network status — all devices, their IPs, and online status.

        Runs 'tailscale status' on the host via Docker exec or direct command.
        """
        try:
            # Try running tailscale status directly
            result_proc = await _run_blocking(
                ["tailscale", "status", "--json"],
                capture_output=True, text=True, timeout=10
            )
            if result_proc.returncode == 0:
                data = json.loads(result_proc.stdout)
                peers = data.get("Peer", {})
                self_info = data.get("Self", {})

                result = "## Tailscale Status\n\n"
                result += f"**Self:** {self_info.get('HostName', '?')} — {', '.join(self_info.get('TailscaleIPs', []))}\n\n"
                result += f"### Peers ({len(peers)})\n"
                for peer_id, peer in peers.items():
                    online = peer.get("Online", False)
                    icon = "🟢" if online else "⚫"
                    hostname = peer.get("HostName", "?")
                    ips = ", ".join(peer.get("TailscaleIPs", []))
                    last_seen = peer.get("LastSeen", "")
                    os_name = peer.get("OS", "")
                    result += f"{icon} **{hostname}** ({os_name})\n"
                    result += f"  IPs: {ips}\n"
                    if not online and last_seen:
                        result += f"  Last seen: {last_seen[:16]}\n"
                    result += "\n"
                return result
            else:
                # Fallback: parse text output
                result_proc2 = await _run_blocking(
                    ["tailscale", "status"],
                    capture_output=True, text=True, timeout=10
                )
                if result_proc2.returncode == 0:
                    return f"## Tailscale Status\n\n```\n{result_proc2.stdout}\n```"
                return f"Error running tailscale: {result_proc.stderr}"
        except FileNotFoundError:
            return "Error: tailscale command not found. Tailscale may not be installed on this container."
        except Exception as e:
            return _handle_error(e, "Tailscale")

    @mcp.tool(name="tailscale_ping", annotations={"readOnlyHint": True})
    async def tailscale_ping(params: "TailscalePingInput") -> str:
        """Ping a device on the Tailscale network to check connectivity."""
        try:
            result_proc = await _run_blocking(
                ["tailscale", "ping", "-c", "3", params.hostname_or_ip],
                capture_output=True, text=True, timeout=15
            )
            output = result_proc.stdout + result_proc.stderr
            return f"## Tailscale Ping: {params.hostname_or_ip}\n\n```\n{output}\n```"
        except Exception as e:
            return _handle_error(e, "Tailscale")

    # ─── FAIL2BAN ─────────────────────────────────────────────────────────────

    @mcp.tool(name="fail2ban_status", annotations={"readOnlyHint": True})
    async def fail2ban_status() -> str:
        """Get Fail2ban status — active jails, banned IPs, and statistics."""
        try:
            # Get list of jails
            jails_proc = await _run_blocking(
                ["fail2ban-client", "status"],
                capture_output=True, text=True, timeout=10
            )
            if jails_proc.returncode != 0:
                # Try via docker exec if not available directly
                jails_proc = await _run_blocking(
                    ["docker", "exec", "fail2ban", "fail2ban-client", "status"],
                    capture_output=True, text=True, timeout=10
                )

            if jails_proc.returncode != 0:
                return "Error: fail2ban-client not accessible. Check if fail2ban is running."

            output = jails_proc.stdout
            result = "## Fail2ban Status\n\n"
            result += f"```\n{output}\n```\n"

            # Extract jail names and get details
            import re
            jail_matches = re.findall(r"Jail list:\s*(.+)", output)
            if jail_matches:
                jails = [j.strip() for j in jail_matches[0].split(",") if j.strip()]
                result += "\n### Jail Details\n"
                for jail in jails[:10]:
                    jail_proc = await _run_blocking(
                        ["fail2ban-client", "status", jail],
                        capture_output=True, text=True, timeout=5
                    )
                    if jail_proc.returncode == 0:
                        result += f"\n**{jail}:**\n```\n{jail_proc.stdout}\n```"

            return result
        except Exception as e:
            return f"Error accessing fail2ban: {e}\nMake sure fail2ban-client is accessible in the container."

    class Fail2banUnbanInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        ip: str = Field(..., description="IP address to unban", min_length=7, max_length=45)
        jail: str = Field(default="sshd", description="Jail name to unban from")

    @mcp.tool(name="fail2ban_unban", annotations={"readOnlyHint": False, "destructiveHint": False})
    async def fail2ban_unban(params: Fail2banUnbanInput) -> str:
        """Unban an IP address from a Fail2ban jail."""
        try:
            proc = await _run_blocking(
                ["fail2ban-client", "set", params.jail, "unbanip", params.ip],
                capture_output=True, text=True, timeout=10
            )
            if proc.returncode == 0:
                return f"✓ Unbanned {params.ip} from jail '{params.jail}'"
            return f"Error: {proc.stderr or proc.stdout}"
        except Exception as e:
            return _handle_error(e, "fail2ban")

    # ─── SSH EXEC ─────────────────────────────────────────────────────────────

    # Allowlisted commands only — security critical
    SSH_ALLOWED_COMMANDS = {
        "df": "df -h",
        "free": "free -m",
        "uptime": "uptime",
        "top": "top -bn1 | head -20",
        "ps": "ps aux | head -30",
        "docker_ps": "docker ps",
        "docker_logs": None,  # handled specially
        "netstat": "netstat -tlnp",
        "dmesg": "dmesg | tail -20",
        "journalctl": "journalctl -n 30 --no-pager",
        "systemctl_status": None,  # handled specially
        "ping": None,  # handled specially
        "tail_log": None,  # handled specially
    }

    class SSHExecInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        host: str = Field(..., description="Hostname or Tailscale IP to SSH into", min_length=1)
        command: str = Field(..., description=f"Command to run. Allowed: {', '.join(SSH_ALLOWED_COMMANDS.keys())}", min_length=1)
        arg: Optional[str] = Field(default=None, description="Optional argument (e.g. service name for systemctl, container for docker_logs)")

    @mcp.tool(name="ssh_exec", annotations={"readOnlyHint": True})
    async def ssh_exec(params: SSHExecInput) -> str:
        """Run an allowlisted command on a remote device via SSH over Tailscale.

        Only pre-approved read-only commands are permitted for security.
        Allowed commands: df, free, uptime, top, ps, docker_ps, netstat, dmesg, journalctl, ping
        """
        if params.command not in SSH_ALLOWED_COMMANDS:
            allowed = ", ".join(SSH_ALLOWED_COMMANDS.keys())
            return f"Error: Command '{params.command}' not in allowlist.\nAllowed: {allowed}"

        # The command string is executed by the REMOTE shell, so any unsanitized
        # interpolation of `arg`/`host` is command injection. Validate the host
        # against a hostname/IP charset (and reject a leading '-' that ssh would
        # treat as an option), and restrict `arg` to a safe charset.
        host = params.host
        if not _valid_ssh_host(host):
            return "Error: Invalid host (expected a hostname or IP, no shell metacharacters)."
        arg = params.arg or ""
        if arg and not _valid_ssh_arg(arg):
            return "Error: Invalid argument — only letters, digits, and . _ - / : are allowed."

        try:
            # Build the actual command (arg already charset-validated above)
            if params.command == "docker_logs" and arg:
                cmd_str = f"docker logs {arg} --tail 30"
            elif params.command == "systemctl_status" and arg:
                cmd_str = f"systemctl status {arg} --no-pager"
            elif params.command == "ping" and arg:
                cmd_str = f"ping -c 3 {arg}"
            elif params.command == "tail_log" and arg:
                # Only allow /var/log paths, and no parent-dir traversal.
                if not arg.startswith("/var/log") or ".." in arg:
                    return "Error: tail_log only allows /var/log/* paths"
                cmd_str = f"tail -n 30 {arg}"
            else:
                cmd_str = SSH_ALLOWED_COMMANDS.get(params.command, "")
                if not cmd_str:
                    return f"Error: Command '{params.command}' requires an argument."

            ssh_cmd = [
                "ssh", "-o", "StrictHostKeyChecking=no",
                "-o", "ConnectTimeout=10",
                "-o", "BatchMode=yes",
                "--", f"root@{host}", cmd_str
            ]

            proc = await _run_blocking(ssh_cmd, capture_output=True, text=True, timeout=30)
            output = proc.stdout or proc.stderr
            return f"## SSH: {params.host} — {cmd_str}\n\n```\n{output[:3000]}\n```"
        except subprocess.TimeoutExpired:
            return f"Error: SSH connection to {params.host} timed out."
        except Exception as e:
            return _handle_error(e, "SSH")

    # ─── PROTON BRIDGE ────────────────────────────────────────────────────────

    @mcp.tool(name="proton_bridge_status", annotations={"readOnlyHint": True})
    async def proton_bridge_status() -> str:
        """Check if Proton Bridge SMTP/IMAP is reachable on the Windows machine.

        Proton Bridge runs locally on 127.0.0.1 so this checks via SSH to the Windows PC.
        """
        return """## Proton Bridge Status

Proton Bridge runs on Friso's Windows PC (192.168.1.5) at:
- SMTP: 127.0.0.1:1025
- IMAP: 127.0.0.1:1143

**Implementation Plan:**
To send emails via Proton Bridge from n8n or the MCP server:
1. Proton Bridge must be running on the Windows PC
2. Since it binds to localhost only, email sending needs to happen FROM the Windows machine
3. Options:
   a. **n8n SMTP node** — configure with host=192.168.1.5 won't work (localhost binding)
   b. **Run a SMTP relay** — set up a relay on plutus that forwards to Windows:1025 via SSH tunnel
   c. **SSH tunnel** — `ssh -L 1025:127.0.0.1:1025 friso@192.168.1.5` from plutus, then use localhost:1025
   d. **Proton Bridge Bridge Proxy** — run proton-bridge in headless mode on a Linux machine

**Recommended approach:**
Set up a persistent SSH tunnel from plutus to Windows:
```bash
ssh -fNL 1025:127.0.0.1:1025 friso@192.168.1.5
```
Then configure n8n SMTP node with host=127.0.0.1, port=1025.

**Status check:** Cannot directly check bridge status without SSH access to Windows."""

    class SendEmailInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        to: str = Field(..., description="Recipient email address", min_length=1)
        subject: str = Field(..., description="Email subject", min_length=1, max_length=500)
        body: str = Field(..., description="Email body (plain text)", min_length=1)

    @mcp.tool(name="send_email", annotations={"readOnlyHint": False})
    async def send_email(params: SendEmailInput) -> str:
        """Send an email via configured SMTP (Proton Bridge or other SMTP server).

        Requires SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD in .env
        """
        if not cfg.is_configured("smtp_host", "smtp_username", "smtp_password"):
            return "Error: SMTP not configured. Set SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD in .env"
        try:
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart

            msg = MIMEMultipart()
            msg["From"] = cfg.smtp_username
            msg["To"] = params.to
            msg["Subject"] = params.subject
            msg.attach(MIMEText(params.body, "plain"))

            port = int(cfg.smtp_port) if cfg.is_configured("smtp_port") else 587
            with smtplib.SMTP(cfg.smtp_host, port, timeout=15) as server:
                if port != 25:
                    server.starttls()
                if cfg.smtp_password:
                    server.login(cfg.smtp_username, cfg.smtp_password)
                server.send_message(msg)

            return f"✓ Email sent to {params.to}: '{params.subject}'"
        except Exception as e:
            return f"Error sending email: {e}"

    # ─── TEST ALL REPORT ──────────────────────────────────────────────────────

    @mcp.tool(name="test_all_tools", annotations={"readOnlyHint": True})
    async def test_all_tools() -> str:
        """Run all zero-parameter tools and return a full pass/fail health report.

        Takes 30-60 seconds. Tests every configured service and reports what's working.
        """
        zero_param_tools = list(ZERO_PARAM_HEALTH_TOOLS)

        tm = ToolRegistryAdapter(mcp)
        results: dict[str, list] = {"pass": [], "fail": [], "skip": [], "unset": []}

        for tool_name in zero_param_tools:
            tool = tm.get_tool(tool_name)
            if not tool:
                results["skip"].append((tool_name, "not registered or disabled"))
                continue
            if not is_tool_environment_ready(tool_name):
                results["unset"].append(tool_name)
                continue
            try:
                merged = merged_tool_payload(tool_name, {})
                output = await asyncio.wait_for(
                    invoke_mcp_tool_fn(tool.fn, payload=merged), timeout=120.0
                )
                text = str(output or "")
                ok = text_looks_successful(text)
                if ok:
                    results["pass"].append(tool_name)
                elif looks_like_missing_service_config(text):
                    results["unset"].append(tool_name)
                else:
                    error_msg = text[:160] if text else "empty response"
                    results["fail"].append((tool_name, error_msg))
            except asyncio.TimeoutError:
                results["fail"].append((tool_name, "timeout after 120s"))
            except Exception as e:
                results["fail"].append((tool_name, str(e)[:120]))

        total = len(zero_param_tools)
        passed = len(results["pass"])
        failed = len(results["fail"])
        unset_unique = sorted(set(results["unset"]))

        report = "# Plutus MCP Health Report\n\n"
        report += (
            f"**{passed} passing** · {failed} failing · "
            f"{len(unset_unique)} not set up · {len(results['skip'])} other skipped · "
            f"{total} total\n\n"
        )

        if results["pass"]:
            report += f"## ✅ Passing ({passed})\n"
            for name in results["pass"]:
                report += f"  - {name}\n"
            report += "\n"

        if unset_unique:
            report += f"## ⚪ Not set up ({len(unset_unique)})\n"
            report += "(Missing .env credentials, tailscale CLI, or tool reported “not configured”.)\n\n"
            for name in unset_unique:
                report += f"  - {name}\n"
            report += "\n"

        if results["fail"]:
            report += f"## ❌ Failing ({failed})\n"
            for name, error in results["fail"]:
                report += f"  - **{name}**: {error}\n"
            report += "\n"

        if results["skip"]:
            report += f"## ⏭ Skipped ({len(results['skip'])})\n"
            for name, reason in results["skip"]:
                report += f"  - {name}: {reason}\n"

        report += (
            "\n_Report counts “not set up” separately from failures so skipped services do not "
            "look broken._"
        )

        return report

    class PlutusToolSlicerInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        intent: str = Field(
            "",
            description=(
                "Comma- or space-separated category names (calendar, tasks, files, media, "
                "ai, system_ops, monitoring, …) and/or free-text keywords. Empty = no filter."
            ),
        )
        apply: bool = Field(
            False,
            description=(
                "If true, persist `intent` as the active server-wide intent so the MCP "
                "tools/list response is actually shrunk to the matching subset. Reconnect "
                "your MCP client after applying for the new manifest to take effect."
            ),
        )

    @mcp.tool(name="plutus_tool_slicer", annotations={"readOnlyHint": False})
    async def plutus_tool_slicer(params: PlutusToolSlicerInput) -> str:
        """Inspect or apply the Plutus tool-slicer.

        - With just `intent`: returns the categorized slice without changing anything.
        - With `apply=true`: also persists the intent so the live MCP manifest is
          filtered to that subset (clients must reconnect to refresh tool list).
        - `intent=""` + `apply=true`: clears the active intent (exposes all tools).

        Available categories: calendar, tasks, contacts, notes, files, home, automation,
        notifications, monitoring, system_ops, ai, weather, search, finance, media,
        photos, trivia, ip_network, crypto, meta.
        """
        from pathlib import Path
        from core.tool_gate import build_tool_slice, set_active_intent

        root = Path(__file__).resolve().parents[1]
        if params.apply:
            set_active_intent(root, params.intent)
        return json.dumps(build_tool_slice(root, params.intent), separators=(",", ":"))


# ─── INPUT MODELS ─────────────────────────────────────────────────────────────

class TailscalePingInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    hostname_or_ip: str = Field(..., description="Tailscale hostname or IP to ping", min_length=1)
