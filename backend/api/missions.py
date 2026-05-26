from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()


class MissionRequest(BaseModel):
    title: str
    agent_id: str
    content: str


@router.post("/missions")
async def start_mission(req: MissionRequest, request: Request):
    conductor = request.app.state.conductor
    return await conductor.start_mission(req.title, req.agent_id, req.content)


@router.get("/missions")
async def list_missions(request: Request):
    conductor = request.app.state.conductor
    return conductor.store.list_missions()


@router.delete("/missions/stale")
async def cleanup_stale_missions(request: Request, min_age_sec: float = 600.0):
    """Löscht hängende Missionen (default: running/timeout/failed älter als 10min)."""
    conductor = request.app.state.conductor
    deleted = conductor.store.delete_stale_missions(min_age_sec=min_age_sec)
    return {"deleted": deleted, "count": len(deleted)}


@router.delete("/missions/{mission_id}")
async def delete_mission(mission_id: str, request: Request):
    conductor = request.app.state.conductor
    if not conductor.store.delete_mission(mission_id):
        raise HTTPException(404, "Mission not found")
    return {"deleted": mission_id}


@router.get("/missions/{mission_id}/verify")
async def verify_mission(mission_id: str, request: Request):
    """Prüft Hash-Chain + ML-DSA-65 Signaturen aller Messages der Mission."""
    conductor = request.app.state.conductor
    result = conductor.store.verify_chain(mission_id)
    if result["count"] == 0:
        raise HTTPException(404, "Mission not found or empty")
    return result


@router.get("/missions/{mission_id}/trace")
async def get_trace(mission_id: str, request: Request):
    conductor = request.app.state.conductor
    trace = conductor.store.get_trace(mission_id)
    if not trace:
        raise HTTPException(404, "Mission not found")
    return [m.to_dict() for m in trace]


@router.get("/missions/{mission_id}/spacetime")
async def get_spacetime(mission_id: str, request: Request):
    conductor = request.app.state.conductor
    messages = conductor.store.get_trace(mission_id)
    if not messages:
        raise HTTPException(404, "Mission not found")

    agents_seen: list[str] = []
    nodes = []
    edges = []

    for msg in messages:
        if msg.sender not in agents_seen:
            agents_seen.append(msg.sender)
        if msg.recipient not in agents_seen:
            agents_seen.append(msg.recipient)

        sender_ez = msg.clock.vector.get(msg.sender, 0)
        nodes.append(
            {
                "id": msg.msg_id,
                "agent": msg.sender,
                "eigenzeit": sender_ez,
                "wall_ts": msg.timestamp,
                "type": msg.type.value,
                "label": f"{msg.type.value[:3].upper()} → {msg.recipient}",
            }
        )

    for i in range(len(messages) - 1):
        a = messages[i]
        b = messages[i + 1]
        relation = a.clock.relate_str(b.clock)
        edges.append(
            {
                "id": f"e_{a.msg_id}",
                "from_agent": a.sender,
                "to_agent": b.sender,
                "from_ez": a.clock.vector.get(a.sender, 0),
                "to_ez": b.clock.vector.get(b.sender, 0),
                "type": b.type.value,
                "relation": relation,
                "wall_ts": b.timestamp,
            }
        )

    return {
        "mission_id": mission_id,
        "agents": agents_seen,
        "nodes": nodes,
        "edges": edges,
        "drift_segments": [e for e in edges if "drift" in e["relation"]],
        "total_messages": len(messages),
    }
