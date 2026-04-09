"""
api/health.py — Health-Check Endpoint.
"""
import logging
import httpx
from fastapi import APIRouter
from config.settings import settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    checks = {
        "app": "ok",
        "ollama": await _check_service(settings.OLLAMA_URL + "/api/tags"),
        "qdrant": await _check_service(settings.QDRANT_URL + "/collections"),
    }
    all_ok = all(v == "ok" for v in checks.values())
    return checks


async def _check_service(url: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(url)
            return "ok" if r.status_code < 400 else "error"
    except Exception:
        return "unreachable"


@router.get("/summary")
def summary():
    """Kompakter Überblick: Anzahl Agenten, Tasks, aktive Aktivität."""
    from services import get_services
    services = get_services()
    tasks = services.tasks.list_all()
    agents = services.agents.list_all()
    activity = services.events.get_all_activity()
    return {
        "agents_total": len(agents),
        "tasks_total": len(tasks),
        "tasks_active": len([t for t in tasks if t.get("status") not in ("completed", "failed", "canceled")]),
        "agents_active": len(activity),
    }
