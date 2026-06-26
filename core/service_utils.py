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
