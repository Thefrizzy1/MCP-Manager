"""
tools/system.py — Homelab system tools.
Covers: Docker, OMV, Ntfy, Filesystem
"""

import os
import json
import httpx
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict
from mcp.server.fastmcp import FastMCP

from config import cfg
from client import fmt_json, fmt_size, TIMEOUT, _handle_error
from core.path_guard import is_within_any
from core.redact import redact_secrets


def register_system_tools(mcp: FastMCP):

    # ─── DOCKER ───────────────────────────────────────────────────────────────

    async def _docker_get(path: str) -> dict | list:
        """Make a request to the Docker socket."""
        import httpx
        transport = httpx.AsyncHTTPTransport(uds=cfg.docker_socket)
        async with httpx.AsyncClient(transport=transport, timeout=TIMEOUT, base_url="http://docker") as client:
            r = await client.get(path)
            r.raise_for_status()
            return r.json()

    async def _docker_post(path: str, body: dict = None) -> dict | list | str:
        """POST to Docker socket."""
        import httpx
        transport = httpx.AsyncHTTPTransport(uds=cfg.docker_socket)
        async with httpx.AsyncClient(transport=transport, timeout=TIMEOUT, base_url="http://docker") as client:
            r = await client.post(path, json=body)
            r.raise_for_status()
            try:
                return r.json()
            except Exception:
                return r.text

    @mcp.tool(
        name="docker_list_containers",
        annotations={"readOnlyHint": True, "destructiveHint": False}
    )
    async def docker_list_containers() -> str:
        """List all Docker containers on plutus with status, ports, and uptime."""
        try:
            containers = await _docker_get("/containers/json?all=true")
            if not containers:
                return "No containers found."

            running = [c for c in containers if c.get("State") == "running"]
            stopped = [c for c in containers if c.get("State") != "running"]

            result = f"## Docker Containers ({len(containers)} total, {len(running)} running)\n\n"

            result += "### Running\n"
            for c in sorted(running, key=lambda x: x.get("Names", [""])[0]):
                name = c.get("Names", ["unknown"])[0].lstrip("/")
                image = c.get("Image", "?")
                status = c.get("Status", "?")
                ports = []
                for p in c.get("Ports", []):
                    if p.get("PublicPort"):
                        ports.append(f"{p.get('PublicPort')}→{p.get('PrivatePort')}")
                port_str = ", ".join(ports[:3]) if ports else "no ports"
                result += f"✅ **{name}** — {status}\n"
                result += f"   Image: {image} | Ports: {port_str}\n"
                result += f"   ID: `{c.get('Id', '')[:12]}`\n\n"

            if stopped:
                result += "### Stopped\n"
                for c in stopped:
                    name = c.get("Names", ["unknown"])[0].lstrip("/")
                    result += f"❌ **{name}** — {c.get('Status')}\n"

            return result
        except Exception as e:
            return _handle_error(e, "Docker")

    class DockerContainerInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        name_or_id: str = Field(..., description="Container name or ID", min_length=1)

    @mcp.tool(
        name="docker_get_logs",
        annotations={"readOnlyHint": True, "destructiveHint": False}
    )
    async def docker_get_logs(params: DockerContainerInput) -> str:
        """Get recent logs from a Docker container (last 50 lines).

        Containers started without a TTY return logs in Docker's 8-byte
        multiplexed frame format; containers WITH a TTY return raw bytes.
        We probe for a valid frame header on the first record and fall back
        to raw decoding when it doesn't look like framing — otherwise
        TTY-attached containers return garbage.
        """
        try:
            import httpx
            transport = httpx.AsyncHTTPTransport(uds=cfg.docker_socket)
            async with httpx.AsyncClient(transport=transport, timeout=TIMEOUT, base_url="http://docker") as client:
                r = await client.get(
                    f"/containers/{params.name_or_id}/logs",
                    params={"tail": 50, "stdout": True, "stderr": True, "timestamps": True},
                )
                r.raise_for_status()
                raw = r.content

            def _looks_framed(buf: bytes) -> bool:
                # First byte = stream type (0/1/2). Bytes 1-3 must be zero. Bytes
                # 4-7 = big-endian payload length. If those constraints hold for
                # the first record we trust the framing format.
                if len(buf) < 8:
                    return False
                if buf[0] not in (0, 1, 2):
                    return False
                if buf[1] != 0 or buf[2] != 0 or buf[3] != 0:
                    return False
                size = int.from_bytes(buf[4:8], "big")
                # Reasonable upper bound — frames bigger than the buffer aren't real.
                return size <= len(buf) - 8 + 1

            lines: list[str] = []
            if _looks_framed(raw):
                i = 0
                while i + 8 <= len(raw):
                    size = int.from_bytes(raw[i + 4:i + 8], "big")
                    chunk = raw[i + 8:i + 8 + size].decode("utf-8", errors="replace")
                    for ln in chunk.splitlines():
                        if ln.rstrip():
                            lines.append(ln.rstrip())
                    i += 8 + size
            else:
                # TTY-mode container: raw text, splitlines straight away.
                for ln in raw.decode("utf-8", errors="replace").splitlines():
                    if ln.rstrip():
                        lines.append(ln.rstrip())

            if not lines:
                return f"No logs found for '{params.name_or_id}'"
            return f"## Logs: {params.name_or_id} (last {len(lines)} lines)\n\n```\n" + "\n".join(lines[-50:]) + "\n```"
        except Exception as e:
            return _handle_error(e, f"Docker logs ({params.name_or_id})")

    @mcp.tool(
        name="docker_restart_container",
        annotations={"readOnlyHint": False, "destructiveHint": False}
    )
    async def docker_restart_container(params: DockerContainerInput) -> str:
        """Restart a Docker container by name or ID."""
        if not cfg.docker_write_enabled:
            return "Error: Docker write access is disabled. Set DOCKER_WRITE_ENABLED=true in .env to enable restarts."
        try:
            await _docker_post(f"/containers/{params.name_or_id}/restart")
            return f"✓ Container '{params.name_or_id}' restarted."
        except Exception as e:
            return _handle_error(e, f"Docker restart ({params.name_or_id})")

    @mcp.tool(
        name="docker_stop_container",
        annotations={"readOnlyHint": False, "destructiveHint": True}
    )
    async def docker_stop_container(params: DockerContainerInput) -> str:
        """Stop a running Docker container."""
        if not cfg.docker_write_enabled:
            return "Error: Docker write access is disabled. Set DOCKER_WRITE_ENABLED=true in .env to enable."
        try:
            await _docker_post(f"/containers/{params.name_or_id}/stop")
            return f"✓ Container '{params.name_or_id}' stopped."
        except Exception as e:
            return _handle_error(e, f"Docker stop ({params.name_or_id})")

    @mcp.tool(
        name="docker_start_container",
        annotations={"readOnlyHint": False, "destructiveHint": False}
    )
    async def docker_start_container(params: DockerContainerInput) -> str:
        """Start a stopped Docker container."""
        if not cfg.docker_write_enabled:
            return "Error: Docker write access is disabled. Set DOCKER_WRITE_ENABLED=true in .env to enable."
        try:
            await _docker_post(f"/containers/{params.name_or_id}/start")
            return f"✓ Container '{params.name_or_id}' started."
        except Exception as e:
            return _handle_error(e, f"Docker start ({params.name_or_id})")

    @mcp.tool(
        name="docker_system_info",
        annotations={"readOnlyHint": True, "destructiveHint": False}
    )
    async def docker_system_info() -> str:
        """Get Docker system stats: total containers, images, volumes, disk usage."""
        try:
            info = await _docker_get("/info")
            result = "## Docker System Info\n\n"
            result += f"Containers: {info.get('Containers')} total ({info.get('ContainersRunning')} running, {info.get('ContainersStopped')} stopped)\n"
            result += f"Images: {info.get('Images')}\n"
            result += f"Docker version: {info.get('ServerVersion')}\n"
            result += f"OS: {info.get('OperatingSystem')}\n"
            result += f"Kernel: {info.get('KernelVersion')}\n"
            mem = info.get('MemTotal', 0)
            result += f"Total memory: {fmt_size(mem)}\n"
            result += f"CPUs: {info.get('NCPU')}\n"
            return result
        except Exception as e:
            return _handle_error(e, "Docker")

    # ─── OMV ──────────────────────────────────────────────────────────────────

    class _OmvBadResponse(Exception):
        """OMV replied with something that isn't JSON-RPC (login page / wrong endpoint)."""

    def _omv_json(resp, base: str) -> dict:
        """Parse an OMV RPC response, turning a non-JSON body into an actionable error."""
        try:
            return resp.json()
        except Exception:
            ct = resp.headers.get("content-type", "?")
            raise _OmvBadResponse(
                f"OMV returned a non-JSON response (content-type: {ct}) from {base}/api. "
                "Usually means OMV_URL is wrong, the credentials are rejected (OMV served a "
                "login/HTML page), or your OMV version doesn't expose JSON-RPC at /api. "
                "Verify OMV_URL points to the OMV web admin and OMV_USERNAME/OMV_PASSWORD."
            )

    async def _omv_api(service: str, method: str, params: dict = None) -> dict:
        """Authenticated OMV API call with redirect + SSL handling."""
        url = f"{cfg.omv_url}/api"
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True, verify=False) as client:
            lr = await client.post(url, json={"service":"Session","method":"login","params":{"username":cfg.omv_username,"password":cfg.omv_password}})
            lr.raise_for_status()
            resp = lr.json().get("response", {})
            token = resp if isinstance(resp, str) else resp.get("token", "")
            headers = {"X-Openmediavault-Sessionid": token} if token else {}
            r = await client.post(url, headers=headers, json={"service":service,"method":method,"params":params or {}})
            r.raise_for_status()
            return r.json()

    @mcp.tool(
        name="omv_disk_health",
        annotations={"readOnlyHint": True, "destructiveHint": False}
    )
    async def omv_disk_health() -> str:
        """Get disk health and SMART status from OpenMediaVault."""
        if not cfg.is_configured("omv_url", "omv_username", "omv_password"):
            return "Error: OMV not configured. Set OMV_URL, OMV_USERNAME, OMV_PASSWORD in .env"
        try:
            base = cfg.omv_url.rstrip("/")
            # OMV usually serves a self-signed cert on its admin UI; verify=True
            # would crash the entire request before the login RPC even runs.
            async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True, verify=False) as client:
                await client.get(f"{base}/", follow_redirects=True)
                login_r = await client.post(
                    f"{base}/api",
                    json={
                        "service": "Session",
                        "method": "login",
                        "params": {"username": cfg.omv_username, "password": cfg.omv_password}
                    }
                )
                login_r.raise_for_status()
                token = _omv_json(login_r, base).get("response", {}).get("token", "")

                headers = {"X-Openmediavault-Sessionid": token}

                # Get disk list
                disks_r = await client.post(
                    f"{base}/api",
                    headers=headers,
                    json={"service": "DiskMgmt", "method": "getList", "params": {"start": 0, "limit": 50}}
                )
                disks_r.raise_for_status()
                disks = _omv_json(disks_r, base).get("response", {}).get("data", [])

            if not disks:
                return "No disks found in OMV."

            result = f"## OMV Disk Health ({len(disks)} disks)\n\n"
            for disk in disks:
                smart = disk.get("temperature", "?")
                result += f"**{disk.get('devicefile')}** — {disk.get('model', 'Unknown')}\n"
                result += f"  Size: {disk.get('size', '?')} | Temp: {smart}°C\n"
                result += f"  SMART: {disk.get('hdparm', {}).get('drivetype', 'N/A')}\n\n"
            return result
        except _OmvBadResponse as e:
            return f"Error: {e}"
        except Exception as e:
            return _handle_error(e, "OMV")

    @mcp.tool(
        name="omv_system_info",
        annotations={"readOnlyHint": True, "destructiveHint": False}
    )
    async def omv_system_info() -> str:
        """Get OMV system information: CPU, memory, uptime, load."""
        if not cfg.is_configured("omv_url", "omv_username", "omv_password"):
            return "Error: OMV not configured."
        try:
            base = cfg.omv_url.rstrip("/")
            # OMV usually serves a self-signed cert on its admin UI; verify=True
            # would crash the entire request before the login RPC even runs.
            async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True, verify=False) as client:
                await client.get(f"{base}/", follow_redirects=True)
                login_r = await client.post(
                    f"{base}/api",
                    json={
                        "service": "Session",
                        "method": "login",
                        "params": {"username": cfg.omv_username, "password": cfg.omv_password}
                    }
                )
                login_r.raise_for_status()
                token = _omv_json(login_r, base).get("response", {}).get("token", "")
                headers = {"X-Openmediavault-Sessionid": token}

                sys_r = await client.post(
                    f"{base}/api",
                    headers=headers,
                    json={"service": "System", "method": "getInformation", "params": {}}
                )
                sys_r.raise_for_status()
                info = _omv_json(sys_r, base).get("response", {})

            result = "## OMV System Info\n\n"
            result += f"Hostname: {info.get('hostname', '?')}\n"
            result += f"Uptime: {info.get('uptime', '?')}\n"
            result += f"Load: {info.get('loadaverage', '?')}\n"
            result += f"CPU usage: {info.get('cpuUsage', '?')}%\n"
            mem = info.get('memTotal', 0)
            mem_used = info.get('memUsed', 0)
            result += f"Memory: {fmt_size(mem_used)} / {fmt_size(mem)}\n"
            return result
        except _OmvBadResponse as e:
            return f"Error: {e}"
        except Exception as e:
            return _handle_error(e, "OMV")

    # ─── NTFY ─────────────────────────────────────────────────────────────────

    class NtfyInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        message: str = Field(..., description="Notification message to send", min_length=1, max_length=4096)
        topic: Optional[str] = Field(default=None, description="Ntfy topic (defaults to NTFY_DEFAULT_TOPIC from config)")
        title: Optional[str] = Field(default=None, description="Notification title")
        priority: Optional[str] = Field(default=None, description="Priority: 'min', 'low', 'default', 'high', 'urgent'")
        tags: Optional[list[str]] = Field(default=None, description="Emoji tags e.g. ['tada', 'warning']")

    @mcp.tool(
        name="ntfy_send",
        annotations={"readOnlyHint": False, "destructiveHint": False}
    )
    async def ntfy_send(params: NtfyInput) -> str:
        """Send a push notification via Ntfy to the configured topic or a specified topic.

        Reaches Friso's phone immediately. Good for reminders, alerts, task completions.
        """
        if not cfg.is_configured("ntfy_url"):
            return "Error: Ntfy not configured. Set NTFY_URL in .env"
        try:
            topic = params.topic or cfg.ntfy_default_topic
            headers = {"Content-Type": "text/plain; charset=utf-8"}

            if cfg.ntfy_username and cfg.ntfy_password:
                import base64
                creds = base64.b64encode(f"{cfg.ntfy_username}:{cfg.ntfy_password}".encode()).decode()
                headers["Authorization"] = f"Basic {creds}"

            if params.title:
                headers["Title"] = params.title
            if params.priority:
                headers["Priority"] = params.priority
            if params.tags:
                headers["Tags"] = ",".join(params.tags)

            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.post(
                    f"{cfg.ntfy_url}/{topic}",
                    headers=headers,
                    content=params.message.encode()
                )
                r.raise_for_status()
            return f"✓ Notification sent to topic '{topic}': {params.message[:50]}{'...' if len(params.message) > 50 else ''}"
        except Exception as e:
            return _handle_error(e, "Ntfy")

    # ─── FILESYSTEM ───────────────────────────────────────────────────────────

    def _check_path(path: str) -> bool:
        """Check if path is within an allowed directory (boundary-aware)."""
        return is_within_any(path, cfg.filesystem_allowed_paths)

    class FsListInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        path: str = Field(..., description="Directory path to list", min_length=1)

    @mcp.tool(
        name="fs_list_directory",
        annotations={"readOnlyHint": True, "destructiveHint": False}
    )
    async def fs_list_directory(params: FsListInput) -> str:
        """List files and directories in an allowed path on the server."""
        if not _check_path(params.path):
            allowed = ", ".join(cfg.filesystem_allowed_paths)
            return f"Error: Path '{params.path}' is not in allowed directories: {allowed}"
        try:
            if not os.path.exists(params.path):
                return f"Error: Path '{params.path}' does not exist."
            if not os.path.isdir(params.path):
                return f"Error: '{params.path}' is a file, not a directory. Use fs_read_file instead."

            entries = os.listdir(params.path)
            dirs = sorted([e for e in entries if os.path.isdir(os.path.join(params.path, e))])
            files = sorted([e for e in entries if os.path.isfile(os.path.join(params.path, e))])

            result = f"## Directory: {params.path}\n\n"
            result += f"📁 {len(dirs)} directories, 📄 {len(files)} files\n\n"
            for d in dirs:
                result += f"📁 {d}/\n"
            for f in files:
                size = os.path.getsize(os.path.join(params.path, f))
                result += f"📄 {f} ({fmt_size(size)})\n"
            return result
        except PermissionError:
            return f"Error: Permission denied reading '{params.path}'"
        except Exception as e:
            return _handle_error(e, "Filesystem")

    class FsReadInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        path: str = Field(..., description="File path to read", min_length=1)
        max_lines: int = Field(default=100, description="Max lines to return", ge=1, le=1000)
        reveal_secrets: bool = Field(default=False, description="If true, do NOT mask secret-looking values (passwords/tokens/keys). Default masks them so they don't leak into transcripts/logs.")

    @mcp.tool(
        name="fs_read_file",
        annotations={"readOnlyHint": True, "destructiveHint": False}
    )
    async def fs_read_file(params: FsReadInput) -> str:
        """Read the contents of a text file in an allowed path."""
        if not _check_path(params.path):
            allowed = ", ".join(cfg.filesystem_allowed_paths)
            return f"Error: Path '{params.path}' is not in allowed directories: {allowed}"
        try:
            if not os.path.exists(params.path):
                return f"Error: File '{params.path}' does not exist."
            if os.path.isdir(params.path):
                return f"Error: '{params.path}' is a directory. Use fs_list_directory instead."

            size = os.path.getsize(params.path)
            if size > 1024 * 1024:  # 1MB limit
                return f"Error: File too large ({fmt_size(size)}). Max 1MB."

            with open(params.path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()

            total = len(lines)
            shown = lines[:params.max_lines]
            body = "".join(shown)
            redacted_note = ""
            if not params.reveal_secrets:
                body, n = redact_secrets(body)
                if n:
                    redacted_note = f"\n\n_{n} secret value(s) masked. Pass reveal_secrets=true to see them._"
            result = f"## File: {params.path} ({total} lines, {fmt_size(size)})\n\n"
            result += "```\n" + body + "\n```"
            if total > params.max_lines:
                result += f"\n\n...{total - params.max_lines} more lines (increase max_lines to see more)"
            result += redacted_note
            return result
        except PermissionError:
            return f"Error: Permission denied reading '{params.path}'"
        except UnicodeDecodeError:
            return f"Error: File '{params.path}' is binary — cannot read as text."
        except Exception as e:
            return _handle_error(e, "Filesystem")

    @mcp.tool(name="fs_list_shares", annotations={"readOnlyHint": True})
    async def fs_list_shares() -> str:
        """List all configured filesystem shares and their paths."""
        result = "## Configured Filesystem Shares\n\n"
        for path in cfg.filesystem_allowed_paths:
            exists = os.path.exists(path)
            icon = "✅" if exists else "❌"
            if exists:
                try:
                    entries = os.listdir(path)
                    count = len(entries)
                    result += f"{icon} **{path}** — {count} items\n"
                except Exception:
                    result += f"{icon} **{path}** — (permission denied)\n"
            else:
                result += f"{icon} **{path}** — not mounted\n"
        return result

    class FsWriteInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        path: str = Field(..., description="File path to write", min_length=1)
        content: str = Field(..., description="Text content to write", min_length=0)
        append: bool = Field(default=False, description="If true, append to existing file instead of overwriting")

    @mcp.tool(name="fs_write_file", annotations={"readOnlyHint": False, "destructiveHint": False})
    async def fs_write_file(params: FsWriteInput) -> str:
        """Write text content to a file in an allowed path."""
        if not _check_path(params.path):
            return f"Error: Path '{params.path}' not in allowed directories."
        try:
            os.makedirs(os.path.dirname(params.path), exist_ok=True)
            mode = "a" if params.append else "w"
            with open(params.path, mode, encoding="utf-8") as f:
                f.write(params.content)
            action = "Appended to" if params.append else "Written"
            return f"✓ {action} '{params.path}' ({len(params.content)} chars)"
        except Exception as e:
            return _handle_error(e, "Filesystem")

    class FsMoveInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        source: str = Field(..., description="Source file path", min_length=1)
        destination: str = Field(..., description="Destination file path", min_length=1)

    @mcp.tool(name="fs_move_file", annotations={"readOnlyHint": False, "destructiveHint": False})
    async def fs_move_file(params: FsMoveInput) -> str:
        """Move or rename a file within allowed paths."""
        if not _check_path(params.source):
            return f"Error: Source path not in allowed directories."
        if not _check_path(params.destination):
            return f"Error: Destination path not in allowed directories."
        try:
            import shutil
            os.makedirs(os.path.dirname(params.destination), exist_ok=True)
            shutil.move(params.source, params.destination)
            return f"✓ Moved '{params.source}' → '{params.destination}'"
        except Exception as e:
            return _handle_error(e, "Filesystem")

    class FsRecentInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        path: str = Field(..., description="Directory to search", min_length=1)
        limit: int = Field(default=20, description="Max files to return", ge=1, le=100)
        days: int = Field(default=7, description="Files modified within this many days", ge=1, le=365)

    @mcp.tool(name="fs_recent_files", annotations={"readOnlyHint": True})
    async def fs_recent_files(params: FsRecentInput) -> str:
        """List recently modified files in a directory."""
        if not _check_path(params.path):
            return f"Error: Path not in allowed directories."
        try:
            import time
            cutoff = time.time() - (params.days * 86400)
            recent = []
            for root, dirs, files in os.walk(params.path):
                if not _check_path(root):
                    continue
                for f in files:
                    full = os.path.join(root, f)
                    try:
                        mtime = os.path.getmtime(full)
                        if mtime >= cutoff:
                            recent.append((mtime, full))
                    except Exception:
                        continue
                if len(recent) > 500:
                    break

            recent.sort(reverse=True)
            recent = recent[:params.limit]

            if not recent:
                return f"No files modified in the last {params.days} days in '{params.path}'"

            import datetime
            result = f"## Recent Files in {params.path} (last {params.days} days)\n\n"
            for mtime, path in recent:
                dt = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
                size = os.path.getsize(path)
                result += f"- {dt} — {os.path.relpath(path, params.path)} ({fmt_size(size)})\n"
            return result
        except Exception as e:
            return _handle_error(e, "Filesystem")

    class FsSearchInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        path: str = Field(..., description="Directory to search in", min_length=1)
        pattern: str = Field(..., description="Filename pattern to search for (case-insensitive substring)", min_length=1, max_length=200)

    @mcp.tool(
        name="fs_search_files",
        annotations={"readOnlyHint": True, "destructiveHint": False}
    )
    async def fs_search_files(params: FsSearchInput) -> str:
        """Search for files matching a pattern in an allowed directory."""
        if not _check_path(params.path):
            allowed = ", ".join(cfg.filesystem_allowed_paths)
            return f"Error: Path '{params.path}' is not in allowed directories: {allowed}"
        try:
            pattern = params.pattern.lower()
            matches = []
            for root, dirs, files in os.walk(params.path):
                if not _check_path(root):
                    continue
                for f in files:
                    if pattern in f.lower():
                        full = os.path.join(root, f)
                        size = os.path.getsize(full)
                        matches.append((full, size))
                if len(matches) >= 100:
                    break

            if not matches:
                return f"No files matching '{params.pattern}' found in '{params.path}'"
            result = f"## Search: '{params.pattern}' in {params.path} ({len(matches)} results)\n\n"
            for path, size in matches[:50]:
                result += f"- {path} ({fmt_size(size)})\n"
            if len(matches) > 50:
                result += f"\n...and {len(matches) - 50} more"
            return result
        except Exception as e:
            return _handle_error(e, "Filesystem")
