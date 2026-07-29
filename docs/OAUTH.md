# Connecting Plutus to browser MCP connectors (claude.ai) via OAuth

Config-file clients (Claude Desktop, Claude Code, Cursor) authenticate to Plutus
with a **static bearer token** — see *Settings → Connect a client*. But
**browser connectors** (claude.ai "custom connectors", and the Claude Desktop
*Connectors* UI) don't do bearer tokens: they require **OAuth**. Plutus ships a
minimal OAuth 2.1 provider for exactly this. It's **opt-in** — off by default,
and turning it off restores the plain bearer behaviour with zero change.

## What it implements

A single-user authorization server that is also the resource server:

- `GET /.well-known/oauth-protected-resource` — RFC 9728
- `GET /.well-known/oauth-authorization-server` — RFC 8414
- `POST /register` — RFC 7591 dynamic client registration (public clients)
- `GET/POST /authorize` — you log in with your **Plutus UI credentials** and approve
- `POST /token` — OAuth 2.1 authorization-code grant with **PKCE (S256)** + refresh tokens

Access tokens are opaque, stored server-side (revocable), and accepted by the
MCP bearer gate alongside the static token. The user login on `/authorize` reuses
`UI_USERNAME` / `UI_PASSWORD`.

## Prerequisites

1. **Public HTTPS.** claude.ai's cloud must reach your server, so the MCP
   endpoint has to be exposed over HTTPS — Tailscale Funnel or a reverse proxy
   (Caddy/nginx) in front of the MCP port. A tailnet-only or LAN URL will not
   work for the browser connector (it works fine for Claude Desktop's config
   file, which connects from your machine).
2. The reverse proxy must forward `X-Forwarded-Proto` and `X-Forwarded-Host`
   (Caddy does this by default) so the issued OAuth URLs use your real public
   origin.

## Enable it

1. **Settings → MCP endpoint**:
   - Turn **Require bearer** on (and generate a token — it stays valid for
     config-file clients).
   - Turn **Browser OAuth** on and Save.
2. **Restart the MCP server** (the routes are wired at startup):
   ```bash
   docker compose restart   # or restart the Plutus MCP process
   ```
3. Confirm discovery works (replace with your public base):
   ```bash
   curl https://mcp.your-domain.tld/.well-known/oauth-authorization-server
   ```
   You should get JSON with `authorization_endpoint`, `token_endpoint`, and
   `registration_endpoint`.

## Add it in claude.ai

1. claude.ai → **Settings → Connectors → Add custom connector**.
2. Paste your public MCP URL: `https://mcp.your-domain.tld/mcp`.
3. Claude registers a client, then opens Plutus's **Allow access** page — sign in
   with your Plutus username/password and approve.
4. Done: the connector is now available in any chat, web and desktop.

## Security notes

- The `/authorize` login is public (it must be, for the browser flow). It uses a
  constant-time credential check against your Plutus password — **use a strong
  `UI_PASSWORD`** since this endpoint is internet-reachable.
- Codes are single-use and expire in 5 minutes; PKCE is required (no code can be
  exchanged without the verifier); redirect URIs must exactly match the ones the
  client registered.
- To revoke everything, delete `data/oauth/tokens.json` (and `clients.json` to
  force re-registration) and restart.

## Turning it off

Untick **Browser OAuth**, Save, restart. The discovery endpoints stop being
served and the gate goes back to static-bearer only.
