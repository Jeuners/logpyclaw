"""
backend/core/faction_protocol.py — Fraktionssystem (Opus 4.7 Design).

Fraktion = persistente Identität eines Agenten über Missionen hinweg.
Ergänzt CDC (V,D) um F: wer hat getickelt, aus welcher Perspektive.

Kernprinzipien (aus Opus-Architekt-Report):
- Fraktion ist Identity, nicht Role (persistent, nicht transient wie Teams).
- Inter-Fraktions-Vertrauen ist gerichtet und frequentistisch gelernt.
- γ_factions ist Fallback unter γ_agents in CDC.transform().
- EXPECTED_DRIFT: cross-faction Drift ist strukturell erwartet, kein Alarm.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import ClassVar

from backend.core.cdc import CDCRelation

# ── Archetypen ────────────────────────────────────────────────────────────────


class FactionArchetype(StrEnum):
    OPERATORS = "operators"  # Routing, Delegation, Orchestration — Martin
    MAKERS = "makers"  # Generative Arbeit: Code, Bild, Video, Text
    AUDITORS = "auditors"  # Verifikation, QC, Review
    GATHERERS = "gatherers"  # Wahrnehmung: Web, Mail, Files
    GUARDIANS = "guardians"  # Policy, Safety, Watchdog
    SCRIBES = "scribes"  # Gedächtnis, Embeddings, History


# ── Beziehungstypen ───────────────────────────────────────────────────────────


class FactionStance(StrEnum):
    ALLIED = "allied"  # freie Delegation, kein Review
    COOPERATIVE = "cooperative"  # Delegation + Plausi-Check
    NEUTRAL = "neutral"  # Standard
    SKEPTICAL = "skeptical"  # nur über definierte Schnittstelle
    ADVERSARIAL = "adversarial"  # nur über Operator-Bridge


# ── Erweiterte CDC-Relationen ─────────────────────────────────────────────────
# Werden in cdc.py als zusätzliche Werte registriert, wenn FactionRegistry
# beim relate()-Call übergeben wird.


class FactionCDCRelation(StrEnum):
    EXPECTED_DRIFT = "expected_drift"  # cross-faction CAUSAL_DRIFT — strukturell ok
    FACTION_RACE = "faction_race"  # cross-faction CONCURRENT_DRIFT — oft gewollt


# ── Tempo-Profil ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TempoProfile:
    """Erwartete Eigenzeit-Rate dieser Fraktion (ops/s)."""

    expected_rate: float = 1.0
    min_rate: float = 0.3
    max_rate: float = 3.0
    tolerance: float = 0.5

    def is_normal(self, rate: float) -> bool:
        return self.min_rate <= rate <= self.max_rate

    def deviation(self, rate: float) -> float:
        """0.0 = normal, >0 = abweichend."""
        if self.is_normal(rate):
            return 0.0
        if rate < self.min_rate:
            return (self.min_rate - rate) / max(self.min_rate, 1e-6)
        return (rate - self.max_rate) / max(self.max_rate, 1e-6)


# ── Charter ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FactionCharter:
    mission_lens: str  # wie Tasks gelesen werden
    do_principles: tuple[str, ...]  # was die Fraktion immer tut
    dont_principles: tuple[str, ...]  # was sie ablehnt
    delegation_policy: str  # wann darf delegiert werden


# ── Fraktion ──────────────────────────────────────────────────────────────────


@dataclass
class Faction:
    id: str
    archetype: FactionArchetype
    label: str
    charter: FactionCharter
    tempo: TempoProfile
    capability_classes: frozenset[str]
    skill_ids: frozenset[str] = field(default_factory=frozenset)
    members: set[str] = field(default_factory=set)
    created_at: float = field(default_factory=time.time)

    def add_member(self, agent_id: str) -> None:
        self.members.add(agent_id)

    def remove_member(self, agent_id: str) -> None:
        self.members.discard(agent_id)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "archetype": self.archetype.value,
            "label": self.label,
            "charter": {
                "mission_lens": self.charter.mission_lens,
                "do": list(self.charter.do_principles),
                "dont": list(self.charter.dont_principles),
                "delegation": self.charter.delegation_policy,
            },
            "tempo": {
                "expected": self.tempo.expected_rate,
                "min": self.tempo.min_rate,
                "max": self.tempo.max_rate,
                "tolerance": self.tempo.tolerance,
            },
            "capability_classes": sorted(self.capability_classes),
            "skill_ids": sorted(self.skill_ids),
            "members": sorted(self.members),
        }


# ── FactionRelation ───────────────────────────────────────────────────────────


@dataclass
class FactionRelation:
    """Gerichtete Beziehung source → target. Trust und γ werden gelernt.

    Mathematische Eigenschaften (siehe README, "Trust & γ"):
    - trust = (S+1)/(N+2) mit Beta(1,1)-Prior (Laplace) — beschränkt auf (0,1),
      Start 0.5, konvergiert gegen die empirische Erfolgsrate.
    - Evidenz ALTERT: vor jedem Update werden S und F exponentiell abgezinst
      (Halbwertszeit TRUST_HALF_LIFE_S). Ohne frische Evidenz kehrt trust beim
      nächsten Kontakt Richtung Prior zurück; alte Beobachtungen verlieren
      Gewicht statt das System unbegrenzt zu versteifen.
    - γ ist ein EWMA (α=0.2): Gewicht einer k Updates alten Beobachtung ist
      α(1-α)^k — effektives Gedächtnis ≈ 1/α = 5 Interaktionen.
    - trust beeinflusst NUR Routing-Prioritäten, niemals Sicherheitsgrenzen:
      die Bridge-Pflicht hängt an stance, nicht an trust — Vertrauen kann
      keine adversariale Schranke "freischalten".
    """

    TRUST_HALF_LIFE_S: ClassVar[float] = 7 * 24 * 3600.0  # 7 Tage Evidenz-Halbwertszeit

    source: str
    target: str
    stance: FactionStance = FactionStance.NEUTRAL
    trust: float = 0.5  # P(brauchbare Antwort), Beta(1,1)-Prior
    gamma: float = 1.0  # Tempo-Verhältnis source/target, EWMA
    interactions: float = 0.0  # gewichtete Evidenzmenge (altert)
    successes: float = 0.0
    failures: float = 0.0
    last_updated: float = field(default_factory=time.time)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def _decay_evidence(self, now: float) -> None:
        """Exponentielles Altern der Evidenz (lazy, beim nächsten Update).

        Faktor 0.5^(Δt/Halbwertszeit) auf S und F — der Erwartungswert von
        trust bleibt erhalten, aber die effektive Stichprobengröße sinkt,
        sodass frische Evidenz wieder schnell wirkt ("Vertrauen verjährt")."""
        dt = max(now - self.last_updated, 0.0)
        if dt <= 0.0:
            return
        f = 0.5 ** (dt / self.TRUST_HALF_LIFE_S)
        self.successes *= f
        self.failures *= f
        self.interactions = self.successes + self.failures

    def record_outcome(self, success: bool) -> None:
        with self._lock:
            now = time.time()
            self._decay_evidence(now)
            if success:
                self.successes += 1.0
            else:
                self.failures += 1.0
            self.interactions = self.successes + self.failures
            # Beta(1,1)-Prior: robust gegen N=1 und N=0
            self.trust = (self.successes + 1.0) / (self.interactions + 2.0)
            self.last_updated = now

    def update_gamma(self, observed_ratio: float, alpha: float = 0.2) -> None:
        """EWMA-Update γ (Tempo-Verhältnis source/target)."""
        with self._lock:
            self.gamma = (1 - alpha) * self.gamma + alpha * observed_ratio
            self.last_updated = time.time()

    def requires_bridge(self) -> bool:
        return self.stance == FactionStance.ADVERSARIAL

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "target": self.target,
            "stance": self.stance.value,
            "trust": round(self.trust, 4),
            "gamma": round(self.gamma, 4),
            "interactions": round(self.interactions, 2),
            "successes": round(self.successes, 2),
            "failures": round(self.failures, 2),
        }


# ── FactionEnvelope ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FactionEnvelope:
    """Wird in payload["_faction"] serialisiert — kein Breaking Change an Message."""

    sender_faction: str
    recipient_faction: str
    stance: FactionStance
    requires_bridge: bool = False
    expected_drift: bool = False  # True = cross-faction, Drift ist strukturell

    def to_dict(self) -> dict:
        return {
            "sender_faction": self.sender_faction,
            "recipient_faction": self.recipient_faction,
            "stance": self.stance.value,
            "requires_bridge": self.requires_bridge,
            "expected_drift": self.expected_drift,
        }


# ── FactionRegistry ───────────────────────────────────────────────────────────


class FactionRegistry:
    """In-Memory Single Source of Truth für alle Fraktionen.

    Singleton — `FactionRegistry.get()` liefert immer dieselbe Instanz.
    Keine externe DB nötig; JSON-Dump für Persistenz kommt in Phase 6.
    """

    _instance: FactionRegistry | None = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._factions: dict[str, Faction] = {}
        self._agent_to_faction: dict[str, str] = {}
        self._relations: dict[tuple[str, str], FactionRelation] = {}
        self._lock = threading.RLock()

    @classmethod
    def get(cls) -> FactionRegistry:
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Für Tests — erzeugt eine frische Registry."""
        with cls._instance_lock:
            cls._instance = None

    # ── Fraktionen ────────────────────────────────────────────────────────────

    def register(self, faction: Faction) -> None:
        with self._lock:
            self._factions[faction.id] = faction
            for member in faction.members:
                self._agent_to_faction[member] = faction.id

    def get_faction(self, fid: str) -> Faction | None:
        with self._lock:
            return self._factions.get(fid)

    def list_factions(self) -> list[Faction]:
        with self._lock:
            return list(self._factions.values())

    def faction_of(self, agent_id: str) -> str | None:
        with self._lock:
            return self._agent_to_faction.get(agent_id)

    def assign(self, agent_id: str, faction_id: str) -> None:
        with self._lock:
            old = self._agent_to_faction.get(agent_id)
            if old and old in self._factions:
                self._factions[old].remove_member(agent_id)
            if faction_id in self._factions:
                self._factions[faction_id].add_member(agent_id)
                self._agent_to_faction[agent_id] = faction_id

    # ── Beziehungen ───────────────────────────────────────────────────────────

    def relation(self, source: str, target: str) -> FactionRelation:
        """Lazy-creates mit Defaults wenn noch nicht vorhanden."""
        with self._lock:
            key = (source, target)
            if key not in self._relations:
                self._relations[key] = FactionRelation(source=source, target=target)
            return self._relations[key]

    def set_stance(self, source: str, target: str, stance: FactionStance) -> None:
        self.relation(source, target).stance = stance

    def all_relations(self) -> list[FactionRelation]:
        with self._lock:
            return list(self._relations.values())

    # ── γ-Matrix Export (für CDC.transform) ──────────────────────────────────

    def gamma_factions(self) -> dict[tuple[str, str], float]:
        with self._lock:
            return {(r.source, r.target): r.gamma for r in self._relations.values()}

    # ── Envelope-Builder ─────────────────────────────────────────────────────

    def build_envelope(self, sender_id: str, recipient_id: str) -> FactionEnvelope | None:
        """Baut FactionEnvelope wenn beide Agents einer Fraktion angehören."""
        sf = self.faction_of(sender_id)
        rf = self.faction_of(recipient_id)
        if not sf or not rf:
            return None
        rel = self.relation(sf, rf)
        return FactionEnvelope(
            sender_faction=sf,
            recipient_faction=rf,
            stance=rel.stance,
            requires_bridge=rel.requires_bridge(),
            expected_drift=(sf != rf),
        )

    # ── Outcome-Feedback ──────────────────────────────────────────────────────

    def record_cross_faction_outcome(
        self,
        sender_id: str,
        recipient_id: str,
        success: bool,
        sender_rate: float = 0.0,
        recipient_rate: float = 0.0,
    ) -> None:
        """Aktualisiert Trust und γ nach einer abgeschlossenen Interaktion."""
        sf = self.faction_of(sender_id)
        rf = self.faction_of(recipient_id)
        if not sf or not rf or sf == rf:
            return
        rel = self.relation(sf, rf)
        rel.record_outcome(success)
        if sender_rate > 0 and recipient_rate > 0:
            rel.update_gamma(sender_rate / recipient_rate)

    # ── Bulk-Load aus Config ──────────────────────────────────────────────────

    @classmethod
    def load_defaults(cls) -> FactionRegistry:
        """Lädt die 6 Standard-Archetypen mit sinnvollen Defaults."""
        reg = cls.get()
        _register_defaults(reg)
        return reg


# ── Drift-Klassifikation (FactionCDCRelation scharf schalten) ─────────────────


def classify_drift(
    relation: CDCRelation,
    sender_faction: str | None,
    recipient_faction: str | None,
    observed_ratio: float,
    registry: FactionRegistry,
) -> str:
    """Re-klassifiziert CDC-Drift im Fraktions-Kontext.

    Cross-faction Drift, dessen beobachtetes Tempo-Verhältnis zum gelernten
    γ der Beziehung passt, ist strukturell erwartet — kein Alarm:
      CAUSAL_DRIFT     → EXPECTED_DRIFT
      CONCURRENT_DRIFT → FACTION_RACE
    Alle anderen Fälle (same-faction, unbekannte Fraktion, Ratio außerhalb
    der Toleranz) reichen relation.value unverändert durch.
    """
    if relation not in (CDCRelation.CAUSAL_DRIFT, CDCRelation.CONCURRENT_DRIFT):
        return relation.value
    if not sender_faction or not recipient_faction or sender_faction == recipient_faction:
        return relation.value

    rel = registry.relation(sender_faction, recipient_faction)
    # Toleranz aus dem Tempo-Profil der Empfänger-Fraktion, Default 0.5
    recipient = registry.get_faction(recipient_faction)
    tolerance = recipient.tempo.tolerance if recipient else 0.5

    if observed_ratio > 0 and abs(observed_ratio - rel.gamma) <= tolerance:
        if relation is CDCRelation.CAUSAL_DRIFT:
            return FactionCDCRelation.EXPECTED_DRIFT.value
        return FactionCDCRelation.FACTION_RACE.value
    return relation.value


# ── Standard-Fraktionen ───────────────────────────────────────────────────────


def _register_defaults(reg: FactionRegistry) -> None:
    factions = [
        Faction(
            id="operators",
            archetype=FactionArchetype.OPERATORS,
            label="Operators",
            charter=FactionCharter(
                mission_lens="Route tasks to the right faction, translate intent.",
                do_principles=(
                    "delegate explicitly",
                    "log routing decisions",
                    "preserve task_id chain",
                ),
                dont_principles=(
                    "execute domain work directly",
                    "skip bridge for adversarial pairs",
                ),
                delegation_policy="free within cooperative+; bridge for adversarial",
            ),
            tempo=TempoProfile(expected_rate=1.0, min_rate=0.5, max_rate=2.0),
            capability_classes=frozenset({"routing", "delegation", "translation"}),
        ),
        Faction(
            id="makers",
            archetype=FactionArchetype.MAKERS,
            label="Makers",
            charter=FactionCharter(
                mission_lens="Produce an artifact that does exactly what was asked.",
                do_principles=("generate", "build", "create"),
                dont_principles=("self-review without explicit QC request",),
                delegation_policy="free to sub-delegate within makers",
            ),
            tempo=TempoProfile(expected_rate=0.8, min_rate=0.3, max_rate=2.5, tolerance=1.0),
            capability_classes=frozenset({"generate", "build", "transform"}),
            skill_ids=frozenset({"coding", "image_gen", "video_gen", "prompt_optimize"}),
        ),
        Faction(
            id="auditors",
            archetype=FactionArchetype.AUDITORS,
            label="Auditors",
            charter=FactionCharter(
                mission_lens="Evaluate an artifact against the task charter. Refuse fast answers.",
                do_principles=("verify claims", "score quality 1-10", "flag drift"),
                dont_principles=("produce original content", "rush reviews"),
                delegation_policy="only delegate to other auditors",
            ),
            tempo=TempoProfile(expected_rate=1.2, min_rate=0.6, max_rate=1.8, tolerance=0.3),
            capability_classes=frozenset({"verify", "review", "score"}),
            skill_ids=frozenset({"security_review", "qc"}),
        ),
        Faction(
            id="gatherers",
            archetype=FactionArchetype.GATHERERS,
            label="Gatherers",
            charter=FactionCharter(
                mission_lens="Fetch raw data. No interpretation.",
                do_principles=("fetch", "transcribe", "observe"),
                dont_principles=("interpret", "summarize without explicit request"),
                delegation_policy="delegate to other gatherers only",
            ),
            tempo=TempoProfile(expected_rate=0.5, min_rate=0.1, max_rate=1.5, tolerance=1.0),
            capability_classes=frozenset({"fetch", "observe", "listen"}),
            skill_ids=frozenset(
                {
                    "url_fetch",
                    "web_search",
                    "mac_mail",
                    "whatsapp",
                    "file_access",
                    "chrome_browser",
                    "transcription",
                    "wikipedia",
                    "tagesschau",
                    "hacker_news",
                    "linkedin",
                    "youtube",
                    "wiki_read",
                }
            ),
        ),
        Faction(
            id="guardians",
            archetype=FactionArchetype.GUARDIANS,
            label="Guardians",
            charter=FactionCharter(
                mission_lens="Prevent harm. No answer beats wrong answer.",
                do_principles=("check policy", "enforce rate limits", "escalate inconsistencies"),
                dont_principles=("block without reason", "produce content"),
                delegation_policy="guardians never delegate outside guardians",
            ),
            tempo=TempoProfile(expected_rate=2.0, min_rate=1.0, max_rate=4.0, tolerance=0.5),
            capability_classes=frozenset({"protect", "enforce", "monitor"}),
        ),
        Faction(
            id="scribes",
            archetype=FactionArchetype.SCRIBES,
            label="Scribes",
            charter=FactionCharter(
                mission_lens="Record what happened in a form future agents can read.",
                do_principles=("persist", "index", "summarize history"),
                dont_principles=("alter facts", "delete without explicit request"),
                delegation_policy="free delegation within scribes",
            ),
            tempo=TempoProfile(expected_rate=0.9, min_rate=0.3, max_rate=1.8, tolerance=0.6),
            capability_classes=frozenset({"persist", "index", "recall"}),
        ),
    ]
    for f in factions:
        reg.register(f)

    # Initiale Stance-Matrix (asymmetrisch — gelernt aus Semantik)
    stances = [
        ("auditors", "makers", FactionStance.SKEPTICAL),
        ("makers", "auditors", FactionStance.COOPERATIVE),
        ("makers", "operators", FactionStance.COOPERATIVE),
        ("gatherers", "makers", FactionStance.COOPERATIVE),
        ("gatherers", "operators", FactionStance.COOPERATIVE),
        ("guardians", "operators", FactionStance.ALLIED),
        ("guardians", "makers", FactionStance.SKEPTICAL),
        ("guardians", "gatherers", FactionStance.SKEPTICAL),
        ("scribes", "operators", FactionStance.ALLIED),
        ("operators", "makers", FactionStance.ALLIED),
        ("operators", "auditors", FactionStance.ALLIED),
        ("operators", "gatherers", FactionStance.ALLIED),
        ("operators", "guardians", FactionStance.ALLIED),
        ("operators", "scribes", FactionStance.ALLIED),
    ]
    for src, tgt, stance in stances:
        reg.set_stance(src, tgt, stance)
