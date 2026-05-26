"""
api/chrome_ws.py — Chrome Extension WebSocket Bridge.

Endpunkte:
  GET  /api/chrome/ws       — WebSocket (Extension verbindet sich hier)
  POST /api/chrome/command  — Skill sendet Befehl, wartet auf Antwort
  GET  /api/chrome/status   — Verbindungsstatus
"""
import asyncio
import json
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/chrome", tags=["chrome"])
logger = logging.getLogger(__name__)

# Aktive Extension-Verbindung (nur eine gleichzeitig)
_active_ws: Optional[WebSocket] = None
# Offene Request-Queues: request_id → asyncio.Queue
_pending: dict[str, asyncio.Queue] = {}

COMMAND_TIMEOUT = 30  # Sekunden


@router.websocket("/ws")
async def chrome_ws_endpoint(websocket: WebSocket):
    """Langlebige WebSocket-Verbindung zur Chrome Extension."""
    global _active_ws
    await websocket.accept()
    _active_ws = websocket
    logger.info("Chrome Extension verbunden")

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Ungültiges JSON von Extension: %r", raw[:100])
                continue

            # Keepalive-Pings ignorieren
            if msg.get("type") == "ping":
                continue

            request_id = msg.get("request_id")
            if request_id and request_id in _pending:
                await _pending[request_id].put(msg)
            else:
                logger.debug("Unbekannte request_id von Extension: %s", request_id)

    except WebSocketDisconnect:
        logger.info("Chrome Extension getrennt")
    except Exception as e:
        logger.error("Chrome WS Fehler: %s", e)
    finally:
        if _active_ws is websocket:
            _active_ws = None


class CommandRequest(BaseModel):
    command: str   # z.B. "screenshot", "navigate", "click", "fill_form", "get_content", "evaluate_js"
    params: dict = {}


@router.post("/command")
async def send_chrome_command(req: CommandRequest):
    """
    Sendet einen Befehl an die Chrome Extension und wartet auf das Ergebnis.
    Wird vom chrome_browser Skill über requests.post() aufgerufen.
    """
    if _active_ws is None:
        raise HTTPException(503, "Chrome Extension nicht verbunden")

    request_id = str(uuid.uuid4())
    queue: asyncio.Queue = asyncio.Queue()
    _pending[request_id] = queue

    try:
        payload = {"request_id": request_id, "command": req.command, **req.params}
        await _active_ws.send_text(json.dumps(payload))
        logger.debug("Chrome Command gesendet: %s (id=%s)", req.command, request_id[:8])

        result = await asyncio.wait_for(queue.get(), timeout=COMMAND_TIMEOUT)
        return result

    except asyncio.TimeoutError:
        raise HTTPException(504, f"Chrome Command '{req.command}' Timeout nach {COMMAND_TIMEOUT}s")
    except Exception as e:
        raise HTTPException(500, f"Chrome Command Fehler: {e}")
    finally:
        _pending.pop(request_id, None)


@router.get("/status")
def chrome_status():
    """Verbindungsstatus der Chrome Extension."""
    return {"connected": _active_ws is not None}
