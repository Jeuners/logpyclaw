"""
backend/core/cdc.py — Causal-Dilation Clock (V,D)-Tupel (§3.4).

Jede interne Message in AgentClaw v3 trägt eine CDC-Instanz.
Keine optionale Metadata-Ergänzung — CDC ist Pflicht.

4-Relations-Klassifikator:
  ORDERED             — kausal und temporal geordnet
  CAUSAL_DRIFT        — kausal geordnet, temporal divergent
  CONCURRENT_DRIFT    — nebenläufig mit Divergenz
  INCONSISTENT        — V und D widersprechen sich (Clock-Korruption)
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Optional


class CDCRelation(Enum):
    ORDERED          = "causally_and_temporally_ordered"
    CAUSAL_DRIFT     = "causally_ordered_temporally_divergent"
    CONCURRENT_DRIFT = "concurrent_with_divergence"
    INCONSISTENT     = "inconsistent"


@dataclass
class CausalDilationClock:
    """(V, D)-Tupel — Vector-Clock + Eigenzeit pro Agent.

    vector   : logische Kausalordnung (Lamport-style)
    dilation : kumulative Eigenzeit τ pro Agent (Σ op_weights)
    """
    vector: dict[str, int]   = field(default_factory=dict)
    dilation: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_lock", threading.Lock())

    # ── Buchhaltung ───────────────────────────────────────────────────────────

    def tick(self, agent_id: str, op_weight: float = 1.0) -> None:
        """Interner Reasoning-Schritt — erhöht V und D."""
        if not agent_id:
            raise ValueError("agent_id darf nicht leer sein")
        if op_weight < 0:
            raise ValueError("op_weight muss ≥ 0 sein")
        with self._lock:  # type: ignore[attr-defined]
            self.vector[agent_id] = self.vector.get(agent_id, 0) + 1
            self.dilation[agent_id] = self.dilation.get(agent_id, 0.0) + float(op_weight)

    def tick_with_rate(self, agent_id: str, rate: float) -> "CausalDilationClock":
        """Tick + Eigenzeit-Rate (ops/s) in dilation speichern. Gibt self zurück."""
        self.tick(agent_id, op_weight=1.0)
        if rate > 0:
            with self._lock:  # type: ignore[attr-defined]
                self.dilation[agent_id] = round(rate, 4)
        return self

    def merge(self, other: "CausalDilationClock") -> "CausalDilationClock":
        """Max-Merge beim Empfang einer Nachricht. Gibt self zurück (fluent)."""
        with self._lock:  # type: ignore[attr-defined]
            for a, v in other.vector.items():
                self.vector[a] = max(self.vector.get(a, 0), v)
            for a, d in other.dilation.items():
                self.dilation[a] = max(self.dilation.get(a, 0.0), d)
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
        other: "CausalDilationClock",
        gamma: Optional[Mapping[tuple[str, str], float]] = None,
        drift_tolerance: float = 0.0,
    ) -> CDCRelation:
        gamma = gamma or {}
        v_le = self._vec_le(self.vector, other.vector)
        v_ge = self._vec_le(other.vector, self.vector)
        v_eq = v_le and v_ge
        v_concurrent = (not v_le) and (not v_ge)

        d_le = self._dil_le(self.dilation, other.dilation, gamma, drift_tolerance)
        d_ge = self._dil_le(other.dilation, self.dilation, gamma, drift_tolerance)

        if v_le and not v_eq:
            return CDCRelation.ORDERED if d_le else CDCRelation.CAUSAL_DRIFT
        if v_eq:
            return CDCRelation.ORDERED if (d_le and d_ge) else CDCRelation.INCONSISTENT
        if v_concurrent:
            return CDCRelation.ORDERED if (d_le and d_ge) else CDCRelation.CONCURRENT_DRIFT
        return CDCRelation.ORDERED if d_ge else CDCRelation.CAUSAL_DRIFT

    # ── LLM-Lesbarkeit ────────────────────────────────────────────────────────

    def llm_summary(self) -> str:
        """Kompakte Lesart für LLM-Kontext: 'alice:fast(ez=4,rate=2.6) | bob:slow(ez=2,rate=0.3)'"""
        if not self.dilation:
            return "no temporal data"
        parts = []
        for agent, val in sorted(self.dilation.items(), key=lambda x: -x[1]):
            ez = self.vector.get(agent, 0)
            if val >= 2.0:   feel = "fast"
            elif val >= 0.8: feel = "normal"
            elif val >= 0.3: feel = "slow"
            else:            feel = "dilated"
            parts.append(f"{agent}:{feel}(ez={ez},rate={val:.2f})")
        return " | ".join(parts)

    def relate_str(self, other: "CausalDilationClock") -> str:
        return self.relate(other).value

    # ── Serialisierung ────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        with self._lock:  # type: ignore[attr-defined]
            return {
                "vector": dict(self.vector),
                "dilation": dict(self.dilation),
                "wall_ts": time.time(),
            }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_dict(cls, d: Mapping) -> "CausalDilationClock":
        return cls(
            vector=dict(d.get("vector", {})),
            dilation={k: float(v) for k, v in dict(d.get("dilation", {})).items()},
        )

    @classmethod
    def from_json(cls, s: str) -> "CausalDilationClock":
        return cls.from_dict(json.loads(s))

    def copy(self) -> "CausalDilationClock":
        with self._lock:  # type: ignore[attr-defined]
            return CausalDilationClock(
                vector=dict(self.vector),
                dilation=dict(self.dilation),
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
        gamma: Mapping[tuple[str, str], float],
        tolerance: float,
    ) -> bool:
        keys = set(a) | set(b)
        return all(a.get(k, 0.0) <= b.get(k, 0.0) + tolerance for k in keys)
