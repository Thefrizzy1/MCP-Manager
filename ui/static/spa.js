"use strict";
/* Plutus SPA — framework-free single-page console. */
const $=(s,r=document)=>r.querySelector(s);
const el=(t,c,h)=>{const e=document.createElement(t);if(c)e.className=c;if(h!=null)e.innerHTML=h;return e;};
const esc=s=>s==null?'':String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
const jsesc=s=>String(s==null?'':s).replace(/\\/g,'\\\\').replace(/'/g,"\\'").replace(/"/g,'&quot;');
const fmtSize=n=>{n=+n||0;const u=['B','KB','MB','GB','TB'];let i=0;while(n>=1024&&i<u.length-1){n/=1024;i++;}return n.toFixed(i?1:0)+' '+u[i];};
async function api(path,opts){const r=await fetch(path,opts);const t=await r.text();let d;try{d=JSON.parse(t);}catch{d=t;}if(!r.ok)throw (d&&d.detail)||('HTTP '+r.status);return d;}

const NAV=[
 {sec:'Main'},
 {id:'dashboard',label:'Dashboard',ico:'▦'},
 {id:'connections',label:'MCP Connections',ico:'🔌'},
 {id:'discover',label:'Discover',ico:'🔍'},
 {id:'slicer',label:'Slicer',ico:'▤'},
 {sec:'Automation'},
 {id:'agents',label:'Agents',ico:'🤖'},
 {id:'builder',label:'AI Builder',ico:'✨'},
 {id:'chat',label:'Chat',ico:'💬',soon:true},
 {sec:'Library'},
 {id:'files',label:'Files',ico:'📁'},
 {id:'integrations',label:'Integrations',ico:'🧩'},
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
// ── Modal helper ──────────────────────────────────────────────────────
function modal(title,bodyHtml,footHtml){
  closeModal();
  const bg=el('div','drawer-bg');bg.id='modal-bg';bg.style.zIndex='60';bg.onclick=e=>{if(e.target===bg)closeModal();};
  const box=el('div','card');box.style.cssText='position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);z-index:61;width:min(560px,94vw);max-height:88vh;overflow:auto';
  box.innerHTML='<div class="card-h"><h3>'+esc(title)+'</h3><div class="spacer"></div><button class="btn btn-ico" onclick="closeModal()">✕</button></div>'+bodyHtml+(footHtml?('<div class="set-row" style="margin-top:14px">'+footHtml+'</div>'):'');
  bg.appendChild(box);document.body.appendChild(bg);requestAnimationFrame(()=>bg.classList.add('open'));
}
function closeModal(){const m=$('#modal-bg');if(m)m.remove();}

async function connAdd(){
  modal('Add connection',
    '<p class="hint" style="margin-bottom:12px">Add a custom service card (base-URL env + health path). Stored in data/custom_integrations.json.</p>'+
    '<div class="set-row"><label>Name</label><input class="field" id="ac-label" placeholder="Audiobookshelf" style="flex:1"></div>'+
    '<div class="set-row"><label>Id (slug)</label><input class="field" id="ac-id" placeholder="audiobookshelf" style="flex:1"></div>'+
    '<div class="set-row"><label>Env key (URL)</label><input class="field" id="ac-urlenv" placeholder="AUDIOBOOKSHELF_URL" style="flex:1"></div>'+
    '<div class="set-row"><label>URL</label><input class="field" id="ac-url" placeholder="http://192.168.1.111:13378" style="flex:1"></div>'+
    '<div class="set-row"><label>Health path</label><input class="field" id="ac-health" value="/" style="flex:1"></div>',
    '<button class="btn btn-primary" onclick="connAddSave(this)">Add connection</button><span class="hint" id="ac-msg"></span>');
}
async function connAddSave(btn){
  const id=($('#ac-id').value||'').trim().toLowerCase().replace(/[^a-z0-9_]/g,'_');
  const label=($('#ac-label').value||'').trim();
  if(!id||!label){$('#ac-msg').textContent='Name and id required.';return;}
  const one={id,label,description:label,url_env:($('#ac-urlenv').value||id.toUpperCase()+'_URL').trim(),url_placeholder:($('#ac-url').value||'').trim(),health_path:($('#ac-health').value||'/').trim()};
  btn.disabled=true;
  try{
    const full=await api('/settings/custom-integrations');
    const list=Array.isArray(full.integrations)?full.integrations:[];
    list.push(one);
    await api('/settings/custom-integrations',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({version:full.version||1,integrations:list})});
    // also save the URL value if provided
    if(one.url_placeholder)await api('/env/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({[one.url_env]:one.url_placeholder})});
    closeModal();pageConnections();
  }catch(e){$('#ac-msg').textContent='Error: '+e;btn.disabled=false;}
}

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

// ── Agents workspace ──────────────────────────────────────────────────
let _agentCfg={},_agentES=null,_healthyConns=[];
async function pageAgents(){
  const c=page('Agents','Launch, schedule and monitor headless agents',
    '<button class="btn" onclick="agToggleWizard()">＋ New agent</button>');
  c.innerHTML='<div id="ag-wizard" class="hidden"></div>'+
    '<div class="card" style="margin-bottom:16px"><div class="card-h"><h3>Running &amp; recent</h3><div class="spacer"></div><span class="hint" id="ag-budget"></span></div><div id="ag-running"><p class="hint">Loading…</p></div></div>'+
    '<div class="card" style="margin-bottom:16px"><div class="card-h"><h3>Scheduled jobs</h3></div><div id="ag-sched"><p class="hint">Loading…</p></div></div>'+
    '<details class="card" id="ag-console-card"><summary style="cursor:pointer;font-weight:700;font-size:14px;list-style:none">Live console</summary><pre class="out" id="ag-console" style="margin-top:12px">Idle.</pre></details>';
  await Promise.all([agLoadStatus(),agLoadSched(),agLoadHealthy()]);
  agRenderWizard();
}
async function agLoadHealthy(){try{const d=await api('/api/v1/dashboard?sections=services');_healthyConns=(d.services||[]).filter(s=>s.configured&&(s.section||'').toLowerCase().indexOf('public')<0);}catch{_healthyConns=[];}}
async function agLoadStatus(){
  try{const d=await api('/api/v1/agent/status');_agentCfg=d.config||{};
    const am=(d.auth&&d.auth.mode)||'none';const onPlan=(am==='session_token'||am==='subscription');
    $('#ag-budget').textContent=(onPlan?'plan · ':(am==='api_key'?'API billing · ':'⚠ not connected · '))+'today '+(d.runs_today||0)+'/'+(d.max_runs_per_day||0)+' · $'+(d.total_cost_usd||0);
    const runs=(await api('/api/v1/agent/runs')).runs||[];
    const running=d.running;
    let html='';
    if(running)html+='<div class="ag-card-run"><span class="dot ok breathing"></span><div class="ag-item-main"><strong>'+esc(d.current_label||'agent')+'</strong><div class="hint">running…</div></div><button class="btn btn-sm btn-danger" onclick="agStop()">Stop</button></div>';
    html+=runs.slice(0,8).map(r=>{const ic=r.cancelled?'⏹':(r.ok?'🟢':(r.error?'🔴':'•'));return '<div class="ag-card-run"><span>'+ic+'</span><div class="ag-item-main"><strong>'+esc(r.label||r.id)+'</strong> '+(r.cost_usd!=null?('<span class="muted">$'+r.cost_usd+'</span>'):'')+'<div class="hint">'+esc((r.result||r.error||'').slice(0,90))+'</div></div><span class="muted">'+esc((r.started||'').replace('T',' ').slice(5,16))+'</span></div>';}).join('');
    $('#ag-running').innerHTML=html||'<p class="hint">No runs yet.</p>';
    if(running&&(!_agentES))agStream();
  }catch(e){$('#ag-running').innerHTML='<p class="hint">Error: '+esc(e)+'</p>';}
}
function agStream(){const con=$('#ag-console');$('#ag-console-card').open=true;if(_agentES){try{_agentES.close();}catch{}}_agentES=new EventSource('/api/v1/agent/stream');con.textContent='';_agentES.onmessage=ev=>{let l=ev.data;try{l=JSON.parse(ev.data);}catch{}con.textContent+=(con.textContent&&con.textContent!=='Idle.'?'\n':'')+l;con.scrollTop=con.scrollHeight;};_agentES.addEventListener('end',()=>{_agentES.close();_agentES=null;agLoadStatus();});_agentES.onerror=()=>{if(_agentES){_agentES.close();_agentES=null;}};}
async function agStop(){try{await api('/api/v1/agent/cancel',{method:'POST'});}catch{}}
async function agLoadSched(){try{const d=await api('/api/v1/schedules');const items=(d.schedules||[]).filter(s=>s.kind==='agent'||s.kind==='task');
  $('#ag-sched').innerHTML=items.length?('<table class="tbl"><thead><tr><th>Name</th><th>Schedule</th><th>Next run</th><th>Status</th><th style="text-align:right">Actions</th></tr></thead><tbody>'+items.map(s=>'<tr><td class="name">'+esc(s.name)+'</td><td class="muted">'+esc(s.cron)+'</td><td class="muted">'+esc(s.next_run?s.next_run.replace('T',' ').slice(0,16):'—')+'</td><td><span class="dot '+(s.enabled?'ok':'warn')+'">'+(s.enabled?'Enabled':'Paused')+'</span></td><td><div class="row-actions"><button class="btn btn-sm" onclick="agSchToggle(\''+s.id+'\','+(!s.enabled)+')">'+(s.enabled?'Pause':'Resume')+'</button><button class="btn btn-sm" onclick="agSchRun(\''+s.id+'\')">Run</button><button class="btn btn-sm btn-danger" onclick="agSchDel(\''+s.id+'\')">Delete</button></div></td></tr>').join('')+'</tbody></table>'):'<p class="hint">No scheduled agents.</p>';
}catch(e){$('#ag-sched').innerHTML='<p class="hint">Error: '+esc(e)+'</p>';}}
async function agSchToggle(id,en){try{const s=(await api('/api/v1/schedules')).schedules.find(x=>x.id===id);if(!s)return;await api('/api/v1/schedules/'+id,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:s.name,kind:s.kind,cron:s.cron,timezone:s.timezone,enabled:en,payload:s.payload})});agLoadSched();}catch(e){alert(e);}}
async function agSchRun(id){try{await api('/api/v1/schedules/'+id+'/run-now',{method:'POST'});setTimeout(()=>{agStream();agLoadStatus();},400);}catch(e){alert(e);}}
async function agSchDel(id){if(!confirm('Delete schedule?'))return;try{await api('/api/v1/schedules/'+id,{method:'DELETE'});agLoadSched();}catch(e){alert(e);}}

function agToggleWizard(){const w=$('#ag-wizard');w.classList.toggle('hidden');if(!w.classList.contains('hidden'))w.scrollIntoView({behavior:'smooth'});}
function agRenderWizard(){
  const cfg=_agentCfg||{};
  $('#ag-wizard').innerHTML='<div class="card ag-accent" style="margin-bottom:16px;border-left:3px solid var(--ac)">'+
    '<div class="card-h"><h3>Launch an agent</h3></div>'+
    '<div class="grid" style="grid-template-columns:1fr 1fr;gap:12px">'+
      '<div class="ag-field"><label>Name</label><input class="field" id="w-name" placeholder="Morning research"></div>'+
      '<div class="ag-field"><label>Model</label><input class="field" id="w-model" list="w-models" placeholder="default"><datalist id="w-models"><option value="opus"><option value="sonnet"><option value="haiku"></datalist></div>'+
    '</div>'+
    '<div class="ag-field" style="margin-top:12px"><label>Goal / prompt</label><textarea class="field" id="w-prompt" rows="3" placeholder="What should the agent do?"></textarea></div>'+
    '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-top:12px">'+
      '<div class="ag-field"><label>Schedule</label><select class="field" id="w-sched" onchange="agWizSched()"><option value="now">Run now</option><option value="daily">Daily</option><option value="weekly">Weekly</option><option value="cron">Custom cron</option></select></div>'+
      '<div class="ag-field" id="w-time-wrap"><label>Time</label><input type="time" class="field" id="w-time" value="07:00"></div>'+
      '<div class="ag-field hidden" id="w-dow-wrap"><label>Day</label><select class="field" id="w-dow"><option value="1">Mon</option><option value="2">Tue</option><option value="3">Wed</option><option value="4">Thu</option><option value="5">Fri</option><option value="6">Sat</option><option value="0">Sun</option></select></div>'+
      '<div class="ag-field hidden" id="w-cron-wrap"><label>Cron</label><input class="field" id="w-cron" value="0 7 * * *"></div>'+
    '</div>'+
    '<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:12px">'+
      '<div class="ag-field"><label>MCP access level</label><select class="field" id="w-perm"><option value="strict_read">Strict read</option><option value="safe" selected>Safe (reads + notes)</option><option value="all">All tools</option></select></div>'+
      '<div class="ag-field"><label>Timeout (min)</label><input type="number" class="field" id="w-timeout" value="'+(cfg.timeout_min||20)+'" min="1" max="120"></div>'+
    '</div>'+
    '<div class="ag-field" style="margin-top:14px"><label>MCP connections the agent may use</label>'+
      '<div style="margin:6px 0"><button class="btn btn-sm" onclick="agWizSel(true)">Select all</button> <button class="btn btn-sm" onclick="agWizSel(false)">Select none</button></div>'+
      '<div id="w-mcp" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:6px;max-height:180px;overflow:auto">'+
        _healthyConns.map(s=>{const st=statusOf(s);return '<label class="chip" style="display:flex;gap:7px;align-items:center;cursor:pointer"><input type="checkbox" class="w-mcp-c" value="'+s.id+'" checked><span class="dot '+st.cls+'" style="width:auto"></span>'+esc(s.label)+'</label>';}).join('')||'<span class="hint">No configured connections.</span>'+
      '</div><p class="hint" style="margin-top:4px">Unchecked connections are blocked for this run (Run now). Web/file/utility tools stay available.</p></div>'+
    '<div class="set-row" style="margin-top:14px"><button class="btn btn-primary" onclick="agWizLaunch(this)">🚀 Launch</button><span class="hint" id="w-msg"></span></div>'+
  '</div>';
  if(window._agentDraft){const n=$('#w-name'),p=$('#w-prompt');if(n)n.value=window._agentDraft.name||'';if(p)p.value=window._agentDraft.prompt||'';window._agentDraft=null;}
}
function agWizSched(){const v=$('#w-sched').value;$('#w-time-wrap').classList.toggle('hidden',!(v==='daily'||v==='weekly'));$('#w-dow-wrap').classList.toggle('hidden',v!=='weekly');$('#w-cron-wrap').classList.toggle('hidden',v!=='cron');}
function agWizSel(on){document.querySelectorAll('.w-mcp-c').forEach(c=>c.checked=on);}
function agWizCron(){const v=$('#w-sched').value;const t=($('#w-time').value||'07:00').split(':');const hh=+t[0],mm=+t[1];if(v==='daily')return mm+' '+hh+' * * *';if(v==='weekly')return mm+' '+hh+' * * '+$('#w-dow').value;if(v==='cron')return $('#w-cron').value.trim();return null;}
async function agWizLaunch(btn){
  const name=($('#w-name').value||'agent').trim(),prompt=($('#w-prompt').value||'').trim();
  if(!prompt){$('#w-msg').textContent='Enter a goal/prompt.';return;}
  btn.disabled=true;$('#w-msg').textContent='…';
  try{
    // save launch options as defaults
    await api('/api/v1/agent/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tool_permission:$('#w-perm').value,timeout_min:+$('#w-timeout').value||20,model:($('#w-model').value||'').trim()})});
    const cron=agWizCron();
    if(cron){await api('/api/v1/schedules',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,kind:'agent',cron,timezone:'Europe/Berlin',enabled:true,payload:{prompt}})});$('#w-msg').textContent='Scheduled.';agLoadSched();}
    else{const sel=[...document.querySelectorAll('.w-mcp-c:checked')].map(c=>c.value);await api('/api/v1/agent/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({prompt,label:name,permission:$('#w-perm').value,mcp_services:sel})});$('#w-msg').textContent='Launched.';agStream();agLoadStatus();}
    setTimeout(()=>{agToggleWizard();},600);
  }catch(e){$('#w-msg').textContent='Error: '+e;}
  btn.disabled=false;
}
function pageChat(){const c=page('Chat','Coming soon');
  c.innerHTML='<div class="empty"><div class="big">💬</div><h3>Chat — Coming soon</h3><p>A conversational interface to talk to your homelab through Plutus\' tools. This lands in a future release.</p></div>';}

// ── Settings ──────────────────────────────────────────────────────────
async function pageSettings(){
  const c=page('Settings','Configure Plutus');
  c.innerHTML=
    '<div class="set-sec"><h3>Appearance</h3>'+
      '<div class="set-row"><label>Theme</label><button class="btn" onclick="applyTheme(\'dark\')">Dark</button><button class="btn" onclick="applyTheme(\'light\')">Light</button></div></div>'+
    '<div class="set-sec"><h3>MCP endpoint</h3>'+
      '<div class="set-row"><label>LAN URL</label><code id="set-lan" class="hint"></code></div>'+
      '<div class="set-row"><label>Public HTTPS base</label><input class="field" id="set-pub" placeholder="https://mcp.your-ts.net" style="flex:1"></div>'+
      '<div class="set-row"><label>LAN host</label><input class="field" id="set-host" style="flex:1"></div>'+
      '<div class="set-row"><label></label><button class="btn btn-primary" onclick="setSaveEndpoints(this)">Save URLs</button><span class="hint">restart Plutus to apply</span></div>'+
      '<div class="set-row"><label>Require bearer</label><input type="checkbox" id="set-req"><button class="btn" onclick="setSaveBearer()">Save</button></div>'+
      '<div class="set-row"><label>Token</label><code id="set-tok" class="hint">…</code><button class="btn" onclick="setGenToken(this)">Generate</button></div>'+
      '<div class="set-row"><label>Connect a client</label><button class="btn" onclick="cmOpen()">Export config (Claude, Cursor, …)</button></div></div>'+
    '<div class="set-sec"><h3>Custom integrations</h3>'+
      '<div class="set-row hint" style="flex-basis:100%">Extra service cards (JSON). Or use ＋ Add on the Connections page.</div>'+
      '<textarea class="field" id="set-ci" spellcheck="false" style="width:100%;height:150px;font-family:ui-monospace,monospace;font-size:11px"></textarea>'+
      '<div class="set-row"><button class="btn btn-primary" onclick="setSaveCI(this)">Save integrations</button><span class="hint" id="set-ci-msg"></span></div></div>'+
    '<div class="set-sec"><h3>Defaults</h3>'+
      '<div class="set-row"><label>Weather city</label><input class="field" id="set-city"><button class="btn" onclick="setSaveCity(this)">Save</button></div>'+
      '<div class="set-row"><label>UI username</label><input class="field" id="set-user"></div>'+
      '<div class="set-row"><label>New password</label><input type="password" class="field" id="set-pass" placeholder="blank = keep"><button class="btn" onclick="setSaveCreds(this)">Save</button></div></div>'+
    '<div class="set-sec"><h3>Maintenance</h3>'+
      '<div class="set-row"><button class="btn" onclick="setReset(\'urls\',this)">Reset URLs</button><button class="btn" onclick="setReset(\'weather\',this)">Reset weather</button><button class="btn btn-danger" onclick="setReset(\'custom_integrations\',this)">Clear custom cards</button></div></div>'+
    '<div class="set-sec"><h3>About</h3><div class="set-row"><label>Version</label><span id="set-ver" class="hint">…</span></div>'+
      '<div class="set-row"><label>Bearer status</label><span class="hint" id="set-bearer">…</span></div></div>';
  try{const d=await api('/api/v1/dashboard?sections=auth,networking');const n=d.networking||{};
    $('#set-lan').textContent=n.http_local||'';$('#set-pub').value=n.public_base||'';$('#set-host').value=n.mcp_lan_host||'';$('#set-req').checked=!!(d.auth&&d.auth.mcp_require_bearer);
    $('#set-bearer').textContent=d.auth&&d.auth.mcp_require_bearer?('Required'+(d.auth.mcp_bearer_configured?' · token set':' · NO token set!')):'Off';
    $('#set-tok').textContent=d.auth&&d.auth.mcp_bearer_configured?'configured (hidden)':'not set';
  }catch{}
  try{const ci=await api('/settings/custom-integrations');$('#set-ci').value=JSON.stringify(ci,null,2);}catch{}
  try{const h=await api('/server/health');$('#set-ver').textContent='Plutus v'+(h.version||'?');}catch{}
  $('#set-city').placeholder='Hamburg';
}
async function setSaveEndpoints(b){b.disabled=true;try{await api('/env/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({PUBLIC_MCP_BASE:$('#set-pub').value.trim(),MCP_LAN_HOST:$('#set-host').value.trim()})});b.textContent='Saved';}catch(e){alert(e);}setTimeout(()=>{b.disabled=false;b.textContent='Save URLs';},1500);}
async function setSaveBearer(){try{await api('/env/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({MCP_REQUIRE_BEARER:$('#set-req').checked})});alert('Saved — takes effect within seconds.');}catch(e){alert(e);}}
async function setGenToken(b){b.disabled=true;try{const d=await api('/settings/generate-token',{method:'POST'});$('#set-tok').textContent=d.token||'error';}catch(e){alert(e);}b.disabled=false;}
async function setSaveCI(b){let o;try{o=JSON.parse($('#set-ci').value);}catch(e){$('#set-ci-msg').textContent='Invalid JSON';return;}b.disabled=true;try{await api('/settings/custom-integrations',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(o)});$('#set-ci-msg').textContent='Saved — reload to see cards.';}catch(e){$('#set-ci-msg').textContent=''+e;}b.disabled=false;}
async function setSaveCity(b){try{await api('/env/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({WEATHER_DEFAULT_LOCATION:$('#set-city').value.trim()})});b.textContent='Saved';setTimeout(()=>b.textContent='Save',1200);}catch(e){alert(e);}}
async function setSaveCreds(b){const d={UI_USERNAME:$('#set-user').value.trim()};const p=$('#set-pass').value;if(p)d.UI_PASSWORD=p;try{await api('/env/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)});alert('Saved — restart to apply.');}catch(e){alert(e);}}
async function setReset(scope,b){if(!confirm('Reset '+scope+'?'))return;try{await api('/api/v1/settings/reset',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scopes:[scope]})});alert('Reset done.');pageSettings();}catch(e){alert(e);}}
// Connection Manager (export client configs)
let _cmData=null;
async function cmOpen(){
  modal('Connect a client','<p class="hint" style="margin-bottom:10px">Download a ready-to-use config for any MCP client.</p><label class="hint" style="display:flex;gap:6px;align-items:center;margin-bottom:8px"><input type="checkbox" id="cm-tok" onchange="cmLoad()"> embed bearer token</label><div id="cm-list"><span class="hint">Loading…</span></div><pre class="out hidden" id="cm-pre" style="margin-top:10px"></pre>','<button class="btn btn-primary hidden" id="cm-dl" onclick="cmDownload()">Download</button>');
  cmLoad();
}
async function cmLoad(){try{const q=$('#cm-tok')&&$('#cm-tok').checked?'?include_token=1':'';_cmData=await api('/api/v1/mcp/connections'+q);$('#cm-list').innerHTML='<div class="chips">'+(_cmData.clients||[]).map(cc=>'<button class="chip" style="cursor:pointer" onclick="cmPick(\''+cc.id+'\')">'+esc(cc.label)+'</button>').join('')+'</div>';}catch(e){$('#cm-list').innerHTML='<span class="hint">Error: '+esc(e)+'</span>';}}
let _cmSel=null;
function cmPick(id){_cmSel=id;const cc=(_cmData.clients||[]).find(x=>x.id===id);if(!cc)return;const pre=$('#cm-pre');pre.classList.remove('hidden');pre.textContent=cc.content;$('#cm-dl').classList.remove('hidden');}
function cmDownload(){const cc=(_cmData.clients||[]).find(x=>x.id===_cmSel);if(!cc)return;const b=new Blob([cc.content],{type:(cc.mime||'text/plain')+';charset=utf-8'});const u=URL.createObjectURL(b);const a=document.createElement('a');a.href=u;a.download=cc.download_name||'mcp.json';document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(u),1000);}

// ── File manager ──────────────────────────────────────────────────────
let _fpath='';
async function pageFiles(){
  const c=page('Files','Browse and manage generated files (within your allowed paths)');
  c.innerHTML='<div id="f-crumb" class="toolbar"></div><div class="card" style="padding:0;overflow:hidden"><table class="tbl"><thead><tr><th>Name</th><th>Size</th><th>Modified</th><th style="text-align:right">Actions</th></tr></thead><tbody id="f-body"></tbody></table></div><pre class="out hidden" id="f-preview" style="margin-top:14px"></pre>';
  filesLoad(_fpath);
}
async function filesLoad(path){_fpath=path;const body=$('#f-body');if(body)body.innerHTML='<tr><td colspan="4" style="padding:16px"><span class="hint">Loading…</span></td></tr>';$('#f-preview')?.classList.add('hidden');
  try{const d=await api('/api/v1/files/list?path='+encodeURIComponent(path||''));
    const crumb=$('#f-crumb');const parts=['<button class="btn btn-sm" onclick="filesLoad(\'\')">Roots</button>'];
    if(d.path){parts.push('<span class="hint">'+esc(d.path)+'</span>');if(d.parent)parts.push('<button class="btn btn-sm" onclick="filesLoad(\''+jsesc(d.parent)+'\')">↑ Up</button>');}
    crumb.innerHTML=parts.join(' ');
    const items=d.items||[];
    if(!items.length){body.innerHTML='<tr><td colspan="4" style="padding:16px"><span class="hint">Empty.</span></td></tr>';return;}
    body.innerHTML=items.map(it=>{const isdir=it.type==='dir';const icon=isdir?'📁':'📄';
      const acts=isdir?'':'<button class="btn btn-sm" onclick="event.stopPropagation();filesPreview(\''+jsesc(it.path)+'\')">Preview</button><a class="btn btn-sm" href="/api/v1/files/download?path='+encodeURIComponent(it.path)+'">Download</a><button class="btn btn-sm btn-danger" onclick="event.stopPropagation();filesDelete(\''+jsesc(it.path)+'\')">Delete</button>';
      const click=isdir?"filesLoad('"+jsesc(it.path)+"')":"filesPreview('"+jsesc(it.path)+"')";
      return '<tr onclick="'+click+'"><td class="name">'+icon+' '+esc(it.name)+(it.exists===false?' <span class="muted">(not mounted)</span>':'')+'</td><td class="muted">'+(isdir?'—':fmtSize(it.size))+'</td><td class="muted">'+(it.mtime?new Date(it.mtime*1000).toLocaleString():'')+'</td><td><div class="row-actions" onclick="event.stopPropagation()">'+acts+'</div></td></tr>';}).join('');
  }catch(e){body.innerHTML='<tr><td colspan="4" style="padding:16px"><span class="hint">Error: '+esc(e)+'</span></td></tr>';}}
async function filesPreview(path){const p=$('#f-preview');p.classList.remove('hidden');p.textContent='Loading…';p.scrollIntoView({behavior:'smooth'});try{const d=await api('/api/v1/files/read?path='+encodeURIComponent(path));p.textContent=d.text||'(empty)';}catch(e){p.textContent='Error: '+e;}}
async function filesDelete(path){if(!confirm('Delete this file?'))return;try{await api('/api/v1/files/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path})});filesLoad(_fpath);}catch(e){alert(e);}}

// ── AI Builder ────────────────────────────────────────────────────────
async function pageBuilder(){
  const c=page('AI Builder','Describe an agent in plain language — Claude drafts it');
  c.innerHTML='<div class="card ag-accent" style="border-left:3px solid var(--ac)"><div class="card-h"><h3>Describe your agent</h3></div>'+
    '<p class="hint" style="margin-bottom:10px">e.g. "Every morning research new low-VRAM ComfyUI workflows and log the best ones to my notes with source links."</p>'+
    '<textarea class="field" id="b-desc" rows="3" style="width:100%"></textarea>'+
    '<div class="set-row" style="margin-top:12px"><button class="btn btn-primary" id="b-gen" onclick="builderGen(this)">✨ Draft with Claude</button><span class="hint" id="b-msg"></span></div></div>'+
    '<div class="card hidden" id="b-review" style="margin-top:16px"><div class="card-h"><h3>Review &amp; launch</h3></div>'+
      '<div class="ag-field"><label>Name</label><input class="field" id="b-name"></div>'+
      '<div class="ag-field" style="margin-top:10px"><label>Goal / prompt</label><textarea class="field" id="b-prompt" rows="7" style="width:100%"></textarea></div>'+
      '<div class="set-row" style="margin-top:12px"><button class="btn btn-primary" onclick="builderLaunch(this)">🚀 Create &amp; run now</button><button class="btn" onclick="builderToWizard()">Open in launch wizard →</button></div></div>';
}
async function builderGen(btn){const desc=($('#b-desc').value||'').trim();if(desc.length<5){$('#b-msg').textContent='Describe what it should do.';return;}btn.disabled=true;$('#b-msg').textContent='Claude is drafting… (up to ~1 min)';
  try{const d=await api('/api/v1/agent/tasks/build',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({description:desc})});
    if(!d.ok){$('#b-msg').textContent=d.error||'failed — connect your Claude account in Settings';btn.disabled=false;return;}
    $('#b-msg').textContent='Drafted — review and launch.';$('#b-review').classList.remove('hidden');
    $('#b-name').value=desc.slice(0,50);$('#b-prompt').value=d.prompt||'';$('#b-review').scrollIntoView({behavior:'smooth'});
  }catch(e){$('#b-msg').textContent=''+e;}btn.disabled=false;}
async function builderLaunch(btn){const name=($('#b-name').value||'agent').trim(),prompt=($('#b-prompt').value||'').trim();if(!prompt)return;btn.disabled=true;try{await api('/api/v1/agent/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({prompt,label:name})});location.hash='#/agents';}catch(e){alert(e);btn.disabled=false;}}
function builderToWizard(){window._agentDraft={name:($('#b-name').value||'').trim(),prompt:($('#b-prompt').value||'').trim()};location.hash='#/agents';setTimeout(()=>{const w=$('#ag-wizard');if(w&&w.classList.contains('hidden'))agToggleWizard();},350);}

// ── Integrations catalog ──────────────────────────────────────────────
const CATALOG=[
 {c:'Google',items:['YouTube','Google Drive','Gmail','Google Docs','Google Sheets','Google Calendar','Google Analytics']},
 {c:'Microsoft',items:['Outlook','OneDrive','Teams','Excel','SharePoint']},
 {c:'Websites',items:['WordPress','WooCommerce','Shopify','Ghost','Webflow']},
 {c:'Social',items:['Reddit','X (Twitter)','Facebook','Instagram','LinkedIn','TikTok','Bluesky','Mastodon','Discord','Telegram']},
 {c:'Development',items:['GitHub','GitLab','Docker','Kubernetes']},
 {c:'Productivity',items:['Notion','Obsidian','Slack','Trello','Jira','Asana','Airtable']},
 {c:'AI services',items:['OpenAI','Anthropic','Gemini','Perplexity']},
 {c:'Storage',items:['Dropbox','S3-compatible']},
 {c:'Creator & payments',items:['Buy Me a Coffee','Patreon','Stripe','PayPal']},
];
function pageIntegrations(){
  const c=page('Integrations','Curated MCP catalog — add a connection to configure it');
  c.innerHTML='<div class="toolbar"><input class="field search" id="i-q" placeholder="Search integrations…" oninput="intFilter()"><div class="spacer"></div><span class="hint">Each needs its own MCP server; Add creates the connection card.</span></div><div id="i-grid"></div>';
  intFilter();
}
function intFilter(){const q=($('#i-q')?.value||'').toLowerCase();
  $('#i-grid').innerHTML=CATALOG.map(cat=>{const items=cat.items.filter(n=>!q||n.toLowerCase().includes(q));if(!items.length)return '';
    return '<div class="set-sec"><h3>'+esc(cat.c)+'</h3><div class="grid" style="grid-template-columns:repeat(auto-fill,minmax(210px,1fr))">'+items.map(n=>'<div class="card" style="display:flex;align-items:center;gap:10px;padding:12px"><span style="font-size:19px">🧩</span><div class="ag-item-main"><strong>'+esc(n)+'</strong><div class="hint">'+esc(cat.c)+'</div></div><button class="btn btn-sm" onclick="intAdd(\''+jsesc(n)+'\')">Add</button></div>').join('')+'</div></div>';}).join('')||'<p class="hint">No matches.</p>';
}
function intAdd(name){connAdd();setTimeout(()=>{const l=$('#ac-label');if(l)l.value=name;const id=$('#ac-id');if(id)id.value=name.toLowerCase().replace(/[^a-z0-9]+/g,'_').replace(/^_|_$/g,'');},70);}

// ── Router ────────────────────────────────────────────────────────────
const ROUTES={dashboard:pageDashboard,connections:pageConnections,discover:pageDiscover,slicer:pageSlicer,agents:pageAgents,builder:pageBuilder,chat:pageChat,files:pageFiles,integrations:pageIntegrations,settings:pageSettings};
function router(){const r=(location.hash.replace(/^#\//,'')||'dashboard');const fn=ROUTES[r]||pageDashboard;setActive(r in ROUTES?r:'dashboard');closeDrawer();fn();}
applyTheme(localStorage.getItem('plutus_theme')||'dark');
buildShell();
window.addEventListener('hashchange',router);
router();
