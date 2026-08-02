"""The server-rendered login page (served at /login, public).

A standalone page rather than a React route: the front door must render with zero
JS bundle and work even if the SPA build is missing. It matches the app's design
tokens (Inter, the same surfaces/borders) so it feels native, but the login is the
one surface allowed expressive colour — a split screen with the form on the left
and slow, reduced-motion-safe gradient half-circles on the right.

The page POSTs to /api/v1/auth/login and, on success, navigates to /app.
"""
from __future__ import annotations

import html


def render_login_page(*, default_active: bool = False, error: str = "") -> str:
    hint = ""
    if default_active:
        hint = (
            '<p class="hint">First run — sign in with <code>admin</code> / '
            '<code>adminadmin</code>, then change it in Settings.</p>'
        )
    err = f'<p class="error" role="alert">{html.escape(error)}</p>' if error else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sign in · Plutus</title>
<script>
  // Match the app's theme before first paint (no flash).
  try {{
    var t = localStorage.getItem('plutus_theme') ||
      (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark');
    document.documentElement.dataset.theme = t;
  }} catch (e) {{ document.documentElement.dataset.theme = 'dark'; }}
</script>
<style>
  :root, :root[data-theme='light'] {{
    --bg:#f6f7f9; --surface:#fff; --border:#e6e8ec; --border-strong:#d4d7de;
    --accent:#3a5ce5; --accent-hover:#2b45c4; --accent-fg:#fff;
    --ink:#1f2430; --ink-2:#5b6472; --ink-3:#8a93a3; --danger:#d6453c;
    --arc-1:#3a5ce5; --arc-2:#7c3aed; --arc-3:#06b6d4; --panel-ink:#f6f7f9;
  }}
  :root[data-theme='dark'] {{
    --bg:#0c0d10; --surface:#16181d; --border:#262a31; --border-strong:#343943;
    --accent:#6e8bff; --accent-hover:#829bff; --accent-fg:#0c0d10;
    --ink:#e6e8ec; --ink-2:#a0a6b0; --ink-3:#6b7280; --danger:#f85149;
    --arc-1:#6e8bff; --arc-2:#a855f7; --arc-3:#22d3ee; --panel-ink:#f6f7f9;
  }}
  * {{ box-sizing:border-box; }}
  html, body {{ height:100%; margin:0; }}
  body {{
    font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,system-ui,sans-serif;
    font-size:13px; color:var(--ink); background:var(--bg);
    -webkit-font-smoothing:antialiased;
    display:grid; grid-template-columns:1fr 1fr; min-height:100%;
  }}
  /* ── left: form ── */
  .pane {{ display:grid; place-items:center; padding:2rem; }}
  .card {{ width:100%; max-width:340px; }}
  .brand {{ display:flex; align-items:center; gap:.6rem; margin-bottom:2rem; }}
  .brand .dot {{
    width:30px; height:30px; border-radius:8px;
    background:linear-gradient(135deg,var(--arc-1),var(--arc-2));
  }}
  .brand b {{ font-size:15px; font-weight:600; letter-spacing:-.01em; }}
  h1 {{ font-size:22px; font-weight:600; letter-spacing:-.02em; margin:0 0 .35rem; }}
  .sub {{ color:var(--ink-2); margin:0 0 1.6rem; font-size:13px; }}
  label {{ display:block; font-size:12.5px; font-weight:500; margin:0 0 .35rem; color:var(--ink-2); }}
  .field {{ margin-bottom:1rem; }}
  input[type=text], input[type=password] {{
    width:100%; height:38px; padding:0 .7rem; font-size:13px; color:var(--ink);
    background:var(--surface); border:1px solid var(--border-strong);
    border-radius:6px; outline:none; transition:border-color .15s, box-shadow .15s;
  }}
  input:focus {{ border-color:var(--accent); box-shadow:0 0 0 3px color-mix(in srgb,var(--accent) 22%,transparent); }}
  .row {{ display:flex; align-items:center; justify-content:space-between; margin:.2rem 0 1.4rem; }}
  .remember {{ display:flex; align-items:center; gap:.45rem; color:var(--ink-2); font-size:12.5px; cursor:pointer; user-select:none; }}
  .remember input {{ width:15px; height:15px; accent-color:var(--accent); }}
  button {{
    width:100%; height:40px; font-size:13px; font-weight:600; cursor:pointer;
    color:var(--accent-fg); background:var(--accent); border:0; border-radius:6px;
    transition:background .15s, transform .05s;
  }}
  button:hover {{ background:var(--accent-hover); }}
  button:active {{ transform:translateY(1px); }}
  button[disabled] {{ opacity:.6; cursor:default; }}
  .hint {{ margin:1.4rem 0 0; padding:.7rem .8rem; font-size:12px; color:var(--ink-2);
    background:color-mix(in srgb,var(--accent) 8%,transparent); border:1px solid var(--border); border-radius:6px; }}
  .hint code {{ font-family:'JetBrains Mono',ui-monospace,Menlo,monospace; font-size:11.5px; color:var(--ink); }}
  .error {{ margin:0 0 1rem; padding:.6rem .75rem; font-size:12.5px; color:var(--danger);
    background:color-mix(in srgb,var(--danger) 12%,transparent); border:1px solid color-mix(in srgb,var(--danger) 35%,transparent); border-radius:6px; }}
  /* ── right: gradient arcs ── */
  .art {{ position:relative; overflow:hidden; background:#0b0d18; }}
  .art .arc {{ position:absolute; border-radius:50%; filter:blur(.5px); opacity:.9; }}
  .art .a1 {{ width:70vh; height:70vh; right:-18vh; top:-12vh;
    background:radial-gradient(circle at 30% 30%,var(--arc-1),transparent 62%); animation:drift1 22s ease-in-out infinite alternate; }}
  .art .a2 {{ width:56vh; height:56vh; right:8vh; bottom:-18vh;
    background:radial-gradient(circle at 50% 50%,var(--arc-2),transparent 60%); animation:drift2 26s ease-in-out infinite alternate; }}
  .art .a3 {{ width:40vh; height:40vh; left:-8vh; top:30%;
    background:radial-gradient(circle at 50% 50%,var(--arc-3),transparent 58%); animation:drift3 19s ease-in-out infinite alternate; }}
  .art .halo {{ position:absolute; inset:0; background:radial-gradient(circle at 70% 40%,transparent 40%,rgba(0,0,0,.35)); }}
  .art .tag {{ position:absolute; left:2.4rem; bottom:2.2rem; color:var(--panel-ink); z-index:2; max-width:22rem; }}
  .art .tag h2 {{ font-size:19px; font-weight:600; margin:0 0 .4rem; letter-spacing:-.01em; }}
  .art .tag p {{ font-size:13px; margin:0; opacity:.75; line-height:1.5; }}
  @keyframes drift1 {{ from{{transform:translate(0,0) scale(1)}} to{{transform:translate(-4vh,5vh) scale(1.08)}} }}
  @keyframes drift2 {{ from{{transform:translate(0,0) scale(1)}} to{{transform:translate(5vh,-4vh) scale(1.12)}} }}
  @keyframes drift3 {{ from{{transform:translate(0,0) scale(1)}} to{{transform:translate(3vh,3vh) scale(1.06)}} }}
  @media (prefers-reduced-motion: reduce) {{ .art .arc {{ animation:none !important; }} }}
  @media (max-width: 820px) {{ body {{ grid-template-columns:1fr; }} .art {{ display:none; }} }}
</style>
</head>
<body>
  <div class="pane">
    <form class="card" id="login-form" autocomplete="on">
      <div class="brand"><span class="dot"></span><b>Plutus</b></div>
      <h1>Welcome back</h1>
      <p class="sub">Sign in to your homelab control plane.</p>
      {err}
      <div class="field">
        <label for="username">Username</label>
        <input id="username" name="username" type="text" autocomplete="username" autofocus required>
      </div>
      <div class="field">
        <label for="password">Password</label>
        <input id="password" name="password" type="password" autocomplete="current-password" required>
      </div>
      <div class="row">
        <label class="remember"><input type="checkbox" id="remember" name="remember"> Remember me</label>
      </div>
      <button type="submit" id="submit">Sign in</button>
      {hint}
    </form>
  </div>
  <div class="art" aria-hidden="true">
    <span class="arc a1"></span><span class="arc a2"></span><span class="arc a3"></span>
    <span class="halo"></span>
    <div class="tag"><h2>One place for every service.</h2><p>Media, photos, home automation, infra and a catalogue of tools — behind one MCP endpoint.</p></div>
  </div>
<script>
  const form = document.getElementById('login-form');
  const btn = document.getElementById('submit');
  form.addEventListener('submit', async (e) => {{
    e.preventDefault();
    btn.disabled = true; btn.textContent = 'Signing in…';
    document.querySelectorAll('.error').forEach(el => el.remove());
    try {{
      const res = await fetch('/api/v1/auth/login', {{
        method:'POST', headers:{{'Content-Type':'application/json'}},
        body: JSON.stringify({{
          username: form.username.value,
          password: form.password.value,
          remember: form.remember.checked,
        }}),
      }});
      const data = await res.json().catch(() => ({{}}));
      if (res.ok) {{ window.location.assign('/app'); return; }}
      showError(data.detail || 'Sign in failed. Check your username and password.');
    }} catch (err) {{
      showError('Could not reach the server. Is Plutus running?');
    }}
    btn.disabled = false; btn.textContent = 'Sign in';
  }});
  function showError(msg) {{
    const p = document.createElement('p');
    p.className = 'error'; p.setAttribute('role','alert'); p.textContent = msg;
    form.querySelector('.sub').after(p);
  }}
</script>
</body>
</html>"""
