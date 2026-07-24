"use strict";
/* Plutus SPA — framework-free single-page console. */
const $=(s,r=document)=>r.querySelector(s);
const el=(t,c,h)=>{const e=document.createElement(t);if(c)e.className=c;if(h!=null)e.innerHTML=h;return e;};
const esc=s=>s==null?'':String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
async function api(path,opts){const r=await fetch(path,opts);const t=await r.text();let d;try{d=JSON.parse(t);}catch{d=t;}if(!r.ok)throw (d&&d.detail)||('HTTP '+r.status);return d;}

const NAV=[
 {sec:'Main'},
 {id:'dashboard',label:'Dashboard',ico:'▦'},
 {id:'connections',label:'MCP Connections',ico:'🔌'},
 {id:'discover',label:'Discover',ico:'🔍'},
 {id:'slicer',label:'Slicer',ico:'▤'},
 {sec:'Automation'},
 {id:'agents',label:'Agents',ico:'🤖'},
 {id:'chat',label:'Chat',ico:'💬',soon:true},
];

function applyTheme(m){document.documentElement.setAttribute('data-theme',m==='light'?'light':'dark');localStorage.setItem('plutus_theme',m);}
function toggleTheme(){applyTheme((document.documentElement.getAttribute('data-theme')||'dark')==='dark'?'light':'dark');}

// ── status mapping ────────────────────────────────────────────────────
function statusOf(s){
  if(!s.configured) return {cls:'bad',label:'Not configured'};
  if(s.health===true) return {cls:'ok',label:'Healthy'};
  if(s.health===false) return {cls:'bad',label:'Failed'};
  return {cls:'warn',label:'Configured'};
}

// ── shell ─────────────────────────────────────────────────────────────
function buildShell(){
  const app=$('#app');app.innerHTML='';
  const sb=el('aside','sb');
  sb.appendChild(el('div','sb-brand','<span class="sb-mark">P</span><div><h1>Plutus</h1><small>MCP Manager</small></div>'));
  const nav=el('nav','sb-nav');
  NAV.forEach(n=>{
    if(n.sec){nav.appendChild(el('div','sb-sec',n.sec));return;}
    const item=el('div','nav-item'+(n.soon?' soon':''));
    item.dataset.route=n.id;
    item.innerHTML='<span class="nav-ico">'+n.ico+'</span><span>'+n.label+'</span>'+(n.soon?'<span class="soon-badge">Soon</span>':'');
    if(!n.soon)item.onclick=()=>location.hash='#/'+n.id;
    nav.appendChild(item);
  });
  sb.appendChild(nav);
  const foot=el('div','sb-foot');
  const setItem=el('div','nav-item');setItem.dataset.route='settings';setItem.innerHTML='<span class="nav-ico">⚙</span><span>Settings</span>';setItem.onclick=()=>location.hash='#/settings';
  foot.appendChild(setItem);
  sb.appendChild(foot);
  app.appendChild(sb);
  const main=el('main','main');main.id='main';app.appendChild(main);
}
function setActive(route){document.querySelectorAll('.nav-item').forEach(i=>i.classList.toggle('active',i.dataset.route===route));}
function page(title,sub,actionsHtml){
  const m=$('#main');m.innerHTML='';
  const top=el('div','topbar');
  top.innerHTML='<div><h2>'+esc(title)+'</h2>'+(sub?'<div class="sub">'+esc(sub)+'</div>':'')+'</div><div class="spacer"></div>';
  if(actionsHtml){const a=el('div');a.style.cssText='display:flex;gap:8px;flex-wrap:wrap';a.innerHTML=actionsHtml;top.appendChild(a);}
  const themeBtn=el('button','btn btn-ico','◐');themeBtn.title='Theme';themeBtn.onclick=toggleTheme;top.appendChild(themeBtn);
  m.appendChild(top);
  const c=el('div','content');c.id='content';m.appendChild(c);
  return c;
}

// ── Dashboard ─────────────────────────────────────────────────────────
async function pageDashboard(){
  const c=page('Dashboard','Overview of your MCP connections');
  c.innerHTML='<p class="hint">Loading…</p>';
  try{
    const d=await api('/api/v1/dashboard?sections=main,services,recent');
    const svcs=d.services||[];const m=d.main||{};
    let ok=0,warn=0,bad=0;
    svcs.forEach(s=>{const st=statusOf(s).cls;if(st==='ok')ok++;else if(st==='warn')warn++;else bad++;});
    const stats=[
      {n:svcs.length,l:'Connections',cls:''},
      {n:ok,l:'🟢 Healthy',cls:'ok'},
      {n:warn,l:'🟡 Configured',cls:'warn'},
      {n:bad,l:'🔴 Needs attention',cls:'bad'},
      {n:m.registered_tools||0,l:'Tools',cls:''},
      {n:m.capabilities||0,l:'Capabilities',cls:''},
    ];
    c.innerHTML='';
    const g=el('div','grid stat-grid');g.style.marginBottom='22px';
    stats.forEach(s=>g.appendChild(el('div','card stat '+s.cls,'<div class="n">'+s.n+'</div><div class="l">'+s.l+'</div>')));
    c.appendChild(g);
    // quick actions
    const qa=el('div','card');qa.style.marginBottom='18px';
    qa.innerHTML='<div class="card-h"><h3>Quick actions</h3></div>';
    const row=el('div');row.style.cssText='display:flex;gap:8px;flex-wrap:wrap';
    const b1=el('button','btn btn-primary','⟳ Test all connections');b1.onclick=async()=>{b1.disabled=true;b1.textContent='Testing…';try{await api('/health/full-report',{method:'POST'});location.hash='#/connections';}catch(e){alert(e);}b1.disabled=false;};
    const b2=el('button','btn','🔍 Discover services');b2.onclick=()=>location.hash='#/discover';
    const b3=el('button','btn','🔌 Manage connections');b3.onclick=()=>location.hash='#/connections';
    row.append(b1,b2,b3);qa.appendChild(row);c.appendChild(qa);
    // recent
    const rc=el('div','card');rc.innerHTML='<div class="card-h"><h3>Recent activity</h3></div>';
    const runs=d.recent_tool_runs||[];
    if(runs.length){rc.insertAdjacentHTML('beforeend',runs.slice().reverse().slice(0,12).map(r=>'<div class="set-row" style="padding:6px 0;border-bottom:1px solid var(--b)"><code style="font-size:12px">'+esc(r.tool||r)+'</code><div class="spacer"></div><span class="muted">'+esc(r.ts||'')+'</span></div>').join(''));}
    else rc.insertAdjacentHTML('beforeend','<p class="hint">No recent tool runs.</p>');
    c.appendChild(rc);
  }catch(e){c.innerHTML='<p class="hint">Error: '+esc(e)+'</p>';}
}

// ── Connections ───────────────────────────────────────────────────────
let _conns=[],_filter={q:'',status:'',sort:'name'};
async function pageConnections(){
  const c=page('MCP Connections','Manage the services this MCP server connects to',
    '<button class="btn btn-primary" onclick="connAdd()">＋ Add</button>'+
    '<button class="btn" onclick="connRefresh()">⟳ Refresh</button>'+
    '<button class="btn" onclick="connTestAll(this)">✓ Test all</button>');
  c.innerHTML='<div class="toolbar">'+
    '<input class="field search" id="c-q" placeholder="Search…" oninput="connApply()">'+
    '<select class="field" id="c-status" onchange="connApply()"><option value="">All status</option><option value="ok">🟢 Healthy</option><option value="warn">🟡 Configured</option><option value="bad">🔴 Needs attention</option></select>'+
    '<select class="field" id="c-sort" onchange="connApply()"><option value="name">Sort: Name</option><option value="status">Sort: Status</option><option value="section">Sort: Category</option></select>'+
    '<div class="spacer"></div><span class="hint" id="c-count"></span></div>'+
    '<div class="card" style="padding:0;overflow:hidden"><table class="tbl"><thead><tr><th>Name</th><th>Category</th><th>Tools</th><th>Status</th><th style="text-align:right">Actions</th></tr></thead><tbody id="c-body"><tr><td colspan="5" style="padding:20px"><span class="hint">Loading…</span></td></tr></tbody></table></div>';
  try{const d=await api('/api/v1/dashboard?sections=services');_conns=d.services||[];connApply();}
  catch(e){$('#c-body').innerHTML='<tr><td colspan="5" style="padding:20px"><span class="hint">Error: '+esc(e)+'</span></td></tr>';}
}
function connApply(){
  _filter.q=($('#c-q')?.value||'').toLowerCase();_filter.status=$('#c-status')?.value||'';_filter.sort=$('#c-sort')?.value||'name';
  let rows=_conns.filter(s=>{
    const st=statusOf(s).cls;
    if(_filter.status&&st!==_filter.status)return false;
    if(_filter.q&&!((s.label||'')+' '+(s.id||'')+' '+(s.section||'')).toLowerCase().includes(_filter.q))return false;
    return true;
  });
  const rank={ok:0,warn:1,bad:2};
  rows.sort((a,b)=>_filter.sort==='status'?rank[statusOf(a).cls]-rank[statusOf(b).cls]:(_filter.sort==='section'?(a.section||'').localeCompare(b.section||''):(a.label||'').localeCompare(b.label||'')));
  const body=$('#c-body');
  if(!rows.length){body.innerHTML='<tr><td colspan="5" style="padding:20px"><span class="hint">No connections match.</span></td></tr>';}
  else body.innerHTML=rows.map(s=>{const st=statusOf(s);return '<tr onclick="connDetails(\''+s.id+'\')">'+
    '<td><div class="name">'+esc(s.label)+'</div><div class="muted">'+esc(s.id)+'</div></td>'+
    '<td><span class="tag">'+esc(s.section||'—')+'</span></td>'+
    '<td class="muted">'+(s.tool_count||0)+'</td>'+
    '<td><span class="dot '+st.cls+'">'+st.label+'</span></td>'+
    '<td><div class="row-actions" onclick="event.stopPropagation()">'+
      '<button class="btn btn-sm" onclick="connTest(\''+s.id+'\',this)">Test</button>'+
      '<button class="btn btn-sm" onclick="connDetails(\''+s.id+'\')">Details</button>'+
    '</div></td></tr>';}).join('');
  $('#c-count').textContent=rows.length+' of '+_conns.length;
}
async function connRefresh(){try{await api('/health/refresh');pageConnections();}catch(e){alert(e);}}
async function connTestAll(btn){btn.disabled=true;btn.textContent='Testing…';try{await api('/health/full-report',{method:'POST'});await pageConnections();}catch(e){alert(e);}btn.disabled=false;}
async function connTest(id,btn){if(btn){btn.disabled=true;btn.textContent='…';}try{const d=await api('/service/test/'+id);const row=_conns.find(x=>x.id===id);if(row)row.health=d.ok?true:(d.tri==='uncfg'?null:false);connApply();}catch(e){alert(e);}if(btn)btn.disabled=false;}
function connAdd(){location.hash='#/settings';setTimeout(()=>alert('Add a connection under Settings → Custom integrations (full add-connection form coming in the next phase).'),50);}

async function connDetails(id){
  const s=_conns.find(x=>x.id===id);if(!s)return;
  let bg=$('#drawer-bg');if(!bg){bg=el('div','drawer-bg');bg.id='drawer-bg';bg.onclick=closeDrawer;document.body.appendChild(bg);const dr=el('aside','drawer');dr.id='drawer';document.body.appendChild(dr);}
  const dr=$('#drawer');const st=statusOf(s);
  dr.innerHTML='<div class="drawer-h"><span class="dot '+st.cls+'"></span><h3>'+esc(s.label)+'</h3><button class="btn btn-ico" onclick="closeDrawer()">✕</button></div>'+
    '<div class="drawer-tabs">'+['Overview','Tools','Test'].map((t,i)=>'<div class="dtab'+(i===0?' active':'')+'" onclick="drawerTab(this,\''+t.toLowerCase()+'\')">'+t+'</div>').join('')+'</div>'+
    '<div class="drawer-body" id="drawer-body"></div>';
  $('#drawer-bg').classList.add('open');dr.classList.add('open');
  drawerRender(s,'overview');
}
function drawerTab(t,name){document.querySelectorAll('.dtab').forEach(x=>x.classList.remove('active'));t.classList.add('active');drawerRender(_conns.find(x=>x.id===_curDrawer),name);}
let _curDrawer=null;
function drawerRender(s,tab){_curDrawer=s.id;const b=$('#drawer-body');const st=statusOf(s);
  if(tab==='overview'){
    b.innerHTML='<div class="kv"><span class="k">ID</span><span>'+esc(s.id)+'</span>'+
      '<span class="k">Category</span><span>'+esc(s.section||'—')+'</span>'+
      '<span class="k">Status</span><span class="dot '+st.cls+'">'+st.label+'</span>'+
      '<span class="k">Configured</span><span>'+(s.configured?'Yes':'No')+'</span>'+
      '<span class="k">Tools</span><span>'+(s.tool_count||0)+'</span></div>'+
      '<div style="display:flex;gap:8px"><button class="btn btn-primary btn-sm" onclick="connTest(\''+s.id+'\',this)">Run test</button></div>';
  }else if(tab==='tools'){
    b.innerHTML='<div class="chips">'+((s.tool_names||[]).map(t=>'<span class="chip">'+esc(t)+'</span>').join('')||'<span class="hint">No tools.</span>')+'</div>';
  }else if(tab==='test'){
    b.innerHTML='<button class="btn btn-primary btn-sm" onclick="drawerTest(\''+s.id+'\')">Run test now</button><pre class="out" id="drawer-out" style="margin-top:12px">Not run yet.</pre>';
  }
}
async function drawerTest(id){const out=$('#drawer-out');out.textContent='Testing…';try{const d=await api('/service/test/'+id);out.textContent=d.output||d.detail||d.summary||'(no output)';const row=_conns.find(x=>x.id===id);if(row){row.health=d.ok?true:(d.tri==='uncfg'?null:false);connApply&&connApply();}}catch(e){out.textContent='Error: '+e;}}
function closeDrawer(){$('#drawer-bg')?.classList.remove('open');$('#drawer')?.classList.remove('open');}

// ── Discover ──────────────────────────────────────────────────────────
async function pageDiscover(){
  const c=page('Discover','Auto-detect services on your network');
  c.innerHTML='<div class="card"><div class="card-h"><h3>Scan a host</h3></div>'+
    '<div class="toolbar"><input class="field" id="d-host" placeholder="LAN host / IP (e.g. 192.168.1.111)" style="min-width:260px">'+
    '<label class="hint" style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="d-ports" checked> also probe common ports</label>'+
    '<button class="btn btn-primary" onclick="doScan(this)">Scan</button></div>'+
    '<p class="hint" id="d-msg"></p><div id="d-rows"></div></div>';
}
async function doScan(btn){const host=($('#d-host').value||'').trim();if(!host){alert('Enter a host.');return;}btn.disabled=true;$('#d-msg').textContent='Scanning…';$('#d-rows').innerHTML='';
  try{const d=await api('/api/v1/wizard/scan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({host,include_port_scan:$('#d-ports').checked})});
    const sug=d.suggestions||[];$('#d-msg').textContent=(d.docker&&d.docker.ok?(d.docker.containers_seen+' containers'):'Docker unavailable')+' · '+sug.length+' suggestion(s)';
    $('#d-rows').innerHTML=sug.map(it=>'<div class="set-row" style="border-bottom:1px solid var(--b)"><strong>'+esc(it.label||it.service_id)+'</strong> <span class="tag">'+esc(it.source||'')+'</span><div class="spacer"></div><code class="muted">'+esc((it.editable_keys&&it.editable_keys[0]&&it.editable_keys[0].value)||'')+'</code></div>').join('')||'<p class="hint">No services detected.</p>';
  }catch(e){$('#d-msg').textContent='Error: '+e;}btn.disabled=false;}

// ── Slicer ────────────────────────────────────────────────────────────
async function pageSlicer(){
  const c=page('Slicer','Shrink the tool manifest exposed to MCP clients');
  c.innerHTML='<div class="card"><div class="toolbar"><input class="field" id="s-intent" placeholder="Intent (e.g. calendar tasks files) — blank = all" style="min-width:280px" oninput="doSlice()"><button class="btn" onclick="doSlice()">Preview</button></div><div id="s-out"><p class="hint">Loading…</p></div></div>';
  doSlice();
}
async function doSlice(){const q=($('#s-intent')?.value||'').trim();try{const d=await api('/api/v1/tools/slicer?intent='+encodeURIComponent(q));
  const secs=Object.entries(d.by_section||{}).map(([k,v])=>'<span class="chip">'+esc(k)+' '+v.exposed+'/'+v.total+'</span>').join('');
  $('#s-out').innerHTML='<div class="stat" style="margin-bottom:12px"><div class="n">'+d.exposed+'</div><div class="l">exposed · '+d.blocked+' blocked · '+d.matched+'/'+d.total+' matched</div></div><div class="chips">'+secs+'</div>';
}catch(e){$('#s-out').innerHTML='<p class="hint">Error: '+esc(e)+'</p>';}}

// ── Agents / Chat ─────────────────────────────────────────────────────
function pageAgents(){const c=page('Agents','Headless Claude Code automation');
  c.innerHTML='<div class="empty"><div class="big">🤖</div><h3>Agent platform</h3><p>Run and schedule headless agents that drive your MCP tools. The full agent workspace opens in a dedicated view.</p><div style="margin-top:18px"><a class="btn btn-primary" href="/agents">Open Agents workspace →</a></div></div>';}
function pageChat(){const c=page('Chat','Coming soon');
  c.innerHTML='<div class="empty"><div class="big">💬</div><h3>Chat — Coming soon</h3><p>A conversational interface to talk to your homelab through Plutus\' tools. This lands in a future release.</p></div>';}

// ── Settings ──────────────────────────────────────────────────────────
async function pageSettings(){
  const c=page('Settings','Configure Plutus');
  const theme=document.documentElement.getAttribute('data-theme')||'dark';
  c.innerHTML=
    '<div class="set-sec"><h3>Appearance</h3>'+
      '<div class="set-row"><label>Theme</label><button class="btn" onclick="applyTheme(\'dark\')">Dark</button><button class="btn" onclick="applyTheme(\'light\')">Light</button></div></div>'+
    '<div class="set-sec"><h3>MCP</h3>'+
      '<div class="set-row"><label>Bearer auth</label><span class="hint" id="set-bearer">…</span></div>'+
      '<div class="set-row"><label>Export client config</label><a class="btn" href="/ui#">Connection Manager (classic UI)</a><span class="hint">Full manager arrives in the redesigned Connections page.</span></div></div>'+
    '<div class="set-sec"><h3>Custom integrations</h3>'+
      '<div class="set-row"><label>Add / edit</label><a class="btn" href="/ui" target="_blank">Open classic settings</a><span class="hint">The native add-connection form is coming to this page next phase.</span></div></div>'+
    '<div class="set-sec"><h3>About</h3><div class="set-row"><label>Version</label><span id="set-ver" class="hint">…</span></div>'+
      '<div class="set-row"><label>Classic dashboard</label><a class="btn" href="/ui">Open /ui</a></div></div>';
  try{const d=await api('/api/v1/dashboard?sections=auth,networking');$('#set-bearer').textContent=d.auth&&d.auth.mcp_require_bearer?('Required'+(d.auth.mcp_bearer_configured?' · token set':' · no token!')):'Off';}catch{}
  try{const h=await api('/server/health');$('#set-ver').textContent='Plutus v'+(h.version||'?');}catch{}
}

// ── Router ────────────────────────────────────────────────────────────
const ROUTES={dashboard:pageDashboard,connections:pageConnections,discover:pageDiscover,slicer:pageSlicer,agents:pageAgents,chat:pageChat,settings:pageSettings};
function router(){const r=(location.hash.replace(/^#\//,'')||'dashboard');const fn=ROUTES[r]||pageDashboard;setActive(r in ROUTES?r:'dashboard');closeDrawer();fn();}
applyTheme(localStorage.getItem('plutus_theme')||'dark');
buildShell();
window.addEventListener('hashchange',router);
router();
