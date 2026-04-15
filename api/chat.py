"""
api/chat.py — Chat-API Endpoints.
POST /api/chat       — klassisch, blockierend, vollständige Antwort
GET  /api/chat/stream — Streaming via SSE, Token-by-Token
"""
import asyncio
import json
import logging
from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from services import get_services
from core.errors import AgentNotFoundError
from core.thread_pools import CHAT_POOL

logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    agent_id: str = Field(..., min_length=1, max_length=64)
    message: str = Field(..., min_length=1, max_length=32000)
    images: list[str] | None = None
    attachment_path: str | None = None


class ChatResponse(BaseModel):
    reply: str
    skill: str | None = None
    image: str | None = None
    agent_id: str
    a2a_dispatches: list = []


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """
    Chat-Nachricht an einen Agenten senden.
    handle_message() ist blockierend (LLM-Calls via requests mit 360s Timeout).
    Wird daher im ThreadPoolExecutor ausgeführt, um den asyncio Event-Loop
    von NiceGUI/FastAPI nicht zu blockieren.
    """
    services = get_services()
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            CHAT_POOL,  # Dedizierter Chat-Pool — nie durch Heartbeat blockiert
            lambda: services.chat.handle_message(
                req.agent_id,
                req.message,
                images=req.images,
                attachment_path=req.attachment_path,
            )
        )
        return ChatResponse(**result)
    except AgentNotFoundError as e:
        raise HTTPException(404, e.message)
    except Exception as e:
        logger.exception("Chat-Fehler für Agent %s", req.agent_id)
        raise HTTPException(500, str(e))


@router.get("/chat/stream")
async def chat_stream(
    agent_id: str = Query(..., min_length=1, max_length=64),
    message: str = Query(..., min_length=1, max_length=32000),
):
    """
    Streaming-Chat via SSE (Server-Sent Events).
    Jeder Token wird sofort als Event gesendet — keine langen Wartezeiten.

    SSE-Event-Format:
      data: {"chunk": "..."}\n\n       — Token-Chunk
      data: {"done": true}\n\n         — Abschluss
      data: {"error": "..."}\n\n       — Fehler
    """
    services = get_services()

    async def event_generator():
        a2a_dispatches: list = []
        chain_steps: list = []
        display_reply: str | None = None
        try:
            async for chunk in services.chat.stream_message(agent_id, message):
                if isinstance(chunk, dict):
                    # Sentinel-Dicts vom Ende des Generators
                    if chunk.get("__a2a__"):
                        a2a_dispatches = chunk.get("a2a_dispatches", [])
                        display_reply = chunk.get("display_reply")
                        continue
                    if chunk.get("__chain__"):
                        chain_steps = chunk.get("chain_steps", [])
                        display_reply = chunk.get("reply")
                        continue
                    # Reguläre Stream-Chunks: content oder thinking
                    if "thinking" in chunk:
                        payload = json.dumps({"thinking": chunk["thinking"]}, ensure_ascii=False)
                        yield f"data: {payload}\n\n"
                        continue
                    if "content" in chunk:
                        payload = json.dumps({"chunk": chunk["content"]}, ensure_ascii=False)
                        yield f"data: {payload}\n\n"
                        continue
                else:
                    # Backwards-compat: plain-string chunks
                    payload = json.dumps({"chunk": chunk}, ensure_ascii=False)
                    yield f"data: {payload}\n\n"
        except AgentNotFoundError as e:
            yield f"data: {json.dumps({'error': e.message})}\n\n"
        except Exception as e:
            logger.exception("Streaming-Fehler für Agent %s", agent_id)
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        finally:
            done_payload = {"done": True}
            if a2a_dispatches:
                done_payload["a2a_dispatches"] = a2a_dispatches
                done_payload["display_reply"] = display_reply
            if chain_steps:
                done_payload["chain_steps"] = chain_steps
                done_payload["display_reply"] = display_reply
            yield f"data: {json.dumps(done_payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/chat/context/{agent_id}")
def get_chat_context(agent_id: str):
    """SPA-Agentenwechsel: Agent-Daten + History-HTML ohne Page-Reload."""
    import html as html_mod
    from ui.pages.chat import _build_history_html, _build_topbar_html
    services = get_services()
    agents = services.agents.list_all()
    agent = next((a for a in agents if a["id"] == agent_id), None)
    if not agent:
        raise HTTPException(404, f"Agent {agent_id} nicht gefunden")

    messages_html = _build_history_html(agent_id)
    topbar_html = _build_topbar_html(agent, agent_id, agent.get("color", "#00e676"))
    agent_light = {
        "id": agent["id"],
        "name": agent.get("name", ""),
        "voice": agent.get("voice", ""),
        "color": agent.get("color", "#00e676"),
        "model": agent.get("model", ""),
        "role": agent.get("role", ""),
    }
    return {"agent": agent_light, "messages_html": messages_html, "topbar_html": topbar_html}


@router.get("/whatsapp/events")
async def whatsapp_events():
    """SSE-Stream für eingehende WhatsApp-Nachrichten (reaktiv via DB-Watcher)."""
    import queue as q_mod
    from services.whatsapp_watcher import add_subscriber, remove_subscriber

    client_q: q_mod.SimpleQueue = q_mod.SimpleQueue()
    add_subscriber(client_q)

    async def event_stream():
        try:
            while True:
                try:
                    event = client_q.get_nowait()
                    yield f"data: {json.dumps(event)}\n\n"
                except q_mod.Empty:
                    yield ": keepalive\n\n"
                await asyncio.sleep(1)
        finally:
            remove_subscriber(client_q)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
