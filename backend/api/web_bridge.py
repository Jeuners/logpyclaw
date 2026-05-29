"""
backend/api/web_bridge.py — Web-Bridge Compatibility Layer.

Stellt /ext/dilles/v1/* für dillenberg.net bereit.
Health-Endpoint: logpyclaw-health.sh auf c2.webbinder.de pollt alle 2 Minuten.
Chat-Stream: wird in Phase 5 an Alice angebunden.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse

from backend.config import get_settings

router = APIRouter(prefix="/ext/dilles/v1")
log = logging.getLogger("logpyclaw.bridge")


def _check_token(token: str | None) -> bool:
    expected = get_settings().web_bridge_token
    return not expected or token == expected


@router.get("/health")
async def health():
    return {
        "ok": True,
        "service": "logpyclaw-web-bridge",
        "version": "3.0.0",
        "token_configured": bool(get_settings().web_bridge_token),
    }


@router.post("/chat/stream")
async def chat_stream(
    request: Request,
    x_logpyclaw_token: str | None = Header(default=None),
):
    client_ip = request.client.host if request.client else "?"
    if not _check_token(x_logpyclaw_token):
        log.warning("🌐 BRIDGE 401 from %s — bad/missing token", client_ip)
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    body = await request.json()
    message = body.get("message", "")
    conductor = request.app.state.conductor

    log.info("🌐 BRIDGE ← dillenberg.net [%s] %r", client_ip, message[:120])

    # Route to Alice (web bridge agent)
    agent_id = "agent:alice"
    agent = conductor.get_agent(agent_id)
    if agent is None:
        agent_id = "agent:echo"
        log.warning("🌐 BRIDGE: alice nicht verfügbar → fallback echo")

    import asyncio
    import json
    import time

    from backend.core.protocol import Message, external_ref, new_mission_id

    mission_id = new_mission_id()
    conductor.store.register_mission(
        mission_id,
        {
            "mission_id": mission_id,
            "title": "web-bridge",
            "state": "running",
            "started_at": time.time(),
            "source": "dillenberg.net",
        },
    )
    msg = Message.request(
        mission_id=mission_id,
        sender=external_ref("dillenberg"),
        recipient=agent_id,
        content=message,
    )

    async def stream():
        final_state = "failed"
        # subscribe() direkt vor dem try, damit unsubscribe() im finally
        # GARANTIERT läuft — auch wenn dispatch() oder der Client-Abbruch
        # (GeneratorExit/CancelledError) zwischendrin etwas wirft.
        queue = conductor.store.subscribe(mission_id)
        try:
            async def run():
                await conductor.dispatch(msg)

            asyncio.create_task(run())
            while True:
                event = await asyncio.wait_for(queue.get(), timeout=30.0)
                if event.get("event") == "message" and event.get("type") == "response":
                    result = event.get("payload", {}).get("result", "")
                    log.info("🌐 BRIDGE → dillenberg.net [%d chars] %r", len(result), result[:120])
                    final_state = "completed"
                    yield f"data: {json.dumps({'chunk': result, 'done': True})}\n\n"
                    break
                if event.get("event") == "task_completed":
                    final_state = "completed"
                    break
                if event.get("event") in ("task_failed", "task_timeout"):
                    final_state = event["event"].replace("task_", "")
                    break
        except TimeoutError:
            final_state = "timeout"
            log.warning("🌐 BRIDGE timeout for mission %s", mission_id)
        finally:
            conductor.store.unsubscribe(mission_id, queue)
            conductor.store.update_mission(mission_id, state=final_state, finished_at=time.time())

    return StreamingResponse(stream(), media_type="text/event-stream")
