"""
backend/api/factions.py — Faction REST-API.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


class StanceRequest(BaseModel):
    source_faction: str
    target_faction: str
    stance: str   # allied | cooperative | neutral | skeptical | adversarial


class OutcomeRequest(BaseModel):
    source_agent: str
    target_agent: str
    success: bool


@router.get("/factions")
async def list_factions():
    from backend.core.faction_protocol import FactionRegistry
    reg = FactionRegistry.get()
    return [f.to_dict() for f in reg.list_factions()]


@router.get("/factions/{faction_id}")
async def get_faction(faction_id: str):
    from backend.core.faction_protocol import FactionRegistry
    reg = FactionRegistry.get()
    f = reg.get_faction(faction_id)
    if not f:
        raise HTTPException(404, f"Faction not found: {faction_id}")
    relations = []
    for rel in reg.all_relations():
        if rel.source == faction_id or rel.target == faction_id:
            relations.append(rel.to_dict())
    return {**f.to_dict(), "relations": relations}


@router.post("/factions/stance")
async def set_stance(body: StanceRequest):
    from backend.core.faction_protocol import FactionRegistry, FactionStance
    reg = FactionRegistry.get()
    try:
        stance = FactionStance[body.stance.upper()]
    except KeyError:
        raise HTTPException(400, f"Unknown stance: {body.stance}")
    reg.set_stance(body.source_faction, body.target_faction, stance)
    return {"ok": True, "source": body.source_faction, "target": body.target_faction, "stance": stance.value}


@router.post("/factions/outcome")
async def record_outcome(body: OutcomeRequest):
    from backend.core.faction_protocol import FactionRegistry
    reg = FactionRegistry.get()
    reg.record_cross_faction_outcome(body.source_agent, body.target_agent, body.success)
    return {"ok": True}
