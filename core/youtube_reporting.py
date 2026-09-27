"""Impressions and click-through rate — the two numbers the Analytics API lacks.

YouTube Analytics (``youtubeanalytics.googleapis.com``) has watch time,
retention and traffic sources, but no thumbnail impressions and no CTR. Those
exist only in the *Reporting* API, which works differently: you register a job
for a report type once, and YouTube then writes one CSV per day for it. The
first files appear about 48 hours after the job is created, together with a
backfill of the 30 days before it; each file is kept for 60 days (30 for the
backfill).

So the job is created when the Google account is connected — the moment the
owner consents — and the ``youtube_reach`` tool only ever reads. Report type
``channel_reach_basic_a1``: date, channel_id, video_id,
video_thumbnail_impressions, video_thumbnail_impressions_ctr.

When YouTube re-issues a day (a "backfill" with corrected numbers), both files
share the same start/end time; only the newest one is used.
"""
from __future__ import annotations

import csv
import io
from urllib.parse import urlparse

BASE = "https://youtubereporting.googleapis.com/v1"
REACH_TYPE = "channel_reach_basic_a1"
JOB_NAME = "Plutus — thumbnail impressions & CTR"


class ReportingError(RuntimeError):
    pass


async def _call(method: str, url: str, token: str, *, params: dict | None = None,
                json: dict | None = None, raw: bool = False):
    import httpx
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
        r = await c.request(method, url, params=params, json=json,
                            headers={"Authorization": f"Bearer {token}"})
    if r.status_code >= 400:
        try:
            msg = r.json().get("error", {}).get("message", "")
        except ValueError:
            msg = r.text[:200]
        raise ReportingError(f"YouTube Reporting API: {msg or f'HTTP {r.status_code}'}")
    return r.text if raw else r.json()


async def find_reach_job(token: str) -> dict | None:
    page = ""
    for _ in range(20):
        body = await _call("GET", f"{BASE}/jobs", token, params={"pageToken": page} if page else None)
        for job in body.get("jobs") or []:
            if job.get("reportTypeId") == REACH_TYPE:
                return job
        page = body.get("nextPageToken") or ""
        if not page:
            return None
    return None


async def ensure_reach_job(token: str) -> tuple[dict, bool]:
    """(job, created) — idempotent, so connecting twice never makes two jobs."""
    job = await find_reach_job(token)
    if job:
        return job, False
    job = await _call("POST", f"{BASE}/jobs", token,
                      json={"reportTypeId": REACH_TYPE, "name": JOB_NAME})
    return job, True


async def list_reports(token: str, job_id: str, since_rfc3339: str) -> list[dict]:
    out: list[dict] = []
    page = ""
    for _ in range(20):
        params = {"startTimeAtOrAfter": since_rfc3339}
        if page:
            params["pageToken"] = page
        body = await _call("GET", f"{BASE}/jobs/{job_id}/reports", token, params=params)
        out += body.get("reports") or []
        page = body.get("nextPageToken") or ""
        if not page:
            break
    return latest_per_period(out)


def latest_per_period(reports: list[dict]) -> list[dict]:
    """One report per day: a backfill replaces the file it corrects."""
    best: dict[tuple, dict] = {}
    for r in reports:
        k = (r.get("startTime"), r.get("endTime"))
        if k not in best or (r.get("createTime") or "") > (best[k].get("createTime") or ""):
            best[k] = r
    return sorted(best.values(), key=lambda r: r.get("startTime") or "")


async def download(token: str, url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if not host.endswith(".googleapis.com"):
        raise ReportingError(f"refusing to download a report from {host!r}")
    return await _call("GET", url, token, raw=True)


def parse_reach_csv(text: str) -> list[dict]:
    rows = []
    for r in csv.DictReader(io.StringIO(text or "")):
        try:
            rows.append({"date": r.get("date", ""), "video_id": r.get("video_id", ""),
                         "impressions": int(float(r.get("video_thumbnail_impressions") or 0)),
                         "ctr": float(r.get("video_thumbnail_impressions_ctr") or 0)})
        except ValueError:
            continue
    return rows


def aggregate(rows: list[dict]) -> dict:
    """Per video, per day and overall: impressions summed, CTR impression-weighted.

    CTR is kept in whatever unit YouTube's file uses — weighting does not change
    the unit, and converting it would mean guessing which one that is.
    """
    def acc():
        return {"impressions": 0, "_w": 0.0}
    by_video: dict[str, dict] = {}
    by_day: dict[str, dict] = {}
    total = acc()
    for r in rows:
        for bucket in (by_video.setdefault(r["video_id"], acc()), by_day.setdefault(r["date"], acc()), total):
            bucket["impressions"] += r["impressions"]
            bucket["_w"] += r["impressions"] * r["ctr"]

    def done(b):
        return {"impressions": b["impressions"],
                "ctr": (b["_w"] / b["impressions"]) if b["impressions"] else None}
    return {"videos": {k: done(v) for k, v in by_video.items()},
            "days": {k: done(v) for k, v in sorted(by_day.items())},
            "total": done(total)}
