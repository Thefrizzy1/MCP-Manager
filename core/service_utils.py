"""Shared helpers for built-in + custom dashboard services."""

from __future__ import annotations

import os

from config import Config


def is_service_configured(svc: dict, cfg: Config) -> bool:
    if svc.get("config_from_env"):
        req = svc.get("configured_env_keys") or ()
        return bool(req) and all(os.getenv(k, "").strip() for k in req)
    keys = svc.get("configured_keys", ())
    return cfg.is_configured(*keys) if keys else True


def _ck_parts(tup) -> tuple[str, str, str, bool]:
    """Normalize a config_keys entry (ENV, label, placeholder, is_secret)."""
    t = list(tup) + ["", "", "", False]
    return str(t[0]), str(t[1] or t[0]), str(t[2] or ""), bool(t[3])


def service_url(svc: dict, env: dict) -> str:
    """Resolve a service's web address from the current .env values (`env` is the
    dict returned by env_store.read_env). Empty string if it has no URL."""
    ofe = svc.get("open_from_env")
    if ofe:
        return (env.get(ofe) or "").strip()
    for tup in svc.get("config_keys", []) or []:
        key, _label, _ph, secret = _ck_parts(tup)
        if not secret and key.upper().endswith("_URL"):
            return (env.get(key) or "").strip()
    okey = svc.get("open_url_key")
    if okey:
        return (env.get(okey.upper()) or "").strip()
    return ""


def service_config_fields(svc: dict, env: dict) -> list[dict]:
    """Editable env fields for the inline Configure form. Secret values are never
    returned — only a `set` flag says whether one is currently present."""
    fields = []
    for tup in svc.get("config_keys", []) or []:
        key, label, ph, secret = _ck_parts(tup)
        cur = (env.get(key) or "")
        fields.append({
            "key": key,
            "label": label,
            "placeholder": ph,
            "secret": secret,
            "value": "" if secret else cur,
            "set": bool(cur),
        })
    return fields
