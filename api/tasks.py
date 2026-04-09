"""
api/tasks.py — A2A Task-Protokoll API.
Ersetzt Flask-Routen: /api/tasks, /api/a2a/tasks, /api/a2a/tasks/<id>/subscribe,
  /api/a2a/tasks/<id>/pushConfig, /api/a2a/tasks/<id>/input, /api/a2a/agents/<id>/card
"""
import asyncio
import base64
import json
import logging
import time
import uuid
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from services import get_services
from core.errors import AgentNotFoundError
from core.state import _TASKS, _tasks_lock

logger = logging.getLogger(__name__)
router = APIRouter(tags=["tasks"])

TERMINAL_STATES = {"completed", "failed", "canceled", "rejected", "input-required"}


class DispatchTaskRequest(BaseModel):
    sender_agent_id: str = Field(default="user")
    sender_agent_name: str = Field(default="User")
    recipient_agent_id: str = Field(..., min_length=1)
    recipient_agent_name: str = Field(default="")
    message: str = Field(..., min_length=1, max_length=32000)
    delegation_depth: int = Field(default=0, ge=0, le=10)
    callback_url: str | None = None


@router.post("/a2a/dispatch", status_code=202)
def dispatch_task(req: DispatchTaskRequest):
    services = get_services()
    agent = services.agents.get(req.recipient_agent_id)
    if not agent:
        raise HTTPException(404, f"Agent {req.recipient_agent_id} nicht gefunden")
    now = datetime.now()
    task = {
        "id": str(uuid.uuid4()),
        "sender_agent_id": req.sender_agent_id,
        "sender_agent_name": req.sender_agent_name,
        "recipient_agent_id": req.recipient_agent_id,
        "recipient_agent_name": agent["name"],
        "message": req.message,
        "skill_used": None,
        "result_text": None,
        "result_image": None,
        "error": None,
        "created_at": now.isoformat(),
        "completed_at": None,
        "timeout_at": (now + timedelta(seconds=1210)).isoformat(),
        "delegation_depth": req.delegation_depth,
        "callback_url": req.callback_url,
    }
    queued, pos = services.tasks.enqueue(task)
    return {
        "task_id": task["id"],
        "status": "queued" if queued else "submitted",
        "queue_position": pos,
    }


@router.get("/tasks/{task_id}")
def get_task(task_id: str):
    services = get_services()
    task = services.tasks.get(task_id)
    if not task:
        raise HTTPException(404, f"Task {task_id} nicht gefunden")
    return task


@router.get("/a2a/tasks")
def list_tasks(status: str | None = None):
    services = get_services()
    tasks = services.tasks.list_all()
    if status:
        tasks = [t for t in tasks if t.get("status") == status]
    return {"tasks": tasks, "count": len(tasks)}


@router.post("/a2a/tasks/{task_id}/cancel", status_code=204)
def cancel_task(task_id: str):
    services = get_services()
    success = services.tasks.cancel(task_id)
    if not success:
        raise HTTPException(404, f"Task {task_id} nicht gefunden oder bereits abgeschlossen")


@router.get("/events")
def get_events(since: int = 0):
    services = get_services()
    events = services.events.get_since(since)
    return {"events": events}


# ── A2A Erweiterte Task-Endpunkte ─────────────────────────────────────────────

@router.get("/a2a/tasks/{task_id}/subscribe")
async def subscribe_to_task(task_id: str):
    """
    SSE-Stream für Task-Status-Updates.
    Client bleibt connected bis Task terminal ist.
    """
    with _tasks_lock:
        task = _TASKS.get(task_id)
    if not task:
        raise HTTPException(404, f"Task {task_id} nicht gefunden")

    async def event_stream():
        # Initial state
        with _tasks_lock:
            current = dict(_TASKS.get(task_id, task))
        yield f"data: {json.dumps({'task': current})}\n\n"

        last_status = current.get("status")
        while last_status not in TERMINAL_STATES:
            await asyncio.sleep(1)
            with _tasks_lock:
                current = _TASKS.get(task_id)
            if not current:
                break
            new_status = current.get("status")
            if new_status != last_status:
                last_status = new_status
                yield f"data: {json.dumps({'statusUpdate': {'state': new_status}})}\n\n"

        # Finaler State senden
        with _tasks_lock:
            final = _TASKS.get(task_id, {})
        yield f"data: {json.dumps({'task': final})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


class PushConfigRequest(BaseModel):
    webhookUrl: str = Field(..., min_length=1)
    authentication: dict | None = None


@router.post("/a2a/tasks/{task_id}/pushConfig")
def create_push_config(task_id: str, req: PushConfigRequest):
    """Push-Notification Webhook für Task-Updates registrieren."""
    with _tasks_lock:
        task = _TASKS.get(task_id)
    if not task:
        raise HTTPException(404, f"Task {task_id} nicht gefunden")

    config = {
        "id": str(uuid.uuid4()),
        "taskId": task_id,
        "webhookUrl": req.webhookUrl,
        "authentication": req.authentication,
    }
    if "pushConfigs" not in task:
        task["pushConfigs"] = []
    task["pushConfigs"].append(config)
    return config


class TaskInputRequest(BaseModel):
    message: str = Field(default="")


@router.post("/a2a/tasks/{task_id}/input")
def task_input_required(task_id: str, req: TaskInputRequest):
    """Task in 'input-required' Status setzen (Agent wartet auf weitere Eingabe)."""
    with _tasks_lock:
        task = _TASKS.get(task_id)
    if not task:
        raise HTTPException(404, f"Task {task_id} nicht gefunden")

    task["status"] = "input-required"
    if "history" not in task:
        task["history"] = []
    task["history"].append({
        "role": "agent",
        "parts": [{"type": "text", "text": req.message}],
    })
    return task


@router.get("/a2a/agents/{agent_id}/card")
def get_extended_agent_card(agent_id: str):
    """Erweiterte A2A Agent Card (inkl. Security-Felder)."""
    services = get_services()
    agent = services.agents.get(agent_id)
    if not agent:
        raise HTTPException(404, f"Agent {agent_id} nicht gefunden")

    card = {
        "agent_id": agent.get("id"),
        "name": agent.get("name"),
        "description": agent.get("role", ""),
        "version": "1.0",
        "extended": True,
        "capabilities": {
            "skills": agent.get("skills", []),
            "providers": [agent.get("provider", "ollama")],
            "model": agent.get("model", ""),
            "max_tokens": agent.get("max_tokens"),
            "features": {
                "voice": bool(agent.get("voice")),
                "telegram": "telegram" in agent.get("skills", []),
                "gmail": "gmail" in agent.get("skills", []),
            },
        },
        "endpoints": {
            "chat": f"/api/chat",
            "task": "/api/a2a/dispatch",
        },
        "securitySchemes": {},
        "security": [],
    }
    return card
