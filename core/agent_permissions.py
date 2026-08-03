"""Capability gating for the headless agent — the "how far may it go" axis.

The agent runs with --dangerously-skip-permissions and can reach Plutus's tools
while reading untrusted web pages, so a prompt-injection could try to trigger
destructive or outward-facing actions. A launch answers two independent questions,
each mapped here to a Claude Code ``--disallowedTools`` list (disallow wins):

- **write** — may it change anything at all? Off is a true audit posture: every
  tool not annotated read-only is blocked.
- **publish** — may it put something in front of other people, or send a message?
  Off by default even when write is on — "edit my library" and "open a public
  issue on my repo" are not the same permission.

(The *where* axis — which services an agent may touch — is separate:
core/agent_orchestrator.service_disallow, driven by the connection picker.)

The earlier ``strict_read``/``safe``/``all`` permission-*level* model was retired;
its ``build_disallowed*`` helpers are gone. The curated blast-radius sets below
remain as the reference for what counts as destructive/outward and as a safety
floor for tools whose annotations under-classify them.
"""
from __future__ import annotations

# Infrastructure control, deletion, sending, and costly generation — the safety
# floor blocked whenever writes are off, even if a tool's own annotation
# under-claims it.
DANGEROUS: frozenset[str] = frozenset({
    "docker_stop_container", "docker_restart_container", "docker_start_container",
    "qbittorrent_delete", "qbittorrent_pause", "qbittorrent_resume",
    "ssh_run", "ssh_exec", "ssh_add_host", "ssh_remove_host", "smb_add_share",
    "ha_turn_on", "ha_turn_off", "ha_call_service",
    "send_email", "ntfy_send", "n8n_trigger_webhook", "syncthing_rescan",
    "fal_generate_image", "fal_flux_pro", "comfyui_generate", "comfyui_interrupt",
    "fail2ban_unban",
    "nextcloud_delete_task", "nextcloud_delete_event", "nextcloud_delete_file",
    "habitica_delete_task", "fs_move_file",
    "radarr_add_movie", "sonarr_add_series", "jellyseerr_request",
})

# Note / content writing — part of the safety floor when writes are off, but not
# destructive: with writes on, a research playbook must be able to persist here.
WRITE: frozenset[str] = frozenset({
    "obsidian_write_note", "obsidian_append_to_note", "obsidian_create_daily_note",
    "fs_write_file",
    "nextcloud_create_note", "nextcloud_add_task", "nextcloud_add_event",
    "nextcloud_add_contact", "nextcloud_upload_file",
    "habitica_add_todo", "habitica_add_habit", "habitica_score_task",
    "weather_remember_city",
})


# Tools that put something in front of *other people*, or send a message. A
# separate axis from write: an agent can be perfectly free to edit your library
# and still have no business opening a public issue or emailing anyone.
OUTWARD: frozenset[str] = frozenset({
    "send_email", "ntfy_send", "n8n_trigger_webhook",
    "nextcloud_share_file",
    "github_create_issue", "github_comment", "github_create_pull",
    "github_create_repo",
    "jellyseerr_request",
})

# Naming conventions for the ones not yet written — reddit_submit_post,
# mastodon_post, bluesky_publish. Only consulted for tools that are *already*
# not read-only, which is what stops `reddit_post_comments` (a read) matching.
_OUTWARD_MARKERS = ("submit", "publish", "share", "tweet", "toot", "upload_video")


def is_outward(name: str, read_only: bool) -> bool:
    if read_only:
        return False          # a read can never publish
    return name in OUTWARD or any(m in name for m in _OUTWARD_MARKERS)


def _tool_annotations_map(tool_manager) -> dict:
    return {t.name: t.annotations for t in tool_manager.list_tools()}


def capability_disallow(tool_manager, *, allow_write: bool = True,
                        allow_publish: bool = False) -> list[str]:
    """`mcp__plutus__…` patterns for what these two switches forbid.

    Two independent axes, because they answer different questions:

    - **write** — may this agent change anything at all? Off is a true audit
      posture: every tool not annotated read-only is blocked.
    - **publish** — may it put something in front of other people, or send a
      message? Off by default even when write is on, because "edit my library"
      and "open a public issue on my repo" are not the same permission and a
      prompt-injected page can ask for either.

    Fail-safe throughout: a missing or None annotation counts as *not* read-only,
    so a tool that forgot to declare itself is treated as the dangerous case.
    """
    amap = _tool_annotations_map(tool_manager)
    blocked: set[str] = set()
    for name, ann in amap.items():
        read_only = bool(ann and ann.readOnlyHint)
        if not allow_write and not read_only:
            blocked.add(name)
        elif not allow_publish and is_outward(name, read_only):
            blocked.add(name)
    # The curated set is a floor, not a substitute: a tool whose annotation
    # under-classifies it (ssh_run, ha_call_service) stays blocked when writes
    # are off, whatever it claims about itself.
    if not allow_write:
        blocked |= (DANGEROUS | WRITE) & set(amap)
    return sorted(f"mcp__plutus__{n}" for n in blocked)
