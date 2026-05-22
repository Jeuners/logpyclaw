"""
backend/core/logging.py — Strukturiertes Logging mit SSE-fähigem LogBroadcaster.

- StreamHandler (stderr, INFO+)
- RotatingFileHandler (/tmp/agentclaw.log, DEBUG+)
- Format: "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
- get_logger(name) → logging.Logger
- LogBroadcaster: hält letzte 200 Zeilen im RAM, unterstützt asyncio.Queue-Subscriber
"""
from __future__ import annotations

import asyncio
import logging
import sys
import threading
from collections import deque
from logging.handlers import RotatingFileHandler

# ── Formatter ─────────────────────────────────────────────────────────────────

_FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%dT%H:%M:%S"

# ── LogBroadcaster (Singleton) ────────────────────────────────────────────────


class LogBroadcaster:
    """Hält die letzten 200 Log-Zeilen im RAM und verteilt neue Zeilen
    an asyncio.Queue-Subscriber (analog MissionStore.subscribe)."""

    _instance: LogBroadcaster | None = None

    def __init__(self, maxlen: int = 200) -> None:
        self._lock = threading.Lock()
        self._history: deque[str] = deque(maxlen=maxlen)
        self._subscribers: list[asyncio.Queue] = []

    @classmethod
    def get(cls) -> LogBroadcaster:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── Subscriber-API (wie MissionStore) ─────────────────────────────────────

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def get_history(self, n: int = 50) -> list[str]:
        with self._lock:
            items = list(self._history)
        return items[-n:]

    # ── Intern: neue Zeile einspeisen ─────────────────────────────────────────

    def emit(self, line: str) -> None:
        with self._lock:
            self._history.append(line)
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(line)
            except asyncio.QueueFull:
                pass


# ── Logging-Handler der den Broadcaster befüllt ───────────────────────────────


class _BroadcastHandler(logging.Handler):
    def __init__(self, broadcaster: LogBroadcaster) -> None:
        super().__init__()
        self._bc = broadcaster

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
            self._bc.emit(line)
        except Exception:
            self.handleError(record)


# ── Root-Logger Initialisierung ───────────────────────────────────────────────

_configured = False
_config_lock = threading.Lock()


def _configure_root() -> None:
    global _configured
    with _config_lock:
        if _configured:
            return
        _configured = True

    fmt = logging.Formatter(_FMT, datefmt=_DATEFMT)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # StreamHandler → stderr, INFO+
    if not any(isinstance(h, logging.StreamHandler) and h.stream is sys.stderr
               for h in root.handlers):
        sh = logging.StreamHandler(sys.stderr)
        sh.setLevel(logging.INFO)
        sh.setFormatter(fmt)
        root.addHandler(sh)

    # RotatingFileHandler → /tmp/agentclaw.log, DEBUG+
    try:
        fh = RotatingFileHandler(
            "/tmp/agentclaw.log",
            maxBytes=5 * 1024 * 1024,   # 5 MB
            backupCount=3,
            encoding="utf-8",
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:
        pass  # /tmp nicht beschreibbar → ignorieren

    # BroadcastHandler → LogBroadcaster
    bh = _BroadcastHandler(LogBroadcaster.get())
    bh.setLevel(logging.DEBUG)
    bh.setFormatter(fmt)
    root.addHandler(bh)


# ── Öffentliche API ───────────────────────────────────────────────────────────


def get_logger(name: str) -> logging.Logger:
    """Gibt einen konfigurierten Logger zurück.

    Beim ersten Aufruf wird der Root-Logger mit StreamHandler,
    RotatingFileHandler und BroadcastHandler eingerichtet.
    """
    _configure_root()
    return logging.getLogger(name)
