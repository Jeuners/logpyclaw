"""backend/api/dreams.py — Dream-Galerie API."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Request

router = APIRouter()

DREAMS_DIR = Path(__file__).parent.parent.parent / "dreams"


@router.post("/dreams/trigger")
async def trigger_dreams(request: Request, background_tasks: BackgroundTasks):
    """Triggert den Traum-Zyklus manuell (für Tests)."""
    from backend.config import get_settings
    from backend.services.dream import run_dream_cycle
    conductor = request.app.state.conductor
    cfg = get_settings()
    background_tasks.add_task(run_dream_cycle, conductor, cfg.comfyui_url)
    return {"status": "dream cycle started"}


@router.get("/dreams")
async def list_dreams():
    """Gibt alle Träume gruppiert nach Datum zurück."""
    if not DREAMS_DIR.exists():
        return []
    days = []
    for day_dir in sorted(DREAMS_DIR.iterdir(), reverse=True):
        if not day_dir.is_dir():
            continue
        entries = []
        for meta_file in sorted(day_dir.glob("*.json")):
            try:
                data = json.loads(meta_file.read_text())
                entries.append(data)
            except Exception:
                pass
        if entries:
            days.append({"date": day_dir.name, "dreams": entries})
    return days
