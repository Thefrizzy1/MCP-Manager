"""Service probes for dashboard: HTTP checks, fail2ban subprocess, structured results."""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx

from config import Config


async def probe_http_service(svc: dict, cfg: Config) -> dict[str, Any]:
    """HTTP GET health probe.

    Result interpretation:
      ok=True  — probe returned <500, OR the service has no probe but its required
                 config (if any) is satisfied so we assume it works (public APIs,
                 local-only tools).
      ok=False — probe ran and failed (5xx, timeout, connection error).
      ok=None  — required config is missing, so probing is not meaningful.
    """
    # First: determine whether the service is "configured" (required keys filled).
    if svc.get("config_from_env"):
        req = svc.get("configured_env_keys") or ()
        if req and not all(os.getenv(k, "").strip() for k in req):
            return {
                "ok": None,
                "kind": "unconfigured",
                "summary": "Not configured — add URL / credentials below",
                "detail": "",
                "status_code": None,
            }
    else:
        keys = svc.get("configured_keys", ())
        if keys and not cfg.is_configured(*keys):
            return {
                "ok": None,
                "kind": "unconfigured",
                "summary": "Not configured — add URL / credentials below",
                "detail": "",
                "status_code": None,
            }

    # No HTTP probe but required config (if any) is satisfied → assume working.
    # This covers public APIs (weather, maps, wiki, currency) and local managers
    # (ssh, smb, filesystem, tailscale) that have nothing to probe over HTTP.
    if not svc.get("health_url"):
        return {
            "ok": True,
            "kind": "nocheck",
            "summary": "No HTTP probe — assumed working (no required config missing)",
            "detail": "",
            "status_code": None,
        }
    try:
        url = svc["health_url"]()
        if not url or not str(url).startswith("http"):
            return {
                "ok": None,
                "kind": "skip",
                "summary": "Invalid or empty URL",
                "detail": repr(url),
                "status_code": None,
            }
        headers = svc["health_headers"]()
        async with httpx.AsyncClient(timeout=httpx.Timeout(8.0), verify=False) as client:
            r = await client.get(url, headers=headers)
        sc = r.status_code
        body = (r.text or "").strip().replace("\r\n", "\n")[:1200]
        # Status interpretation:
        #   200–399  → working (ok=True)
        #   401/403  → auth missing/wrong → ok=None ("configured but unverified")
        #   404      → wrong probe path or service not exposing it → ok=False
        #   400, 5xx → genuine failure → ok=False
        if 200 <= sc < 400:
            ok: bool | None = True
            summ = f"HTTP {sc}"
        elif sc in (401, 403):
            ok = None
            summ = f"HTTP {sc} (authentication required — credentials missing or invalid)"
        elif sc == 404:
            ok = False
            summ = f"HTTP 404 (probe path not found)"
        elif sc >= 500:
            ok = False
            summ = f"HTTP {sc} (server error)"
        else:
            ok = False
            summ = f"HTTP {sc}"
        return {"ok": ok, "kind": "http", "summary": summ, "detail": body, "status_code": sc}
    except Exception as e:
        return {
            "ok": False,
            "kind": "http",
            "summary": f"Request failed: {type(e).__name__}",
            "detail": str(e),
            "status_code": None,
        }


def probe_fail2ban_sync() -> dict[str, Any]:
    import subprocess

    try:
        jails_proc = subprocess.run(
            ["fail2ban-client", "status"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if jails_proc.returncode != 0:
            jails_proc = subprocess.run(
                ["docker", "exec", "fail2ban", "fail2ban-client", "status"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        out = ((jails_proc.stdout or "") + "\n" + (jails_proc.stderr or "")).strip()
        if jails_proc.returncode != 0:
            return {
                "ok": False,
                "kind": "fail2ban",
                "summary": "fail2ban-client returned non-zero",
                "detail": out[:2000] or "(no output)",
                "status_code": None,
            }
        return {
            "ok": True,
            "kind": "fail2ban",
            "summary": "fail2ban responding",
            "detail": out[:2000],
            "status_code": None,
        }
    except FileNotFoundError:
        return {
            "ok": False,
            "kind": "fail2ban",
            "summary": "fail2ban-client / docker not found",
            "detail": "Install fail2ban or ensure Docker can exec the fail2ban container.",
            "status_code": None,
        }
    except Exception as e:
        return {
            "ok": False,
            "kind": "fail2ban",
            "summary": type(e).__name__,
            "detail": str(e),
            "status_code": None,
        }


async def probe_service_row(svc: dict, cfg: Config) -> dict[str, Any]:
    sid = svc["id"]
    label = svc["label"]
    if sid == "fail2ban":
        keys = svc.get("configured_keys", ())
        if keys and not cfg.is_configured(*keys):
            row = {
                "ok": None,
                "kind": "unconfigured",
                "summary": "Not configured — set FAIL2BAN_WEB_URL to enable probe",
                "detail": "",
                "status_code": None,
            }
        else:
            loop = asyncio.get_running_loop()
            row = await loop.run_in_executor(None, probe_fail2ban_sync)
    else:
        row = await probe_http_service(svc, cfg)
    row["id"] = sid
    row["label"] = label
    return row


def health_bool_from_row(row: dict[str, Any]) -> bool | None:
    """Collapse probe row to legacy cache value."""
    return row.get("ok")


async def gather_service_health(services: list[dict], cfg: Config) -> tuple[dict[str, bool | None], list[dict[str, Any]]]:
    rows = await asyncio.gather(*[probe_service_row(s, cfg) for s in services])
    cache = {r["id"]: r["ok"] for r in rows}
    return cache, list(rows)


def build_health_report_markdown(service_rows: list[dict[str, Any]], tool_rows: list[dict[str, Any]]) -> str:
    """Markdown aligned with services-first control plane reporting."""
    lines: list[str] = [
        "# Plutus MCP — health report",
        "",
        "Automated checks: HTTP endpoints where configured, fail2ban via client, plus zero-arg tool smoke tests.",
        "",
        "## Services",
        "",
    ]

    def _rank(r: dict[str, Any]) -> tuple[int, str]:
        o = r.get("ok")
        tier = 0 if o is True else (1 if o is False else 2)
        return (tier, r.get("label", r.get("id", "")).lower())

    for r in sorted(service_rows, key=_rank):
        o = r.get("ok")
        sym = "✅" if o is True else ("❌" if o is False else "⚪")
        lines.append(f"- **{r.get('label', r['id'])}** {sym} — {r.get('summary', '')}")
        det = (r.get("detail") or "").strip()
        if det:
            lines.append("```")
            lines.append(det[:900] + ("…" if len(det) > 900 else ""))
            lines.append("```")
        lines.append("")

    lines.append("## Tool smoke tests (zero-parameter)")
    lines.append("")
    kind_sym = {"pass": "✅", "fail": "❌", "unset": "⚪", "skip": "⏭️"}

    def _tr_rank(row: dict[str, Any]) -> tuple[int, str]:
        k = row.get("kind", "")
        tier = 0 if k == "pass" else (1 if k == "fail" else 2)
        return (tier, row.get("name", "").lower())

    for row in sorted(tool_rows, key=_tr_rank):
        k = row.get("kind", "?")
        sym = kind_sym.get(k, "•")
        name = row.get("name", "?")
        detail = row.get("detail", "")
        lines.append(f"- {sym} `{name}` — {detail}")
        sn = (row.get("snippet") or "").strip()
        if sn and k != "pass":
            lines.append("```")
            lines.append(sn[:500] + ("…" if len(sn) > 500 else ""))
            lines.append("```")
        lines.append("")

    return "\n".join(lines)
