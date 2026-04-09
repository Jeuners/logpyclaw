"""
api/m2m.py — Martin-to-Martin Peer-Netzwerk API.
"""
import logging
from datetime import datetime
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from services import get_services

logger = logging.getLogger(__name__)
router = APIRouter(tags=["m2m"])


class AddNodeRequest(BaseModel):
    name: str = Field(..., min_length=1)
    url: str = Field(..., min_length=1)


@router.get("/.well-known/martin-agent.json")
def agent_discovery():
    """A2A Discovery Endpoint."""
    from storage.providers import load_providers
    providers = load_providers()
    m2m = providers.get("martin_m2m", {})
    return {
        "node_id": m2m.get("node_id", ""),
        "node_name": m2m.get("node_name", "AgentClaw"),
        "public_url": m2m.get("public_url", ""),
        "version": "2.0",
    }


@router.get("/api/m2m/nodes")
def list_nodes():
    services = get_services()
    return {"nodes": services.m2m.list_nodes()}


@router.post("/api/m2m/nodes", status_code=201)
def add_node(req: AddNodeRequest):
    services = get_services()
    node = services.m2m.add_node(req.model_dump())
    return node


@router.delete("/api/m2m/nodes/{node_id}", status_code=204)
def remove_node(node_id: str):
    services = get_services()
    services.m2m.remove_node(node_id)


@router.post("/api/m2m/nodes/{node_id}/sync")
def sync_node(node_id: str):
    services = get_services()
    try:
        nodes = services.m2m.list_nodes()
        node = next((n for n in nodes if n["id"] == node_id), None)
        if not node:
            raise HTTPException(404, f"Node {node_id} nicht gefunden")
        services.m2m._refresh_node_cache(node)
        return {"status": "synced"}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/api/m2m/agents")
def m2m_all_agents():
    """Lokale + gecachte Remote-Agents zusammengeführt (für UI und A2A-Discovery)."""
    from storage.agents import load_agents
    from storage.nodes import load_nodes
    local = [dict(a, remote=False, node_id="local") for a in load_agents()]
    remote = []
    for node in load_nodes():
        for card in node.get("agent_cache", []):
            remote.append({
                **card,
                "remote": True,
                "node_id": node.get("node_id", node.get("id", "")),
                "node_name": node.get("node_name", node.get("id", "")),
                "node_url": node.get("base_url", ""),
                "node_online": node.get("status") == "online",
                "mention_prefix": f"@{node.get('alias', node.get('id', ''))}::",
            })
    return {"local": local, "remote": remote}


@router.post("/api/m2m/dispatch", status_code=202)
async def m2m_dispatch(request: Request):
    """Eingehender Task von einem Remote-Node — wird lokal eingereiht."""
    import uuid as _uuid
    from datetime import timedelta
    data = await request.json()
    services = get_services()

    target_name = data.get("target_agent_name", "")
    message = data.get("message", "")
    callback_url = data.get("origin_callback_url", "")
    sender_name = data.get("sender_agent_name", "Remote")
    origin_node = data.get("origin_node", "unknown")
    origin_task_id = data.get("task_id", str(_uuid.uuid4()))

    from storage.agents import load_agents
    agents = load_agents()
    target = next((a for a in agents if a["name"].lower() == target_name.lower()), None)
    if not target:
        raise HTTPException(404, f"Agent '{target_name}' nicht gefunden")

    now = datetime.now()
    task = {
        "id": str(_uuid.uuid4()),
        "sender_agent_id": f"remote::{origin_node}",
        "sender_agent_name": f"{sender_name} ({origin_node})",
        "recipient_agent_id": target["id"],
        "recipient_agent_name": target["name"],
        "message": message,
        "skill_used": None,
        "result_text": None,
        "result_image": None,
        "error": None,
        "created_at": now.isoformat(),
        "completed_at": None,
        "timeout_at": (now + timedelta(seconds=300)).isoformat(),
        "m2m": True,
        "callback_url": callback_url,
        "origin_task_id": origin_task_id,
        "remote_node": origin_node,
        "delegation_depth": data.get("delegation_depth", 1),
    }
    queued, pos = services.tasks.enqueue(task)
    logger.info("M2M Task eingehend: %s → @%s (queued=%s)", origin_node, target["name"], queued)
    return {"ok": True, "task_id": task["id"], "queued": queued}


@router.post("/api/m2m/callback")
async def m2m_callback(request: Request):
    """Empfängt Task-Ergebnisse von Remote-Nodes."""
    data = await request.json()
    services = get_services()
    task_id = data.get("task_id")
    if task_id:
        services.events.emit_task_result(
            task_id, data.get("agent_id", ""),
            data.get("result_text"), data.get("result_image"),
            data.get("status", "completed"), data.get("error")
        )
    return {"status": "received"}
