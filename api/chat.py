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
                # Sentinel-Dict vom Ende des Generators
                if isinstance(chunk, dict):
                    if chunk.get("__a2a__"):
                        a2a_dispatches = chunk.get("a2a_dispatches", [])
                        display_reply = chunk.get("display_reply")
                    elif chunk.get("__chain__"):
                        chain_steps = chunk.get("chain_steps", [])
                        display_reply = chunk.get("reply")
                    continue
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
