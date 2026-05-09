"""
core/time_provider.py — Eigenzeit-Abstraktion für AgentClaw-Agenten.

Hintergrund: Dillenberg, "Time Dilation in LLM Agent Systems" (Working Draft,
06 May 2026), §3 (Agent Proper Time) und §4.3 (Operationalisation).

Ein TimeProvider ersetzt direkte ``datetime.now()``-Aufrufe in Agenten-Pfaden
durch eine frame-bewusste Schnittstelle. Default-Verhalten (WallClockProvider)
ist verhaltensgleich zu ``datetime.now()`` — die Erweiterung ist additiv.

Begriffe (frei nach §3.2):
- reference_now : Eigenzeit-Now des Agenten (kann == wall_now sein)
- parent_reference_now : reference_now des Elternagenten beim Spawn (Frame-Erbe)
- dilation_factor : heuristisches γ relativ zum Orchestrator (1.0 = ungedehnt)
- tau : monoton wachsende Proper-Time-Akkumulation (Σ Operations-Gewichte)
- frame_id : eindeutige ID des aktuellen Eigenzeit-Frames

Discipline (§4.3): Agent-Code soll ``datetime.now()`` nicht direkt aufrufen.
Stattdessen den injizierten ``TimeProvider`` benutzen. Verstöße sind
review-fähige Ausnahmen, kein Hard-Fail.
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping, Optional


@dataclass
class Frame:
    """Eigenzeit-Kontext eines Agenten zum Spawn-Zeitpunkt.

    Wird mit einem Task/Dispatch mitgeführt und im Logging persistiert,
    damit Replays in der Frame des ursprünglichen Entscheids stattfinden.

    ``metadata`` ist ein freier Tag-Container für Frame-Quellen
    (z.B. ``{"kind": "heartbeat"}`` oder ``{"kind": "interactive"}``) —
    nutzbar für §4.2 Source-2-Trennung von Heartbeat- vs. Chat-Frames.
    """
    frame_id: str
    agent_id: str
    parent_frame_id: Optional[str] = None
    parent_reference_now: Optional[datetime] = None
    dilation_factor: float = 1.0
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "frame_id": self.frame_id,
            "agent_id": self.agent_id,
            "parent_frame_id": self.parent_frame_id,
            "parent_reference_now": (
                self.parent_reference_now.isoformat()
                if self.parent_reference_now is not None else None
            ),
            "dilation_factor": self.dilation_factor,
            "metadata": dict(self.metadata),
        }


class TimeProvider:
    """Basis-Interface. Konkrete Implementierungen siehe ``WallClockProvider``.

    Verhalten:
    - ``now()`` liefert die Eigenzeit-Now des Agenten. In WallClockProvider == wall_now().
    - ``wall_now()`` liefert die System-Wallclock (für Logging/Re-Sync).
    - ``dilation()`` liefert den aktuellen γ-Faktor.
    - ``tick(weight)`` schreibt einen Reasoning-Schritt fort (τ += weight).
    - ``fork(agent_id, dilation)`` liefert einen Child-Provider für Sub-Agenten.

    Threadsafe per Default (Tick + Read).
    """

    def now(self) -> datetime:
        raise NotImplementedError

    def wall_now(self) -> datetime:
        raise NotImplementedError

    def dilation(self) -> float:
        raise NotImplementedError

    def tick(self, weight: float = 1.0) -> float:
        raise NotImplementedError

    def fork(
        self,
        agent_id: str,
        dilation_factor: Optional[float] = None,
    ) -> "TimeProvider":
        raise NotImplementedError

    @property
    def frame(self) -> Frame:
        raise NotImplementedError

    @property
    def tau(self) -> float:
        raise NotImplementedError


class WallClockProvider(TimeProvider):
    """Default-Implementierung. Eigenzeit == Wallclock, γ konfigurierbar.

    Diese Klasse verändert das Laufzeitverhalten gegenüber direktem
    ``datetime.now()`` nicht — sie führt nur die Buchhaltung (frame, tau)
    zusätzlich. Ein Aufruf von ``tick()`` ist optional; kein Pfad in
    AgentClaw bricht, wenn er fehlt.
    """

    def __init__(
        self,
        agent_id: str = "orchestrator",
        dilation_factor: float = 1.0,
        parent_frame_id: Optional[str] = None,
        parent_reference_now: Optional[datetime] = None,
        frame_id: Optional[str] = None,
        metadata: Optional[Mapping[str, object]] = None,
    ):
        self._frame = Frame(
            frame_id=frame_id or uuid.uuid4().hex,
            agent_id=agent_id,
            parent_frame_id=parent_frame_id,
            parent_reference_now=parent_reference_now,
            dilation_factor=float(dilation_factor),
            metadata=dict(metadata) if metadata else {},
        )
        self._tau: float = 0.0
        self._lock = threading.Lock()

    # ── Time accessors ────────────────────────────────────────────────────────
    def now(self) -> datetime:
        return datetime.now()

    def wall_now(self) -> datetime:
        return datetime.now()

    def dilation(self) -> float:
        return self._frame.dilation_factor

    # ── Eigenzeit-Buchhaltung ─────────────────────────────────────────────────
    def tick(self, weight: float = 1.0) -> float:
        if weight < 0:
            raise ValueError("tick weight must be non-negative")
        with self._lock:
            self._tau += float(weight)
            return self._tau

    @property
    def tau(self) -> float:
        with self._lock:
            return self._tau

    @property
    def frame(self) -> Frame:
        return self._frame

    # ── Frame-Vererbung an Sub-Agenten ────────────────────────────────────────
    def fork(
        self,
        agent_id: str,
        dilation_factor: Optional[float] = None,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> "WallClockProvider":
        """Liefert einen Child-Provider mit geerbtem parent_reference_now.

        Der Child startet mit τ = 0 — Eigenzeit ist frame-lokal (§3.2, P2).

        ``metadata`` überschreibt/ergänzt die Tags des Parents (kind, source, …).
        """
        merged = dict(self._frame.metadata)
        if metadata:
            merged.update(metadata)
        return WallClockProvider(
            agent_id=agent_id,
            dilation_factor=(
                self._frame.dilation_factor if dilation_factor is None
                else float(dilation_factor)
            ),
            parent_frame_id=self._frame.frame_id,
            parent_reference_now=self.now(),
            metadata=merged,
        )


# ── Modul-Level Default-Provider ──────────────────────────────────────────────
# Wird genutzt, wenn kein expliziter Provider injiziert wurde. So bleibt
# bestehender Code (der ``datetime.now()`` direkt benutzt) unverändert
# funktionsfähig, kann aber stückweise migriert werden.

_default_provider: Optional[TimeProvider] = None
_default_lock = threading.Lock()


def get_default_provider() -> TimeProvider:
    """Lazy-singleton — verhindert Import-Reihenfolge-Probleme."""
    global _default_provider
    with _default_lock:
        if _default_provider is None:
            _default_provider = WallClockProvider(agent_id="orchestrator")
        return _default_provider


def set_default_provider(provider: TimeProvider) -> None:
    """Test-Hook: erlaubt Injection eines Fake-Providers in Tests."""
    global _default_provider
    with _default_lock:
        _default_provider = provider


# ── γ-Heuristik (§3.3) ────────────────────────────────────────────────────────
# Skalare Dilation-Faktoren relativ zum Orchestrator-Frame (γ=1.0).
# Werte sind grobe Erfahrungswerte — siehe §3.3, „We leave such refinements as
# future work; the scalar form is sufficient for the purpose of this paper."
_DILATION_BY_PROVIDER: dict[str, float] = {
    "ollama":     1.0,   # lokal, kleines Modell — Referenzrahmen
    "openrouter": 4.0,   # frontier remote — typisch ~4× langsamer pro Step
    "openai":     3.0,
    "anthropic":  4.0,
    "google":     3.0,
    "lmstudio":   1.5,
}

# Modellgrößen-Multiplikator (über Provider-Default hinaus)
_MODEL_HINTS: list[tuple[str, float]] = [
    ("opus",     1.8),
    ("sonnet",   1.2),
    ("haiku",    0.6),
    ("frontier", 1.5),
    ("70b",      1.6),
    ("405b",     2.5),
    ("e4b",      0.9),
    ("e2b",      0.7),
    ("turbo",    0.8),
]


def estimate_dilation(agent: Mapping[str, object]) -> float:
    """Heuristische γ für einen Agenten (Provider+Model-Hint).

    Bewusst grob: §3.3 selbst beschreibt das als skalare First-Approximation.
    Liefert 1.0 wenn der Agent nicht erkannt wird (sicherer Default = kein
    Drift gegenüber Orchestrator).
    """
    if not agent:
        return 1.0
    provider = str(agent.get("provider") or "").lower()
    model = str(agent.get("model") or "").lower()
    base = _DILATION_BY_PROVIDER.get(provider, 1.0)
    multiplier = 1.0
    for hint, mult in _MODEL_HINTS:
        if hint in model:
            multiplier = mult
            break
    return round(base * multiplier, 4)
