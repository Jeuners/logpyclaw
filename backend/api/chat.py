import asyncio
import json
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

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
    from backend.core.protocol import new_mission_id, external_ref, Message
    import time

    mission_id = new_mission_id()
    conductor.store.register_mission(mission_id, {
        "mission_id": mission_id,
        "title": f"chat:{agent_id}",
        "state": "running",
        "started_at": time.time(),
    })

    queue = conductor.store.subscribe(mission_id)
    msg = Message.request(
        mission_id=mission_id,
        sender=external_ref("user"),
        recipient=agent_id,
        content=message,
    )

    async def stream():
        # Dispatch starten (im Hintergrund)
        async def run():
            resp = await conductor.dispatch(msg)
            conductor.store.update_mission(mission_id, state="completed")

        task = asyncio.create_task(run())

        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {json.dumps(event)}\n\n"
                    if event.get("event") in ("task_completed", "task_failed", "task_timeout"):
                        break
                except asyncio.TimeoutError:
                    yield "data: {\"event\":\"timeout\"}\n\n"
                    break
        finally:
            conductor.store.unsubscribe(mission_id, queue)

    return StreamingResponse(stream(), media_type="text/event-stream")
