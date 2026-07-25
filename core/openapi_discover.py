"""Introspect a service's OpenAPI / Swagger spec and normalize its operations.

Powers the API Discovery wizard: point it at a FastAPI/OpenAPI base URL and it
finds the machine-readable spec (FastAPI serves `/openapi.json`), lists the
endpoints with their parameters, and hands back a summary the UI can render and
save as a connection. `parse_spec` is pure/offline so it is unit-testable; only
`introspect` touches the network.
"""
from __future__ import annotations

import httpx

# Common locations a spec is served from, tried in order after the base URL.
SPEC_PATHS = [
    "/openapi.json", "/swagger.json", "/v3/api-docs", "/api-docs",
    "/api/openapi.json", "/docs/openapi.json", "/swagger/v1/swagger.json",
]
_METHODS = {"get", "post", "put", "patch", "delete"}


def parse_spec(spec: dict) -> dict:
    """Normalize an OpenAPI/Swagger document into a flat operation list."""
    info = spec.get("info") or {}
    servers = spec.get("servers") or []
    ops: list[dict] = []
    for path, item in (spec.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method, op in item.items():
            if method.lower() not in _METHODS:
                continue
            if not isinstance(op, dict):
                op = {}
            params = [
                p.get("name")
                for p in (op.get("parameters") or [])
                if isinstance(p, dict) and p.get("name")
            ]
            ops.append({
                "method": method.upper(),
                "path": path,
                "summary": (op.get("summary") or op.get("description") or "").strip()[:160],
                "operation_id": op.get("operationId") or "",
                "params": params,
                "has_body": bool(op.get("requestBody")),
                "tags": op.get("tags") or [],
            })
    ops.sort(key=lambda o: (o["path"], o["method"]))
    server = ""
    if servers and isinstance(servers[0], dict):
        server = str(servers[0].get("url") or "")
    return {
        "title": info.get("title") or "API",
        "version": str(info.get("version") or ""),
        "description": (info.get("description") or "").strip()[:400],
        "server": server,
        "operation_count": len(ops),
        "operations": ops,
    }


def _candidates(base: str) -> list[str]:
    if base.endswith((".json", ".yaml", ".yml")):
        return [base]
    return [base + p for p in SPEC_PATHS]


async def introspect(base_url: str, *, timeout: float = 8.0) -> dict:
    """Fetch and parse a service's OpenAPI spec. Admin-initiated homelab
    discovery, so LAN targets and self-signed TLS are allowed on purpose."""
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return {"ok": False, "error": "No URL given"}
    if not base.startswith(("http://", "https://")):
        base = "http://" + base
    tried: list[str] = []
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, verify=False) as cli:
        for cand in _candidates(base):
            tried.append(cand)
            try:
                r = await cli.get(cand)
            except Exception:
                continue
            if r.status_code != 200:
                continue
            try:
                spec = r.json()
            except Exception:
                continue
            if isinstance(spec, dict) and (spec.get("openapi") or spec.get("swagger") or spec.get("paths")):
                out = parse_spec(spec)
                out.update({"ok": True, "spec_url": cand, "base": base})
                return out
    return {"ok": False, "error": "No OpenAPI/Swagger spec found at that host.",
            "base": base, "tried": tried}
