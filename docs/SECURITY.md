# Plutus — Security Model & Audit

Plutus brokers powerful homelab actions (Docker, SSH, the filesystem, service APIs),
so its security posture matters even on a private network. This document describes the
intended threat model, the implemented controls, the findings from a structured audit,
and the hardening required before any exposure beyond LAN/Tailscale.

---

## 1. Intended deployment & threat model

**Intended:** LAN or Tailscale only. The Web UI is behind HTTP Basic auth; the MCP
endpoint is optionally behind a Bearer token. The network is treated as a *trusted
boundary but not the only one* — controls are layered so a single mistake (a forwarded
port, a phished admin browser) is not immediately catastrophic.

**Primary assets:** the Docker socket, configured SSH hosts, mounted filesystems,
and the credentials in `.env` (service API keys, `UI_PASSWORD`, `MCP_BEARER_TOKEN`).

**Primary adversaries considered:**
- A device on the LAN that should not reach the dashboard.
- A malicious web page visited by the authenticated admin (CSRF).
- A crafted instruction to the model causing it to misuse a tool (SSRF, injection).
- Accidental secret disclosure into model transcripts/logs.

**Explicitly out of scope** for the default deployment: a fully public, untrusted
internet surface (see [§5](#5-exposure-posture)).

---

## 2. Implemented controls

| Area | Control | Where |
|---|---|---|
| UI authentication | HTTP Basic, constant-time compare (`secrets.compare_digest`); 503 if no password set | `main.py` `verify_auth` |
| Brute force | Per-client sliding-window login lockout | `core/rate_limit.py` |
| CSRF | Origin-check middleware rejects cross-site state-changing requests; non-browser clients (no Origin) unaffected; `PLUTUS_DISABLE_CSRF` escape hatch | `main.py` `_csrf_origin_guard` |
| MCP authentication | Optional Bearer gate, constant-time compare, **read live from `.env`** so UI toggles apply without restart | `core/mcp_bearer_middleware.py` |
| Command injection | `ssh_exec` host/arg charset-validated; `--` before host; `ssh_run` arg allowlisting | `tools/infrastructure.py`, `tools/ssh_smb.py` |
| Path traversal | Boundary-aware confinement (realpath + `== root or startswith(root + sep)`) | `core/path_guard.py` |
| SSRF | `web_fetch` screens scheme + resolved IP against private/loopback/link-local | `core/ssrf_guard.py` |
| Secret disclosure (files) | `fs_read_file` masks secret-looking values, auth headers, PEM bodies by default | `core/redact.py` |
| Secret disclosure (errors) | Upstream bodies / exception text suppressed unless `PLUTUS_VERBOSE_ERRORS=1` | `client.py` `_handle_error` |
| Secret disclosure (output) | `smb_add_share` never echoes the stored password | `tools/ssh_smb.py` |
| Env tampering | UI-writable env keys allowlisted; `PATH`/`LD_PRELOAD`/etc. blocked; newlines rejected; atomic writes | `config.py`, `core/env_store.py` |
| Destructive actions | Docker writes gated by `DOCKER_WRITE_ENABLED` (default off); SSH hosts read-only by default | `tools/system.py`, `tools/ssh_smb.py` |
| Input validation | All tool inputs are pydantic models with `extra="forbid"` | `tools/*` |
| Agent blast-radius | Two capability switches (**write**, **publish** — publish off by default) plus a per-connection ACL cap the agent's tools; destructive/outward tools blocked via Claude Code `--disallowedTools` | `core/agent_permissions.py`, `core/agent_orchestrator.py` |
| Agent auth | Web login stores a session/OAuth token (`CLAUDE_CODE_OAUTH_TOKEN`), never an API key | `core/agent_login.py` |
| Secrets at rest | `.env`, `data/` excluded from image & VCS | `.dockerignore`, `.gitignore` |

No `eval`/`exec`/`pickle`/`yaml.load`/`shell=True` is used anywhere in the codebase.

---

## 3. Audit findings & resolutions

A structured review was performed from three standpoints (application security,
software architecture, and SRE). Findings and their current status:

| ID | Finding | Severity | Status |
|---|---|---|---|
| P-01 | `ssh_exec` interpolated `arg`/`host` into a remote shell command → command/argument injection | High | **Fixed** — `_valid_ssh_host`/`_valid_ssh_arg` charset validation + `--` before host (`tools/infrastructure.py`); regression-tested (`tests/test_ssh_exec_validation.py`) |
| P-02 | MCP bearer token/flag read from a stale per-process `cfg`, so enabling/rotating it in the UI had no effect until restart | High | **Fixed** — gate reads `.env` live with a short TTL cache (`core/mcp_bearer_middleware.py`); tested (`tests/test_bearer_live.py`) |
| P-03 | No CSRF protection on browser-reachable state-changing POSTs | Medium | **Fixed** — Origin-check middleware (`main.py`) |
| P-04 | `web_fetch` had no SSRF guard (could read metadata/loopback/internal services) | Medium | **Fixed** — `core/ssrf_guard.py`, applied in `web_fetch`; tested (`tests/test_ssrf_guard.py`) |
| P-05 | SSH/SMB passwords stored plaintext in `.env`; SSH password passed via `sshpass -p` (visible in process table) | Medium | **Documented / mitigated** — key auth preferred and is the default path; `.env` is `chmod 600` per [OPERATIONS.md](OPERATIONS.md). Use key-based auth where possible. |
| P-06 | Docker socket + write-enabled SSH = root-equivalent blast radius | Medium (by design) | **Documented** — defaults are read-only; socket `:ro` is not a security boundary against the Docker API; consider a socket proxy if exposing. |
| P-07 | `verify=False` on OMV/Obsidian LAN HTTPS calls | Low | **Accepted** for LAN self-signed certs; CA-pinning via uploaded `data/ca.pem` is a future option. |
| P-08 | `/settings/upload-cert` writes bytes to `data/ca.pem` without PEM validation | Low | **Accepted** — fixed path (no traversal), size-capped, not yet trusted by outbound clients. |
| P-09 | `MCP_ALLOWED_ORIGINS` loaded but unenforced | Low | **Documented** — no false sense of protection; remove or wire if needed. |
| P-10 | Theoretical TOCTOU between `_check_path` and `makedirs` in `fs_write_file` | Low | **Accepted** for the single-admin threat model. |
| P-11 | Default `admin`/`adminadmin` and empty-password dev bypass | Low (High if exposed) | **Mitigated** — startup warns; UI returns 503 if no password is set; set a strong `UI_PASSWORD`. |

The error-handling sweep (raw `return f"Error: {e}"` → `_handle_error`) and the
consolidation of the two `.env` writers into `core/env_store.py` also closed
information-leak and data-race inconsistencies surfaced during the review.

---

## 3b. Agent prompt-injection & permission model

The headless agent runs with `--dangerously-skip-permissions` and, when researching,
reads untrusted web pages. Treat any such page as capable of trying to steer the agent
into destructive tool calls. Mitigations, layered:

- **Capability switches** (`core/agent_permissions.py`) cap what the agent may do: **write**
  off is a true audit posture (every non-read-only tool blocked); **publish** off — the
  default — blocks outward tools (`send_email`, `ntfy_send`, `n8n_trigger_webhook`, public
  GitHub issues/PRs, share links) even when write is on. Curated `DANGEROUS`/`WRITE` sets are
  a safety floor for tools whose annotations under-classify them. Which *services* an agent
  may touch is the separate per-connection ACL (`core/agent_orchestrator.service_disallow`).
- **Server-side gates** remain the backstop even at `all`: `DOCKER_WRITE_ENABLED=false`
  and SSH hosts `readonly: true` mean the most dangerous actions are refused by the tools
  themselves regardless of what the agent is talked into.
- **Cost/usage guards**: `max_cost_usd`, `timeout_min`, `max_runs_per_day`, single-run
  serialization, and a Stop button.
- Keep research playbooks at `safe`; only raise a playbook to `all` when you trust its task
  and have a reason.

The agent's session token and the MCP bearer token live under `data/` (git/image-excluded);
`data/agent_mcp.json` is written `0600`.

## 4. Hardening checklist

- [ ] Set a strong `UI_PASSWORD` (never run on `adminadmin`).
- [ ] `chmod 600 .env` on the host.
- [ ] Prefer SSH **key** auth; keep SSH hosts `readonly: true` unless a host genuinely needs writes.
- [ ] Keep `DOCKER_WRITE_ENABLED=false` unless you need container control.
- [ ] If you need MCP auth, set `MCP_REQUIRE_BEARER=true` and generate a token (now applies live).
- [ ] Keep ports 8765/8766 off the public internet — Tailscale/LAN only.
- [ ] Back up `data/` (contains the health baseline and uploaded CA).

---

## 5. Exposure posture

The control set is calibrated for a trusted network. **Removing that boundary changes
the risk sharply** and requires, at minimum, all of the following *before* exposure:

1. Terminate TLS at a reverse proxy (Tailscale Serve / Caddy) — HTTP Basic in the clear is unacceptable publicly.
2. Strong `UI_PASSWORD`; never the default; consider refusing to bind `0.0.0.0` with default creds.
3. `MCP_REQUIRE_BEARER=true` with a generated token (8765 is otherwise unauthenticated).
4. Keep the Origin/CSRF guard on and the SSRF guard on; keep Docker writes off and SSH read-only.
5. Consider a Docker socket proxy restricting to read endpoints.

Tailscale-only remains by far the safer posture. Treat public exposure as a distinct
project requiring the above plus a fresh review.

---

## 6. Reporting

This is a single-maintainer homelab project. Security-relevant issues should be tracked
privately in the maintainer's issue list rather than disclosed publicly, given the
deployment is personal infrastructure.
