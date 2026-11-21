"""core/permissions.py — what the automation may do without asking.

Every outward or hard-to-undo action the studio automation can take is listed
here with one of three settings: ``allow`` (do it), ``ask`` (prepare it, then
wait for an approval in the dashboard) or ``never``. Research and drafting are
not listed because they are always allowed.

Settings and approvals can only be changed from the dashboard. The MCP tools
can read them and *file* a request, never grant one — so an agent cannot talk
itself into a permission.
"""
from __future__ import annotations

import time
from pathlib import Path

from core.atomic_json import read_json, write_json

VALUES = ("allow", "ask", "never")

# id -> (group, label, default, what it means)
ACTIONS: dict[str, tuple[str, str, str, str]] = {
    "lab.download_models": ("Model lab", "Download models", "allow",
                            "Fetch model files into the lab's own folder on the server."),
    "lab.run_comfyui": ("Model lab", "Run test generations", "allow",
                        "Queue workflows on the lab's ComfyUI under the GPU lock."),
    "lab.update_comfyui": ("Model lab", "Update the lab's ComfyUI", "allow",
                           "Move the lab's own ComfyUI install to the newest version. Never the production one."),
    "lab.delete_own_models": ("Model lab", "Delete models it downloaded", "allow",
                              "Remove files inside the lab's own models folder after a run."),
    "lab.open_pr": ("Model lab", "Open a draft pull request", "allow",
                    "Push a lab/ branch to the workflows repo and open a draft PR."),
    "lab.merge_pr": ("Model lab", "Publish workflows (merge to main)", "ask",
                     "Merge a reviewed workflow PR so it is public on GitHub."),
    "email.create_draft": ("Email", "Write reply drafts", "allow",
                           "Save a drafted reply in the mailbox. Nothing is sent."),
    "email.send": ("Email", "Send email", "ask", "Send a message from your address."),
    "social.post": ("Publishing", "Post to Reddit / socials", "ask",
                    "Submit a post or comment from one of your accounts."),
    "youtube.publish": ("Publishing", "Change anything on YouTube", "ask",
                        "Upload, schedule, or edit titles, descriptions and thumbnails."),
    "print.morning_page": ("Reports", "Print the morning page", "allow",
                           "Send the daily one-page report to the printer."),
}


def _path(root: Path) -> Path:
    return Path(root) / "data" / "permissions.json"


def _load(root: Path) -> dict:
    data = read_json(_path(root), {})
    data = data if isinstance(data, dict) else {}
    data.setdefault("values", {})
    data.setdefault("requests", [])
    return data


def check(root: Path, action: str) -> str:
    """allow / ask / never for one action. Unknown actions are never allowed."""
    if action not in ACTIONS:
        return "never"
    val = _load(root)["values"].get(action)
    return val if val in VALUES else ACTIONS[action][2]


def overview(root: Path) -> dict:
    data = _load(root)
    rows = [{"id": aid, "group": g, "label": label, "help": help_, "default": default,
             "value": data["values"].get(aid) if data["values"].get(aid) in VALUES else default}
            for aid, (g, label, default, help_) in ACTIONS.items()]
    return {"actions": rows, "requests": list(reversed(data["requests"][-100:]))}


def set_value(root: Path, action: str, value: str) -> None:
    if action not in ACTIONS:
        raise KeyError(f"unknown action {action!r}")
    if value not in VALUES:
        raise ValueError(f"value must be one of {VALUES}")
    data = _load(root)
    data["values"][action] = value
    write_json(_path(root), data)


def request(root: Path, action: str, summary: str, link: str = "", ref: str = "") -> dict:
    """File a request for an ``ask`` action. Returns the row (status pending).

    A second request with the same action + ref while one is still open returns
    the existing row, so a nightly job re-running does not stack duplicates.
    """
    setting = check(root, action)
    if setting == "never":
        raise PermissionError(f"{action} is set to never")
    data = _load(root)
    for r in data["requests"]:
        if r["action"] == action and ref and r.get("ref") == ref and r["status"] in ("pending", "approved"):
            return r
    row = {"id": int(time.time() * 1000), "ts": int(time.time()), "action": action,
           "summary": (summary or "").strip()[:600], "link": (link or "")[:500],
           "ref": (ref or "")[:200], "status": "approved" if setting == "allow" else "pending",
           "decided": None}
    data["requests"].append(row)
    data["requests"] = data["requests"][-500:]
    write_json(_path(root), data)
    return row


def requests(root: Path, status: str = "") -> list[dict]:
    rows = _load(root)["requests"]
    return [r for r in rows if not status or r["status"] == status]


def _transition(root: Path, request_id: int, allowed_from: tuple[str, ...], to: str) -> dict:
    data = _load(root)
    for r in data["requests"]:
        if r["id"] == request_id:
            if r["status"] not in allowed_from:
                raise ValueError(f"request is {r['status']}, not {' or '.join(allowed_from)}")
            r["status"], r["decided"] = to, int(time.time())
            write_json(_path(root), data)
            return r
    raise KeyError(f"no request {request_id}")


def decide(root: Path, request_id: int, approve: bool) -> dict:
    """Dashboard only: approve or deny a pending request."""
    return _transition(root, request_id, ("pending",), "approved" if approve else "denied")


def mark_done(root: Path, request_id: int) -> dict:
    """The automation reports it carried out an approved request."""
    return _transition(root, request_id, ("approved",), "done")
