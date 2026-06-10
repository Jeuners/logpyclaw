"""
backend/agents/base.py — AsyncAgent Basisklasse.

Jeder Agent in LogpyClaw v3 hat:
- eine eigene CausalDilationClock
- handle(msg) → verarbeitet eingehende CDC-Message, gibt Response/Error zurück
- advance_clock() → merged eingehende Clock + eigener Tick
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod

from backend.core.cdc import CausalDilationClock
from backend.core.protocol import Message


class AsyncAgent(ABC):
    def __init__(self, agent_id: str, name: str) -> None:
        self.agent_id = agent_id
        self.name = name
        self._clock = CausalDilationClock()
        self._started_at: float = 0.0
        self._op_count: int = 0
        self._last_op_ts: float = 0.0  # Wall-Time des letzten Ops (EWMA-Basis)
        self._rate: float = 1.0  # EWMA-geglättete Momentanrate (ops/s)

    @abstractmethod
    async def handle(self, msg: Message) -> Message:
        """Verarbeite eine eingehende CDC-Message. Gibt Response oder Error zurück."""

    async def start(self) -> None:
        self._started_at = time.time()

    async def stop(self) -> None:
        pass

    def advance_clock(self, incoming: CausalDilationClock | None = None) -> CausalDilationClock:
        """Merge eingehende Clock + eigener Tick. Gibt Snapshot zurück.

        Die Rate ist eine EWMA über die Momentanrate (1/dt seit letztem Op) —
        kein Lifetime-Durchschnitt, sonst driften idle Agents gegen 0.
        """
        if incoming:
            self._clock.merge(incoming)
        self._op_count += 1
        now = time.time()
        if self._last_op_ts:
            dt = max(now - self._last_op_ts, 1e-3)
            inst_rate = 1.0 / dt
            self._rate = 0.7 * self._rate + 0.3 * inst_rate
        self._last_op_ts = now
        self._rate = round(min(max(self._rate, 0.01), 100.0), 4)
        self._clock.tick_with_rate(self.agent_id, rate=self._rate)
        return self._clock.copy()

    @property
    def clock(self) -> CausalDilationClock:
        return self._clock.copy()

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "op_count": self._op_count,
            "clock": self._clock.to_dict(),
        }
