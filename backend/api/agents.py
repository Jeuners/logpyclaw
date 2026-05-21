from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()


class SpawnRequest(BaseModel):
    name: str
    model: str = "gemma4:e4b"
    provider: str = "ollama"
    soul: str = ""


@router.get("/agents")
async def list_agents(request: Request):
    conductor = request.app.state.conductor
    return [a.to_dict() for a in conductor.list_agents()]


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str, request: Request):
    conductor = request.app.state.conductor
    agent = conductor.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, f"Agent not found: {agent_id}")
    return agent.to_dict()
