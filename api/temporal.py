"""
api/temporal.py — Read-only Endpoints für Eigenzeit-Daten (§4.4 Drift-Observability).

Liefert die zuletzt persistierten Tasks mit Eigenzeit-Feldern, sodass die
``/temporal`` UI einen Drift-Überblick rendern kann ohne SQL direkt zu sprechen.
"""
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query

from sqlmodel import select

from services import get_services
from storage.database import TaskDB, get_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/temporal", tags=["temporal"])


def _drift_seconds(reference: Optional[str], wall: Optional[str]) -> Optional[float]:
    """Differenz wall − reference in Sekunden. None bei fehlenden Werten."""
    if not reference or not wall:
        return None
    try:
        return (datetime.fromisoformat(wall) - datetime.fromisoformat(reference)).total_seconds()
    except Exception:
        return None


@router.get("/frames")
def list_frames(limit: int = Query(default=100, ge=1, le=500)):
    """Liefert die letzten N Tasks mit Eigenzeit-Feldern.

    Returns:
        {
          "frames": [
            {
              "task_id", "agent_id", "agent_name", "frame_id",
              "dilation_factor", "reference_now", "parent_reference_now",
              "wall_clock", "drift_seconds", "status"
            }, ...
          ],
          "now": "<iso>"
        }
    """
    with get_session() as session:
        stmt = (
            select(TaskDB)
            .order_by(TaskDB.created_at.desc())
            .limit(limit)
        )
        rows = session.exec(stmt).all()
    frames = []
    for r in rows:
        frames.append({
            "task_id":              r.id,
            "agent_id":             r.recipient_agent_id,
            "agent_name":           r.recipient_agent_name,
            "frame_id":             r.frame_id,
            "dilation_factor":      r.dilation_factor,
            "reference_now":        r.reference_now,
            "parent_reference_now": r.parent_reference_now,
            "wall_clock":           r.created_at,
            "drift_seconds":        _drift_seconds(r.reference_now, r.created_at),
            "status":               r.status,
        })
    return {"frames": frames, "now": datetime.now().isoformat()}


@router.get("/orchestrator")
def orchestrator_state():
    """Aktueller Zustand des Orchestrator-TimeProvider (§3.2)."""
    services = get_services()
    tp = services.time
    f = tp.frame
    return {
        "agent_id":              f.agent_id,
        "frame_id":              f.frame_id,
        "dilation_factor":       f.dilation_factor,
        "tau":                   tp.tau,
        "wall_now":              tp.wall_now().isoformat(),
        "reference_now":         tp.now().isoformat(),
        "parent_frame_id":       f.parent_frame_id,
        "parent_reference_now":  (
            f.parent_reference_now.isoformat()
            if f.parent_reference_now is not None else None
        ),
        "metadata":              dict(f.metadata),
    }
