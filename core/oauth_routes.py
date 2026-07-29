"""HTTP wiring for the OAuth provider (core/oauth_provider.py), served on the MCP
origin alongside /mcp so an MCP client (e.g. claude.ai) can discover and use it.

Routes (all public — exempted from the bearer gate):
  GET  /.well-known/oauth-protected-resource   RFC 9728
  GET  /.well-known/oauth-authorization-server RFC 8414
  POST /register                               RFC 7591 dynamic client registration
  GET  /authorize                              login + consent page
  POST /authorize                              verify Plutus login -> issue code -> redirect
  POST /token                                  authorization_code + refresh_token grants

Form bodies are urlencoded and parsed by hand (no python-multipart dependency).
User authentication reuses the Plutus UI credentials, read live from .env.
"""
from __future__ import annotations

import html
from urllib.parse import parse_qs

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from config import DEFAULT_UI_PASSWORD
from core import oauth_provider as op
from core.env_store import ENV_PATH, read_env

ROOT = ENV_PATH.parent

OAUTH_PATHS = ("/.well-known/oauth-protected-resource", "/.well-known/oauth-authorization-server",
               "/register", "/authorize", "/token")


def is_oauth_path(path: str) -> bool:
    p = (path or "").rstrip("/") or "/"
    return p in OAUTH_PATHS or p.startswith("/.well-known/oauth")


def external_base(request: Request) -> str:
    """Public origin the client reached us on — honours the reverse proxy
    (Tailscale Funnel / Caddy) via X-Forwarded-* , else PUBLIC_MCP_BASE."""
    proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip() or request.url.scheme
    host = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip() \
        or (request.headers.get("host") or "").strip() or request.url.netloc
    if host:
        return f"{proto}://{host}"
    return (read_env().get("PUBLIC_MCP_BASE") or "").rstrip("/")


async def _params(request: Request) -> dict:
    if request.method == "GET":
        return dict(request.query_params)
    raw = (await request.body()).decode("utf-8", "replace")
    return {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}


def _ui_credentials() -> tuple[str, str]:
    env = read_env()
    return (env.get("UI_USERNAME") or "admin").strip(), (env.get("UI_PASSWORD") or DEFAULT_UI_PASSWORD)


# ── discovery ─────────────────────────────────────────────────────────────────

async def protected_resource(request: Request) -> Response:
    base = external_base(request)
    return JSONResponse(op.protected_resource_metadata(base, f"{base}/mcp"))


async def authorization_server(request: Request) -> Response:
    return JSONResponse(op.authorization_server_metadata(external_base(request)))


# ── dynamic client registration ───────────────────────────────────────────────

async def register(request: Request) -> Response:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_client_metadata", "error_description": "body must be JSON"}, 400)
    try:
        rec = op.register_client(ROOT, body if isinstance(body, dict) else {})
    except ValueError as e:
        return JSONResponse({"error": "invalid_redirect_uri", "error_description": str(e)}, 400)
    return JSONResponse(rec, status_code=201)


# ── authorization endpoint ─────────────────────────────────────────────────────

def _login_page(p: dict, *, error: str = "") -> HTMLResponse:
    client_name = html.escape(p.get("_client_name", "An MCP client"))
    hidden = "".join(
        f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">'
        for k, v in p.items() if not k.startswith("_") and v
    )
    err = f'<p class="err">{html.escape(error)}</p>' if error else ""
    page = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Authorize — Plutus</title><style>
:root{{color-scheme:light dark}}
body{{font:15px/1.5 system-ui,sans-serif;margin:0;display:grid;place-items:center;min-height:100vh;background:#0b0d10;color:#e7e9ee}}
.card{{width:min(92vw,380px);background:#15181d;border:1px solid #262b33;border-radius:14px;padding:26px}}
h1{{font-size:17px;margin:0 0 4px}} p.sub{{margin:0 0 18px;color:#9aa3af;font-size:13px}}
label{{display:block;font-size:12px;color:#9aa3af;margin:12px 0 5px}}
input[type=text],input[type=password]{{width:100%;box-sizing:border-box;padding:9px 11px;border:1px solid #2b313a;border-radius:8px;background:#0e1116;color:#e7e9ee;font-size:14px}}
button{{margin-top:18px;width:100%;padding:10px;border:0;border-radius:8px;background:#4f7cff;color:#fff;font-size:14px;font-weight:600;cursor:pointer}}
.err{{color:#ff6b6b;font-size:13px;margin:10px 0 0}} .who{{color:#e7e9ee;font-weight:600}}
</style></head><body><form class="card" method="post" action="/authorize">
<h1>Connect to Plutus</h1>
<p class="sub"><span class="who">{client_name}</span> wants to access your MCP tools. Sign in with your Plutus credentials to allow it.</p>
{hidden}
<label>Username</label><input type="text" name="username" autocomplete="username" autofocus>
<label>Password</label><input type="password" name="password" autocomplete="current-password">
{err}
<button type="submit">Allow access</button></form></body></html>"""
    return HTMLResponse(page)


async def authorize(request: Request) -> Response:
    p = await _params(request)
    client_id = (p.get("client_id") or "").strip()
    redirect_uri = (p.get("redirect_uri") or "").strip()
    client = op.get_client(ROOT, client_id)

    # Never redirect to an unregistered URI — show an error page instead.
    if not client:
        return HTMLResponse("<h3>Unknown client_id</h3>", status_code=400)
    if not op.redirect_uri_registered(client, redirect_uri):
        return HTMLResponse("<h3>redirect_uri is not registered for this client</h3>", status_code=400)
    if (p.get("response_type") or "").strip() != "code":
        return RedirectResponse(op.build_redirect(redirect_uri, {
            "error": "unsupported_response_type", "state": p.get("state")}), status_code=302)

    fields = {
        "client_id": client_id, "redirect_uri": redirect_uri,
        "code_challenge": (p.get("code_challenge") or "").strip(),
        "code_challenge_method": (p.get("code_challenge_method") or "S256").strip(),
        "state": p.get("state") or "", "scope": p.get("scope") or op.DEFAULT_SCOPE,
        "resource": p.get("resource") or "", "_client_name": client.get("client_name", "An MCP client"),
    }

    if request.method == "GET":
        return _login_page(fields)

    # POST: verify Plutus login, then mint a code and redirect back to the client.
    exp_user, exp_pass = _ui_credentials()
    if not op.check_user_credentials(p.get("username", ""), p.get("password", ""),
                                     expected_user=exp_user, expected_pass=exp_pass):
        return _login_page(fields, error="Incorrect username or password.")
    if not fields["code_challenge"]:
        return RedirectResponse(op.build_redirect(redirect_uri, {
            "error": "invalid_request", "error_description": "PKCE required",
            "state": fields["state"]}), status_code=302)

    code = op.issue_code(ROOT, client_id=client_id, redirect_uri=redirect_uri,
                         code_challenge=fields["code_challenge"],
                         code_challenge_method=fields["code_challenge_method"],
                         scope=fields["scope"], resource=fields["resource"])
    return RedirectResponse(op.build_redirect(redirect_uri, {"code": code, "state": fields["state"]}),
                            status_code=302)


# ── token endpoint ────────────────────────────────────────────────────────────

async def token(request: Request) -> Response:
    p = await _params(request)
    grant = (p.get("grant_type") or "").strip()
    try:
        if grant == "authorization_code":
            out = op.exchange_code(ROOT, code=(p.get("code") or "").strip(),
                                   code_verifier=(p.get("code_verifier") or "").strip(),
                                   client_id=(p.get("client_id") or "").strip(),
                                   redirect_uri=(p.get("redirect_uri") or "").strip())
        elif grant == "refresh_token":
            out = op.refresh_token(ROOT, refresh=(p.get("refresh_token") or "").strip(),
                                   client_id=(p.get("client_id") or "").strip())
        else:
            return JSONResponse({"error": "unsupported_grant_type"}, 400,
                                headers={"Cache-Control": "no-store"})
    except ValueError as e:
        return JSONResponse({"error": "invalid_grant", "error_description": str(e)}, 400,
                            headers={"Cache-Control": "no-store"})
    return JSONResponse(out, headers={"Cache-Control": "no-store"})


def oauth_routes() -> list[Route]:
    return [
        Route("/.well-known/oauth-protected-resource", protected_resource, methods=["GET"]),
        Route("/.well-known/oauth-authorization-server", authorization_server, methods=["GET"]),
        Route("/register", register, methods=["POST"]),
        Route("/authorize", authorize, methods=["GET", "POST"]),
        Route("/token", token, methods=["POST"]),
    ]
