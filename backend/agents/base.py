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
        self._rate_dev: float = 0.0  # EWMA der |inst_rate − rate|-Abweichung (Streuung)

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
            self._rate = round(min(max(self._rate, 0.01), 100.0), 4)
            # Streuung NACH dem Rate-Update: frische inst_rate gegen die NEUE Rate.
            self._rate_dev = round(0.7 * self._rate_dev + 0.3 * abs(inst_rate - self._rate), 4)
        self._last_op_ts = now
        self._rate = round(min(max(self._rate, 0.01), 100.0), 4)
        self._clock.tick_with_rate(self.agent_id, rate=self._rate)
        return self._clock.copy()

    @property
    def rate_stats(self) -> dict:
        """Momentanrate + Streuung + Variationskoeffizient (cv = dev/rate)."""
        rate = self._rate
        dev = self._rate_dev
        cv = dev / rate if rate else 0.0
        return {"rate": round(rate, 4), "dev": round(dev, 4), "cv": round(cv, 4)}

    def time_sense(self) -> str:
        """LLM-lesbarer Ein-Zeiler über die eigene Zeit-Verteilung, nicht nur den Punkt."""
        s = self.rate_stats
        cv = s["cv"]
        if cv < 0.25:
            wort = "stabil"
        elif cv < 0.75:
            wort = "schwankend"
        else:
            wort = "unstet"
        return f"~{s['rate']:.1f} ops/s, Streuung ±{s['dev']:.1f} ({wort})"

    @property
    def clock(self) -> CausalDilationClock:
        return self._clock.copy()

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "op_count": self._op_count,
            "clock": self._clock.to_dict(),
            "rate_stats": self.rate_stats,
        }

    # Hinweis: Die Streuung lebt bewusst NUR hier im Agenten, nicht in der CDC.
    # Die Clock (cdc.py) bleibt unberührt — kein neues Feld, kein Eingriff in
    # tick/merge/serialize. Grund: Das Wire-Format ist PQC-signiert und muss
    # kompatibel bleiben; die Streuung ist Agenten-Selbstwissen (wie breit ist
    # mein "meistens"), keine kausal übertragbare Nachrichteneigenschaft.
