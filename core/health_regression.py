"""Health regression detection.

Runs the zero-param tool health batch, diffs the result against a saved
baseline, and reports only *regressions* — tools that worked last time and fail
now — plus recoveries. Designed to be triggered on a schedule (n8n cron, the
scheduled-tasks plugin, or a plain cron curl) so a broken integration pages you
instead of being discovered by accident.

The diff (`diff_statuses`) is a pure function so it can be unit-tested offline.
The baseline lives in data/health_baseline.json.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from core.batch_health import run_health_batch_for_ui

BASELINE_FILE = "health_baseline.json"

# batch kinds we treat as "the tool works" vs "broken". "unset"/"skip" are
# neither — an unconfigured service is not a regression.
_OK = {"pass"}
_BROKEN = {"fail"}


def _baseline_path(root: Path) -> Path:
    return root / "data" / BASELINE_FILE


def load_baseline(root: Path) -> dict[str, str]:
    p = _baseline_path(root)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        statuses = data.get("statuses", {}) if isinstance(data, dict) else {}
        return {k: str(v) for k, v in statuses.items()} if isinstance(statuses, dict) else {}
    except Exception:
        return {}


def save_baseline(root: Path, statuses: dict[str, str]) -> None:
    p = _baseline_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"updated": time.strftime("%Y-%m-%d %H:%M:%S"), "statuses": statuses}
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def diff_statuses(baseline: dict[str, str], current: dict[str, str]) -> dict[str, list[str]]:
    """Compare prior vs current tool statuses.

    - regressions: was OK, now broken
    - recovered:   was broken, now OK
    - new_broken:  broken now with no prior OK baseline (info, not alerted)
    Returns sorted name lists.
    """
    regressions, recovered, new_broken = [], [], []
    for name, cur in current.items():
        prev = baseline.get(name)
        if cur in _BROKEN:
            if prev in _OK:
                regressions.append(name)
            elif prev not in _BROKEN:
                new_broken.append(name)
        elif cur in _OK and prev in _BROKEN:
            recovered.append(name)
    return {
        "regressions": sorted(regressions),
        "recovered": sorted(recovered),
        "new_broken": sorted(new_broken),
    }


def _merge_baseline(prev: dict[str, str], current: dict[str, str]) -> dict[str, str]:
    """Update the baseline, but never erase a known-good ('pass') entry with a
    failure. This keeps a regressed tool registering as a *regression* on every
    subsequent run until it actually recovers — instead of being promoted to
    'fail' after the first alert and then going silent. Recoveries and brand-new
    passing tools are recorded normally.
    """
    merged = dict(prev)
    for name, status in current.items():
        if status in _OK:
            merged[name] = "pass"
        elif status in _BROKEN and merged.get(name) != "pass":
            merged[name] = "fail"
    return merged


async def run_regression_check(
    root: Path,
    tool_manager: Any,
    *,
    notify: Callable[[str], Any] | None = None,
    update_baseline: bool = True,
) -> dict[str, Any]:
    """Run the tool batch, diff vs baseline, optionally notify and persist.

    `notify` is an (async or sync) callable taking the alert message; it is
    invoked only when there are regressions.
    """
    rows = await run_health_batch_for_ui(tool_manager)
    current = {r["name"]: r.get("kind", "") for r in rows}
    baseline = load_baseline(root)
    diff = diff_statuses(baseline, current)

    ok_n = sum(1 for v in current.values() if v in _OK)
    broken_n = sum(1 for v in current.values() if v in _BROKEN)

    notified = False
    if diff["regressions"] and notify is not None:
        msg = "Plutus health regression — these tools worked before and fail now:\n" + "\n".join(
            f"• {name}" for name in diff["regressions"]
        )
        try:
            res = notify(msg)
            if hasattr(res, "__await__"):
                await res
            notified = True
        except Exception:
            notified = False

    if update_baseline:
        save_baseline(root, _merge_baseline(baseline, current))

    return {
        "ok": not diff["regressions"],
        "checked": len(current),
        "passing": ok_n,
        "broken": broken_n,
        "regressions": diff["regressions"],
        "recovered": diff["recovered"],
        "new_broken": diff["new_broken"],
        "notified": notified,
        "had_baseline": bool(baseline),
    }
