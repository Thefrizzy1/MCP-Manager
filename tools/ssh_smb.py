"""
tools/ssh_smb.py — SSH client and SMB share management tools
SSH: multi-host support, readonly/non-root guardrails, allowlisted commands
SMB: list shares, autodiscover folders, read files from mounted shares
"""
import asyncio
import json
import os
import subprocess
from typing import Optional

from pydantic import BaseModel, Field, ConfigDict
from mcp.server.fastmcp import FastMCP
from config import cfg
from client import _handle_error, fmt_size
from core.path_guard import is_within


# ─── ALLOWED COMMANDS (read-only guardrail) ────────────────────────────────

READONLY_COMMANDS: dict[str, str] = {
    "df":           "df -h",
    "free":         "free -m",
    "uptime":       "uptime",
    "top":          "top -bn1 | head -25",
    "ps":           "ps aux --sort=-%cpu | head -30",
    "netstat":      "ss -tlnp",
    "dmesg":        "dmesg | tail -30",
    "journalctl":   "journalctl -n 50 --no-pager",
    "docker_ps":    "docker ps --format 'table {{.Names}}\\t{{.Status}}\\t{{.Ports}}'",
    "docker_stats": "docker stats --no-stream --format 'table {{.Name}}\\t{{.CPUPerc}}\\t{{.MemUsage}}'",
    "lsblk":        "lsblk -o NAME,SIZE,TYPE,MOUNTPOINT,FSTYPE",
    "ip":           "ip addr show",
    "ifconfig":     "ifconfig 2>/dev/null || ip addr",
    "who":          "who",
    "last":         "last -n 20",
    "env_safe":     "env | grep -v -i 'pass\\|secret\\|token\\|key' | sort",
    "disk_usage":   "du -sh /* 2>/dev/null | sort -rh | head -20",
}

WRITE_COMMANDS: dict[str, str] = {
    "docker_restart": None,  # arg required
    "systemctl_restart": None,  # arg required
    "systemctl_stop": None,
    "systemctl_start": None,
    "reboot": "sudo reboot",
}


def _load_ssh_hosts() -> list[dict]:
    try:
        return json.loads(cfg.ssh_hosts_json or "[]")
    except Exception:
        return []


def _get_host(name: str) -> dict | None:
    hosts = _load_ssh_hosts()
    return next((h for h in hosts if h.get("name") == name), None)


def _load_smb_shares() -> list[dict]:
    try:
        return json.loads(cfg.smb_shares_json or "[]")
    except Exception:
        return []


def register_ssh_smb_tools(mcp: FastMCP):

    # ─── SSH TOOLS ─────────────────────────────────────────────────────────

    @mcp.tool(name="ssh_list_hosts", annotations={"readOnlyHint": True})
    async def ssh_list_hosts() -> str:
        """List all configured SSH hosts with their access level."""
        hosts = _load_ssh_hosts()
        if not hosts:
            return "No SSH hosts configured. Add SSH_HOSTS to .env as JSON array."
        result = f"## SSH Hosts ({len(hosts)})\n\n"
        for h in hosts:
            readonly = h.get("readonly", True)
            guard = "🔒 read-only" if readonly else "⚡ write-enabled"
            result += f"**{h.get('name')}** — {h.get('user', 'root')}@{h.get('host')} {guard}\n"
        return result

    class SSHRunInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        host: str = Field(..., description="Host name from ssh_list_hosts")
        command: str = Field(..., description="Command shortcut (from ssh_list_commands) or prefix with 'raw:' for direct command on write-enabled hosts")
        arg: Optional[str] = Field(default=None, description="Argument for commands that need one (e.g. container name for docker_restart)")

    @mcp.tool(name="ssh_run", annotations={"readOnlyHint": False})
    async def ssh_run(params: SSHRunInput) -> str:
        """Run a command on a configured SSH host.

        For read-only hosts: only allowlisted commands work (use ssh_list_commands).
        For write-enabled hosts: prefix with 'raw:' to run any command.
        Example: ssh_run(host='plutus', command='docker_ps')
        Example: ssh_run(host='plutus', command='docker_restart', arg='jellyfin')
        """
        host = _get_host(params.host)
        if not host:
            hosts = [h.get("name") for h in _load_ssh_hosts()]
            return f"Error: Host '{params.host}' not found. Available: {', '.join(hosts) or 'none'}"

        readonly = host.get("readonly", True)
        cmd_key = params.command.strip()

        # Build the actual command string
        if cmd_key.startswith("raw:"):
            if readonly:
                return f"Error: Host '{params.host}' is read-only. Cannot run raw commands."
            cmd_str = cmd_key[4:].strip()
            if not cmd_str:
                return "Error: Empty command after 'raw:'"
        elif cmd_key in READONLY_COMMANDS:
            cmd_str = READONLY_COMMANDS[cmd_key]
        elif cmd_key in WRITE_COMMANDS:
            if readonly:
                return f"Error: '{cmd_key}' is a write command but host '{params.host}' is read-only."
            if cmd_key == "docker_restart":
                if not params.arg:
                    return "Error: docker_restart requires arg (container name)"
                # Sanitize: only alphanumeric, dash, underscore
                safe = "".join(c for c in params.arg if c.isalnum() or c in "-_.")
                cmd_str = f"docker restart {safe}"
            elif cmd_key in ("systemctl_restart", "systemctl_stop", "systemctl_start"):
                if not params.arg:
                    return f"Error: {cmd_key} requires arg (service name)"
                safe = "".join(c for c in params.arg if c.isalnum() or c in "-_.")
                action = cmd_key.replace("systemctl_", "")
                cmd_str = f"sudo systemctl {action} {safe}"
            elif cmd_key == "reboot":
                cmd_str = WRITE_COMMANDS["reboot"]
            else:
                return f"Error: Unknown write command '{cmd_key}'"
        else:
            all_cmds = list(READONLY_COMMANDS.keys()) + list(WRITE_COMMANDS.keys())
            return f"Error: Unknown command '{cmd_key}'.\nAvailable: {', '.join(all_cmds)}\nOr prefix with 'raw:' on write-enabled hosts."

        # Build SSH command — prefer key auth; fall back to sshpass if a password is stored.
        password = host.get("password") or ""
        ssh_args = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10"]
        if not password:
            ssh_args += ["-o", "BatchMode=yes"]
        else:
            ssh_args += ["-o", "PubkeyAuthentication=no", "-o", "PreferredAuthentications=password"]
        if host.get("key"):
            ssh_args += ["-i", host["key"]]
        if host.get("port"):
            ssh_args += ["-p", str(host["port"])]
        ssh_args.append(f"{host.get('user', 'root')}@{host['host']}")
        ssh_args.append(cmd_str)
        if password:
            ssh_args = ["sshpass", "-p", password] + ssh_args

        try:
            proc = await asyncio.create_subprocess_exec(
                *ssh_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            output = stdout.decode("utf-8", errors="replace")
            err_out = stderr.decode("utf-8", errors="replace")
            result = f"## SSH: {params.host} — `{cmd_str}`\n\n"
            if output:
                result += f"```\n{output[:4000]}\n```"
            if err_out and proc.returncode != 0:
                result += f"\n**stderr:**\n```\n{err_out[:500]}\n```"
            return result
        except asyncio.TimeoutError:
            return f"Error: SSH to {params.host} timed out after 30s"
        except FileNotFoundError as e:
            missing = "sshpass" if password else "ssh"
            return f"Error: '{missing}' binary not found. {e}"
        except Exception as e:
            return _handle_error(e, "SSH")

    @mcp.tool(name="ssh_list_commands", annotations={"readOnlyHint": True})
    async def ssh_list_commands() -> str:
        """List all available SSH command shortcuts with their actual commands."""
        result = "## SSH Available Commands\n\n### Read-Only (safe on all hosts)\n"
        for name, cmd in READONLY_COMMANDS.items():
            result += f"  - `{name}` → `{cmd}`\n"
        result += "\n### Write (only on write-enabled hosts)\n"
        for name in WRITE_COMMANDS:
            result += f"  - `{name}` (requires arg)\n"
        result += "\n💡 Write-enabled hosts also accept `raw:<command>` for arbitrary commands."
        return result

    class SSHHostAddInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        name: str = Field(..., description="Short name for this host e.g. 'plutus'")
        host: str = Field(..., description="IP or hostname e.g. '192.168.1.111'")
        user: str = Field(default="root", description="SSH username")
        port: int = Field(default=22, description="SSH port")
        key_path: Optional[str] = Field(default=None, description="Path to private key file e.g. '/root/.ssh/id_rsa'")
        readonly: bool = Field(default=True, description="If true, only allowlisted read-only commands are allowed")

    @mcp.tool(name="ssh_add_host", annotations={"readOnlyHint": False})
    async def ssh_add_host(params: SSHHostAddInput) -> str:
        """Add a new SSH host to the configuration.

        After adding, you can use ssh_run to execute commands on this host.
        The host config is saved to SSH_HOSTS in .env as a JSON array.
        """
        hosts = _load_ssh_hosts()
        if any(h.get("name") == params.name for h in hosts):
            return f"Error: Host '{params.name}' already exists. Remove it first."
        new_host = {
            "name": params.name,
            "host": params.host,
            "user": params.user,
            "port": params.port,
            "readonly": params.readonly,
        }
        if params.key_path:
            new_host["key"] = params.key_path
        hosts.append(new_host)
        # Save back to .env
        try:
            _save_env_key("SSH_HOSTS", json.dumps(hosts))
            return f"✓ Host '{params.name}' added ({'read-only' if params.readonly else 'write-enabled'})"
        except Exception as e:
            return f"Error saving: {e}"

    @mcp.tool(name="ssh_remove_host", annotations={"readOnlyHint": False})
    async def ssh_remove_host(params: "SSHRemoveInput") -> str:
        """Remove an SSH host from the configuration."""
        hosts = _load_ssh_hosts()
        before = len(hosts)
        hosts = [h for h in hosts if h.get("name") != params.name]
        if len(hosts) == before:
            return f"Error: Host '{params.name}' not found."
        _save_env_key("SSH_HOSTS", json.dumps(hosts))
        return f"✓ Host '{params.name}' removed."

    @mcp.tool(name="ssh_test_host", annotations={"readOnlyHint": True})
    async def ssh_test_host(params: "SSHTestInput") -> str:
        """Test SSH connectivity to a configured host."""
        host = _get_host(params.name)
        if not host:
            return f"Error: Host '{params.name}' not found."
        password = host.get("password") or ""
        ssh_args = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=8"]
        if not password:
            ssh_args += ["-o", "BatchMode=yes"]
        else:
            ssh_args += ["-o", "PubkeyAuthentication=no", "-o", "PreferredAuthentications=password"]
        if host.get("key"):
            ssh_args += ["-i", host["key"]]
        if host.get("port"):
            ssh_args += ["-p", str(host["port"])]
        ssh_args.append(f"{host.get('user', 'root')}@{host['host']}")
        ssh_args.append("echo OK_PLUTUS")
        if password:
            ssh_args = ["sshpass", "-p", password] + ssh_args
        try:
            proc = await asyncio.create_subprocess_exec(
                *ssh_args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=12)
            if b"OK_PLUTUS" in stdout:
                return f"✅ SSH connection to '{params.name}' ({host['host']}) working"
            return f"❌ SSH failed: {stderr.decode()[:200]}"
        except asyncio.TimeoutError:
            return f"❌ SSH timeout connecting to '{params.name}'"
        except FileNotFoundError as e:
            missing = "sshpass" if password else "ssh"
            return f"❌ '{missing}' binary not available: {e}"

    # ─── SMB TOOLS ─────────────────────────────────────────────────────────

    @mcp.tool(name="smb_list_shares", annotations={"readOnlyHint": True})
    async def smb_list_shares() -> str:
        """List all configured SMB shares and their mount status."""
        shares = _load_smb_shares()
        if not shares:
            return "No SMB shares configured. Add SMB_SHARES to .env as JSON array."
        result = f"## SMB Shares ({len(shares)})\n\n"
        for s in shares:
            mount = s.get("mount", "")
            mounted = os.path.ismount(mount) if mount else False
            icon = "✅" if mounted else "⚪"
            result += f"{icon} **{s.get('name')}** → //{s.get('server')}/{s.get('share')}\n"
            result += f"  Mount: {mount or '(not set)'} | User: {s.get('user', 'guest')}\n\n"
        return result

    class SMBBrowseInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        share_name: str = Field(..., description="Share name from smb_list_shares")
        path: str = Field(default="", description="Subfolder path within the share")

    @mcp.tool(name="smb_browse", annotations={"readOnlyHint": True})
    async def smb_browse(params: SMBBrowseInput) -> str:
        """Browse files and folders in a configured SMB share.

        Works via the local mount point. Share must be mounted.
        Autodiscovers folder structure.
        """
        shares = _load_smb_shares()
        share = next((s for s in shares if s.get("name") == params.share_name), None)
        if not share:
            names = [s.get("name") for s in shares]
            return f"Error: Share '{params.share_name}' not found. Available: {', '.join(names)}"

        mount = share.get("mount", "")
        if not mount:
            return f"Error: Share '{params.share_name}' has no mount point configured."

        browse_path = os.path.join(mount, params.path.lstrip("/"))

        # Security: stay within the mount point. Boundary-aware (exact match or
        # "<mount>/...") so a sibling mount like "/mnt/jobs_other" can't be
        # reached from a "/mnt/jobs" share. realpath resolves ".." first.
        if not is_within(browse_path, mount):
            return "Error: Path traversal detected."
        browse_path = os.path.realpath(browse_path)

        if not os.path.exists(browse_path):
            return f"Error: Path '{params.path}' does not exist in share '{params.share_name}'"

        try:
            entries = os.listdir(browse_path)
            dirs = sorted([e for e in entries if os.path.isdir(os.path.join(browse_path, e))])
            files = sorted([e for e in entries if os.path.isfile(os.path.join(browse_path, e))])

            result = f"## {params.share_name}/{params.path or ''}\n\n"
            result += f"📁 {len(dirs)} folders  📄 {len(files)} files\n\n"
            for d in dirs:
                result += f"📁 {d}/\n"
            for f in files:
                try:
                    size = os.path.getsize(os.path.join(browse_path, f))
                    result += f"📄 {f} ({fmt_size(size)})\n"
                except Exception:
                    result += f"📄 {f}\n"
            return result
        except PermissionError:
            return f"Error: Permission denied browsing '{params.share_name}/{params.path}'"
        except Exception as e:
            return _handle_error(e, "SMB")

    @mcp.tool(name="smb_autodiscover", annotations={"readOnlyHint": True})
    async def smb_autodiscover(params: "SMBAutodiscoverInput") -> str:
        """Autodiscover folder structure of an SMB share (up to 2 levels deep).

        Returns a tree view of the share's top-level and second-level folders.
        """
        shares = _load_smb_shares()
        share = next((s for s in shares if s.get("name") == params.share_name), None)
        if not share:
            return f"Error: Share '{params.share_name}' not found."

        mount = share.get("mount", "")
        if not mount or not os.path.exists(mount):
            return f"Error: Mount point '{mount}' not accessible. Is the share mounted?"

        result = f"## {params.share_name} — Folder Structure\n\n"
        try:
            top_dirs = sorted([
                e for e in os.listdir(mount)
                if os.path.isdir(os.path.join(mount, e)) and not e.startswith(".")
            ])
            for d in top_dirs[:30]:
                result += f"📁 **{d}/**\n"
                sub_path = os.path.join(mount, d)
                try:
                    subs = sorted([
                        e for e in os.listdir(sub_path)
                        if os.path.isdir(os.path.join(sub_path, e)) and not e.startswith(".")
                    ])
                    for s in subs[:10]:
                        result += f"  📁 {s}/\n"
                    if len(subs) > 10:
                        result += f"  ... and {len(subs)-10} more\n"
                except PermissionError:
                    result += "  (permission denied)\n"
            if len(top_dirs) > 30:
                result += f"\n...and {len(top_dirs)-30} more top-level folders"
        except Exception as e:
            result += f"Error reading share: {e}"
        return result

    class SMBAddShareInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        name: str = Field(..., description="Short name e.g. 'Offene Jobs'")
        server: str = Field(..., description="Server IP or hostname e.g. '192.168.1.111'")
        share: str = Field(..., description="Share name e.g. '01_Offene_Jobs'")
        user: str = Field(default="guest", description="SMB username")
        password: str = Field(default="", description="SMB password")
        mount: str = Field(..., description="Local mount point e.g. '/mnt/jobs'")

    @mcp.tool(name="smb_add_share", annotations={"readOnlyHint": False})
    async def smb_add_share(params: SMBAddShareInput) -> str:
        """Add a new SMB share to the configuration.

        Password is stored in .env. Mount point must be created manually.
        To actually mount: mount -t cifs //{server}/{share} {mount} -o user={user},pass={password}
        """
        shares = _load_smb_shares()
        if any(s.get("name") == params.name for s in shares):
            return f"Error: Share '{params.name}' already exists."
        shares.append({
            "name": params.name,
            "server": params.server,
            "share": params.share,
            "user": params.user,
            "password": params.password,
            "mount": params.mount,
        })
        _save_env_key("SMB_SHARES", json.dumps(shares))
        # Never echo the stored password back into tool output (it lands in
        # transcripts/logs). Show a placeholder; the real value is in .env.
        pw_hint = "***" if params.password else "(empty)"
        return (
            f"✓ Share '{params.name}' added.\n\n"
            f"To mount it:\n"
            f"```\nmkdir -p {params.mount}\n"
            f"mount -t cifs //{params.server}/{params.share} {params.mount} "
            f"-o user={params.user},password={pw_hint},uid=1000,gid=1000\n```\n\n"
            f"Replace {pw_hint} with the share password (saved in SMB_SHARES in .env). "
            f"Or add it to /etc/fstab for auto-mount."
        )

    @mcp.tool(name="smb_mount_status", annotations={"readOnlyHint": True})
    async def smb_mount_status() -> str:
        """Check mount status of all configured SMB shares and show mount command for each."""
        shares = _load_smb_shares()
        if not shares:
            return "No SMB shares configured."
        result = "## SMB Mount Status\n\n"
        for s in shares:
            mount = s.get("mount", "")
            server = s.get("server", "")
            share_name = s.get("share", "")
            user = s.get("user", "guest")
            mounted = os.path.ismount(mount) if mount else False
            icon = "✅ Mounted" if mounted else "❌ Not mounted"
            result += f"**{s.get('name')}** — {icon}\n"
            result += f"  Path: `{mount}`\n"
            if not mounted and mount:
                result += f"  Mount cmd: `mount -t cifs //{server}/{share_name} {mount} -o user={user},password=***`\n"
            result += "\n"
        return result


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _save_env_key(key: str, value: str):
    """Persist a single .env key via the canonical env store (atomic write,
    validation, and cfg sync all handled in one place). See core/env_store.py."""
    from core.env_store import update_env
    update_env({key: value})


# ─── INPUT MODELS ─────────────────────────────────────────────────────────────

class SSHRemoveInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(..., description="Host name to remove")

class SSHTestInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(..., description="Host name to test")

class SMBAutodiscoverInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    share_name: str = Field(..., description="Share name from smb_list_shares")
