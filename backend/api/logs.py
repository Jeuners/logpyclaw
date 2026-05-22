"""
backend/api/logs.py — SSE-Endpoint für Live-Log-Streaming.

GET /api/logs
  - Sendet zuerst die letzten 50 Zeilen als History
  - Dann neue Zeilen live as they come
  - Heartbeat alle 5 Sekunden
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from backend.core.logging import LogBroadcaster

router = APIRouter()


@router.get("/logs")
async def log_stream(request: Request):
    """SSE-Stream aller Log-Zeilen (History + Live)."""
    broadcaster = LogBroadcaster.get()
    queue = broadcaster.subscribe()

    async def stream():
        try:
            # 1. History: letzte 50 Zeilen senden
            for line in broadcaster.get_history(50):
                payload = json.dumps({"line": line})
                yield f"data: {payload}\n\n"

            # 2. Live-Stream + Heartbeat alle 5 s
            while True:
                if await request.is_disconnected():
                    break
                try:
                    line = await asyncio.wait_for(queue.get(), timeout=5.0)
                    payload = json.dumps({"line": line})
                    yield f"data: {payload}\n\n"
                except TimeoutError:
                    # Heartbeat
                    yield ": heartbeat\n\n"
        finally:
            broadcaster.unsubscribe(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
