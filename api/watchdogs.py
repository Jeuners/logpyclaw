"""
api/watchdogs.py — Watchdog-Monitoring API.
Portiert aus app.py: /api/watchdogs*, /api/watchdog/events, /api/watchdog/status
"""
import logging
import uuid
from datetime import datetime
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from services import get_services
from storage.watchdogs import load_watchdogs, save_watchdogs

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["watchdogs"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class WatchdogCreate(BaseModel):
    name: str = Field(default="New Watchdog")
    url: str = Field(..., min_length=1)
    interval_min: int = Field(default=30, ge=1, le=1440)
    agent_id: str = Field(default="")
    prompt: str = Field(
        default="Has anything relevant changed on this page? Answer with YES or NO, "
                "followed by a one-sentence summary of what changed."
    )
    alert_keyword: str = Field(default="YES")
    active: bool = Field(default=True)


class WatchdogUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    interval_min: int | None = Field(default=None, ge=1, le=1440)
    agent_id: str | None = None
    prompt: str | None = None
    alert_keyword: str | None = None
    active: bool | None = None


# ── Status / Events ───────────────────────────────────────────────────────────

@router.get("/watchdog/events")
def get_watchdog_events(
    limit: int = Query(default=50, ge=1, le=500),
    agent: str | None = Query(default=None),
):
    """A2A-Events aus der Event-Queue abrufen (gefiltert)."""
    services = get_services()
    events = services.events.get_since(0)
    if agent:
        events = [e for e in events if e.get("agent_id") == agent]
    return events[-limit:]


@router.get("/watchdog/status")
def get_watchdog_status():
    """Watchdog-Systemstatus (Redis-Kompatibilitäts-Endpoint)."""
    watchdogs = load_watchdogs()
    active = sum(1 for w in watchdogs if w.get("active"))
    return {
        "status": "active",
        "redis_connected": False,  # Redis nicht genutzt in neuem Stack
        "watchdog_count": len(watchdogs),
        "active_count": active,
    }


# ── CRUD ──────────────────────────────────────────────────────────────────────

@router.get("/watchdogs")
def list_watchdogs():
    return load_watchdogs()


@router.post("/watchdogs", status_code=201)
def create_watchdog(req: WatchdogCreate):
    now = datetime.now().isoformat()
    wd = {
        "id": str(uuid.uuid4()),
        "name": req.name,
        "url": req.url,
        "interval_min": req.interval_min,
        "agent_id": req.agent_id,
        "prompt": req.prompt,
        "alert_keyword": req.alert_keyword,
        "active": req.active,
        "created_at": now,
        "last_run": None,
        "last_result": None,
        "last_hash": None,
        "next_run": None,
        "check_count": 0,
        "hit_count": 0,
        "history": [],
    }
    watchdogs = load_watchdogs()
    watchdogs.append(wd)
    save_watchdogs(watchdogs)
    logger.info("Watchdog erstellt: %s → %s", wd["name"], wd["url"][:60])
    return wd


@router.put("/watchdogs/{wd_id}")
def update_watchdog(wd_id: str, req: WatchdogUpdate):
    watchdogs = load_watchdogs()
    for i, wd in enumerate(watchdogs):
        if wd["id"] == wd_id:
            url_changed = req.url is not None and req.url != wd["url"]
            if req.name is not None:
                watchdogs[i]["name"] = req.name
            if req.url is not None:
                watchdogs[i]["url"] = req.url
            if req.interval_min is not None:
                watchdogs[i]["interval_min"] = req.interval_min
            if req.agent_id is not None:
                watchdogs[i]["agent_id"] = req.agent_id
            if req.prompt is not None:
                watchdogs[i]["prompt"] = req.prompt
            if req.alert_keyword is not None:
                watchdogs[i]["alert_keyword"] = req.alert_keyword
            if req.active is not None:
                watchdogs[i]["active"] = req.active
            # URL geändert → Hash zurücksetzen (neuer Baseline)
            if url_changed:
                watchdogs[i]["last_hash"] = None
                watchdogs[i]["next_run"] = None
            save_watchdogs(watchdogs)
            return watchdogs[i]
    raise HTTPException(404, f"Watchdog {wd_id} nicht gefunden")


@router.delete("/watchdogs/{wd_id}", status_code=204)
def delete_watchdog(wd_id: str):
    watchdogs = load_watchdogs()
    filtered = [w for w in watchdogs if w["id"] != wd_id]
    if len(filtered) == len(watchdogs):
        raise HTTPException(404, f"Watchdog {wd_id} nicht gefunden")
    save_watchdogs(filtered)


# ── Actions ───────────────────────────────────────────────────────────────────

@router.post("/watchdogs/{wd_id}/run")
def run_watchdog_now(wd_id: str):
    """Watchdog sofort manuell ausführen."""
    services = get_services()
    watchdogs = load_watchdogs()
    wd = next((w for w in watchdogs if w["id"] == wd_id), None)
    if not wd:
        raise HTTPException(404, f"Watchdog {wd_id} nicht gefunden")
    from core.config import spawn_background
    spawn_background(services.watchdog.run, wd_id)
    return {"ok": True, "message": "Watchdog wird ausgeführt…"}


@router.post("/watchdogs/{wd_id}/toggle")
def toggle_watchdog(wd_id: str):
    """Watchdog aktivieren / deaktivieren."""
    services = get_services()
    active = services.watchdog.toggle(wd_id)
    if active is None:
        raise HTTPException(404, f"Watchdog {wd_id} nicht gefunden")
    return {"active": active}
