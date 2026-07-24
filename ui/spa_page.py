"""Shell for the Plutus SPA (served at /app). Minimal: the JS builds the UI."""
from __future__ import annotations

import html

from core.version_info import VERSION


def render_spa() -> str:
    v = html.escape(str(VERSION))
    return (
        '<!DOCTYPE html><html lang="en" data-theme="dark"><head><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Plutus — MCP Manager</title>'
        '<link rel="stylesheet" href="/static/spa.css?v=' + v + '">'
        '</head><body><div id="app"></div>'
        '<script src="/static/spa.js?v=' + v + '"></script>'
        '</body></html>'
    )
