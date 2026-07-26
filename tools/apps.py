"""MCP App widget (SEP-1865): a connection-status panel Plutus can render inside
a compliant client instead of a wall of text.

Design goals from the brief:
- Self-contained HTML (inline CSS/JS, no external fetch) — it runs in a sandboxed
  iframe.
- Degrades: a host without MCP Apps support gets the markdown report from the
  tool and everything still works.
- Any action goes back through a real tool call — the template never fetches
  Plutus's own API directly.
"""
from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

_ROOT = Path(__file__).resolve().parents[1]
_UI_URI = "ui://plutus/connections"
# Current MCP Apps MIME (the older text/html+skybridge is the OpenAI-era form).
_UI_MIME = "text/html;profile=mcp-app"

# Self-contained: no external CSS/JS/fonts/images. The host forwards the tool's
# text result (the markdown table below) over the postMessage bridge; the script
# parses that known table format into a styled panel. Until data arrives it shows
# a clean loading header, so it degrades on any host.
CONNECTIONS_TEMPLATE = """<!doctype html><html><head><meta charset="utf-8">
<style>
:root{color-scheme:light dark}
*{box-sizing:border-box}
body{margin:0;font:13px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
 background:#16181d;color:#e6e8ec;padding:14px}
h1{font-size:15px;margin:0 0 2px}
.sub{color:#a0a6b0;font-size:12px;margin-bottom:12px}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #262a31}
th{color:#a0a6b0;font-size:11px;text-transform:uppercase;letter-spacing:.04em}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px}
.ok{background:#3fb950}.no{background:#8a93a3}
.mono{font-family:ui-monospace,Menlo,monospace}
</style></head><body>
<h1>Plutus connections</h1>
<div class="sub" id="sub">Loading connection status…</div>
<table><thead><tr><th>Service</th><th>Status</th><th>Tools</th></tr></thead>
<tbody id="rows"></tbody></table>
<script>
function esc(s){return String(s==null?'':s).replace(/[&<>]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c]})}
function renderMarkdown(md){
  var lines=String(md||'').split('\\n'), body=[], seenSep=false, header='';
  var m=/^(\\d+)\\/(\\d+) configured/.exec(md||''); if(m){header=m[0];}
  for(var i=0;i<lines.length;i++){
    var ln=lines[i].trim();
    if(/^\\|\\s*-+/.test(ln)){seenSep=true;continue;}
    if(seenSep && ln.charAt(0)==='|'){
      var c=ln.split('|').map(function(x){return x.trim()}).filter(function(x){return x.length});
      if(c.length>=3){var ok=/config/i.test(c[1])&&!/not/i.test(c[1]);
        body.push('<tr><td>'+esc(c[0])+'</td><td><span class="dot '+(ok?'ok':'no')+'"></span>'+esc(c[1])+'</td><td class="mono">'+esc(c[2])+'</td></tr>');}
    }
  }
  if(header)document.getElementById('sub').textContent=header;
  if(body.length)document.getElementById('rows').innerHTML=body.join('');
}
function extractText(p){
  if(!p)return '';
  if(typeof p==='string')return p;
  if(p.markdown)return p.markdown;
  if(Array.isArray(p.content)){for(var i=0;i<p.content.length;i++){if(p.content[i]&&p.content[i].text)return p.content[i].text;}}
  return p.text||'';
}
window.addEventListener('message',function(ev){
  var d=ev.data||{};
  var p=(d.params&&d.params.result)||d.result||d.toolResult||d;
  var t=extractText(p); if(t)renderMarkdown(t);
});
</script></body></html>"""


class StatusInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _connection_rows() -> list[dict]:
    from config import cfg
    from core.service_registry import all_services
    from core.service_utils import is_service_configured

    rows: list[dict] = []
    for s in all_services(_ROOT):
        if "public" in (s.get("section") or "").lower():
            continue  # homelab connections only
        rows.append(
            {
                "id": s["id"],
                "label": s["label"],
                "configured": bool(is_service_configured(s, cfg)),
                "tools": len(s.get("tools", [])),
            }
        )
    return rows


def status_markdown(rows: list[dict]) -> str:
    conf = sum(1 for r in rows if r["configured"])
    out = [
        f"# Plutus connections\n\n{conf}/{len(rows)} configured\n",
        "| Service | Status | Tools |",
        "|---|---|---|",
    ]
    for r in rows:
        out.append(f"| {r['label']} | {'Configured' if r['configured'] else 'Not configured'} | {r['tools']} |")
    return "\n".join(out)


def register_app_tools(mcp: FastMCP, *, allow: "set[str] | None" = None) -> None:
    from core.profiles import tool_filter
    mcp = tool_filter(mcp, allow)

    @mcp.resource(_UI_URI, mime_type=_UI_MIME)
    def connections_ui() -> str:
        return CONNECTIONS_TEMPLATE

    @mcp.tool(
        name="plutus_status",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        meta={"ui": {"resourceUri": _UI_URI}},
    )
    async def plutus_status(params: StatusInput) -> str:
        """Connection status for Plutus. Renders as a live panel in an MCP Apps
        host, and as a markdown report everywhere else."""
        return status_markdown(_connection_rows())
