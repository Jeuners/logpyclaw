"""
backend/api/web_bridge.py — Web-Bridge Compatibility Layer.

Stellt /ext/dilles/v1/* für dillenberg.net bereit.
Health-Endpoint: agentclaw-health.sh auf c2.webbinder.de pollt alle 2 Minuten.
Chat-Stream: wird in Phase 5 an Alice angebunden.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse

router = APIRouter(prefix="/ext/dilles/v1")

_TOKEN = os.environ.get("WEB_BRIDGE_TOKEN", "")


def _check_token(token: str | None) -> bool:
    return not _TOKEN or token == _TOKEN


@router.get("/health")
async def health():
    return {
        "ok": True,
        "service": "agentclaw-web-bridge",
        "version": "3.0.0",
        "token_configured": bool(_TOKEN),
    }


@router.post("/chat/stream")
async def chat_stream(
    request: Request,
    x_agentclaw_token: str | None = Header(default=None),
):
    if not _check_token(x_agentclaw_token):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    body = await request.json()
    message = body.get("message", "")
    conductor = request.app.state.conductor

    # Route to Alice (web bridge agent)
    agent_id = "agent:alice"
    agent = conductor.get_agent(agent_id)
    if agent is None:
        agent_id = "agent:echo"

    import asyncio
    import json
    import time

    from backend.core.protocol import Message, external_ref, new_mission_id

    mission_id = new_mission_id()
    conductor.store.register_mission(mission_id, {
        "mission_id": mission_id,
        "title": "web-bridge",
        "state": "running",
        "started_at": time.time(),
        "source": "dillenberg.net",
    })
    queue = conductor.store.subscribe(mission_id)
    msg = Message.request(
        mission_id=mission_id,
        sender=external_ref("dillenberg"),
        recipient=agent_id,
        content=message,
    )

    async def stream():
        async def run():
            await conductor.dispatch(msg)

        asyncio.create_task(run())
        try:
            while True:
                event = await asyncio.wait_for(queue.get(), timeout=30.0)
                if event.get("event") == "message" and event.get("type") == "response":
                    result = event.get("payload", {}).get("result", "")
                    yield f"data: {json.dumps({'chunk': result, 'done': True})}\n\n"
                    break
                if event.get("event") in ("task_completed", "task_failed", "task_timeout"):
                    break
        finally:
            conductor.store.unsubscribe(mission_id, queue)

    return StreamingResponse(stream(), media_type="text/event-stream")
