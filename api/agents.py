"""
api/agents.py — Agent CRUD + History API + Sub-Routen.
Ersetzt Flask-Routen aus app.py:
  /api/agents, /api/agents/<id>, /api/history/<id>,
  /api/agents/<id>/heartbeat, /api/agents/<id>/dream,
  /api/agents/<id>/settings, /api/agents/<id>/skills, /api/agents/<id>/voice
"""
import logging
import os
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from services import get_services
from core.errors import AgentNotFoundError, ValidationError

logger = logging.getLogger(__name__)
router = APIRouter(tags=["agents"])


class CreateAgentRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    soul: str = Field(default="")
    model: str = Field(default="llama3")
    provider: str = Field(default="ollama")
    color: str = Field(default="#00e676")
    role: str = Field(default="")
    skills: list[str] = Field(default_factory=list)
    max_tokens: int = Field(default=2048, ge=128, le=32768)
    favorite: bool = Field(default=False)
    voice: str = Field(default="")


class UpdateAgentRequest(BaseModel):
    name: str | None = None
    soul: str | None = None
    model: str | None = None
    provider: str | None = None
    color: str | None = None
    role: str | None = None
    skills: list[str] | None = None
    max_tokens: int | None = Field(default=None, ge=128, le=32768)
    favorite: bool | None = None
    voice: str | None = None
    web_search: bool | None = None
    heartbeat: dict | None = None
    dream: dict | None = None


@router.get("/agents")
def list_agents():
    services = get_services()
    agents = services.agents.list_all()
    return {"agents": agents, "count": len(agents)}


@router.get("/agents/{agent_id}")
def get_agent(agent_id: str):
    services = get_services()
    agent = services.agents.get(agent_id)
    if not agent:
        raise HTTPException(404, f"Agent {agent_id} nicht gefunden")
    return agent


@router.post("/agents", status_code=201)
def create_agent(req: CreateAgentRequest):
    services = get_services()
    try:
        agent = services.agents.create(req.model_dump())
        return agent
    except ValidationError as e:
        raise HTTPException(422, e.message)


@router.put("/agents/{agent_id}")
def update_agent(agent_id: str, req: UpdateAgentRequest):
    services = get_services()
    try:
        data = {k: v for k, v in req.model_dump().items() if v is not None}
        agent = services.agents.update(agent_id, data)
        return agent
    except AgentNotFoundError as e:
        raise HTTPException(404, e.message)


@router.delete("/agents/{agent_id}", status_code=204)
def delete_agent(agent_id: str):
    services = get_services()
    try:
        services.agents.delete(agent_id)
    except AgentNotFoundError as e:
        raise HTTPException(404, e.message)


@router.get("/history/{agent_id}")
def get_history(agent_id: str):
    services = get_services()
    history = services.agents.get_history(agent_id)
    return {"history": history, "agent_id": agent_id}


@router.delete("/history/{agent_id}", status_code=204)
def clear_history(agent_id: str):
    services = get_services()
    services.agents.clear_history(agent_id)


@router.get("/agents/{agent_id}/card")
def get_agent_card(agent_id: str):
    """A2A Agent Card — Capabilities Discovery."""
    services = get_services()
    agent = services.agents.get(agent_id)
    if not agent:
        raise HTTPException(404, f"Agent {agent_id} nicht gefunden")
    skills_info = []
    for skill_id in agent.get("skills", []):
        skill = services.registry.get(skill_id)
        if skill:
            skills_info.append({"id": skill.id, "name": skill.name, "description": skill.description})
    return {
        "id": agent["id"],
        "name": agent["name"],
        "role": agent.get("role", ""),
        "skills": skills_info,
        "provider": agent.get("provider", "ollama"),
        "model": agent.get("model", ""),
    }


# ── Heartbeat ─────────────────────────────────────────────────────────────────

class HeartbeatRequest(BaseModel):
    active: bool = Field(default=False)
    prompt: str = Field(default="")
    interval_min: int = Field(default=30, ge=1, le=10080)


@router.put("/agents/{agent_id}/heartbeat")
def set_heartbeat(agent_id: str, req: HeartbeatRequest):
    """Heartbeat-Konfiguration für einen Agenten setzen."""
    services = get_services()
    agent = services.agents.get(agent_id)
    if not agent:
        raise HTTPException(404, f"Agent {agent_id} nicht gefunden")

    prompt = req.prompt.strip()
    if req.active and not prompt:
        prompt = "What are your current thoughts? Give a brief status update."

    hb = agent.get("heartbeat", {})
    hb["active"] = req.active
    hb["prompt"] = prompt
    hb["interval_min"] = req.interval_min
    if req.active:
        hb["next_run"] = None  # sofort beim nächsten Scheduler-Tick
    updated = services.agents.update(agent_id, {"heartbeat": hb})
    services.events.emit("agent_updated", {"id": agent_id})
    logger.info("Heartbeat gesetzt für Agent %s: active=%s", agent_id, req.active)
    return {"ok": True, "agent": updated}


@router.post("/agents/{agent_id}/heartbeat/run")
def run_heartbeat_now(agent_id: str):
    """Heartbeat sofort manuell ausführen."""
    services = get_services()
    if not services.agents.get(agent_id):
        raise HTTPException(404, f"Agent {agent_id} nicht gefunden")
    from core.config import spawn_background
    spawn_background(services.heartbeat.run, agent_id)
    return {"ok": True}


# ── Dream ─────────────────────────────────────────────────────────────────────

class DreamRequest(BaseModel):
    active: bool = Field(default=False)
    retention_days: int = Field(default=30, ge=1, le=3650)


@router.put("/agents/{agent_id}/dream")
def set_dream(agent_id: str, req: DreamRequest):
    """Dream-Zyklus (Memory-Optimierung) konfigurieren."""
    services = get_services()
    agent = services.agents.get(agent_id)
    if not agent:
        raise HTTPException(404, f"Agent {agent_id} nicht gefunden")

    dream = agent.get("dream", {})
    dream["active"] = req.active
    dream["retention_days"] = req.retention_days
    updated = services.agents.update(agent_id, {"dream": dream})
    services.events.emit("agent_updated", {"id": agent_id})
    logger.info("Dream gesetzt für Agent %s: active=%s", agent_id, req.active)
    return {"ok": True, "agent": updated}


@router.post("/agents/{agent_id}/dream/run")
def run_dream_now(agent_id: str):
    """Dream-Zyklus sofort manuell starten."""
    services = get_services()
    if not services.agents.get(agent_id):
        raise HTTPException(404, f"Agent {agent_id} nicht gefunden")
    services.heartbeat.run_dream(agent_id)
    return {"ok": True}


# ── Settings / Skills / Voice ─────────────────────────────────────────────────

class AgentSettingsRequest(BaseModel):
    name: str | None = None
    role: str | None = None
    soul: str | None = None
    model: str | None = None
    provider: str | None = None
    max_tokens: int | None = Field(default=None, ge=128, le=32768)
    color: str | None = None
    avatar: str | None = None  # base64 data URL oder ""
    orchestrator: bool | None = None
    web_search: bool | None = None


@router.put("/agents/{agent_id}/settings")
def update_agent_settings(agent_id: str, req: AgentSettingsRequest):
    """Grundeinstellungen eines Agenten aktualisieren (Patch-Semantik)."""
    services = get_services()
    if not services.agents.get(agent_id):
        raise HTTPException(404, f"Agent {agent_id} nicht gefunden")
    data = {k: v for k, v in req.model_dump().items() if v is not None}
    if not data:
        raise HTTPException(422, "Keine Felder zum Aktualisieren angegeben")
    updated = services.agents.update(agent_id, data)
    services.events.emit("agent_updated", {"id": agent_id})
    return {"ok": True, "agent": updated}


class AgentSkillsRequest(BaseModel):
    skills: list[str] = Field(default_factory=list)


@router.put("/agents/{agent_id}/skills")
def update_agent_skills(agent_id: str, req: AgentSkillsRequest):
    """Skills-Liste eines Agenten überschreiben."""
    services = get_services()
    if not services.agents.get(agent_id):
        raise HTTPException(404, f"Agent {agent_id} nicht gefunden")
    updated = services.agents.update(agent_id, {"skills": req.skills})
    services.events.emit("agent_updated", {"id": agent_id})
    logger.info("Skills aktualisiert für Agent %s: %s", agent_id, req.skills)
    return {"ok": True, "skills": req.skills}


class AgentVoiceRequest(BaseModel):
    voice: str = Field(default="")


@router.put("/agents/{agent_id}/voice")
def update_agent_voice(agent_id: str, req: AgentVoiceRequest):
    """TTS-Stimme eines Agenten setzen."""
    services = get_services()
    if not services.agents.get(agent_id):
        raise HTTPException(404, f"Agent {agent_id} nicht gefunden")
    services.agents.update(agent_id, {"voice": req.voice})
    services.events.emit("agent_updated", {"id": agent_id})
    return {"ok": True, "voice": req.voice}


@router.get("/agents/{agent_id}/avatar")
def get_avatar(agent_id: str):
    """Avatar-Bild für einen Agenten liefern (aus Datei)."""
    from core.config import BASE_DIR
    services = get_services()
    agent = services.agents.get(agent_id)
    if not agent:
        raise HTTPException(404, "Agent nicht gefunden")
    avatar = agent.get("avatar", "")
    if avatar and avatar.startswith("file:"):
        path = avatar[5:]
        if not os.path.isabs(path):
            path = os.path.join(BASE_DIR, path)
        if os.path.exists(path):
            return FileResponse(path)
    raise HTTPException(404, "Kein Avatar vorhanden")
