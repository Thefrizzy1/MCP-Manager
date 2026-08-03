"""A typed, single-source view of every built-in service's metadata.

The audit's finding D#1/D#2: a service is described in 6–7 places — ``config.py``
fields, ``builtin_services.SERVICES`` ``config_keys``, the two logo maps, the
open-URL map, and ``.env.example`` — and those copies drift silently (the logo
maps even had duplicate keys, now fixed). Fully *generating* ``config.py``'s ~111
pydantic fields from a registry is the highest-blast-radius change in the whole
audit (``cfg.<svc>_url`` is read across the entire codebase), so this module
takes the safe half: it assembles one typed ``ServiceDef`` view from the already
canonical sources, and the accompanying tests turn "these copies can drift
silently" into "they fail CI if they drift".

Nothing here changes runtime behaviour — it is a read model over
``builtin_services`` that callers can adopt in place of poking SERVICES, the logo
maps and OPEN_URL_BY_ID separately.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.builtin_services import (
    OPEN_URL_BY_ID,
    SERVICE_ICON_SLUG,
    SERVICE_LOGO_DOMAIN,
    SERVICES,
)


@dataclass(frozen=True)
class EnvKey:
    """One environment variable a service reads."""
    key: str            # UPPER_SNAKE env var, e.g. "JELLYFIN_URL"
    label: str          # human label shown in the Configure form
    placeholder: str    # example value
    secret: bool        # masked in the UI, never echoed


@dataclass(frozen=True)
class ServiceDef:
    """Everything one built-in service is, in one place."""
    id: str
    label: str
    section: str
    tag: str
    env_keys: tuple[EnvKey, ...]
    configured_attrs: tuple[str, ...]   # cfg attrs that, if set, mark it configured
    url_key: str | None                 # cfg attr for the open-in-browser base URL
    logo_domain: str | None
    icon_slug: str | None

    @property
    def env_key_names(self) -> tuple[str, ...]:
        return tuple(e.key for e in self.env_keys)


def service_defs() -> list[ServiceDef]:
    """The typed registry, assembled from the canonical sources."""
    out: list[ServiceDef] = []
    for s in SERVICES:
        env_keys = tuple(
            EnvKey(key=k, label=lbl, placeholder=ph, secret=bool(sec))
            for (k, lbl, ph, sec) in s.get("config_keys", [])
        )
        sid = s["id"]
        out.append(ServiceDef(
            id=sid,
            label=s.get("label", sid),
            section=s.get("section", ""),
            tag=s.get("tag", ""),
            env_keys=env_keys,
            configured_attrs=tuple(s.get("configured_keys", ())),
            url_key=s.get("open_url_key") or OPEN_URL_BY_ID.get(sid),
            logo_domain=SERVICE_LOGO_DOMAIN.get(sid),
            icon_slug=SERVICE_ICON_SLUG.get(sid),
        ))
    return out


def service_def(service_id: str) -> ServiceDef | None:
    return next((d for d in service_defs() if d.id == service_id), None)


def all_env_keys() -> list[str]:
    """Every environment variable declared by any built-in service (deduped)."""
    seen: dict[str, None] = {}
    for d in service_defs():
        for k in d.env_key_names:
            seen.setdefault(k, None)
    return list(seen)
