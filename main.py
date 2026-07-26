"""main.py — Plutus MCP v5 — MCP on 8765, Web UI on 8766.

Thin orchestrator. The MCP surface (FastMCP + tools) and the app-wide singletons
live in ``ui.runtime``; every HTTP endpoint lives in ``ui.api.*`` and is assembled
by ``ui.api.build_ui_app``. This file only does: dependency check, the two-process
launch (MCP in this process, Web UI in a child), and lifecycle/signals.
"""
from __future__ import annotations


def _ensure_plutus_runtime_dependencies() -> None:
    """Validate dependencies; auto-install only when PLUTUS_AUTO_INSTALL=1."""
    import importlib
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent
    missing = ""
    for mod in (
        "uvicorn",
        "fastapi",
        "httpx",
        "pydantic",
        "dotenv",
        "mcp.server.fastmcp",
    ):
        try:
            importlib.import_module(mod)
        except ImportError:
            missing = mod
            break
    else:
        return

    if os.getenv("PLUTUS_AUTO_INSTALL", "").strip().lower() not in {"1", "true", "yes"}:
        print(
            "Plutus: missing Python package: "
            f"{missing}\nRun:\n  {sys.executable} -m pip install -r \"{root / 'requirements.txt'}\"\n"
            "Or set PLUTUS_AUTO_INSTALL=1 for development auto-install.",
            file=sys.stderr,
            flush=True,
        )
        sys.exit(1)

    print("Plutus: missing Python packages — installing from requirements.txt …", flush=True)
    r = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(root / "requirements.txt")],
        cwd=str(root),
    )
    if r.returncode != 0:
        print(
            "Plutus: pip install failed. Run manually:\n"
            f"  {sys.executable} -m pip install -r \"{root / 'requirements.txt'}\"",
            flush=True,
        )
        sys.exit(1)
    os.execv(sys.executable, [sys.executable, *sys.argv])


_ensure_plutus_runtime_dependencies()

import asyncio
import errno
import multiprocessing
import os
import sys
import threading
import time
from urllib.request import urlopen

import uvicorn

from config import DEFAULT_UI_PASSWORD, allow_empty_ui_password, cfg
from core.mcp_bearer_middleware import MCPBearerGateMiddleware
from core.recent_runs import ensure_data_dir
from ui.api import build_ui_app
from ui.runtime import ROOT, mcp

_shutting_down = False


def _is_address_in_use(exc: OSError) -> bool:
    if exc.errno == errno.EADDRINUSE:
        return True
    # Windows: WSAEADDRINUSE
    if getattr(exc, "winerror", None) == 10048:
        return True
    return False


def run_ui():
    try:
        uvicorn.run(build_ui_app(), host="0.0.0.0", port=cfg.ui_port, log_level="warning")
    except OSError as e:
        if _is_address_in_use(e):
            print(
                f"Plutus: Web UI cannot bind 0.0.0.0:{cfg.ui_port} — port already in use.\n"
                f"   ({e})\n"
                "   Stop the other process, change UI_PORT in .env, or set UI_ENABLED=false for MCP-only.",
                file=sys.stderr,
                flush=True,
            )
        else:
            print(f"Plutus: Web UI failed: {e}", file=sys.stderr, flush=True)
        sys.exit(1)


async def _run_mcp_streamable_http() -> None:
    starlette_app = mcp.streamable_http_app()
    starlette_app.add_middleware(MCPBearerGateMiddleware)
    server = uvicorn.Server(
        uvicorn.Config(starlette_app, host=cfg.mcp_host, port=cfg.mcp_port, log_level="warning")
    )
    try:
        await server.serve()
    except OSError as e:
        if _is_address_in_use(e):
            print(
                f"Plutus: MCP cannot bind {cfg.mcp_host}:{cfg.mcp_port} — port already in use.\n"
                f"   ({e})\n"
                "   Stop the other process or set MCP_PORT in .env.",
                file=sys.stderr,
                flush=True,
            )
        raise


def _run_mcp_main() -> None:
    try:
        asyncio.run(_run_mcp_streamable_http())
    except OSError:
        sys.exit(1)


def _wait_for_ui_start(proc: multiprocessing.Process, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{cfg.ui_port}/server/health"
    while time.time() < deadline:
        if proc.exitcode not in (None, 0):
            return False
        try:
            with urlopen(url, timeout=0.35) as resp:
                if 200 <= resp.status < 500:
                    return True
        except Exception:
            time.sleep(0.15)
    return proc.is_alive()


def _sweep_stale_tmp() -> None:
    """Remove leftover *.tmp files from atomic writes interrupted by a crash."""
    for d in (ROOT, ROOT / "data"):
        try:
            for f in d.glob("*.tmp"):
                f.unlink(missing_ok=True)
        except OSError:
            pass


def _install_signal_handlers(ui_proc: "multiprocessing.Process | None") -> None:
    import signal

    def _terminate_ui() -> None:
        if ui_proc and ui_proc.is_alive():
            ui_proc.terminate()
            ui_proc.join(timeout=5)

    def _handler(signum, _frame):
        global _shutting_down
        _shutting_down = True
        _terminate_ui()
        sys.exit(0)

    import atexit
    atexit.register(_terminate_ui)
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):
            pass  # not in main thread / unsupported platform


def _start_ui_watchdog(ui_proc: "multiprocessing.Process") -> None:
    """If the UI child dies unexpectedly, exit the main process so Docker's
    restart policy recycles the whole container (otherwise the dashboard would
    be down while MCP keeps the container 'up')."""
    def _watch() -> None:
        ui_proc.join()
        if not _shutting_down:
            print("⚠️  Plutus: Web UI process exited unexpectedly — stopping for container restart.",
                  file=sys.stderr, flush=True)
            os._exit(1)

    threading.Thread(target=_watch, name="ui-watchdog", daemon=True).start()


if __name__ == "__main__":
    ensure_data_dir(ROOT)
    _sweep_stale_tmp()
    if not cfg.ui_password and not allow_empty_ui_password():
        print(
            "⚠️  UI_PASSWORD is empty in .env — /ui will return 503 until you set a password "
            "(or PLUTUS_ALLOW_EMPTY_UI_PASSWORD=1 for dev only).",
            flush=True,
        )
    ui_proc = None
    if cfg.ui_enabled:
        ui_proc = multiprocessing.Process(target=run_ui, daemon=True)
        ui_proc.start()
        if not _wait_for_ui_start(ui_proc):
            print(
                f"Plutus: Web UI process exited early (code {ui_proc.exitcode}) — "
                f"check port {cfg.ui_port}.",
                flush=True,
            )
        else:
            _start_ui_watchdog(ui_proc)
    _install_signal_handlers(ui_proc)
    print("🚀 Plutus MCP v5", flush=True)
    print(f"   MCP:    http://0.0.0.0:{cfg.mcp_port}/mcp", flush=True)
    if cfg.mcp_require_bearer:
        print("   MCP auth: Bearer token required (MCP_REQUIRE_BEARER=true)", flush=True)
    if cfg.ui_enabled:
        print(f"   Web UI: http://0.0.0.0:{cfg.ui_port}/ui", flush=True)
        if os.getenv("UI_PASSWORD") is None:
            print(
                f"   ℹ️  Login: username `{cfg.ui_username}`, password `{DEFAULT_UI_PASSWORD}` "
                "(set UI_PASSWORD in .env to change)",
                flush=True,
            )
    else:
        print("   Web UI: off (UI_ENABLED=false) — MCP API only, lower RAM", flush=True)
    _run_mcp_main()
