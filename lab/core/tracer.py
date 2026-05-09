"""
lab/core/tracer.py — Live-Event-Stream pro Mission.

Jede Subscription bekommt eigene Queue. Verlustfrei innerhalb der Queue-Größe.
Wird vom SSE-Endpoint angezapft + von Tests.
"""
from __future__ import annotations
import asyncio
import time
from threading import RLock
from typing import Any

# mission_id → list[asyncio.Queue]  — Subscriber-Queues
_subscribers: dict[str, list[asyncio.Queue]] = {}
_lock = RLock()


def subscribe(mission_id: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=500)
    with _lock:
        _subscribers.setdefault(mission_id, []).append(q)
    return q


def unsubscribe(mission_id: str, q: asyncio.Queue) -> None:
    with _lock:
        if mission_id in _subscribers:
            try:
                _subscribers[mission_id].remove(q)
            except ValueError:
                pass
            if not _subscribers[mission_id]:
                del _subscribers[mission_id]


def emit(_mission: str, _event: str, **data: Any) -> None:
    """Event an alle Subscriber der Mission senden. Non-blocking, drop bei voller Queue.
    Erste zwei Args bewusst mit Underscore-Prefix, damit `mission_id`/`event` als
    keyword-args durchgereicht werden können ohne Namenskonflikt."""
    payload = {"event": _event, "ts": time.time(), **data}
    with _lock:
        queues = list(_subscribers.get(_mission, []))
    for q in queues:
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            pass  # Subscriber zu langsam — wir droppen lieber als zu blocken
