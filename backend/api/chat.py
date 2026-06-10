import asyncio
import json
import os
import time

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.config import get_settings
from backend.core.protocol import Message, external_ref, new_mission_id

router = APIRouter()

# Vision: lokales Ollama-Modell für Bildanalyse (gemma4:e4b — multimodal, liest
# auch Text im Bild zuverlässig; per ENV VISION_MODEL überschreibbar).
VISION_MODEL = os.environ.get("VISION_MODEL", "gemma4:e4b")


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

    msg = Message.request(
        mission_id=mission_id,
        sender=external_ref("user"),
        recipient=agent_id,
        content=message,
    )
    root_task_id = msg.task_id  # nur auf diesen Root-Task warten

    async def stream():
        # subscribe() direkt vor dem try, damit unsubscribe() im finally
        # GARANTIERT läuft — auch wenn dispatch() oder der Client-Abbruch
        # (GeneratorExit/CancelledError) zwischendrin etwas wirft.
        queue = conductor.store.subscribe(mission_id)
        try:
            async def run():
                await conductor.dispatch(msg)
                conductor.store.update_mission(mission_id, state="completed")

            asyncio.create_task(run())
            yield f"data: {json.dumps({'event': 'init', 'root_task_id': root_task_id, 'mission_id': mission_id})}\n\n"

            total = 0
            max_wait = 1200  # 20 min — synchron zum Conductor-Timeout (900s) + Puffer
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


class VisionRequest(BaseModel):
    image: str            # base64 (optional mit data:-Präfix)
    prompt: str = ""
    model: str | None = None
    agent_id: str | None = None   # gewählter Agent → seine Persona "sieht" das Bild


@router.post("/vision")
async def vision(req: VisionRequest, request: Request):
    """Lässt den gewählten Agenten ein Bild lokal über ein Ollama-Vision-Modell
    (Default gemma4:e4b) analysieren — mit der Persona des Agenten via /api/chat,
    sodass die Antwort aus dem Gespräch kommt statt aus einem anonymen One-Shot."""
    image = req.image or ""
    if image.startswith("data:"):
        image = image.split(",", 1)[-1]  # data:image/png;base64,XXXX → XXXX
    if not image.strip():
        return {"error": "Kein Bild übergeben."}
    prompt = (req.prompt or "").strip() or "Beschreibe dieses Bild ausführlich und präzise auf Deutsch."
    model = req.model or VISION_MODEL
    cfg = get_settings()

    # Persona des gewählten Agenten übernehmen, damit DER AGENT das Bild sieht.
    soul = ""
    if req.agent_id:
        agent = request.app.state.conductor.get_agent(req.agent_id)
        soul = (getattr(agent, "soul", "") or "").strip()

    messages = []
    if soul:
        messages.append({"role": "system", "content": soul})
    messages.append({"role": "user", "content": prompt, "images": [image]})

    try:
        async with httpx.AsyncClient(timeout=240.0) as client:
            r = await client.post(
                f"{cfg.ollama_url}/api/chat",
                json={"model": model, "messages": messages, "stream": False},
            )
            r.raise_for_status()
            data = r.json()
        return {"result": (data.get("message", {}).get("content") or "").strip(), "model": model}
    except Exception as e:
        return {"error": f"Vision-Fehler ({model}): {e}"}
