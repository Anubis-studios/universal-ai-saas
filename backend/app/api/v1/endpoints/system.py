"""Telemetry endpoints powering the HUD dashboard."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.services import telemetry

router = APIRouter(tags=["system"])


@router.get("/system/telemetry")
async def get_telemetry(request: Request) -> dict[str, Any]:
    """Live host and platform counters. Cheap enough to poll every few seconds."""
    return telemetry.snapshot(request)


@router.get("/system/activity")
async def get_activity(request: Request, limit: int = 8) -> dict[str, Any]:
    """Newest library records, shaped for the 'activity log' column."""
    db = getattr(request.app.state, "db", None)
    if db is None:
        return {"events": []}
    rows = db.search("", limit=min(max(limit, 1), 50))["records"]
    return {
        "events": [
            {
                "id": r["id"],
                "kind": r["kind"],
                "title": r["title"],
                "model": r.get("model") or "—",
                "at": r.get("created_at"),
                "url": r.get("url"),
            }
            for r in rows
        ]
    }
