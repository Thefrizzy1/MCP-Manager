"""Full-page Agents console (served at /agents).

Layout philosophy: *launch fast, hide power*. A persistent launcher (prompt +
one-click playbook chips) sits at the top so the common action is one gesture;
the live console is collapsible; every powerful control — permissions, model,
login, scheduling, editing — lives in clean tabbed cards below. Behaviour is in
ui/static/agents.js; styling in ui/static/dashboard.css (.ag-*).
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
<link rel="stylesheet" href="/static/dashboard.css?v=""" + v + '"></head><body class="plutus-dash ag-body">' + """
<datalist id="model-list"><option value="opus"><option value="sonnet"><option value="haiku"><option value="claude-opus-4-8"><option value="claude-sonnet-5"></datalist>

<header class="ag-header">
  <a href="/ui" class="ag-back" title="Back to dashboard">&larr;</a>
  <div class="ag-brand"><span class="ag-brand-mark">P</span><h1>Agents<span> · Plutus</span></h1></div>
  <div class="ag-status">
    <span class="ag-chip" id="ag-mode">…</span>
    <span class="ag-chip" id="ag-cost"></span>
    <span class="ag-chip ag-chip-live" id="ag-run">idle</span>
  </div>
  <div class="ag-flex"></div>
  <button type="button" class="ag-ico" onclick="toggleTheme()" title="Light / dark">◐</button>
</header>
<p class="ag-warn" id="ag-warn"></p>

<!-- LAUNCHER — always visible, the fast path -->
<section class="ag-launcher">
  <div class="ag-launch-main">
    <div class="ag-launch-head"><span class="ag-eyebrow">Launch</span><span class="ag-hint" id="run-status"></span></div>
    <textarea id="run-prompt" class="ag-input ag-area" rows="3" placeholder="Ask the agent to do something across your homelab — e.g. find stuck *arr queues and summarize, or research new low-VRAM ComfyUI workflows."></textarea>
    <div class="ag-launch-row">
      <input type="text" id="run-label" class="ag-input ag-input-slim" placeholder="label (optional)">
      <div class="ag-flex"></div>
      <button type="button" class="ag-btn ag-btn-ghost" id="stop-btn" onclick="agStop()" disabled>Stop</button>
      <button type="button" class="ag-btn ag-btn-primary" id="run-btn" onclick="agRun()">Run agent</button>
    </div>
  </div>
  <div class="ag-launch-quick">
    <span class="ag-eyebrow">Quick-launch a playbook</span>
    <div id="quick-playbooks" class="ag-quick"><span class="ag-hint">Loading…</span></div>
  </div>
</section>

<details class="ag-console-wrap" id="console-wrap">
  <summary><span>Live console</span><span class="ag-hint" id="console-hint"></span></summary>
  <pre class="ag-console" id="run-console">Idle — launch something above.</pre>
</details>

<nav class="ag-tabs" role="tablist">
  <button class="ag-tab active" data-tab="playbooks">Playbooks</button>
  <button class="ag-tab" data-tab="schedules">Schedules</button>
  <button class="ag-tab" data-tab="settings">Settings</button>
  <button class="ag-tab" data-tab="history">History</button>
</nav>

<!-- PLAYBOOKS -->
<section class="ag-panel" id="tab-playbooks">
  <div class="ag-card ag-accent">
    <h2 class="ag-h2">Build an agent with Claude</h2>
    <p class="ag-hint">Describe what you want; the running Claude Code drafts a playbook prompt you can edit and save.</p>
    <textarea id="build-desc" class="ag-input ag-area" rows="2" placeholder="e.g. Each morning research trending AI video models that run on 8GB VRAM and log the best to my Obsidian research folder with source links."></textarea>
    <div class="ag-row"><button type="button" class="ag-btn ag-btn-primary" id="build-btn" onclick="agBuild()">Draft with Claude</button><span class="ag-hint" id="build-status"></span></div>
  </div>

  <div class="ag-card">
    <div class="ag-card-head"><h2 class="ag-h2">Playbooks</h2><span class="ag-hint">reusable research tasks that grow your knowledge library</span></div>
    <div id="pb-list"><span class="ag-hint">Loading…</span></div>
  </div>

  <details class="ag-card ag-editor" id="pb-editor-wrap">
    <summary><strong>Playbook editor</strong><span class="ag-hint">create or edit</span></summary>
    <input type="hidden" id="pb-id">
    <div class="ag-grid2">
      <div class="ag-field"><label>Name</label><input type="text" id="pb-name" class="ag-input"></div>
      <div class="ag-field"><label>Description</label><input type="text" id="pb-desc" class="ag-input"></div>
      <div class="ag-field"><label>Tool permission <span class="ag-hint">blank = global</span></label>
        <select id="pb-perm" class="ag-input"><option value="">(global default)</option><option value="strict_read">Strict read</option><option value="safe">Safe</option><option value="all">All tools</option></select></div>
      <div class="ag-field"><label>Model <span class="ag-hint">blank = global</span></label><input type="text" id="pb-model" class="ag-input" list="model-list"></div>
    </div>
    <div class="ag-field"><label>Prompt <span class="ag-hint">placeholders: {{LIBRARY}} {{OUTPUT_HINT}} {{DATE}}</span></label><textarea id="pb-prompt" class="ag-input ag-area" rows="8"></textarea></div>
    <div class="ag-row"><button type="button" class="ag-btn ag-btn-primary" onclick="agSavePlaybook()">Save playbook</button><button type="button" class="ag-btn ag-btn-ghost" onclick="agClearEditor()">Clear</button><span class="ag-hint" id="pb-status"></span></div>
  </details>
</section>

<!-- SCHEDULES -->
<section class="ag-panel hidden" id="tab-schedules">
  <div class="ag-card">
    <div class="ag-card-head"><h2 class="ag-h2">Schedules</h2><span class="ag-hint" id="sched-avail"></span></div>
    <div id="sched-list"><span class="ag-hint">Loading…</span></div>
  </div>
  <details class="ag-card ag-editor">
    <summary><strong>Add a schedule</strong><span class="ag-hint">no cron required</span></summary>
    <div class="ag-grid2">
      <div class="ag-field"><label>Name</label><input type="text" id="s-name" class="ag-input"></div>
      <div class="ag-field"><label>Type</label>
        <select id="s-kind" class="ag-input" onchange="agSchedKind()"><option value="task">Playbook</option><option value="agent">Agent prompt</option><option value="tool">Tool call</option></select></div>
      <div class="ag-field"><label>When</label>
        <select id="s-preset" class="ag-input" onchange="agPreset()"><option value="daily">Daily at a time</option><option value="hourly">Hourly</option><option value="everyN">Every N minutes</option><option value="weekly">Weekly on a day</option><option value="custom">Custom cron</option></select></div>
      <div class="ag-field" id="preset-daily"><label>Time</label><input type="time" id="p-time" class="ag-input" value="03:00" onchange="agPreset()"></div>
      <div class="ag-field hidden" id="preset-everyN"><label>Every N minutes</label><input type="number" id="p-n" class="ag-input" min="1" max="1440" value="30" onchange="agPreset()"></div>
      <div class="ag-field hidden" id="preset-weekly"><label>Day</label><select id="p-dow" class="ag-input" onchange="agPreset()"><option value="1">Mon</option><option value="2">Tue</option><option value="3">Wed</option><option value="4">Thu</option><option value="5">Fri</option><option value="6">Sat</option><option value="0">Sun</option></select></div>
      <div class="ag-field"><label>Cron <span class="ag-hint">auto; editable</span></label><input type="text" id="s-cron" class="ag-input" value="0 3 * * *"></div>
      <div class="ag-field"><label>Timezone</label><input type="text" id="s-tz" class="ag-input" value="Europe/Berlin"></div>
      <div class="ag-field" id="s-task-wrap"><label>Playbook</label><select id="s-task" class="ag-input"></select></div>
      <div class="ag-field hidden" id="s-agent-wrap"><label>Prompt</label><textarea id="s-prompt" class="ag-input ag-area" rows="2"></textarea></div>
      <div class="ag-field hidden" id="s-tool-wrap"><label>Tool + params (JSON)</label><input type="text" id="s-tool" class="ag-input" placeholder="sonarr_queue"><textarea id="s-params" class="ag-input ag-area" rows="2" placeholder="{{}}" style="margin-top:6px">{{}}</textarea></div>
    </div>
    <div class="ag-row"><button type="button" class="ag-btn ag-btn-primary" onclick="agAddSchedule()">Add schedule</button><span class="ag-hint" id="s-status"></span></div>
  </details>
</section>

<!-- SETTINGS -->
<section class="ag-panel hidden" id="tab-settings">
  <div class="ag-card">
    <h2 class="ag-h2">Model &amp; limits</h2>
    <div class="ag-grid2">
      <div class="ag-field"><label>Model <span class="ag-hint">blank = default</span></label><input type="text" id="c-model" class="ag-input" list="model-list" placeholder="opus / sonnet / haiku"></div>
      <div class="ag-field"><label>Timeout (min)</label><input type="number" id="c-timeout" class="ag-input" min="1" max="120" value="20"></div>
      <div class="ag-field"><label>Max cost guard (USD)</label><input type="number" id="c-cost" class="ag-input" min="0" step="0.5" value="2"></div>
      <div class="ag-field"><label>Max runs per day</label><input type="number" id="c-maxruns" class="ag-input" min="0" max="500" value="20"></div>
    </div>
    <div class="ag-field"><label>Allowed tools <span class="ag-hint">mcp__plutus = all Plutus tools</span></label><input type="text" id="c-tools" class="ag-input" placeholder="mcp__plutus,Read,Write,WebSearch,WebFetch"></div>
    <div class="ag-checks">
      <label class="ag-chk"><input type="checkbox" id="c-plutus"> Give the agent Plutus's tools</label>
      <label class="ag-chk"><input type="checkbox" id="c-skip"> Headless (skip prompts)</label>
    </div>
  </div>

  <div class="ag-card">
    <h2 class="ag-h2">Tool permission <span class="ag-hint">blast-radius control</span></h2>
    <select id="c-perm" class="ag-input" style="max-width:420px">
      <option value="strict_read">Strict read — reads only (audits)</option>
      <option value="safe">Safe — reads + note-writing (recommended)</option>
      <option value="all">All — full tool access</option>
    </select>
    <p class="ag-hint" id="c-perm-note" style="margin-top:8px"></p>
  </div>

  <div class="ag-card">
    <h2 class="ag-h2">Where output goes</h2>
    <div class="ag-grid2">
      <div class="ag-field"><label>Destination</label>
        <select id="c-outmode" class="ag-input" onchange="agOutMode()"><option value="obsidian">Obsidian vault</option><option value="filesystem">Filesystem path</option></select></div>
      <div class="ag-field" id="c-obs-wrap"><label>Obsidian folder</label><input type="text" id="c-obsfolder" class="ag-input" placeholder="20-research/agents"></div>
      <div class="ag-field hidden" id="c-fs-wrap"><label>Filesystem path <span class="ag-hint">in FILESYSTEM_ALLOWED_PATHS</span></label><input type="text" id="c-fspath" class="ag-input" placeholder="/data/library"></div>
    </div>
  </div>

  <div class="ag-card">
    <h2 class="ag-h2">Notifications</h2>
    <label class="ag-chk"><input type="checkbox" id="c-notify"> ntfy after each run</label>
    <div class="ag-field" style="max-width:220px;margin-top:8px"><label>Notify on</label><select id="c-notifyon" class="ag-input"><option value="all">Every run</option><option value="error">Failures only</option></select></div>
  </div>

  <div class="ag-row"><button type="button" class="ag-btn ag-btn-primary" onclick="agSaveSettings()">Save settings</button><span class="ag-hint" id="c-status"></span></div>

  <details class="ag-card ag-editor" id="login-card">
    <summary><strong>Connect Claude account</strong><span class="ag-hint" id="login-badge">session token — not an API key</span></summary>
    <p class="ag-hint">Runs draw from your Claude <strong>subscription</strong> (a session/OAuth token), not per-token API billing.</p>
    <ol class="ag-steps">
      <li>On any machine signed into your Claude account, run <code>claude setup-token</code> in a terminal — it opens your browser, you approve, and it prints a token.</li>
      <li>Paste that token below and Save. That's it — no container shell, applies immediately.</li>
    </ol>
    <div class="ag-field"><label>Session token</label><input type="password" id="login-token" class="ag-input" placeholder="sk-ant-oat…"></div>
    <div class="ag-row"><button type="button" class="ag-btn ag-btn-primary" onclick="agLoginToken()">Save token</button><span class="ag-hint" id="login-status"></span></div>
  </details>
</section>

<!-- HISTORY -->
<section class="ag-panel hidden" id="tab-history">
  <div class="ag-card">
    <h2 class="ag-h2">Recent runs</h2>
    <div id="hist-list"><span class="ag-hint">Loading…</span></div>
  </div>
</section>

<script src="/static/agents.js?v=""" + v + """"></script>
</body></html>"""
    )
