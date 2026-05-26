import asyncio
import json
import time

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.core.protocol import Message, external_ref, new_mission_id

router = APIRouter()


class ChatRequest(BaseModel):
    agent_id: str
    message: str


@router.post("/chat")
async def chat(req: ChatRequest, request: Request):
    conductor = request.app.state.conductor
    result = await conductor.start_mission(
        title=f"chat:{req.agent_id}",
        start_agent_id=req.agent_id,
        content=req.message,
    )
    return result


@router.get("/chat/stream")
async def chat_stream(agent_id: str, message: str, request: Request):
    """SSE-Streaming: sendet Mission-Events live."""
    conductor = request.app.state.conductor
    mission_id = new_mission_id()
    conductor.store.register_mission(
        mission_id,
        {
            "mission_id": mission_id,
            "title": f"chat:{agent_id}",
            "state": "running",
            "started_at": time.time(),
        },
    )

    queue = conductor.store.subscribe(mission_id)
    msg = Message.request(
        mission_id=mission_id,
        sender=external_ref("user"),
        recipient=agent_id,
        content=message,
    )
    root_task_id = msg.task_id  # nur auf diesen Root-Task warten

    async def stream():
        async def run():
            await conductor.dispatch(msg)
            conductor.store.update_mission(mission_id, state="completed")

        asyncio.create_task(run())
        yield f"data: {json.dumps({'event': 'init', 'root_task_id': root_task_id, 'mission_id': mission_id})}\n\n"

        total = 0
        max_wait = 1200  # 20 min — synchron zum Conductor-Timeout (900s) + Puffer
        try:
            while total < max_wait:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=5.0)
                    yield f"data: {json.dumps(event)}\n\n"
                    # Nur schließen wenn der ROOT-Task fertig ist, nicht Sub-Tasks
                    if event.get("event") in ("task_completed", "task_failed", "task_timeout"):
                        if event.get("task_id") == root_task_id:
                            break
                except TimeoutError:
                    total += 5
                    yield 'data: {"event":"heartbeat"}\n\n'
            else:
                yield 'data: {"event":"timeout"}\n\n'
        finally:
            conductor.store.unsubscribe(mission_id, queue)

    return StreamingResponse(stream(), media_type="text/event-stream")
