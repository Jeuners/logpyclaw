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

from backend.api.agent_select import build_content, is_allowed, resolve_agent
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

    cfg = get_settings()
    # Agent wählbar; ohne 'agent' greift der Server-Default (BRIDGE_DEFAULT_AGENT).
    # Kurzform "alice"/"martin" oder voll "agent:claude".
    requested = body.get("agent") or cfg.bridge_default_agent or "alice"
    agent_id = resolve_agent(requested, conductor)
    if agent_id is None:
        return JSONResponse({"error": f"unknown agent '{requested}'"}, status_code=400)
    if not is_allowed(agent_id):
        return JSONResponse(
            {"error": f"agent '{requested}' not permitted (siehe PROVIDER_MODELS)"},
            status_code=403,
        )

    # Inject-/System-Prompt: aus der Anfrage (inject/system) oder Server-Default.
    inject = body.get("inject") or body.get("system") or (cfg.bridge_default_inject or None)
    content = build_content(message, inject)

    log.info(
        "🌐 BRIDGE ← dillenberg.net [%s] agent=%s inject=%s %r",
        client_ip, agent_id, bool(inject), message[:120],
    )

    import asyncio
    import json
    import time

    from backend.core.protocol import Message, external_ref, new_mission_id

    mission_id = new_mission_id()
    conductor.store.register_mission(
        mission_id,
        {
            "mission_id": mission_id,
            "title": f"web-bridge:{agent_id}",
            "state": "running",
            "started_at": time.time(),
            "source": "dillenberg.net",
        },
    )
    msg = Message.request(
        mission_id=mission_id,
        sender=external_ref("dillenberg"),
        recipient=agent_id,
        content=content,
    )

    async def stream():
        final_state = "failed"
        # subscribe() direkt vor dem try, damit unsubscribe() im finally
        # GARANTIERT läuft — auch wenn dispatch() oder der Client-Abbruch
        # (GeneratorExit/CancelledError) zwischendrin etwas wirft.
        queue = conductor.store.subscribe(mission_id)
        # Heartbeats halten Verbindung + Widget am Leben, während ein langsamer
        # Agent (z. B. Claude, oft 10-40s) noch denkt. Ohne sie sieht das Widget
        # nur Stille → blinkender Cursor ohne Text. Großzügiges Gesamt-Limit.
        HEARTBEAT = 4.0
        DEADLINE = 180.0
        try:
            async def run():
                await conductor.dispatch(msg)

            asyncio.create_task(run())
            yield ": connected\n\n"  # sofort Bytes → Widget wartet nicht auf Stille
            waited = 0.0
            while waited < DEADLINE:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT)
                except TimeoutError:
                    waited += HEARTBEAT
                    yield ": keepalive\n\n"  # SSE-Kommentar (vom Widget ignoriert)
                    continue
                ev = event.get("event")
                if ev == "message" and event.get("type") == "response":
                    result = event.get("payload", {}).get("result", "")
                    log.info("🌐 BRIDGE → dillenberg.net [%d chars] %r", len(result), result[:120])
                    final_state = "completed"
                    yield f"data: {json.dumps({'chunk': result, 'done': True})}\n\n"
                    break
                if ev == "task_completed":
                    final_state = "completed"
                    break
                if ev in ("task_failed", "task_timeout"):
                    final_state = ev.replace("task_", "")
                    yield f"data: {json.dumps({'error': 'Agent-Fehler', 'done': True})}\n\n"
                    break
            else:
                final_state = "timeout"
                log.warning("🌐 BRIDGE deadline for mission %s", mission_id)
                yield f"data: {json.dumps({'error': 'Zeitüberschreitung', 'done': True})}\n\n"
        finally:
            conductor.store.unsubscribe(mission_id, queue)
            conductor.store.update_mission(mission_id, state=final_state, finished_at=time.time())

    return StreamingResponse(stream(), media_type="text/event-stream")
