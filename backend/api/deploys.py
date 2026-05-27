"""
backend/api/deploys.py — REST für Deploy-Verwaltung im Frontend.

GET    /api/deploys           → Liste aller deploys (aus frontend/builds/.deploys.json)
DELETE /api/deploys/<slug>    → undeploy via DeploySkill
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/deploys")

REPO_ROOT  = Path(__file__).resolve().parent.parent.parent
META_FILE  = REPO_ROOT / "frontend" / "builds" / ".deploys.json"


@router.get("")
async def list_deploys():
    if not META_FILE.exists():
        return {"deploys": []}
    try:
        data = json.loads(META_FILE.read_text())
        return {"deploys": list(data.get("deploys", {}).values())}
    except Exception as e:
        raise HTTPException(500, f"meta read error: {e}")


@router.delete("/{slug}")
async def undeploy(slug: str, request: Request):
    conductor = request.app.state.conductor
    skill_agent = conductor.get_agent("skill:deploy")
    if not skill_agent:
        raise HTTPException(503, "deploy skill not loaded")
    skill = skill_agent._skill
    result = await skill._undeploy(slug)
    return {"result": result}
