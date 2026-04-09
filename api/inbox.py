"""
api/inbox.py — Agent Inbox API.
"""
import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from services import get_services
from storage.agents import load_agents, save_agents

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/agents", tags=["inbox"])


class InboxItem(BaseModel):
    task: str
    added_by: str = "User"
    sender_agent_id: str = ""
    priority: int = 0


@router.get("/{agent_id}/inbox")
async def get_agent_inbox(agent_id: str):
    agents = load_agents()
    agent = next((a for a in agents if a["id"] == agent_id), None)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent nicht gefunden")
    inbox = agent.get("inbox", [])
    inbox.sort(key=lambda x: (x.get("priority", 0), x.get("added_at", "")))
    return inbox


@router.post("/{agent_id}/inbox", status_code=201)
async def add_inbox_item(agent_id: str, body: InboxItem):
    task_text = body.task.strip()
    if not task_text:
        raise HTTPException(status_code=400, detail="Kein Task-Text")

    agents = load_agents()
    idx = next((i for i, a in enumerate(agents) if a["id"] == agent_id), None)
    if idx is None:
        raise HTTPException(status_code=404, detail="Agent nicht gefunden")

    item = {
        "id": str(uuid.uuid4()),
        "task": task_text,
        "added_by": body.added_by,
        "sender_agent_id": body.sender_agent_id,
        "added_at": datetime.now().isoformat(),
        "priority": body.priority,
    }
    agents[idx].setdefault("inbox", []).append(item)
    save_agents(agents)

    # Notify via EventService
    try:
        sc = get_services()
        sc.events.emit("inbox_updated", {
            "agent_id": agent_id,
            "inbox": agents[idx]["inbox"],
        })
    except Exception:
        pass

    return item


@router.delete("/{agent_id}/inbox/{item_id}")
async def delete_inbox_item(agent_id: str, item_id: str):
    agents = load_agents()
    idx = next((i for i, a in enumerate(agents) if a["id"] == agent_id), None)
    if idx is None:
        raise HTTPException(status_code=404, detail="Agent nicht gefunden")

    inbox = agents[idx].get("inbox", [])
    agents[idx]["inbox"] = [i for i in inbox if i["id"] != item_id]
    save_agents(agents)

    try:
        sc = get_services()
        sc.events.emit("inbox_updated", {
            "agent_id": agent_id,
            "inbox": agents[idx]["inbox"],
        })
    except Exception:
        pass

    return {"ok": True}


@router.delete("/{agent_id}/inbox")
async def clear_agent_inbox(agent_id: str):
    agents = load_agents()
    idx = next((i for i, a in enumerate(agents) if a["id"] == agent_id), None)
    if idx is None:
        raise HTTPException(status_code=404, detail="Agent nicht gefunden")

    agents[idx]["inbox"] = []
    save_agents(agents)

    try:
        sc = get_services()
        sc.events.emit("inbox_updated", {"agent_id": agent_id, "inbox": []})
    except Exception:
        pass

    return {"ok": True}
