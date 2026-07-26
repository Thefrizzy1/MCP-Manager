"""MCP profiles: named tool subsets, each served at its own ``/mcp/p/<name>``.

This replaces the old global tool gate (``core/tool_gate.py``, deleted). The gate
filtered one shared manifest at list-time by monkeypatching the SDK's tool
manager — global (every client saw the same slice) and fail-open (any new SDK
code path around the manager bypassed it). Profiles instead filter at
*registration*: a profile's endpoint is a separate FastMCP instance that only
ever had its allowed tools registered, so a tool that isn't allowed does not
exist on that endpoint. Nothing to bypass.

Contents:
- ``tool_filter`` — a thin proxy so ``register_*_tools(mcp, allow=...)`` filters
  every ``@mcp.tool(...)`` without touching the decorators.
- category/intent matching (moved verbatim from the old gate).
- the ``data/profiles.json`` store + validation + ``resolve_tool_names``.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

PROFILES_FILE = "profiles.json"
_SCHEMA_VERSION = 1

# A profile name goes in a URL path (``/mcp/p/<name>``), so keep it strict.
PROFILE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


# ── Registration-time tool filter ─────────────────────────────────────────────

class _ToolFilter:
    """Wrap a FastMCP so ``.tool(...)`` (a) completes the four annotation hints
    and (b) only registers allowed tools.

    ``allow=None`` means the full surface (no filtering) but annotations are
    still completed. Any attribute other than ``tool`` is forwarded to the real
    server unchanged, so ``.prompt``, ``.resource``, ``.add_tool`` etc. work.

    Fail-closed: if a tool's name cannot be determined and it is not explicitly
    allowed, it is *not* registered.
    """

    def __init__(self, mcp: Any, allow: set[str] | None):
        self._mcp = mcp
        self._allow = allow

    def tool(self, *args: Any, **kwargs: Any):
        from core.tool_annotations import fill_annotations
        name = kwargs.get("name")
        kwargs["annotations"] = fill_annotations(name, kwargs.get("annotations"))
        if self._allow is None:
            return self._mcp.tool(*args, **kwargs)
        inner = self._mcp.tool(*args, **kwargs)
        allow = self._allow

        def deco(fn):
            n = name or getattr(fn, "__name__", None)
            if n in allow:
                return inner(fn)
            return fn  # not allowed on this profile -> never registered

        return deco

    def __getattr__(self, item: str) -> Any:
        return getattr(self._mcp, item)


def tool_filter(mcp: Any, allow: set[str] | None):
    """Wrap ``mcp`` so every ``@mcp.tool`` gets its annotation hints completed
    (and, when ``allow`` is a set, is filtered to that set)."""
    return _ToolFilter(mcp, allow)


# ── Categories & intent matching (moved from the deleted tool_gate) ───────────

TOOL_CATEGORIES: dict[str, list[str]] = {
    "media":         ["jellyfin_", "sonarr_", "radarr_", "lidarr_", "jellyseerr_", "qbittorrent_"],
    "photos":        ["immich_"],
    "calendar":      ["nextcloud_list_calendars", "nextcloud_get_events", "nextcloud_add_event", "nextcloud_delete_event"],
    "tasks":         ["nextcloud_get_tasks", "nextcloud_add_task", "nextcloud_complete_task", "nextcloud_delete_task", "habitica_"],
    "contacts":      ["nextcloud_list_contacts", "nextcloud_search_contacts", "nextcloud_add_contact"],
    "notes":         ["nextcloud_get_notes", "nextcloud_read_note", "nextcloud_create_note", "nextcloud_append_to_note", "nextcloud_write_note", "obsidian_"],
    "files":         ["nextcloud_list_files", "nextcloud_read_file", "nextcloud_upload_file", "nextcloud_share_file", "nextcloud_move_file", "nextcloud_delete_file", "nextcloud_list_shares", "fs_", "smb_"],
    "home":          ["ha_"],
    "automation":    ["n8n_"],
    "notifications": ["ntfy_", "send_email", "nextcloud_share_file"],
    "monitoring":    ["uptime_status", "syncthing_", "docker_get_logs", "fail2ban_status"],
    "system_ops":    ["docker_", "omv_", "ssh_", "fail2ban_", "tailscale_"],
    "ai":            ["comfyui_", "fal_"],
    "weather":       ["weather_", "get_context"],
    "search":        ["web_", "wikipedia_", "google_search", "maps_"],
    "finance":       ["currency_"],
    "trivia": [
        "pub_chuck_joke", "pub_kanye_quote", "pub_bored_activity", "pub_animechan_quote", "pub_breaking_bad_quote",
        "pub_advice_slip", "pub_anime_random", "pub_dog_image", "pub_cat_fact", "pub_quotable_random", "pub_zenquotes_today",
        "pub_joke_any", "pub_official_joke", "pub_random_user", "pub_shibe_image", "pub_pokemon", "pub_swapi_person",
        "pub_rick_morty_character", "pub_deck_new", "pub_deck_draw", "pub_numbers_trivia", "pub_numbers_year",
        "pub_opentrivia_questions", "pub_xkcd_current", "pub_nasa_apod", "pub_iss_location", "pub_people_in_space",
        "pub_spaceflight_news", "pub_met_search", "pub_artic_artworks", "pub_tvmaze_search",
    ],
    "ip_network": [
        "pub_ipify", "pub_ip_api_lookup", "pub_ipwho", "pub_dns_resolve", "pub_cloudflare_trace",
        "pub_httpbin_ip", "pub_httpbin_uuid", "pub_uuid_v4_local", "pub_unix_timestamp",
        "pub_worldtime_timezone", "pub_worldtime_ip", "pub_zippopotam", "pub_nominatim_search",
        "pub_open_meteo_forecast", "pub_restcountries_region", "pub_restcountries_name",
        "pub_univ_search", "pub_openlibrary_search", "pub_agify_name", "pub_genderize_name",
        "pub_nationalize_name", "pub_github_zen",
    ],
    "crypto":        ["pub_coingecko_price", "pub_binance_ticker", "pub_coincap_assets", "pub_blockchain_btc_ticker", "pub_er_api_latest"],
    "meta":          ["plutus_tool_slicer", "test_all_tools"],
}

# Curated multi-category presets so users don't have to memorize the granular list.
INTENT_PRESETS: dict[str, list[str]] = {
    "all":      list(TOOL_CATEGORIES.keys()),
    "personal": ["calendar", "tasks", "contacts", "notes"],
    "office":   ["calendar", "tasks", "contacts", "notes", "files", "notifications"],
    "homelab":  ["system_ops", "monitoring", "automation"],
    "smarthome": ["home", "automation", "notifications", "monitoring"],
    "creative": ["ai", "photos", "files"],
    "web":      ["search", "weather", "finance", "ip_network"],
    "fun":      ["trivia", "crypto"],
}

# Tools that must always be reachable, even on an otherwise-empty profile, so a
# client is never fully locked out of the slicer/meta surface.
ALWAYS_EXPOSED: set[str] = {"plutus_tool_slicer"}


def infer_tool_categories(tool_name: str) -> set[str]:
    """Return *all* categories the tool belongs to. Empty set if uncategorised."""
    tn = str(tool_name or "").strip()
    if not tn:
        return set()
    cats: set[str] = set()
    for cat, markers in TOOL_CATEGORIES.items():
        for m in markers:
            if m == tn or (m.endswith("_") and tn.startswith(m)):
                cats.add(cat)
                break
    return cats


def infer_tool_category(tool_name: str) -> str:
    """Primary category (first match in declaration order). Falls back to 'other'."""
    cats = infer_tool_categories(tool_name)
    if not cats:
        return "other"
    for cat in TOOL_CATEGORIES:
        if cat in cats:
            return cat
    return next(iter(cats))


def _intent_terms(intent: str) -> list[str]:
    return [t for t in str(intent or "").lower().replace(",", " ").split() if len(t) > 1]


def _expand_intent(intent: str) -> tuple[list[str], list[str]]:
    """Expand presets and split include/exclude terms. ``-term`` is an exclusion."""
    include: list[str] = []
    exclude: list[str] = []
    seen_presets: set[str] = set()

    def _walk(token: str, sink: list[str]) -> None:
        t = token.strip().lower()
        if not t:
            return
        if t in INTENT_PRESETS:
            if t in seen_presets:
                return
            seen_presets.add(t)
            for child in INTENT_PRESETS[t]:
                _walk(child, sink)
            return
        sink.append(t)

    for raw in _intent_terms(intent):
        if raw.startswith("-") and len(raw) > 2:
            _walk(raw[1:], exclude)
        else:
            _walk(raw, include)
    return include, exclude


def _matches_any_term(terms: list[str], cats: set[str], name_l: str, label_l: str, tokens: set[str]) -> bool:
    for t in terms:
        if t in cats:
            return True
        if t in tokens:
            return True
        if len(t) >= 4 and (t in name_l or t in label_l):
            return True
    return False


def tool_matches_intent(name: str, label: str, intent: str) -> bool:
    """Whether a tool should be exposed under a free-text intent (empty = all).

    Match rule: a term equals one of the tool's categories, an exact token of
    name/label, or a >=4-char substring of name/label. Always-exposed tools
    bypass the filter so a client can never be fully locked out.
    """
    if str(name or "").strip() in ALWAYS_EXPOSED:
        return True
    include, exclude = _expand_intent(intent)
    if not include and not exclude:
        return True
    cats = infer_tool_categories(name)
    name_l = str(name or "").lower()
    label_l = str(label or "").lower()
    tokens = (set(re.split(r"[^a-z0-9]+", name_l)) | set(re.split(r"[^a-z0-9]+", label_l))) - {""}
    if include and not _matches_any_term(include, cats, name_l, label_l, tokens):
        return False
    if exclude and _matches_any_term(exclude, cats, name_l, label_l, tokens):
        return False
    return True


def build_tool_slice(all_tool_names: list[str], intent: str = "") -> dict[str, Any]:
    """Read-only preview: categorize every tool and report which match ``intent``.

    Powers the ``plutus_tool_slicer`` discovery tool. There is no global gate to
    apply anymore — scoping is done by creating a profile — so this never mutates
    anything.
    """
    rows: list[dict[str, Any]] = []
    by_category: dict[str, dict[str, int]] = {}
    for name in all_tool_names:
        if not name:
            continue
        category = infer_tool_category(name)
        matched = tool_matches_intent(name, "", intent)
        c = by_category.setdefault(category, {"total": 0, "matched": 0})
        c["total"] += 1
        c["matched"] += 1 if matched else 0
        rows.append({"name": name, "category": category, "matched": matched})
    matched_names = [r["name"] for r in rows if r["matched"]]
    include_terms, exclude_terms = _expand_intent(intent)
    return {
        "status": "ok",
        "intent": intent,
        "expansion": {"include": include_terms, "exclude": exclude_terms},
        "total": len(rows),
        "matched": len(matched_names),
        "by_category": by_category,
        "categories": sorted(TOOL_CATEGORIES.keys()),
        "presets": {name: list(members) for name, members in INTENT_PRESETS.items()},
        "compact": {"tool_names": matched_names},
        "hint": "Create a profile (POST /api/v1/profiles) to serve this subset at /mcp/p/<name>.",
    }


# ── Profile store ─────────────────────────────────────────────────────────────

def profiles_path(root: Path) -> Path:
    return root / "data" / PROFILES_FILE


def load_profiles(root: Path) -> list[dict[str, Any]]:
    """Return the validated profile list (empty on missing/invalid file)."""
    p = profiles_path(root)
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, dict):
        return []
    out: list[dict[str, Any]] = []
    for raw in data.get("profiles") or []:
        try:
            out.append(_normalize_profile(raw))
        except ValueError:
            continue  # skip a malformed row rather than fail the whole endpoint
    return out


def _normalize_profile(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("profile must be an object")
    name = str(raw.get("name") or "").strip().lower()
    if not PROFILE_NAME_RE.match(name):
        raise ValueError(f"profile name must match {PROFILE_NAME_RE.pattern!r} (got {raw.get('name')!r})")
    return {
        "name": name,
        "label": str(raw.get("label") or name).strip(),
        "intent": str(raw.get("intent") or "").strip(),
        "sections": [str(s).strip().lower() for s in (raw.get("sections") or []) if str(s).strip()],
        "include_tools": [str(s).strip() for s in (raw.get("include_tools") or []) if str(s).strip()],
        "exclude_tools": [str(s).strip() for s in (raw.get("exclude_tools") or []) if str(s).strip()],
        "created": str(raw.get("created") or ""),
    }


def save_profiles(root: Path, profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate + atomically write the profile list. Rejects duplicate names."""
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in profiles:
        prof = _normalize_profile(raw)
        if prof["name"] in seen:
            raise ValueError(f"duplicate profile name: {prof['name']}")
        seen.add(prof["name"])
        normalized.append(prof)
    p = profiles_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"_v": _SCHEMA_VERSION, "profiles": normalized}, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return normalized


def resolve_tool_names(profile: dict[str, Any], all_tool_names: list[str]) -> set[str]:
    """Resolve a profile to the concrete set of tool names it exposes.

    Order: (sections ∪ intent matches ∪ include_tools) − exclude_tools, then the
    always-exposed meta tool is re-added. Fail-closed: an empty/misconfigured
    profile resolves to just the always-exposed tool, never the full surface.
    ``include_tools`` is intersected with real tools so a typo can't inject one.
    """
    universe = set(all_tool_names)
    secs = set(profile.get("sections") or [])
    intent = str(profile.get("intent") or "")
    include = set(profile.get("include_tools") or []) & universe
    exclude = set(profile.get("exclude_tools") or [])

    allow: set[str] = set()
    for name in all_tool_names:
        if secs and (secs & infer_tool_categories(name)):
            allow.add(name)
        elif intent and tool_matches_intent(name, "", intent):
            allow.add(name)
    allow |= include
    allow -= exclude
    allow |= (ALWAYS_EXPOSED & universe)
    return allow
