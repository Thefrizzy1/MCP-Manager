"""Full-page Agents console (served at /agents).

A dedicated, tabbed page — Run, Playbooks (incl. "build with Claude"), Schedules,
Settings, History — kept separate from the dashboard's single-page f-string so it
has room to grow. All behaviour lives in ui/static/agents.js.
"""
from __future__ import annotations

import html

from core.version_info import VERSION


def render_agents_page() -> str:
    v = html.escape(str(VERSION))
    return (
        """<!DOCTYPE html>
<html lang="en" data-theme="dark"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Plutus Agents</title>
<link rel="stylesheet" href="/static/dashboard.css?v=""" + v + '"></head><body class="plutus-dash">' + """
<div class="ag-top">
  <a href="/ui" class="tbtn">&larr; Dashboard</a>
  <h1 class="ag-title">Agents</h1>
  <span class="ag-badge" id="ag-mode">…</span>
  <span class="ag-badge" id="ag-run">idle</span>
  <span class="ag-badge" id="ag-cost"></span>
  <div class="tb-spacer"></div>
  <button type="button" class="tbtn" onclick="toggleTheme&&toggleTheme()">Theme</button>
</div>
<p class="ag-warn phint" id="ag-warn"></p>

<div class="ag-tabs" role="tablist">
  <button class="ag-tab active" data-tab="run">Run</button>
  <button class="ag-tab" data-tab="playbooks">Playbooks</button>
  <button class="ag-tab" data-tab="schedules">Schedules</button>
  <button class="ag-tab" data-tab="settings">Settings</button>
  <button class="ag-tab" data-tab="history">History</button>
</div>

<!-- RUN -->
<section class="ag-panel" id="tab-run">
  <h2 class="ag-h2">Run an agent</h2>
  <p class="phint">The agent can use Plutus's own ~193 tools plus web search. One run at a time.</p>
  <div class="mf"><label>Label (optional)</label><input type="text" id="run-label" class="cf-input" style="max-width:220px" placeholder="ad-hoc"></div>
  <div class="mf"><label>Prompt</label><textarea id="run-prompt" class="cf-input" rows="5" placeholder="e.g. Check which *arr queues are stuck and summarize; flag any unhealthy Docker containers."></textarea></div>
  <div class="brow"><button type="button" class="btn-tsmoke" id="run-btn" onclick="agRun()">Run agent</button><button type="button" class="mbtn mbg" id="stop-btn" onclick="agStop()">Stop</button><span class="phint" id="run-status"></span></div>
  <pre class="wiz-pre ag-console" id="run-console">Idle.</pre>
</section>

<!-- PLAYBOOKS -->
<section class="ag-panel hidden" id="tab-playbooks">
  <h2 class="ag-h2">Build an agent with Claude</h2>
  <p class="phint">Describe what you want in plain language; the running Claude Code drafts a playbook prompt you can edit and save.</p>
  <div class="mf"><textarea id="build-desc" class="cf-input" rows="3" placeholder="e.g. Every morning, research new low-VRAM ComfyUI video workflows and log the best ones to my Obsidian research folder with source links."></textarea></div>
  <div class="brow"><button type="button" class="mbtn mbp" id="build-btn" onclick="agBuild()">Draft with Claude</button><span class="phint" id="build-status"></span></div>

  <h2 class="ag-h2" style="margin-top:18px">Playbook editor</h2>
  <input type="hidden" id="pb-id">
  <div class="mf"><label>Name</label><input type="text" id="pb-name" class="cf-input" placeholder="My research playbook"></div>
  <div class="mf"><label>Description</label><input type="text" id="pb-desc" class="cf-input" placeholder="Short summary"></div>
  <div class="ag-form">
    <div class="mf"><label>Tool permission <span class="phint">blank = global default</span></label>
      <select id="pb-perm" class="cf-input"><option value="">(global default)</option><option value="strict_read">Strict read (audits)</option><option value="safe">Safe (reads + notes)</option><option value="all">All tools</option></select></div>
    <div class="mf"><label>Model override <span class="phint">blank = global</span></label><input type="text" id="pb-model" class="cf-input" list="model-list" placeholder=""></div>
  </div>
  <div class="mf"><label>Prompt <span class="phint">— placeholders: {{LIBRARY}} {{OUTPUT_HINT}} {{DATE}}</span></label><textarea id="pb-prompt" class="cf-input" rows="8"></textarea></div>
  <div class="brow"><button type="button" class="mbtn mbp" onclick="agSavePlaybook()">Save playbook</button><button type="button" class="mbtn mbg" onclick="agClearEditor()">New / clear</button><span class="phint" id="pb-status"></span></div>

  <h2 class="ag-h2" style="margin-top:18px">Playbooks</h2>
  <div id="pb-list"><span class="phint">Loading…</span></div>
</section>

<!-- SCHEDULES -->
<section class="ag-panel hidden" id="tab-schedules">
  <h2 class="ag-h2">Schedules</h2>
  <p class="phint" id="sched-avail"></p>
  <div id="sched-list"><span class="phint">Loading…</span></div>
  <h2 class="ag-h2" style="margin-top:16px">Add a schedule</h2>
  <div class="ag-form">
    <div class="mf"><label>Name</label><input type="text" id="s-name" class="cf-input"></div>
    <div class="mf"><label>Type</label>
      <select id="s-kind" class="cf-input" onchange="agSchedKind()">
        <option value="task">Playbook</option>
        <option value="agent">Agent prompt</option>
        <option value="tool">Tool call</option>
      </select></div>
    <div class="mf"><label>When</label>
      <select id="s-preset" class="cf-input" onchange="agPreset()">
        <option value="daily">Daily at a time</option>
        <option value="hourly">Hourly</option>
        <option value="everyN">Every N minutes</option>
        <option value="weekly">Weekly on a day</option>
        <option value="custom">Custom cron</option>
      </select></div>
    <div class="mf" id="preset-daily"><label>Time (HH:MM)</label><input type="time" id="p-time" class="cf-input" value="03:00" onchange="agPreset()"></div>
    <div class="mf hidden" id="preset-everyN"><label>Every N minutes</label><input type="number" id="p-n" class="cf-input" min="1" max="1440" value="30" onchange="agPreset()"></div>
    <div class="mf hidden" id="preset-weekly"><label>Day</label><select id="p-dow" class="cf-input" onchange="agPreset()"><option value="1">Mon</option><option value="2">Tue</option><option value="3">Wed</option><option value="4">Thu</option><option value="5">Fri</option><option value="6">Sat</option><option value="0">Sun</option></select></div>
    <div class="mf"><label>Cron <span class="phint">(auto from above; editable)</span></label><input type="text" id="s-cron" class="cf-input" value="0 3 * * *"></div>
    <div class="mf"><label>Timezone</label><input type="text" id="s-tz" class="cf-input" value="Europe/Berlin"></div>
    <div class="mf" id="s-task-wrap"><label>Playbook</label><select id="s-task" class="cf-input"></select></div>
    <div class="mf hidden" id="s-agent-wrap"><label>Prompt</label><textarea id="s-prompt" class="cf-input" rows="3"></textarea></div>
    <div class="mf hidden" id="s-tool-wrap"><label>Tool + params (JSON)</label><input type="text" id="s-tool" class="cf-input" placeholder="sonarr_queue"><textarea id="s-params" class="cf-input" rows="2" placeholder="{}" style="margin-top:6px">{}</textarea></div>
  </div>
  <div class="brow"><button type="button" class="mbtn mbp" onclick="agAddSchedule()">Add schedule</button><span class="phint" id="s-status"></span></div>
</section>

<!-- SETTINGS -->
<section class="ag-panel hidden" id="tab-settings">
  <h2 class="ag-h2">Agent settings</h2>
  <div class="ag-form">
    <div class="mf"><label>Model <span class="phint">blank = default</span></label>
      <input type="text" id="c-model" class="cf-input" list="model-list" placeholder="opus / sonnet / haiku or full id">
      <datalist id="model-list"><option value="opus"><option value="sonnet"><option value="haiku"><option value="claude-opus-4-8"><option value="claude-sonnet-5"></datalist>
    </div>
    <div class="mf"><label>Timeout (minutes)</label><input type="number" id="c-timeout" class="cf-input" min="1" max="120" value="20"></div>
    <div class="mf"><label>Max cost guard (USD)</label><input type="number" id="c-cost" class="cf-input" min="0" step="0.5" value="2"></div>
    <div class="mf"><label>Tool permission <span class="phint">blast-radius control</span></label>
      <select id="c-perm" class="cf-input">
        <option value="strict_read">Strict read — reads only (audits)</option>
        <option value="safe">Safe — reads + note-writing (recommended)</option>
        <option value="all">All — full tool access</option>
      </select></div>
    <div class="mf"><label>Max runs per day</label><input type="number" id="c-maxruns" class="cf-input" min="0" max="500" value="20"></div>
    <div class="mf"><label>Allowed tools <span class="phint">comma-separated; mcp__plutus = all Plutus tools</span></label><input type="text" id="c-tools" class="cf-input" placeholder="mcp__plutus,Read,Write,WebSearch,WebFetch"></div>
  </div>
  <p class="phint" id="c-perm-note"></p>
  <label class="ag-chk"><input type="checkbox" id="c-plutus"> Give the agent Plutus's own MCP tools</label>
  <label class="ag-chk"><input type="checkbox" id="c-skip"> Headless (skip permission prompts)</label>

  <h2 class="ag-h2" style="margin-top:16px">Where output goes (knowledge library)</h2>
  <div class="ag-form">
    <div class="mf"><label>Destination</label>
      <select id="c-outmode" class="cf-input" onchange="agOutMode()">
        <option value="obsidian">Obsidian vault</option>
        <option value="filesystem">Filesystem path</option>
      </select></div>
    <div class="mf" id="c-obs-wrap"><label>Obsidian folder (vault-relative)</label><input type="text" id="c-obsfolder" class="cf-input" placeholder="20-research/agents"></div>
    <div class="mf hidden" id="c-fs-wrap"><label>Filesystem path (must be in FILESYSTEM_ALLOWED_PATHS)</label><input type="text" id="c-fspath" class="cf-input" placeholder="/data/library"></div>
  </div>

  <h2 class="ag-h2" style="margin-top:16px">Notifications</h2>
  <label class="ag-chk"><input type="checkbox" id="c-notify"> Send an ntfy notification after each run</label>
  <div class="mf" style="max-width:220px"><label>Notify on</label><select id="c-notifyon" class="cf-input"><option value="all">Every run</option><option value="error">Failures only</option></select></div>

  <div class="brow" style="margin-top:12px"><button type="button" class="mbtn mbp" onclick="agSaveSettings()">Save settings</button><span class="phint" id="c-status"></span></div>

  <h2 class="ag-h2" style="margin-top:18px">Connect Claude account (session token — not an API key)</h2>
  <p class="phint">Uses your Claude <strong>subscription</strong> (session/OAuth token), so runs draw from your plan, not per-token API billing. No container shell needed.</p>
  <div class="mf"><label>Paste a token from <code>claude setup-token</code> (run it on any machine signed into your Claude account)</label>
    <input type="password" id="login-token" class="cf-input" placeholder="sk-ant-oat…"></div>
  <div class="brow"><button type="button" class="mbtn mbp" onclick="agLoginToken()">Save token</button>
    <button type="button" class="mbtn mbg" onclick="agLoginStart()">Or log in via browser</button>
    <span class="phint" id="login-status"></span></div>
  <div id="login-oauth" class="hidden" style="margin-top:8px">
    <p class="phint">1. Open the authorization page, sign in, approve. 2. Paste the code it gives you.</p>
    <a id="login-url" class="mbtn mbg" href="#" target="_blank" rel="noopener noreferrer">Open authorization page</a>
    <div class="mf" style="margin-top:8px"><label>Code</label><input type="text" id="login-code" class="cf-input"></div>
    <div class="brow"><button type="button" class="mbtn mbp" onclick="agLoginFinish()">Finish login</button></div>
  </div>
</section>

<!-- HISTORY -->
<section class="ag-panel hidden" id="tab-history">
  <h2 class="ag-h2">Recent runs</h2>
  <div id="hist-list"><span class="phint">Loading…</span></div>
</section>

<script src="/static/agents.js?v=""" + v + """"></script>
</body></html>"""
    )
