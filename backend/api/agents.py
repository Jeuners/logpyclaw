"""backend/api/agents.py — Agent REST-API inkl. Spawn."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()


class SpawnRequest(BaseModel):
    name: str
    model: str = "gemma4:e4b"
    provider: str = "ollama"
    soul: str = ""
    faction: str = ""  # Faction-ID zum Zuweisen (optional)


@router.get("/agents")
async def list_agents(request: Request):
    conductor = request.app.state.conductor
    return [a.to_dict() for a in conductor.list_agents()]


@router.get("/agents/{agent_id:path}")
async def get_agent(agent_id: str, request: Request):
    conductor = request.app.state.conductor
    agent = conductor.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, f"Agent not found: {agent_id}")
    return agent.to_dict()


@router.post("/agents/spawn")
async def spawn_agent(body: SpawnRequest, request: Request):
    from backend.agents.llm_agent import LLMAgent
    from backend.config import get_settings

    conductor = request.app.state.conductor
    cfg = get_settings()

    slug = body.name.lower().replace(" ", "_")
    agent_id = f"agent:{slug}"
    if conductor.get_agent(agent_id):
        raise HTTPException(409, f"Agent already exists: {agent_id}")

    agent = LLMAgent(
        agent_id=agent_id,
        name=body.name,
        model=body.model,
        provider=body.provider,
        soul=body.soul,
        ollama_url=cfg.ollama_url,
    )
    conductor.register(agent)
    await agent.start()

    if body.faction:
        from backend.core.faction_protocol import FactionRegistry

        FactionRegistry.get().assign(agent_id, body.faction)

    return agent.to_dict()
