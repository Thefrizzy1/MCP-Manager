"""Guided CLI login: drive the provider's interactive login without exposing a shell.

The OAuth handshake in `claude` / `claude setup-token` is a terminal program — it
paints a prompt and reads a pasted code from a tty, so plain pipes are not enough.
This module gives it a pty *under the hood* and mediates the three steps the user
actually cares about:

    start()        -> spawn the CLI, wait for it to print a login URL
    submit(code)   -> type the pasted verification code back into the CLI
    status()       -> where the flow is, plus the raw tail for diagnosis

That is deliberately narrower than a terminal in the browser: nothing here accepts
an arbitrary command, so the dashboard never becomes container shell access. One
flow runs at a time — concurrent logins would race over the same config dir.

POSIX only: `pty` does not exist on Windows. The container is Linux, so this works
where it matters, and `available()` reports false elsewhere so callers can fall
back to the `docker exec` one-liner instead of failing mysteriously.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

try:  # POSIX only
    import pty as _pty
except ImportError:  # pragma: no cover - Windows dev machines
    _pty = None

_URL_RE = re.compile(r"https?://[^\s\"'<>\\]+")
_MAX_TAIL = 8000
_LOCK = threading.Lock()

# Phrases that mean the CLI finished successfully even if no credentials file has
# appeared yet (setup-token prints a token instead of writing one).
_TOKEN_RE = re.compile(r"\b(sk-ant-oat[0-9A-Za-z_\-]+)\b")


def available() -> bool:
    return _pty is not None


class LoginFlow:
    """One in-flight guided login."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.state = "idle"          # idle|starting|awaiting_code|finishing|done|failed
        self.provider = ""
        self.account_id = ""
        self.url = ""
        self.token = ""
        self.error = ""
        self.tail = ""
        self._fd: int | None = None
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._started = 0.0

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self, *, provider: str, account_id: str, cmd: list[str],
              env: dict, cwd: str, url_timeout: float = 60.0) -> dict:
        if not available():
            return self._fail("Guided login needs a POSIX pty, which this host does not "
                              "provide. Use the shown `docker exec` command instead.")
        with _LOCK:
            if self.state in ("starting", "awaiting_code", "finishing"):
                return {"ok": False, "error": "A login is already in progress.",
                        **self.snapshot()}
            self.reset()
            self.state = "starting"
            self.provider, self.account_id = provider, account_id
            self._started = time.time()

        if not shutil.which(cmd[0]):
            return self._fail(f"`{cmd[0]}` is not installed in this container.")

        master, slave = _pty.openpty()
        try:
            self._proc = subprocess.Popen(
                cmd, stdin=slave, stdout=slave, stderr=slave,
                env=env, cwd=cwd, close_fds=True, start_new_session=True,
            )
        except OSError as e:
            os.close(master), os.close(slave)
            return self._fail(f"could not start `{cmd[0]}`: {e}")
        os.close(slave)
        self._fd = master
        self._thread = threading.Thread(target=self._pump, name="provider-login", daemon=True)
        self._thread.start()

        deadline = time.time() + url_timeout
        while time.time() < deadline:
            if self.url:
                self.state = "awaiting_code"
                return {"ok": True, **self.snapshot()}
            if self.state in ("failed", "done"):
                return {"ok": self.state == "done", **self.snapshot()}
            time.sleep(0.2)
        return self._fail("the CLI did not print a login URL in time")

    def submit(self, code: str) -> dict:
        code = (code or "").strip()
        if not code:
            return {"ok": False, "error": "Paste the code from the browser.", **self.snapshot()}
        if self.state != "awaiting_code" or self._fd is None:
            return {"ok": False, "error": "No login is waiting for a code.", **self.snapshot()}
        try:
            os.write(self._fd, (code + "\n").encode())
        except OSError as e:
            return self._fail(f"could not send the code to the CLI: {e}")
        self.state = "finishing"
        return {"ok": True, **self.snapshot()}

    def cancel(self) -> dict:
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass
        self.state = "failed"
        self.error = self.error or "cancelled"
        return self.snapshot()

    # ── internals ────────────────────────────────────────────────────────────

    def _pump(self) -> None:
        """Read the pty until the child exits, harvesting the URL and any token."""
        buf = ""
        while True:
            try:
                chunk = os.read(self._fd, 4096).decode("utf-8", "replace")
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            self.tail = (self.tail + chunk)[-_MAX_TAIL:]
            if not self.url:
                m = _URL_RE.search(buf)
                if m:
                    self.url = m.group(0).rstrip(".,)")
            if not self.token:
                t = _TOKEN_RE.search(buf)
                if t:
                    self.token = t.group(1)
        proc = self._proc
        code = proc.wait() if proc else -1
        if self.state not in ("failed",):
            self.state = "done" if code == 0 else "failed"
            if code != 0 and not self.error:
                self.error = self._diagnose(code)

    def _diagnose(self, code: int) -> str:
        low = self.tail.lower()
        if "already logged in" in low:
            return "the CLI reports it is already logged in for this account"
        if "invalid" in low and "code" in low:
            return "the verification code was rejected — start the login again"
        if "timed out" in low or "timeout" in low:
            return "the CLI timed out waiting for the browser step"
        return f"the CLI exited {code}. Tail: {self.tail[-300:].strip()}" if self.tail else \
               f"the CLI exited {code} with no output"

    def _fail(self, msg: str) -> dict:
        self.state = "failed"
        self.error = msg
        return {"ok": False, **self.snapshot()}

    def snapshot(self) -> dict:
        return {
            "state": self.state, "provider": self.provider, "account_id": self.account_id,
            "url": self.url, "error": self.error,
            # The tail is diagnostic output, capped, and shown only to an
            # authenticated dashboard user. The token is reported as present, never
            # echoed back.
            "output_tail": self.tail[-1200:],
            "token_captured": bool(self.token),
            "available": available(),
            "elapsed": int(time.time() - self._started) if self._started else 0,
        }

    def persist_token(self, root: Path, write) -> bool:
        """Hand a captured `setup-token` value to the caller's writer, then forget it."""
        if not self.token:
            return False
        write(self.token)
        self.token = ""
        return True


# One flow per process: concurrent logins would fight over the same config dir.
FLOW = LoginFlow()
