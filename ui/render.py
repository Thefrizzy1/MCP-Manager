"""
ui/render.py  Plutus MCP v5  Service-first dashboard
Status: CSS state dots; no emoji glyph dependency.
"""
from __future__ import annotations
import html
import json
import os
from pathlib import Path

from config import DEFAULT_UI_PASSWORD, cfg
from core.tool_gate import load_gate
from core.service_utils import is_service_configured as _svc_configured

from core.builtin_services import OPEN_URL_BY_ID

def _ui_text(value: object) -> str:
    text = str(value or "")
    return " ".join("".join(ch if ord(ch) < 128 else " " for ch in text).split())


def _is_configured(svc: dict) -> bool:
    return _svc_configured(svc, cfg)


def _open_url(svc: dict) -> str:
    if svc.get("open_from_env"):
        return (os.getenv(svc["open_from_env"], "") or "").strip()
    key = svc.get("open_url_key") or OPEN_URL_BY_ID.get(svc["id"])
    if not key:
        return ""
    return str(getattr(cfg, key, "") or "").strip()


def _service_sort_rank(svc: dict, hc: dict) -> tuple[int, str]:
    configured = _is_configured(svc)
    h = hc.get(svc["id"])
    if configured and h is True:
        return (0, svc["label"].lower())
    if configured and h is False:
        return (1, svc["label"].lower())
    if configured:
        return (2, svc["label"].lower())
    return (3, svc["label"].lower())


def _card_class(svc: dict, hc: dict) -> tuple[str,str,str]:
    sid,configured = svc["id"],_is_configured(svc)
    h = hc.get(sid)
    if not configured:
        return "card-unconfigured","","Not configured"
    if h is True:
        return "card-working","","Working"
    if h is False:
        return "card-failing","","Configured but not working"
    return "card-configured","","Configured"


def _tools_gate_block(svc: dict, root: Path) -> str:
    tools = svc.get("tools") or []
    if not tools:
        return (
            '<div class="pl-full pl-muted"><h4 class="rep-hdr">MCP tools</h4>'
            '<p class="phint">No tools on this card.</p></div>'
        )
    g = load_gate(root)
    dis_t = set(g.get("disabled_tools") or [])
    dis_sec = set(g.get("disabled_sections") or [])
    sec = str(svc.get("section") or "").strip().lower()
    section_off = sec in dis_sec
    parts: list[str] = [
        '<div class="pl-full tool-gate-box"><h4 class="rep-hdr">MCP tools</h4>',
        '<p class="phint">Toggle whether each tool appears in MCP (clients may need reconnect).</p>',
    ]
    if section_off:
        parts.append(
            '<p class="phint" style="color:var(--yw)">This section is disabled globally  enable it in '
            "Settings  Tools &amp; MCP exposure.</p>"
        )
    parts.append('<ul class="tool-gate-ul">')
    for t in tools:
        tn = str(t.get("name") or "").strip()
        if not tn:
            continue
        lab = html.escape(t.get("label") or tn)
        tq = html.escape(tn, quote=True)
        enabled = (tn not in dis_t) and (not section_off)
        cls = "tgt-on" if enabled else "tgt-off"
        btn = "Disable" if enabled else "Enable"
        nxt = "false" if enabled else "true"
        parts.append(
            f'<li class="tool-gate-li" data-tool-gate="{html.escape(tn, quote=True)}">'
            f'<span class="tg-meta"><code>{html.escape(tn)}</code>  {lab}</span>'
            f'<button type="button" class="btn-tgate {cls}" onclick="event.stopPropagation();'
            f'plutusToggleTool(\'{tq}\',{nxt})">{btn}</button></li>'
        )
    parts.append("</ul></div>")
    return "".join(parts)


def _ssh_manager_html() -> str:
    return """<div class="pl-full hostmgr" data-mgr="ssh">
  <h4>SSH hosts</h4>
  <p class="phint">Configured hosts  added here are saved to <code>SSH_HOSTS</code> in <code>.env</code>.</p>
  <div class="hostmgr-list" id="ssh-hosts-list"><span class="phint">Loading</span></div>
  <h4 style="margin-top:14px">Add a host</h4>
  <div class="hostmgr-form">
    <div class="cf"><label>Name (short alias)</label><input type="text" id="ssh-add-name" placeholder="plutus" autocomplete="off"></div>
    <div class="cf"><label>Host (IP or hostname)</label><input type="text" id="ssh-add-host" placeholder="192.168.1.111" autocomplete="off"></div>
    <div class="cf"><label>User</label><input type="text" id="ssh-add-user" value="root" autocomplete="off"></div>
    <div class="cf"><label>Port</label><input type="number" id="ssh-add-port" value="22" min="1" max="65535"></div>
    <div class="cf"><label>Password (optional, key auth preferred)</label><input type="password" id="ssh-add-password" autocomplete="new-password"></div>
    <div class="cf"><label>Private key path (optional)</label><input type="text" id="ssh-add-key" placeholder="/root/.ssh/id_rsa" autocomplete="off"></div>
    <div class="cf cf-chk"><label class="chk"><input type="checkbox" id="ssh-add-readonly" checked> Read-only mode (allowlisted commands only)</label></div>
    <div class="hostmgr-actions">
      <button type="button" class="btn-save-row" onclick="event.stopPropagation();sshAddHost()">Add host</button>
      <span class="save-ok" id="ssh-add-ok"></span>
    </div>
  </div>
</div>"""


def _smb_manager_html() -> str:
    return """<div class="pl-full hostmgr" data-mgr="smb">
  <h4>SMB shares</h4>
  <p class="phint">Configured shares  added here are saved to <code>SMB_SHARES</code> in <code>.env</code>. Mount point must be created on the host.</p>
  <div class="hostmgr-list" id="smb-shares-list"><span class="phint">Loading</span></div>
  <h4 style="margin-top:14px">Add a share</h4>
  <div class="hostmgr-form">
    <div class="cf"><label>Name (alias)</label><input type="text" id="smb-add-name" placeholder="Offene Jobs" autocomplete="off"></div>
    <div class="cf"><label>Server (IP or hostname)</label><input type="text" id="smb-add-server" placeholder="192.168.1.111" autocomplete="off"></div>
    <div class="cf"><label>Share name</label><input type="text" id="smb-add-share" placeholder="01_Offene_Jobs" autocomplete="off"></div>
    <div class="cf"><label>User</label><input type="text" id="smb-add-user" value="guest" autocomplete="off"></div>
    <div class="cf"><label>Password</label><input type="password" id="smb-add-password" autocomplete="new-password"></div>
    <div class="cf"><label>Local mount point</label><input type="text" id="smb-add-mount" placeholder="/mnt/jobs" autocomplete="off"></div>
    <div class="hostmgr-actions">
      <button type="button" class="btn-save-row" onclick="event.stopPropagation();smbAddShare()">Add share</button>
      <span class="save-ok" id="smb-add-ok"></span>
    </div>
  </div>
</div>"""


def _service_card(svc: dict, hc: dict, root: Path) -> str:
    from core.service_logos import service_logo_img_html

    sid = svc["id"]
    label = _ui_text(svc["label"])
    tag = _ui_text(svc["tag"])
    desc = _ui_text(svc.get("desc", ""))
    n_tools = len(svc.get("tools", []))
    config_keys = svc.get("config_keys", [])
    configured = _is_configured(svc)
    cc, dot, dot_title = _card_class(svc, hc)
    rank_n = _service_sort_rank(svc, hc)[0]
    lbl_esc = html.escape(label, quote=True)

    ou = _open_url(svc).strip()
    base_http = ou
    if (not base_http.startswith("http")) and config_keys:
        fk = config_keys[0][0]
        if svc.get("config_from_env"):
            base_http = (os.getenv(fk, "") or "").strip()
        else:
            base_http = str(getattr(cfg, fk.lower(), "") or "").strip()
    logo_dom = (svc.get("logo_domain") or "").strip() or None
    logo_html = service_logo_img_html(
        service_id=sid,
        root=root,
        logo_domain_override=logo_dom,
        http_base_url=base_http if base_http.startswith("http") else None,
        alt_label=label,
    )
    initial = ""
    for ch in label:
        if ch.isalnum():
            initial = ch.upper()
            break
    if not initial:
        initial = "?"
    fb = (
        f'<span class="svc-logo-fallback" title="{lbl_esc}" aria-hidden="true">'
        f"{html.escape(initial)}</span>"
    )
    si_visual = (
        f'<span class="si-stack">{logo_html}</span>' if logo_html else f'<span class="si-stack">{fb}</span>'
    )

    open_btn = ""
    if ou:
        href_esc = html.escape(ou, quote=True)
        open_btn = (
            f'<a class="btn-open" href="{href_esc}" target="_blank" '
            f'rel="noopener noreferrer" onclick="event.stopPropagation()">Open</a>'
        )

    extras_note = ""
    doc = (svc.get("documentation_url") or "").strip()
    if doc:
        href_esc = html.escape(doc, quote=True)
        extras_note += (
            f'<p class="phint"><a class="btn-open" href="{href_esc}" target="_blank" '
            f'rel="noopener noreferrer" onclick="event.stopPropagation()">API documentation</a></p>'
        )
    notes = (svc.get("api_notes") or "").strip()
    if notes:
        extras_note += (
            '<div class="pl-full"><h4>API notes</h4>'
            f'<pre class="wiz-pre api-notes">{html.escape(notes)}</pre></div>'
        )

    cfg_html = ""
    if config_keys:
        for key, lbl, ph, secret in config_keys:
            if svc.get("config_from_env"):
                val = (os.getenv(key, "") or "").strip()
            else:
                val = getattr(cfg, key.lower(), "") or ""
            itype = "password" if secret else "text"
            cfg_html += (
                f'<div class="cf"><label>{html.escape(lbl)}</label>'
                f'<input type="{itype}" data-key="{key}" value="{html.escape(str(val), quote=True)}" '
                f'placeholder="{html.escape(ph, quote=True)}" autocomplete="off"></div>'
            )
        cfg_html += (
            f'<button type="button" class="btn-save-row" onclick="event.stopPropagation();'
            f'saveConfig(\'{sid}\')">Save this row</button>'
            f'<span class="save-ok" id="ok-{sid}"></span>'
        )

    conn_block = (
        f'<div class="pl-full"><h4>Connection</h4>{extras_note}{cfg_html}</div>'
        if cfg_html
        else (
            f'<div class="pl-full pl-muted"><h4>Connection</h4>{extras_note}'
            '<p class="phint">No URL fields for this integration.</p></div>'
            if extras_note
            else '<div class="pl-full pl-muted"><h4>Connection</h4>'
            '<p class="phint">No URL fields for this integration.</p></div>'
        )
    )
    custom_block = ""
    if sid == "ssh":
        custom_block = _ssh_manager_html()
    elif sid == "filesystem":
        custom_block = _smb_manager_html()
    tools_block = _tools_gate_block(svc, root)

    dot_title_esc = html.escape(dot_title, quote=True)
    smoke_btn = (
        f'<button type="button" class="btn-tsmoke" onclick="smokeTools(\'{sid}\',this)" title="Run each tool with safe inputs and verify it returns real data">Test</button>'
        if n_tools
        else ""
    )
    tool_count_html = f'<span class="tool-count" title="Callable MCP functions on this card">{n_tools} fn</span>'
    return f"""<div class="sc {cc}" id="card-{sid}" data-tag="{tag}" data-configured="{"1" if configured else "0"}" data-status="{cc}" data-rank="{rank_n}" data-label="{lbl_esc}">
<div class="row-line">
  <button type="button" class="chev-btn" onclick="toggleCard('{sid}')" aria-label="Expand"><span class="chv" id="chev-{sid}"></span></button>
  <div class="sl" onclick="toggleCard('{sid}')">{si_visual}<div class="sl-text"><span class="sn">{html.escape(label)}</span><span class="sd">{html.escape(desc)}</span></div></div>
  <div class="row-actions" onclick="event.stopPropagation()">
    {open_btn or '<span class="no-open">No URL</span>'}
    <button type="button" class="btn-tconn" onclick="testService('{sid}')" title="Check connection">Check</button>
    {smoke_btn}
    <button type="button" class="btn-disable-svc svc-enabled" id="dis-{sid}" onclick="toggleServiceDisable('{sid}',this)" title="Toggle MCP exposure">On</button>
    <span class="sdot" title="{dot_title_esc}">{dot}</span>
    {tool_count_html}
  </div>
</div>
<div class="sb" id="body-{sid}">
  <div class="expand-inner">
    {conn_block}
    {custom_block}
    {tools_block}
    <div class="rep-wrap">
      <h4 class="rep-hdr">Output</h4>
      <pre class="pre-out" id="res-{sid}">Use Check or Run. Fixed inputs only.</pre>
    </div>
  </div>
</div>
</div>"""


def _sechdr_row(title: str, *, add_button: bool = False) -> str:
    t = html.escape(title, quote=False)
    add = (
        '<button type="button" class="btn-sechdr-add" onclick="openAddCustomModal()" '
        'title="Add a custom integration (JSON snippet + save)">+</button>'
        if add_button
        else ""
    )
    return (
        f'<div class="sechdr sechdr-row"><span class="sechdr-txt">{t}</span>'
        f'<span class="sechdr-line"></span>{add}</div>'
    )


def dashboard_page(health_cache: dict, tool_count: int, recent: list) -> str:
    from core.service_registry import all_services
    from core.version_info import VERSION as plutus_ver

    root = Path(__file__).resolve().parent.parent
    merged = all_services(root)

    # Split into three sections: system first, then selfhosted, then public
    sys_svcs = [s for s in merged if s["section"] == "system"]
    sh = [s for s in merged if s["section"] == "selfhosted"]
    pub = [s for s in merged if s["section"] == "public"]
    cust = [s for s in merged if s["section"] == "custom"]

    sys_svcs = sorted(sys_svcs, key=lambda s: _service_sort_rank(s, health_cache))
    sh = sorted(sh, key=lambda s: _service_sort_rank(s, health_cache))
    pub = sorted(pub, key=lambda s: _service_sort_rank(s, health_cache))
    cust = sorted(cust, key=lambda s: _service_sort_rank(s, health_cache))

    all_svcs = sys_svcs + sh + pub + cust
    function_count = sum(len(s.get("tools") or []) for s in all_svcs)
    conf_n = sum(1 for s in all_svcs if _is_configured(s))
    fail_n = sum(1 for s in all_svcs if _is_configured(s) and health_cache.get(s["id"]) is False)
    work_n = sum(
        1 for s in all_svcs
        if _is_configured(s) and health_cache.get(s["id"]) is not False
    )

    # Subgroup helpers
    def _subgroup(svcs, tag_groups):
        """Render cards grouped by tag with a subheader."""
        parts = []
        for group_label, tags in tag_groups:
            group_svcs = [s for s in svcs if s.get("tag") in tags]
            if not group_svcs:
                continue
            parts.append(f'<div class="subgroup-hdr">{html.escape(group_label)}</div>')
            for s in group_svcs:
                parts.append(_service_card(s, health_cache, root))
        # Any remaining not in a group
        tagged = {s["id"] for g_label, tags in tag_groups for t in tags for s in svcs if s.get("tag") == t}
        rest = [s for s in svcs if s["id"] not in tagged]
        for s in rest:
            parts.append(_service_card(s, health_cache, root))
        return "\n".join(parts)

    sys_cards = _subgroup(sys_svcs, [
        ("File Storage", ["storage"]),
        ("Remote Access", ["network"]),
        ("Container & Host", ["system"]),
    ])
    sh_cards = _subgroup(sh, [
        ("Media & Downloads", ["media", "arr", "photos"]),
        ("Cloud & Productivity", ["cloud", "personal", "notes"]),
        ("Home & Automation", ["home", "automation", "notifications"]),
        ("Monitoring & Security", ["monitoring", "security", "sync"]),
        ("AI & Local Compute", ["ai"]),
    ])
    pub_cards = _subgroup(pub, [
        ("AI & Creative", ["ai"]),
        ("Utilities", ["utilities", "search", "reference"]),
        ("Finance & Communication", ["finance", "communication"]),
    ])

    custom_cards = "\n".join(_service_card(s, health_cache, root) for s in cust)
    custom_inner = (
        custom_cards if custom_cards.strip()
        else '<p class="custom-empty">No custom cards yet. Use + here to add one.</p>'
    )
    custom_section = (
        f'<div class="sec">{_sechdr_row("Custom integrations", add_button=True)}'
        f'<div class="sg">{custom_inner}</div></div>'
    )

    rec_html = "".join(
        f'<div class="rr"><code>{r.get("tool", "?")}</code><span>{r.get("ts", "")}</span></div>'
        for r in reversed((recent or [])[-8:])
    )
    tkn = cfg.mcp_bearer_token
    tkn_disp = tkn[:8] + "..." if tkn else "(not set)"

    _ca = Path(__file__).resolve().parent.parent / "data" / "ca.pem"
    cert_ok = _ca.exists() and _ca.stat().st_size > 0
    mcp_http_url = f"http://{cfg.mcp_lan_host}:{cfg.mcp_port}/mcp"
    pub_b = (cfg.public_mcp_base or "").strip().rstrip("/")
    mcp_https_url = ""
    if pub_b.startswith(("https://", "http://")):
        mcp_https_url = pub_b + "/mcp"
    https_ready = pub_b.startswith("https://")
    primary_copy_url = mcp_https_url if https_ready else mcp_http_url
    ui_show_url = f"http://{cfg.mcp_lan_host}:{cfg.ui_port}/ui"
    bearer_chk = " checked" if cfg.mcp_require_bearer else ""
    ui_on_chk = " checked" if cfg.ui_enabled else ""
    # Pre-compute snippet to avoid f-string nesting issues
    _mcp_primary = mcp_https_url or mcp_http_url
    _allow_flag = "" if https_ready else ',"--allow-http"'
    _claude_snippet = html.escape(
        '{"mcpServers":{"plutus":{"command":"npx","args":["mcp-remote","' + _mcp_primary + '"' + _allow_flag + ']}}}}',
        quote=False
    )
    from core.dashboard_api import tailscale_snippet as _ts_hint

    raw_ts_block = _ts_hint().replace("\u2014", "-").replace("\u2026", "...")
    ts_block = html.escape("".join(ch if ord(ch) < 128 else " " for ch in raw_ts_block))
    upd_repo = (os.getenv("PLUTUS_UPDATES_REPO") or "").strip()
    upd_line = (
        f'<code>{html.escape(upd_repo, quote=True)}</code>'
        if upd_repo
        else '(not set - add <code>PLUTUS_UPDATES_REPO</code> to .env as <code>owner/repo</code>)'
    )

    return (f"""<!DOCTYPE html>
<html lang="en" data-theme="dark"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Plutus MCP</title>
<link rel="stylesheet" href="/static/dashboard.css?v={plutus_ver}"></head><body class="plutus-dash">
<div class="tb">
  <div class="tb-brand">
    <span class="tb-brand-mark" aria-hidden="true">P</span>
    <h1>Plutus MCP</h1>
  </div>
  <div class="ts" title="Service health summary">
    <span class="ch cg">{work_n} ok</span>
    <span class="ch cw">{fail_n} fail</span>
    <span class="ch cb">{conf_n} cfg</span>
    <span class="ch cb">{function_count} fn</span>
  </div>
  <input type="search" class="tb-fsrch" placeholder="Search services" aria-label="Search" oninput="searchSvcs(this.value)">
  <div class="tb-spacer"></div>
  <button type="button" class="tlink" id="btn-mcp-copy" title="Copy MCP URL for Claude Desktop / clients"><span>MCP URL</span> <code id="mcp-url-code">{html.escape(primary_copy_url)}</code></button>
  <button type="button" class="tbtn" id="btn-hide-uncfg" onclick="toggleHideUnconfigured()">Hide unconfigured</button>
  <button type="button" class="tbtn" id="btn-theme" onclick="toggleTheme()" title="Dark / light">Theme</button>
  <button type="button" class="tbtn" onclick="document.getElementById('modal').classList.add('open')">Settings</button>
</div>

<div class="sec">{_sechdr_row("System")}<div class="sg">{sys_cards}</div></div>
<div class="sec">{_sechdr_row("Self-Hosted Services")}<div class="sg">{sh_cards}</div></div>
<div class="sec">{_sechdr_row("Public APIs")}<div class="sg">{pub_cards}</div></div>
{custom_section}

<div class="modal" id="report-modal">
  <div class="mbox report-box">
    <h2>Health report</h2>
    <p class="rep-sub">Per-service probes plus zero-argument tool smoke tests.</p>
    <pre id="report-pre"></pre>
    <div class="brow">
      <button type="button" class="mbtn mbp" onclick="copyReport()">Copy markdown</button>
      <button type="button" class="mbtn mbg" onclick="document.getElementById('report-modal').classList.remove('open')">Close</button>
    </div>
  </div>
</div>

<div class="modal" id="modal-add-custom">
  <div class="mbox">
    <h2>Add custom integration</h2>
    <p class="setting-note">Generates one object for the <code>integrations</code> array in <code>data/custom_integrations.json</code>. <strong>Append &amp; save</strong> merges server-side (duplicate <code>id</code> is rejected). On save, ids are normalized to <code>cust_&lt;slug&gt;</code>.</p>
    <div class="mf"><label>Id (slug, unique)</label><input type="text" id="ac-id" placeholder="audiobookshelf" autocomplete="off"></div>
    <div class="mf"><label>Card label</label><input type="text" id="ac-label" placeholder="Audiobookshelf" autocomplete="off"></div>
    <div class="mf"><label>Short mark (optional)</label><input type="text" id="ac-icon" value="" maxlength="8" autocomplete="off"></div>
    <div class="mf"><label>Description</label><input type="text" id="ac-desc" placeholder="Short subtitle on the card" autocomplete="off"></div>
    <div class="mf"><label>Env key for base URL</label><input type="text" id="ac-url-env" placeholder="AUDIOBOOKSHELF_URL" autocomplete="off"></div>
    <div class="mf"><label>URL placeholder</label><input type="text" id="ac-url-ph" placeholder="http://192.168.1.111:13378" autocomplete="off"></div>
    <div class="mf"><label>Health path</label><input type="text" id="ac-health" value="/" autocomplete="off"></div>
    <div class="mf"><label>Documentation URL (optional)</label><input type="text" id="ac-doc" placeholder="https://..." autocomplete="off"></div>
    <div class="mf"><label>Logo domain (optional, for icons)</label><input type="text" id="ac-logo" placeholder="audiobookshelf.org" autocomplete="off"></div>
    <p class="setting-note">Snippet (single integration object - edit freely):</p>
    <pre class="wiz-pre" id="ac-snippet" style="max-height:240px"></pre>
    <div class="brow">
      <button type="button" class="mbtn mbp" onclick="genAcSnippet()">Refresh snippet</button>
      <button type="button" class="mbtn mbg" onclick="copyAcSnippet()">Copy</button>
      <button type="button" class="mbtn mbp" onclick="saveAcMerge()">Append &amp; save</button>
    </div>
    <button type="button" class="mcls" onclick="document.getElementById('modal-add-custom').classList.remove('open')">Close</button>
  </div>
</div>

<div class="modal" id="modal" onclick="if(event.target===this)document.getElementById('modal').classList.remove('open')">
  <div class="mbox settings-mbox" onclick="event.stopPropagation()">
    <button type="button" class="modal-close-x" onclick="document.getElementById('modal').classList.remove('open')" aria-label="Close settings">x</button>
    <div class="sett-modal-head">
      <h2>Settings</h2>
    </div>
    <p class="settings-sub">Plutus MCP <strong>v{html.escape(str(plutus_ver), quote=False)}</strong> - expandable sections.</p>

    <details class="sett" open>
      <summary>MCP Connection Settings</summary>
      <div class="msec">
        <p class="setting-note">Connect any MCP client to Plutus. Supports Claude Desktop, Claude.ai, n8n AI Agent, Open WebUI, LM Studio, and any MCP-compatible client.</p>

        <p class="setting-note"><strong>LAN (internal):</strong></p>
        <div class="tkbox">{html.escape(mcp_http_url)}</div>
        <p class="setting-note"><strong>Public HTTPS (Tailscale/proxy):</strong></p>
        <div class="tkbox">{("OK " + html.escape(mcp_https_url)) if mcp_https_url else "Not configured - set PUBLIC_MCP_BASE below."}</div>

        <div class="mf" style="margin-top:10px"><label>PUBLIC_MCP_BASE (your HTTPS base URL)</label>
        <input type="text" id="pub-base" class="cf-input inp-url" value="{html.escape(cfg.public_mcp_base, quote=True)}" placeholder="https://mcp.cobia-alioth.ts.net"></div>
        <div class="mf"><label>MCP_LAN_HOST</label>
        <input type="text" id="lan-host" class="cf-input inp-url" value="{html.escape(cfg.mcp_lan_host, quote=True)}"></div>
        <div class="brow"><button type="button" class="mbtn mbp" onclick="saveEndpoints()">Save URL settings</button></div>

        <p class="setting-note" style="margin-top:12px"><strong>Claude Desktop config snippet:</strong></p>
        <pre class="wiz-pre" style="font-size:10px">{_claude_snippet}</pre>

        <p class="setting-note" style="margin-top:10px"><strong>n8n AI Agent:</strong> Use SSE URL <code>{html.escape(mcp_https_url or mcp_http_url)}</code></p>
        <p class="setting-note"><strong>Open WebUI / LM Studio:</strong> Add as custom MCP server with URL above.</p>

        <details class="tailhint"><summary>Tailscale HTTPS setup</summary><pre>{ts_block}</pre></details>
      </div>
    </details>

    <details class="sett" open>
      <summary>Connection Manager - export client config files</summary>
      <div class="msec">
        <p class="setting-note">Generate a ready-to-use config file for any AI client and connect it to Plutus <strong>directly</strong> - drop the file in, no need to keep this dashboard open. Files are pre-filled with the URL above.</p>
        <label class="setting-note" style="display:flex;gap:8px;align-items:center;cursor:pointer">
          <input type="checkbox" id="conn-include-token" onchange="loadConnections()"> Embed Bearer token in the files (only if MCP auth is enabled)
        </label>
        <div class="brow"><button type="button" class="mbtn mbg" onclick="testMcpConnection()">Test MCP connection</button><span class="phint" id="conn-selftest" style="align-self:center"></span></div>
        <div class="conn-grid" id="conn-grid"><p class="phint">Loading clients...</p></div>
        <div class="conn-detail" id="conn-detail" hidden>
          <div class="conn-detail-head">
            <strong id="conn-detail-title"></strong>
            <span class="phint" id="conn-detail-target"></span>
          </div>
          <p class="setting-note" id="conn-detail-instr"></p>
          <pre class="wiz-pre" id="conn-detail-pre" style="max-height:220px"></pre>
          <div class="brow">
            <button type="button" class="mbtn mbp" onclick="downloadConnection()">Download file</button>
            <button type="button" class="mbtn mbg" onclick="copyConnection()">Copy</button>
            <a class="mbtn mbg" id="conn-detail-docs" href="#" target="_blank" rel="noopener noreferrer">Docs</a>
          </div>
        </div>
      </div>
    </details>

    <details class="sett" open>
      <summary>MCP Bearer Auth</summary>
      <div class="msec">
        <label class="setting-note" style="display:flex;gap:8px;align-items:center;cursor:pointer">
          <input type="checkbox" id="req-bearer"{bearer_chk}> Require Bearer token for <code>/mcp</code>
        </label>
        <p class="setting-note">When enabled, clients must send <code>Authorization: Bearer &lt;token&gt;</code>. Recommended for public endpoints.</p>
        <div class="brow"><button type="button" class="mbtn mbg" onclick="saveBearerToggle()">Save auth toggle</button></div>
        <p class="setting-note">Current token:</p>
        <div class="tkbox" id="tkv">{tkn_disp}</div>
        <div class="brow">
          <button type="button" class="mbtn mbp" onclick="genToken()">Generate token</button>
          <button type="button" class="mbtn mbg" onclick="copyToken()">Copy</button>
        </div>
      </div>
    </details>

    <details class="sett">
      <summary>TLS - Custom CA Certificate</summary>
      <div class="msec">
        <p class="setting-note">Upload <code>data/ca.pem</code> for internal TLS issuers (e.g. self-signed certs). Status: <strong>{"Installed" if cert_ok else "Not installed"}</strong></p>
        <div class="mf"><label>CA PEM file</label><input type="file" id="certfile" accept=".pem,.crt,.cer"></div>
        <div class="brow"><button type="button" class="mbtn mbg" onclick="uploadCert()">Upload CA Cert</button></div>
        <p class="setting-note">For Tailscale or Caddy HTTPS: not needed - they handle certs automatically.</p>
      </div>
    </details>

    <details class="sett">
      <summary>HTTP JSON API Reference</summary>
      <div class="msec">
        <p class="setting-note">Same Basic auth as this UI. Useful for n8n, scripts, and monitoring.</p>
        <pre class="wiz-pre" style="max-height:160px">GET /api/v1/dashboard
POST /api/v1/discover  body {{"host":"192.168.1.111"}}
POST /api/v1/wizard/scan  body {{"host":"192.168.1.111","include_port_scan":true}}
POST /api/v1/settings/check-updates
GET/POST /api/v1/tools/gate
GET /api/v1/mcp/connections?include_token=1   (client config exporter)
POST /api/v1/health/regression-check?notify=1   (schedule this: alerts on newly-broken tools)</pre>
      </div>
    </details>

    <details class="sett" open>
      <summary>Tools &amp; MCP Exposure</summary>
      <div class="msec">
        <p class="setting-note">Hide whole categories from MCP. Fine-grained toggles are on each service card under <strong>MCP tools</strong>. Data saved to <code>data/plutus_tool_gate.json</code>.</p>
        <div class="brow" style="flex-wrap:wrap">
          <button type="button" class="mbtn mbg" onclick="plutusGateSection('system',true)">Disable system tools</button>
          <button type="button" class="mbtn mbg" onclick="plutusGateSection('system',false)">Enable system tools</button>
          <button type="button" class="mbtn mbg" onclick="plutusGateSection('selfhosted',true)">Disable all self-hosted</button>
          <button type="button" class="mbtn mbg" onclick="plutusGateSection('selfhosted',false)">Enable self-hosted</button>
          <button type="button" class="mbtn mbg" onclick="plutusGateSection('public',true)">Disable all public APIs</button>
          <button type="button" class="mbtn mbg" onclick="plutusGateSection('public',false)">Enable public APIs</button>
          <button type="button" class="mbtn mbg" onclick="plutusGateSection('custom',true)">Disable custom cards</button>
          <button type="button" class="mbtn mbg" onclick="plutusGateSection('custom',false)">Enable custom cards</button>
        </div>
        <p class="setting-note">Inspired by community lists: <a href="https://github.com/public-apis/public-apis" target="_blank" rel="noopener noreferrer">public-apis</a> / <a href="https://github.com/hotheadhacker/awesome-selfhost-docker" target="_blank" rel="noopener noreferrer">awesome-selfhost-docker</a>.</p>
      </div>
    </details>

    <details class="sett" open>
      <summary>Custom integrations (dashboard)</summary>
      <div class="msec">
        <p class="setting-note">Define extra service cards (base URL env, optional tokens, API notes, doc link). Optional <code>logo_domain</code> (e.g. <code>immich.app</code>) improves Clearbit logos. Drop SVG/PNG under <code>icons/&lt;service_id&gt;.svg</code> for offline branding (see <code>/icons/</code> mount). Stored in <code>data/custom_integrations.json</code>. MCP <strong>tools</strong> still need Python - use <strong>Extensions</strong> or fork.</p>
        <textarea id="sett-json-custom" spellcheck="false"></textarea>
        <div class="brow">
          <button type="button" class="mbtn mbp" onclick="saveCustomIntegrations()">Save integrations</button>
          <button type="button" class="mbtn mbg" onclick="loadCustomIntegrations()">Reload</button>
        </div>
      </div>
    </details>

    <details class="sett">
      <summary>Updates</summary>
      <div class="msec">
        <p class="setting-note">This build: <code>v{html.escape(str(plutus_ver), quote=False)}</code>. GitHub releases: {upd_line}</p>
        <p class="setting-note">Updating: pull newer code or redeploy your container, then restart. Plutus does not run <code>git pull</code> automatically.</p>
        <p class="setting-note">Optional: set <code>GITHUB_TOKEN</code> if you hit public API rate limits.</p>
        <div class="brow"><button type="button" class="mbtn mbp" onclick="checkAppUpdates()">Check latest GitHub release</button></div>
        <pre id="upd-out"></pre>
      </div>
    </details>

    <details class="sett">
      <summary>Extensions &amp; sharing</summary>
      <div class="msec">
        <p class="setting-note">Optional <code>extensions/__init__.py</code> with <code>def register(mcp):</code> adds MCP tools at startup (same privilege as Plutus). See <code>extensions/README.md</code> in the install folder.</p>
        <p class="setting-note">Share with others by publishing a small repo: JSON-only custom cards (this settings panel) or Python tools (extensions hook), or both.</p>
      </div>
    </details>

    <details class="sett">
      <summary>Beta - tool result cache</summary>
      <div class="msec">
        <p class="setting-note">Caches last smoke-style outputs under <code>data/beta_tool_cache_entries.json</code>. Refresh runs all configured tools with fixed inputs (can take minutes). Updating when you use <code>/tool/run</code> from this UI merges into the cache.</p>
        <label class="setting-note" style="display:flex;gap:8px;align-items:center;cursor:pointer">
          <input type="checkbox" id="beta-enabled"> Enable scheduled refresh
        </label>
        <div class="mf"><label>Cache refresh scope</label>
          <select id="beta-scope" style="width:100%;background:var(--bg);border:1px solid var(--b);border-radius:4px;padding:6px 8px;color:var(--tx);font-size:12px">
            <option value="all">All services (default)</option>
            <option value="public_apis">Public API cards only</option>
            <option value="selfhosted_only">Self-hosted cards only</option>
            <option value="information">Public pub_* tools only</option>
          </select>
        </div>
        <p class="setting-note">Narrow scope to save CPU/RAM on scheduled cache refresh (read-only smoke payloads).</p>
        <div class="mf"><label>Hours between refreshes</label><input type="number" id="beta-hours" value="5" min="0.25" step="0.25"></div>
        <div class="mf"><label>Disabled service ids (comma-separated)</label><input type="text" id="beta-dis-svc" placeholder="docker,pub_met_search"></div>
        <div class="mf"><label>Disabled tool names (comma-separated)</label><input type="text" id="beta-dis-tools" placeholder="docker_list_containers"></div>
        <div class="brow">
          <button type="button" class="mbtn mbp" onclick="loadBetaCachePrefs()">Load prefs</button>
          <button type="button" class="mbtn mbp" onclick="saveBetaCachePrefs()">Save prefs</button>
          <button type="button" class="mbtn mbg" onclick="runBetaCacheRefresh()">Refresh cache now</button>
        </div>
        <pre class="wiz-pre" id="beta-cache-status" style="max-height:180px;margin-top:10px">Open and tap Load prefs.</pre>
      </div>
    </details>

    <details class="sett" open>
      <summary>Preferences</summary>
      <div class="msec">
        <h3 style="margin-bottom:6px">Web UI process</h3>
        <p class="setting-note">Turn off to run <strong>MCP only</strong> (saves RAM). After disabling, change <code>UI_ENABLED</code> in <code>.env</code> to turn the dashboard back on, then restart.</p>
        <label class="setting-note" style="display:flex;gap:8px;align-items:center;cursor:pointer">
          <input type="checkbox" id="ui-enabled"{ui_on_chk}> <strong>Enable Web UI</strong> (serves <code>/ui</code> on UI_PORT)
        </label>
        <div class="brow"><button type="button" class="mbtn mbg" onclick="saveUiEnabled()">Save, restart Plutus</button></div>
      </div>
      <div class="msec">
        <h3 style="margin-bottom:6px">UI login</h3>
        <p class="setting-note">If <code>UI_PASSWORD</code> is <strong>not</strong> set in <code>.env</code>, the default password is <code>{html.escape(DEFAULT_UI_PASSWORD, quote=True)}</code> (username <code>{html.escape(cfg.ui_username, quote=True)}</code>). Set <code>UI_PASSWORD</code> in <code>.env</code> or below to use your own.</p>
        <div class="mf"><label>Username</label><input type="text" id="su" value="{html.escape(cfg.ui_username, quote=True)}"></div>
        <div class="mf"><label>New password (blank = keep)</label><input type="password" id="sp"></div>
        <div class="brow"><button class="mbtn mbg" onclick="saveCreds()">Save</button></div>
      </div>
      <div class="msec">
        <h3 style="margin-bottom:6px">Weather default city</h3>
        <div class="mf"><input type="text" id="sc" value="{html.escape(cfg.weather_default_location, quote=True)}" placeholder="Hamburg"></div>
        <div class="brow"><button class="mbtn mbg" onclick="saveCity()">Save</button></div>
      </div>
      <div class="msec">
        <h3 style="margin-bottom:6px">Recent tool runs</h3>
        <div style="font-size:10px;color:var(--dim)">{rec_html or '<span style="color:#444">No runs yet</span>'}</div>
      </div>
      <div class="msec">
        <h3 style="margin-bottom:6px">Reset to defaults</h3>
        <p class="setting-note">Resets only the selected areas. Service API keys in <code>.env</code> are not removed. Restart after URL/auth toggles if prompted.</p>
        <div class="brow" style="flex-wrap:wrap">
          <button type="button" class="mbtn mbg" onclick="plutusResetScope('urls')">LAN / public URLs</button>
          <button type="button" class="mbtn mbg" onclick="plutusResetScope('weather')">Weather city</button>
          <button type="button" class="mbtn mbg" onclick="plutusResetScope('custom_integrations')">Custom cards</button>
          <button type="button" class="mbtn mbg" onclick="plutusResetScope('beta_cache')">Beta cache prefs</button>
        </div>
      </div>
    </details>

    <button class="mcls" onclick="document.getElementById('modal').classList.remove('open')">Close</button>
  </div>
</div>

<div class="rf"><div class="rfh" onclick="toggleRec()">Recent <span id="rfa">></span></div><div class="rfb" id="rfb">{rec_html or '<div style="color:#444;font-size:10px">No runs</div>'}</div></div>

<div id="wiz-panel" class="wiz-panel" aria-hidden="true">
  <div class="wiz-inner">
    <h2 class="wiz-title">Auto-discover</h2>
    <p class="wiz-desc">Docker: reads published ports on <strong>this</strong> machine (HTTP socket or <code>docker ps</code>) and fills URL fields using the LAN host below. Optional second pass probes common ports on that host.</p>
    <div class="wiz-row0">
      <div class="mf"><label>LAN host for URLs</label><input type="text" class="cf-input inp-url" id="wiz-host" autocomplete="off" placeholder="{html.escape(cfg.mcp_lan_host, quote=True)}" value="{html.escape(cfg.mcp_lan_host, quote=True)}"></div>
      <label class="chk"><input type="checkbox" id="wiz-scan-ports" checked> Also probe known service ports on that host</label>
      <button type="button" class="btn-wiz-scan" id="btn-wiz-scan" onclick="runWizardScan()">Scan</button>
    </div>
    <div class="wiz-msg" id="wiz-msg"></div>
    <div id="wiz-rows"></div>
    <div class="wiz-actions">
      <button type="button" class="btn-wiz-apply btn-wiz-all" onclick="applyWizardAll()">Apply all to dashboard</button>
      <span class="wiz-desc" style="margin:0;align-self:center">Then use <strong>Save all connection fields</strong> in the bar below.</span>
    </div>
  </div>
</div>

<div id="slicer-panel" class="slicer-panel" aria-hidden="true">
  <div class="slicer-inner">
    <div class="slicer-head">
      <div>
        <h2 class="wiz-title">Tool slicer</h2>
        <p class="wiz-desc">Shrink the MCP manifest the AI sees. Pick a preset or type a custom intent. Reconnect your MCP client (e.g. restart Claude Desktop) for the new manifest to take effect.</p>
      </div>
      <div class="slicer-active" id="slicer-active">
        <span class="slicer-active-label">Active intent</span>
        <code id="slicer-active-val">(none — all tools exposed)</code>
      </div>
    </div>
    <div class="slicer-presets" role="group" aria-label="Intent presets">
      <button type="button" class="slicer-preset" data-preset="">All tools</button>
      <button type="button" class="slicer-preset" data-preset="personal">Personal</button>
      <button type="button" class="slicer-preset" data-preset="office">Office</button>
      <button type="button" class="slicer-preset" data-preset="homelab">Homelab</button>
      <button type="button" class="slicer-preset" data-preset="smarthome">Smart home</button>
      <button type="button" class="slicer-preset" data-preset="creative">Creative</button>
      <button type="button" class="slicer-preset" data-preset="web">Web</button>
      <button type="button" class="slicer-preset" data-preset="fun">Fun</button>
    </div>
    <div class="slicer-form">
      <label class="slicer-form-label" for="slicer-intent">Custom intent</label>
      <input type="search" id="slicer-intent" class="cf-input" placeholder="e.g.  calendar tasks files     -trivia -crypto" oninput="loadToolSlicer()" aria-label="Intent filter">
      <div class="slicer-form-actions">
        <button type="button" class="btn-tsmoke" id="btn-slicer-apply" onclick="applySlicerIntent()">Apply &amp; persist</button>
        <button type="button" class="tbtn" onclick="clearSlicerIntent()">Clear</button>
        <span class="phint slicer-form-hint">Apply writes to <code>data/plutus_tool_gate.json</code>. New MCP sessions see the slice; existing ones must reconnect.</span>
      </div>
    </div>
    <div id="slicer-out"><p class="phint">Open to inspect exposed tools.</p></div>
  </div>
</div>

<div class="footer-bar">
  <div class="footer-group" data-label="Discovery">
    <button type="button" class="tbtn tbtn-wiz" id="btn-wiz-toggle" onclick="toggleWizPanel()" aria-expanded="false" title="Auto-discover services on the LAN">Discover</button>
    <button type="button" class="tbtn tbtn-wiz" id="btn-slicer-toggle" onclick="toggleSlicerPanel()" aria-expanded="false" title="Shrink the MCP manifest the AI sees">Slicer</button>
  </div>
  <span class="footer-sep" aria-hidden="true">·</span>
  <div class="footer-group" data-label="Maintenance">
    <button type="button" class="tbtn" onclick="saveAllConfigs()" title="Persist every connection field on the page">Save all</button>
    <button type="button" class="tbtn tbtn-primary" id="btn-test-all" onclick="refreshAll()" title="Probe every service and run zero-arg tool smoke tests">Full check</button>
  </div>
</div>

<script>window.PLUTUS_MCP_URL = {json.dumps(primary_copy_url)};</script><script src="/static/dashboard.js?v={plutus_ver}"></script></body></html>""")


