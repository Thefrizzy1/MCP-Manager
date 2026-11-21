"""Models surface: what the Trend Radar found, and what the model lab did about it."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core import lab_board
from core import trend_radar as tr
from ui.api.deps import verify_auth
from ui.runtime import ROOT

router = APIRouter(dependencies=[Depends(verify_auth)])

# Radar stages that mean "files you can download exist".
RELEASED = ("weights", "gguf")


@router.get("/api/v1/models")
async def api_v1_models():
    lab = lab_board.entries(ROOT)
    now = time.time()
    rows = []
    for c in tr.candidates(ROOT, limit=300):
        entry = lab.get(c["slug"])
        rows.append({
            "slug": c["slug"], "name": c["name"], "hot": round(c.get("hot") or 0),
            "stage": c.get("stage") or "", "released": (c.get("stage") or "") in RELEASED,
            "covered": str(c.get("action") or "").startswith("DONE"),
            "first_seen": c.get("first_ts") or 0,
            "age_days": int((now - (c.get("first_ts") or now)) / 86400),
            "signals": c.get("signal_count", 0),
            "lab": entry,
        })
    known = {r["slug"] for r in rows}
    for slug, entry in lab.items():          # queued by hand, not (yet) on the radar
        if slug not in known:
            rows.append({"slug": slug, "name": entry.get("name") or slug, "hot": 0, "stage": "",
                         "released": True, "covered": False, "first_seen": entry.get("added", 0),
                         "age_days": 0, "signals": 0, "lab": entry})
    return {"models": rows, "heartbeat": lab_board.heartbeat(ROOT)}


class QueueBody(BaseModel):
    slug: str = Field(..., min_length=1, max_length=120)
    name: str = Field(default="", max_length=200)
    status: str = Field(default="queued", max_length=12)   # queued | skipped | "" (remove)


@router.post("/api/v1/models/set")
async def api_v1_models_set(body: QueueBody):
    try:
        if body.status == "queued":
            lab_board.queue(ROOT, body.slug, body.name)
        else:
            lab_board.set_status(ROOT, body.slug.strip().lower(), body.status)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return await api_v1_models()


class QueueNewBody(BaseModel):
    max_age_days: int = Field(default=45, ge=1, le=400)


@router.post("/api/v1/models/queue_new")
async def api_v1_models_queue_new(body: QueueNewBody):
    """Queue every released model first seen recently that is neither covered nor on the list."""
    data = await api_v1_models()
    for m in sorted(data["models"], key=lambda m: -m["hot"]):
        if m["released"] and not m["covered"] and not m["lab"] and m["age_days"] <= body.max_age_days:
            lab_board.queue(ROOT, m["slug"], m["name"])
    return await api_v1_models()
