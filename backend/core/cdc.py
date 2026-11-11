"""
backend/core/cdc.py — Causal-Dilation Clock (V, D, τ)-Tripel (§3.4).

Jede interne Message in LogpyClaw v3 trägt eine CDC-Instanz.
Keine optionale Metadata-Ergänzung — CDC ist Pflicht.

4-Relations-Klassifikator:
  ORDERED             — kausal und temporal geordnet
  CAUSAL_DRIFT        — kausal geordnet, temporal divergent
  CONCURRENT_DRIFT    — nebenläufig mit Divergenz
  INCONSISTENT        — V und τ widersprechen sich (Clock-Korruption)
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum


class CDCRelation(Enum):
    ORDERED = "causally_and_temporally_ordered"
    CAUSAL_DRIFT = "causally_ordered_temporally_divergent"
    CONCURRENT_DRIFT = "concurrent_with_divergence"
    INCONSISTENT = "inconsistent"


@dataclass
class CausalDilationClock:
    """(V, D, τ)-Tripel — Vector-Clock + Momentanrate + Eigenzeit pro Agent.

    vector   : logische Kausalordnung (Lamport-style)
    dilation : zuletzt beobachtete Momentanrate (ops/s) pro Agent —
               KEINE kumulative Größe, sondern eine Beobachtung
    tau      : kumulative Eigenzeit τ pro Agent (Σ op_weights),
               monoton wachsend, Basis für relate()
    """

    vector: dict[str, int] = field(default_factory=dict)
    dilation: dict[str, float] = field(default_factory=dict)
    tau: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_lock", threading.Lock())

    # ── Buchhaltung ───────────────────────────────────────────────────────────

    def tick(self, agent_id: str, op_weight: float = 1.0) -> None:
        """Interner Reasoning-Schritt — erhöht V und τ. dilation bleibt unberührt."""
        if not agent_id:
            raise ValueError("agent_id darf nicht leer sein")
        if op_weight < 0:
            raise ValueError("op_weight muss ≥ 0 sein")
        with self._lock:  # type: ignore[attr-defined]
            self.vector[agent_id] = self.vector.get(agent_id, 0) + 1
            self.tau[agent_id] = self.tau.get(agent_id, 0.0) + float(op_weight)

    def tick_with_rate(self, agent_id: str, rate: float) -> CausalDilationClock:
        """Tick (τ += 1.0) + Momentanrate (ops/s) in dilation speichern. Gibt self zurück."""
        self.tick(agent_id, op_weight=1.0)
        if rate > 0:
            with self._lock:  # type: ignore[attr-defined]
                self.dilation[agent_id] = round(rate, 4)
        return self

    def merge(self, other: CausalDilationClock) -> CausalDilationClock:
        """Merge beim Empfang einer Nachricht. Gibt self zurück (fluent).

        vector und tau: elementweises Max (monoton wachsende Größen).
        dilation: Es gewinnt die Rate des Clocks mit dem höheren
        vector-Eintrag für diesen Agenten (neueste Beobachtung) —
        bei Gleichstand Max. Ein Max über Raten wäre falsch: einmal
        schnell hieße sonst für immer schnell.
        """
        with self._lock:  # type: ignore[attr-defined]
            # dilation zuerst — braucht den Vektor-Stand VOR dem Max-Merge
            for a, d in other.dilation.items():
                own_v = self.vector.get(a, 0)
                oth_v = other.vector.get(a, 0)
                if a not in self.dilation or oth_v > own_v:
                    self.dilation[a] = d
                elif oth_v == own_v:
                    self.dilation[a] = max(self.dilation[a], d)
            for a, v in other.vector.items():
                self.vector[a] = max(self.vector.get(a, 0), v)
            for a, t in other.tau.items():
                self.tau[a] = max(self.tau.get(a, 0.0), t)
        return self

    # ── Frame-Transformation (§3.3) ───────────────────────────────────────────

    @staticmethod
    def transform(
        source_dilation: float,
        source_agent: str,
        target_agent: str,
        gamma: Mapping[tuple[str, str], float],
    ) -> float:
        """Heuristisches γ_ij · τ_i → τ_j (skalare Approximation)."""
        if source_agent == target_agent:
            return source_dilation
        return source_dilation * gamma.get((source_agent, target_agent), 1.0)

    # ── 4-Relations-Klassifikator (§3.4) ──────────────────────────────────────

    def relate(
        self,
        other: CausalDilationClock,
        gamma: Mapping[tuple[str, str], float] | None = None,
        drift_tolerance: float = 0.0,
    ) -> CDCRelation:
        """Klassifiziert das Verhältnis zweier Clocks (§3.4).

        Der temporale Vergleich läuft über τ (kumulative Eigenzeit) —
        Momentanraten (dilation) sind keine kausal geordnete Größe.
        gamma bleibt aus API-Kompatibilität in der Signatur, wird hier
        aber nicht angewandt: pro Key wird die Eigenzeit DESSELBEN
        Agenten verglichen, eine Frame-Transformation ist dafür nicht
        nötig. γ_ij wird auf höherer Ebene (faction_protocol) für die
        Drift-Klassifikation genutzt.
        """
        del gamma  # bewusst ungenutzt — siehe Docstring
        v_le = self._vec_le(self.vector, other.vector)
        v_ge = self._vec_le(other.vector, self.vector)
        v_eq = v_le and v_ge
        v_concurrent = (not v_le) and (not v_ge)

        d_le = self._dil_le(self.tau, other.tau, drift_tolerance)
        d_ge = self._dil_le(other.tau, self.tau, drift_tolerance)

        if v_le and not v_eq:
            return CDCRelation.ORDERED if d_le else CDCRelation.CAUSAL_DRIFT
        if v_eq:
            return CDCRelation.ORDERED if (d_le and d_ge) else CDCRelation.INCONSISTENT
        if v_concurrent:
            return CDCRelation.ORDERED if (d_le and d_ge) else CDCRelation.CONCURRENT_DRIFT
        return CDCRelation.ORDERED if d_ge else CDCRelation.CAUSAL_DRIFT

    # ── LLM-Lesbarkeit ────────────────────────────────────────────────────────

    def llm_summary(self) -> str:
        """Kompakte Lesart für LLM-Kontext: 'alice:fast(ez=4,rate=2.60,tau=12.0)'.

        Das Geschwindigkeits-Gefühl (fast/slow/…) basiert auf der
        Momentanrate (dilation), tau wird zusätzlich angezeigt.
        """
        if not self.dilation:
            return "no temporal data"
        parts = []
        for agent, val in sorted(self.dilation.items(), key=lambda x: -x[1]):
            ez = self.vector.get(agent, 0)
            if val >= 2.0:
                feel = "fast"
            elif val >= 0.8:
                feel = "normal"
            elif val >= 0.3:
                feel = "slow"
            else:
                feel = "dilated"
            tau = self.tau.get(agent, 0.0)
            parts.append(f"{agent}:{feel}(ez={ez},rate={val:.2f},tau={tau:.1f})")
        return " | ".join(parts)

    def relate_str(self, other: CausalDilationClock) -> str:
        return self.relate(other).value

    # ── Serialisierung ────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        with self._lock:  # type: ignore[attr-defined]
            return {
                "vector": dict(self.vector),
                "dilation": dict(self.dilation),
                "tau": dict(self.tau),
                "wall_ts": time.time(),
            }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_dict(cls, d: Mapping) -> CausalDilationClock:
        # tau tolerant lesen — Legacy-Payloads (vor dem tau/rate-Split) haben keins
        return cls(
            vector=dict(d.get("vector", {})),
            dilation={k: float(v) for k, v in dict(d.get("dilation", {})).items()},
            tau={k: float(v) for k, v in dict(d.get("tau", {})).items()},
        )

    @classmethod
    def from_json(cls, s: str) -> CausalDilationClock:
        return cls.from_dict(json.loads(s))

    def copy(self) -> CausalDilationClock:
        with self._lock:  # type: ignore[attr-defined]
            return CausalDilationClock(
                vector=dict(self.vector),
                dilation=dict(self.dilation),
                tau=dict(self.tau),
            )

    # ── Hilfs-Methoden ────────────────────────────────────────────────────────

    @staticmethod
    def _vec_le(a: Mapping[str, int], b: Mapping[str, int]) -> bool:
        keys = set(a) | set(b)
        return all(a.get(k, 0) <= b.get(k, 0) for k in keys)

    @staticmethod
    def _dil_le(
        a: Mapping[str, float],
        b: Mapping[str, float],
        tolerance: float,
    ) -> bool:
        keys = set(a) | set(b)
        return all(a.get(k, 0.0) <= b.get(k, 0.0) + tolerance for k in keys)
