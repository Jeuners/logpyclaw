"""
backend/services/initiative.py — konfigurierbarer Initiative-Loop.

Lässt Agenten von sich aus regelmäßig Missionen anstoßen (conductor.initiate).
Bewusst NUR die Mechanik — keine LLM-getriebene Spontanität. Asyncio statt
Threads; pro Entry ein Task mit sleep-then-await-Loop.
"""

from __future__ import annotations

import asyncio

from backend.core.logging import get_logger

log = get_logger(__name__)


class InitiativeService:
    """Startet pro konfiguriertem Entry einen Eigenzeit-Loop.

    Entry-Form: {"agent_id": str, "recipient": str, "content": str,
                 "every_sec": float, "enabled": bool}.
    """

    # DoS-Schutz: zu kleine every_sec-Werte (Fehlkonfiguration) werden hierauf
    # geclampt. Als Klassenkonstante ausgelegt, damit Tests sie überschreiben können.
    MIN_INTERVAL: float = 5.0

    def __init__(self, conductor, entries: list[dict]) -> None:
        self._conductor = conductor
        self._entries = entries
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        for entry in self._entries:
            if not entry.get("enabled", True):
                continue
            self._tasks.append(asyncio.create_task(self._run_entry(entry)))

    async def _run_entry(self, entry: dict) -> None:
        # every_sec auf MIN_INTERVAL clampen (DoS-Schutz gegen Fehlkonfiguration).
        every = max(float(entry.get("every_sec", self.MIN_INTERVAL)), self.MIN_INTERVAL)
        agent_id = entry["agent_id"]
        recipient = entry["recipient"]
        content = entry["content"]
        # sleep-then-await garantiert von selbst, dass pro Entry maximal EINE
        # Initiative gleichzeitig läuft — der nächste Loop-Durchlauf beginnt erst,
        # wenn die vorige initiate() abgeschlossen ist. Kein Drift-Ausgleich nötig.
        while True:
            await asyncio.sleep(every)
            try:
                await self._conductor.initiate(agent_id, recipient, content)
            except Exception as e:
                # fail-soft: Einzelfehler beendet den Loop nicht.
                log.warning("Initiative %s → %s fehlgeschlagen: %s", agent_id, recipient, e)

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()

    def to_dict(self) -> dict:
        """Status-Export — für späteres API-Exposing (hier ohne Router)."""
        return {
            "running": any(not t.done() for t in self._tasks),
            "entries": [
                {
                    "agent_id": e["agent_id"],
                    "recipient": e["recipient"],
                    "every_sec": max(
                        float(e.get("every_sec", self.MIN_INTERVAL)), self.MIN_INTERVAL
                    ),
                    "enabled": e.get("enabled", True),
                }
                for e in self._entries
            ],
        }
