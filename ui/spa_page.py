"""Shell for the Plutus SPA (served at /app).

Vite builds the real shell to ``ui/static/dist/index.html`` with ``base=/spa/``,
and ``ui.api`` mounts that directory at ``/spa``. This module's only job is to
hand that file to the browser.

It used to return a *hand-written* shell loading ``/static/spa.js`` — a legacy
pre-React bundle that still sits in ui/static. The consequence was severe and
silent: the built React app shipped in the image, was reachable at ``/spa/…``, and
was never served, so /app rendered a months-old UI and every frontend change
looked like it had no effect. When the build is missing we now say so loudly
rather than falling back to that stale bundle — the silent fallback is exactly
what let this hide.
"""
from __future__ import annotations

from pathlib import Path

_DIST_INDEX = Path(__file__).resolve().parent / "static" / "dist" / "index.html"


def dist_available() -> bool:
    return _DIST_INDEX.is_file()


def render_spa() -> str:
    """The built SPA shell, or a visible error saying the build is missing."""
    try:
        if _DIST_INDEX.is_file():
            return _DIST_INDEX.read_text(encoding="utf-8")
    except OSError:
        pass
    return _missing_build_page()


def _missing_build_page() -> str:
    return (
        '<!DOCTYPE html><html lang="en" data-theme="dark"><head><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Plutus — frontend not built</title>"
        "<style>body{background:#0b0d10;color:#e6e8eb;font:14px/1.6 system-ui,sans-serif;"
        "margin:0;display:grid;place-items:center;min-height:100vh}"
        "div{max-width:34rem;padding:2rem}code{background:#181b20;padding:.15rem .4rem;"
        "border-radius:4px}h1{font-size:1.1rem;margin:0 0 .75rem}</style></head><body><div>"
        "<h1>The web UI has not been built</h1>"
        f"<p>Expected <code>{_DIST_INDEX}</code>.</p>"
        "<p>The Docker image builds it automatically. From source, run:</p>"
        "<p><code>npm --prefix ui/web install &amp;&amp; npm --prefix ui/web run build</code></p>"
        "<p>The MCP server and API are unaffected and still running.</p>"
        "</div></body></html>"
    )
