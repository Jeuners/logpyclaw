"""
backend/agents/base.py — AsyncAgent Basisklasse.

Jeder Agent in AgentClaw v3 hat:
- eine eigene CausalDilationClock
- handle(msg) → verarbeitet eingehende CDC-Message, gibt Response/Error zurück
- advance_clock() → merged eingehende Clock + eigener Tick
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Optional

from backend.core.cdc import CausalDilationClock
from backend.core.protocol import Message


class AsyncAgent(ABC):
    def __init__(self, agent_id: str, name: str) -> None:
        self.agent_id = agent_id
        self.name = name
        self._clock = CausalDilationClock()
        self._started_at: float = 0.0
        self._op_count: int = 0

    @abstractmethod
    async def handle(self, msg: Message) -> Message:
        """Verarbeite eine eingehende CDC-Message. Gibt Response oder Error zurück."""

    async def start(self) -> None:
        self._started_at = time.time()

    async def stop(self) -> None:
        pass

    def advance_clock(self, incoming: Optional[CausalDilationClock] = None) -> CausalDilationClock:
        """Merge eingehende Clock + eigener Tick. Gibt Snapshot zurück."""
        if incoming:
            self._clock.merge(incoming)
        self._op_count += 1
        age = time.time() - self._started_at if self._started_at else 1.0
        rate = self._op_count / max(age, 0.001)
        self._clock.tick_with_rate(self.agent_id, rate=round(rate, 4))
        return self._clock.copy()

    @property
    def clock(self) -> CausalDilationClock:
        return self._clock.copy()

    def to_dict(self) -> dict:
        return {
            "agent_id":  self.agent_id,
            "name":      self.name,
            "op_count":  self._op_count,
            "clock":     self._clock.to_dict(),
        }
