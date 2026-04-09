"""
api/skills.py — Skill-Registry API.
Nur: GET /api/skills
Watchdog-Routen → api/watchdogs.py
Agent-Skills-Route → api/agents.py
"""
import logging
from fastapi import APIRouter

from services import get_services

logger = logging.getLogger(__name__)
router = APIRouter(tags=["skills"])


@router.get("/skills")
def list_skills():
    """Alle verfügbaren Skills mit Metadaten auflisten."""
    services = get_services()
    return {"skills": services.registry.list_for_api()}
