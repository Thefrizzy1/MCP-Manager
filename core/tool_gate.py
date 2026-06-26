"""Per-tool and per-section MCP exposure (list_tools / call_tool) + dashboard toggles."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp.server.fastmcp.tools.tool_manager import ToolManager

GATE_NAME = "plutus_tool_gate.json"


def gate_path(root: Path) -> Path:
    return root / "data" / GATE_NAME


DEFAULT_GATE: dict[str, Any] = {
    "disabled_tools": [],
    "disabled_sections": [],
    "disabled_tags": [],
    # Active intent restricts the MCP tool list to tools whose category matches.
    # Empty string = no restriction (all enabled tools exposed).
    "active_intent": "",
}


# Fine-grained semantic categories — used to slice the MCP tool manifest down
# to "what the AI actually needs right now". Section ("selfhosted"/"public") is
# too coarse to be useful for token-cost reduction.
#
# A tool may legitimately belong to more than one category (e.g. nextcloud_share_file
# is files + notifications). `infer_tool_categories` returns the full set so any
# matching intent surfaces it.
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
    "trivia":        [
        "pub_chuck_joke", "pub_kanye_quote", "pub_bored_activity", "pub_animechan_quote", "pub_breaking_bad_quote",
        "pub_advice_slip", "pub_anime_random", "pub_dog_image", "pub_cat_fact", "pub_quotable_random", "pub_zenquotes_today",
        "pub_joke_any", "pub_official_joke", "pub_random_user", "pub_shibe_image", "pub_pokemon", "pub_swapi_person",
        "pub_rick_morty_character", "pub_deck_new", "pub_deck_draw", "pub_numbers_trivia", "pub_numbers_year",
        "pub_opentrivia_questions", "pub_xkcd_current", "pub_nasa_apod", "pub_iss_location", "pub_people_in_space",
        "pub_spaceflight_news", "pub_met_search", "pub_artic_artworks", "pub_tvmaze_search",
    ],
    "ip_network":    [
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
# A preset value can include other preset names (resolved transitively) and category
# names. Resolution stops once nothing new is added (cycles are safe).
INTENT_PRESETS: dict[str, list[str]] = {
    "all":      list(TOOL_CATEGORIES.keys()),
    "personal": ["calendar", "tasks", "contacts", "notes"],
    "office":   ["calendar", "tasks", "contacts", "notes", "files", "notifications"],
    "homelab":  ["system_ops", "monitoring", "automation"],
    "smarthome":["home", "automation", "notifications", "monitoring"],
    "creative": ["ai", "photos", "files"],
    "web":      ["search", "weather", "finance", "ip_network"],
    "fun":      ["trivia", "crypto"],
}

# Tools that must NEVER be filtered out, regardless of intent. Without this the
# slicer can lock itself out (you set intent='calendar', then have no way to
# undo it because plutus_tool_slicer is in 'meta' which doesn't match).
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
    # Walk TOOL_CATEGORIES in declaration order so we get a stable "primary".
    for cat in TOOL_CATEGORIES:
        if cat in cats:
            return cat
    return next(iter(cats))


def load_gate(root: Path) -> dict[str, Any]:
    p = gate_path(root)
    if not p.is_file():
        return {k: list(v) if isinstance(v, list) else v for k, v in DEFAULT_GATE.items()}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {k: list(v) if isinstance(v, list) else v for k, v in DEFAULT_GATE.items()}
    if not isinstance(raw, dict):
        return {k: list(v) if isinstance(v, list) else v for k, v in DEFAULT_GATE.items()}
    out = {k: list(v) if isinstance(v, list) else v for k, v in DEFAULT_GATE.items()}
    dt = raw.get("disabled_tools")
    ds = raw.get("disabled_sections")
    dg = raw.get("disabled_tags")
    ai_raw = raw.get("active_intent")
    if isinstance(dt, list):
        out["disabled_tools"] = sorted({str(x).strip() for x in dt if str(x).strip()})
    if isinstance(ds, list):
        out["disabled_sections"] = sorted({str(x).strip().lower() for x in ds if str(x).strip()})
    if isinstance(dg, list):
        out["disabled_tags"] = sorted({str(x).strip().lower() for x in dg if str(x).strip()})
    if isinstance(ai_raw, str):
        out["active_intent"] = ai_raw.strip().lower()
    return out


def save_gate(root: Path, data: dict[str, Any]) -> None:
    p = gate_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    merged = dict(DEFAULT_GATE)
    merged.update({k: data[k] for k in DEFAULT_GATE if k in data})
    p.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")


def set_active_intent(root: Path, intent: str) -> str:
    """Persist the active intent. Returns the normalized intent string."""
    g = load_gate(root)
    g["active_intent"] = str(intent or "").strip().lower()
    save_gate(root, g)
    return g["active_intent"]


def _intent_terms(intent: str) -> list[str]:
    """Tokenize intent on whitespace and commas. Preserves underscores so
    compound category names (system_ops, ip_network) survive as single tokens.
    Drops tokens shorter than 2 chars.
    """
    return [t for t in str(intent or "").lower().replace(",", " ").split() if len(t) > 1]


def _expand_intent(intent: str) -> tuple[list[str], list[str]]:
    """Expand presets and split include/exclude terms.

    Returns (include_terms, exclude_terms). A token prefixed with ``-`` is treated
    as an exclusion. Preset names ('personal', 'all', …) are recursively replaced
    with their member categories. Cycles are safe; visited names short-circuit.
    """
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


def _tool_matches_intent(name: str, label: str, intent: str) -> bool:
    """Decide whether a tool should be exposed under the given intent.

    Order of evaluation:
      1. Always-exposed tools (e.g. plutus_tool_slicer) bypass the filter so the
         user can never lock themselves out.
      2. Empty intent (after preset expansion) matches everything.
      3. The tool must match at least one *include* term.
      4. The tool must NOT match any *exclude* term (``-trivia`` etc.).

    Match rule for a term: equals one of the tool's categories OR an exact token
    of name/label OR a >=4-char substring of name/label. The 4-char minimum
    prevents ``ai`` from matching ``tAIlscale``/``emAIl``.
    """
    import re
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


def set_tool_enabled(root: Path, tool_name: str, enabled: bool) -> None:
    name = str(tool_name or "").strip()
    if not name:
        return
    g = load_gate(root)
    dis = set(g.get("disabled_tools", []))
    if enabled:
        dis.discard(name)
    else:
        dis.add(name)
    g["disabled_tools"] = sorted(dis)
    save_gate(root, g)


def set_section_disabled(root: Path, section: str, disabled: bool) -> None:
    sec = str(section or "").strip().lower()
    if sec not in ("selfhosted", "public", "custom"):
        raise ValueError("section must be selfhosted, public, or custom")
    g = load_gate(root)
    cur = set(g.get("disabled_sections", []))
    if disabled:
        cur.add(sec)
    else:
        cur.discard(sec)
    g["disabled_sections"] = sorted(cur)
    save_gate(root, g)


def _tool_section_index(root: Path) -> dict[str, str]:
    from core.service_registry import all_services

    idx: dict[str, str] = {}
    for svc in all_services(root):
        sec = str(svc.get("section") or "").strip().lower()
        if not sec:
            continue
        for t in svc.get("tools") or []:
            n = str(t.get("name") or "").strip()
            if n:
                idx[n] = sec
    return idx


def infer_tool_section(tool_name: str, root: Path) -> str:
    tn = str(tool_name or "").strip()
    idx = _tool_section_index(root)
    if tn in idx:
        return idx[tn]
    if tn.startswith("pub_"):
        return "public"
    if tn.startswith(("weather_", "web_", "wikipedia_", "currency_", "google_search", "maps_")):
        return "public"
    if tn.startswith(("docker_", "fs_", "fail2ban_", "tailscale_", "get_context", "send_email")):
        return "selfhosted"
    return "selfhosted"


def is_mcp_tool_enabled(tool_name: str, root: Path, *, label: str = "") -> bool:
    tn = str(tool_name or "").strip()
    if not tn:
        return False
    g = load_gate(root)
    if tn in set(g.get("disabled_tools", [])):
        return False
    sec = infer_tool_section(tn, root)
    if sec in set(g.get("disabled_sections", [])):
        return False
    intent = str(g.get("active_intent") or "").strip()
    if intent and not _tool_matches_intent(tn, label, intent):
        return False
    return True


def build_tool_slice(root: Path, intent: str = "") -> dict[str, Any]:
    """Categorize every registered tool and report which would be exposed under `intent`.

    Categories are fine-grained (calendar, files, media, ai, …). When `intent`
    is set, the response's `compact.tool_names` lists only tools whose category
    or name/label matches — that's the subset to expose to a focused agent.
    """
    from core.service_registry import all_services

    g = load_gate(root)
    disabled_tools = set(g.get("disabled_tools") or [])
    disabled_sections = set(g.get("disabled_sections") or [])
    rows: list[dict[str, Any]] = []
    by_section: dict[str, dict[str, int]] = {}
    by_category: dict[str, dict[str, int]] = {}
    for svc in all_services(root):
        section = str(svc.get("section") or "selfhosted").lower()
        service_id = str(svc.get("id") or "")
        svc_label = str(svc.get("label") or service_id)
        for tool in svc.get("tools") or []:
            name = str(tool.get("name") or "")
            if not name:
                continue
            tool_label = str(tool.get("label") or "")
            category = infer_tool_category(name)
            matched = _tool_matches_intent(name, f"{svc_label} {tool_label}", intent)
            reason = "exposed"
            exposed = True
            if section in disabled_sections:
                exposed, reason = False, "section_disabled"
            if name in disabled_tools:
                exposed, reason = False, "tool_disabled"
            b = by_section.setdefault(section, {"total": 0, "exposed": 0, "blocked": 0, "matched": 0})
            b["total"] += 1
            b["exposed" if exposed else "blocked"] += 1
            b["matched"] += 1 if matched else 0
            c = by_category.setdefault(category, {"total": 0, "exposed": 0, "blocked": 0, "matched": 0})
            c["total"] += 1
            c["exposed" if exposed else "blocked"] += 1
            c["matched"] += 1 if matched else 0
            rows.append({
                "name": name,
                "service": service_id,
                "section": section,
                "category": category,
                "matched": matched,
                "exposed": exposed,
                "reason": reason,
            })
    visible = [r for r in rows if r["matched"]]
    exposed_rows = [r for r in visible if r["exposed"]]
    blocked_rows = [r for r in visible if not r["exposed"]]

    # Build a "shopping menu" so the user/AI can pick categories without guessing.
    # For each category: show the count and 3 sample tool names from rows.
    samples_by_category: dict[str, list[str]] = {}
    for r in rows:
        cat = r["category"]
        bucket = samples_by_category.setdefault(cat, [])
        if len(bucket) < 3:
            bucket.append(r["name"])

    include_terms, exclude_terms = _expand_intent(intent)
    return {
        "status": "ok",
        "intent": intent,
        "active_intent": g.get("active_intent", ""),
        "expansion": {"include": include_terms, "exclude": exclude_terms},
        "total": len(rows),
        "matched": len(visible),
        "exposed": len(exposed_rows),
        "blocked": len(blocked_rows),
        "by_section": by_section,
        "by_category": by_category,
        "categories": sorted(TOOL_CATEGORIES.keys()),
        "category_samples": samples_by_category,
        "presets": {name: list(members) for name, members in INTENT_PRESETS.items()},
        "tools": visible,
        "compact": {
            "tool_names": [r["name"] for r in exposed_rows],
            "by_category": {
                cat: [r["name"] for r in exposed_rows if r["category"] == cat]
                for cat in sorted({r["category"] for r in exposed_rows})
            },
            "syntax_help": {
                "categories": "comma- or space-separated category names: 'calendar tasks files'",
                "presets": "use a preset name as shorthand: 'personal', 'office', 'homelab', 'creative', 'web', 'all'",
                "exclusions": "prefix a term with '-' to remove it: 'all -trivia -crypto'",
                "free_text": "any 4+ char substring of a tool name or label (e.g. 'sonarr')",
            },
            "context_rules": [
                "tool names only — no full MCP manifest",
                "set active_intent via plutus_tool_slicer(intent=..., apply=true) or POST /api/v1/tools/intent",
                "plutus_tool_slicer is always exposed regardless of intent so you can always undo",
            ],
        },
    }


def apply_tool_gate_patch(tm: ToolManager, root: Path) -> None:
    if getattr(tm, "_plutus_tool_gate_patched", False):
        return
    orig_list = tm.list_tools
    orig_get = tm.get_tool
    r = root.resolve()

    def list_tools():  # type: ignore[no-untyped-def]
        return [t for t in orig_list() if is_mcp_tool_enabled(t.name, r, label=getattr(t, "description", "") or "")]

    def get_tool(name: str):  # type: ignore[no-untyped-def]
        if not is_mcp_tool_enabled(name, r):
            return None
        return orig_get(name)

    tm.list_tools = list_tools  # type: ignore[method-assign]
    tm.get_tool = get_tool  # type: ignore[method-assign]
    tm._plutus_tool_gate_patched = True  # type: ignore[attr-defined]
