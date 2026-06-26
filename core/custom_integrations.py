"""User-defined dashboard segments (URLs + notes). Stored in data/custom_integrations.json."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


def integrations_path(root: Path) -> Path:
    return root / "data" / "custom_integrations.json"


def load_raw(root: Path) -> dict[str, Any]:
    p = integrations_path(root)
    if not p.is_file():
        return {"version": 1, "integrations": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"version": 1, "integrations": []}
    if not isinstance(data, dict):
        return {"version": 1, "integrations": []}
    data.setdefault("version", 1)
    data.setdefault("integrations", [])
    if not isinstance(data["integrations"], list):
        data["integrations"] = []
    return data


def validate_and_normalize(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data.get("integrations"), list):
        raise ValueError("'integrations' must be a list")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, raw in enumerate(data["integrations"]):
        if not isinstance(raw, dict):
            raise ValueError(f"integrations[{i}] must be an object")
        sid = str(raw.get("id") or "").strip().lower().replace("-", "_")
        if not _ID_RE.match(sid):
            raise ValueError(
                f"integrations[{i}].id must match /^[a-z][a-z0-9_]{{0,62}}$/ (got {raw.get('id')!r})"
            )
        if not sid.startswith("cust_"):
            sid = "cust_" + sid
        if sid in seen:
            raise ValueError(f"duplicate id (after prefix): {sid}")
        seen.add(sid)
        label = str(raw.get("label") or "").strip()
        if not label:
            raise ValueError(f"integrations[{i}].label is required")
        url_env = str(raw.get("url_env") or "").strip()
        if not url_env or not url_env.isupper():
            raise ValueError(f"integrations[{i}].url_env must be a UPPER_SNAKE env key")
        hp = str(raw.get("health_path") if raw.get("health_path") is not None else "/").strip() or "/"
        extras_in = raw.get("extra_env") or []
        if not isinstance(extras_in, list):
            raise ValueError(f"integrations[{i}].extra_env must be a list")
        extras: list[dict[str, Any]] = []
        for j, ex in enumerate(extras_in):
            if not isinstance(ex, dict):
                raise ValueError(f"integrations[{i}].extra_env[{j}] must be an object")
            ek = str(ex.get("key") or "").strip()
            if not ek or not ek.isupper():
                raise ValueError(f"integrations[{i}].extra_env[{j}].key must be UPPER_SNAKE")
            extras.append(
                {
                    "key": ek,
                    "label": str(ex.get("label") or ek),
                    "placeholder": str(ex.get("placeholder") or ""),
                    "secret": bool(ex.get("secret")),
                }
            )
        logo_dom = str(raw.get("logo_domain") or "").strip().lower()
        row = {
            "id": sid,
            "label": label,
            "icon": str(raw.get("icon") or "🔗").strip() or "🔗",
            "description": str(raw.get("description") or "").strip(),
            "documentation_url": str(raw.get("documentation_url") or "").strip(),
            "api_notes": str(raw.get("api_notes") or "").strip(),
            "logo_domain": logo_dom,
            "url_env": url_env,
            "url_placeholder": str(raw.get("url_placeholder") or "https://192.168.1.10:8080").strip(),
            "health_path": hp,
            "extra_env": extras,
        }
        out.append(row)
    return {"version": 1, "integrations": out}


def save_raw(root: Path, data: dict[str, Any]) -> None:
    normalized = validate_and_normalize(data)
    p = integrations_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(normalized, indent=2) + "\n", encoding="utf-8")


def _health_url_factory(url_env: str, health_path: str):
    def health_url() -> str:
        base = os.getenv(url_env, "").strip().rstrip("/")
        if not base.startswith("http"):
            return ""
        p = health_path if health_path.startswith("/") else "/" + health_path
        return base + p

    return health_url


def custom_integrations_as_services(root: Path) -> list[dict[str, Any]]:
    try:
        data = validate_and_normalize(load_raw(root))
    except ValueError:
        return []
    rows: list[dict[str, Any]] = []
    for item in data["integrations"]:
        url_env = item["url_env"]
        ph = item.get("url_placeholder") or "https://192.168.1.10:8080"
        config_keys: list[tuple[str, str, str, bool]] = [
            (url_env, "Base URL", ph, False),
        ]
        for ex in item["extra_env"]:
            config_keys.append(
                (ex["key"], ex["label"], ex["placeholder"], ex["secret"]),
            )
        required = (url_env,)

        rows.append(
            {
                "id": item["id"],
                "label": item["label"],
                "icon": item["icon"],
                "tag": "custom",
                "section": "custom",
                "desc": item["description"] or "Custom integration",
                "config_keys": config_keys,
                "config_from_env": True,
                "configured_env_keys": required,
                "configured_keys": (),
                "open_from_env": url_env,
                "health_url": _health_url_factory(url_env, item["health_path"]),
                "health_headers": lambda: {},
                "tools": [],
                "documentation_url": item.get("documentation_url") or "",
                "api_notes": item.get("api_notes") or "",
                "logo_domain": (item.get("logo_domain") or "").strip().lower() or None,
            }
        )
    return rows
