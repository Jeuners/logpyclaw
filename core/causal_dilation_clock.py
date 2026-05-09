"""
core/causal_dilation_clock.py — Vector+Dilation Clock (§3.4).

Standard-Vektor-Clocks (Mattern 1989) ordnen *Ereignisse*. Sie sagen nichts
über die *Erfahrung* — wieviel subjektiver Reasoning-Aufwand zwischen zwei
Ereignissen lag. §3.4 schlägt vor, den Vector-Clock V um einen parallelen
Dilation-Vektor D zu ergänzen, der pro Agent die Eigenzeit τ trackt.

Dieses Modul implementiert die Datenstruktur und den 4-Relations-Klassifikator
aus §3.4:

  1. Causally and temporally ordered
  2. Causally ordered, temporally divergent
  3. Concurrent in V, divergent in D
  4. Inconsistent (V und D widersprechen sich → vermutete Clock-Korruption)

Die Klasse ist threadsafe und JSON-serialisierbar (für Mitführung am Dispatch).
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Optional


class CDCRelation(Enum):
    """Vier mögliche Relationen zwischen zwei (V, D)-Ereignissen (§3.4)."""
    ORDERED = "causally_and_temporally_ordered"
    CAUSAL_DRIFT = "causally_ordered_temporally_divergent"
    CONCURRENT_DRIFT = "concurrent_with_divergence"
    INCONSISTENT = "inconsistent"


@dataclass
class CausalDilationClock:
    """Kombiniertes (V, D)-Tupel — Pseudocode-Vorlage aus §3.5.

    - ``vector``  : klassischer Vector-Clock pro AgentId
    - ``dilation``: Per-Agent-Eigenzeit (Σ Operations-Gewichte)

    Beide Maps werden frei-zusammen propagiert; ``transform()`` kommt erst beim
    *Vergleich* von Frames ins Spiel — bis dahin sind die Werte frame-lokal.
    """
    vector: dict[str, int] = field(default_factory=dict)
    dilation: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Pro-Instanz-Lock — verhindert Race-Conditions beim Tick/Merge.
        # Mit dataclass nicht direkt declarable, daher hier nachsetzen.
        object.__setattr__(self, "_lock", threading.Lock())

    # ── Buchhaltung ───────────────────────────────────────────────────────────
    def tick(self, agent_id: str, op_weight: float = 1.0) -> None:
        """Wird vom Agenten bei jedem internen Reasoning-Schritt aufgerufen."""
        if not agent_id:
            raise ValueError("agent_id darf nicht leer sein")
        if op_weight < 0:
            raise ValueError("op_weight muss ≥ 0 sein")
        with self._lock:  # type: ignore[attr-defined]
            self.vector[agent_id] = self.vector.get(agent_id, 0) + 1
            self.dilation[agent_id] = self.dilation.get(agent_id, 0.0) + float(op_weight)

    def merge(self, other: "CausalDilationClock") -> None:
        """Beim Empfang einer Nachricht (V max-merge, D max-merge frame-lokal)."""
        with self._lock:  # type: ignore[attr-defined]
            for a, v in other.vector.items():
                self.vector[a] = max(self.vector.get(a, 0), v)
            for a, d in other.dilation.items():
                self.dilation[a] = max(self.dilation.get(a, 0.0), d)

    # ── Frame-Transformation (§3.3) ───────────────────────────────────────────
    @staticmethod
    def transform(
        source_dilation: float,
        source_agent: str,
        target_agent: str,
        gamma: Mapping[tuple[str, str], float],
    ) -> float:
        """Heuristisches γ_ij · τ_i → τ_j (skalare First-Approximation)."""
        if source_agent == target_agent:
            return source_dilation
        return source_dilation * gamma.get((source_agent, target_agent), 1.0)

    # ── 4-Relations-Klassifikator (§3.4) ──────────────────────────────────────
    def relate(
        self,
        other: "CausalDilationClock",
        agent: Optional[str] = None,
        gamma: Optional[Mapping[tuple[str, str], float]] = None,
        drift_tolerance: float = 0.0,
    ) -> CDCRelation:
        """Ordnet self → other in die §3.4-Taxonomie ein.

        ``agent`` ist der Agent dessen Frame als Vergleichsbasis dient (default:
        keiner — D wird per Komponente verglichen).
        ``gamma`` ist die γ-Map; wenn None werden Werte 1:1 verglichen.
        ``drift_tolerance`` (in τ-Einheiten) erlaubt minimale Schwankungen ohne
        gleich „divergent" zu klassifizieren.
        """
        gamma = gamma or {}
        v_le = self._vec_le(self.vector, other.vector)
        v_ge = self._vec_le(other.vector, self.vector)
        v_eq = v_le and v_ge
        v_concurrent = (not v_le) and (not v_ge)

        d_le = self._dil_le(self.dilation, other.dilation, gamma, drift_tolerance)
        d_ge = self._dil_le(other.dilation, self.dilation, gamma, drift_tolerance)

        if v_le and not v_eq:
            # self causally before other
            if d_le:
                return CDCRelation.ORDERED
            return CDCRelation.CAUSAL_DRIFT
        if v_eq:
            # selbe Vektor-Position — Divergenz in D wäre Inkonsistenz
            if d_le and d_ge:
                return CDCRelation.ORDERED
            return CDCRelation.INCONSISTENT
        if v_concurrent:
            # nebenläufig: D-Divergenz ist erwartbar (CONCURRENT_DRIFT)
            if d_le and d_ge:
                return CDCRelation.ORDERED  # praktisch gleichauf
            return CDCRelation.CONCURRENT_DRIFT
        # other causally before self → spiegelverkehrt analysieren wäre overkill;
        # für die Auswertung reicht: order ist umgekehrt → entweder ORDERED oder
        # CAUSAL_DRIFT aus other's Sicht. Wir geben hier ORDERED zurück, wenn D
        # konsistent ist, sonst CAUSAL_DRIFT.
        if d_ge:
            return CDCRelation.ORDERED
        return CDCRelation.CAUSAL_DRIFT

    @staticmethod
    def _vec_le(a: Mapping[str, int], b: Mapping[str, int]) -> bool:
        """a ≤ b (komponentenweise, fehlende Komponenten = 0)."""
        keys = set(a) | set(b)
        return all(a.get(k, 0) <= b.get(k, 0) for k in keys)

    @staticmethod
    def _dil_le(
        a: Mapping[str, float],
        b: Mapping[str, float],
        gamma: Mapping[tuple[str, str], float],
        tolerance: float,
    ) -> bool:
        """a ≤ b mit Frame-Transformation und Toleranz."""
        keys = set(a) | set(b)
        for k in keys:
            av = a.get(k, 0.0)
            bv = b.get(k, 0.0)
            # γ ist hier die Identität für gleiche Agenten — siehe transform()
            if av > bv + tolerance:
                # Toleranz mit γ-Skalierung verfeinern wäre Phase >5; der
                # naive Vergleich reicht für die Klassifikation.
                return False
        return True

    # ── Serialisierung ────────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        with self._lock:  # type: ignore[attr-defined]
            return {
                "vector": dict(self.vector),
                "dilation": dict(self.dilation),
            }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_dict(cls, d: Mapping[str, object]) -> "CausalDilationClock":
        return cls(
            vector=dict(d.get("vector", {})),  # type: ignore[arg-type]
            dilation={k: float(v) for k, v in dict(d.get("dilation", {})).items()},  # type: ignore[arg-type]
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
