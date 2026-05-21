"""
backend/core/team_protocol.py — CDC-Team-Protokoll.

Warum Teams ein eigenes Protokoll brauchen (über standard A2A hinaus):

Standard A2A ist Point-to-Point. Ein Team hat:
  - Gemeinsame Eigenzeit (max-merge aller Mitglieder-Clocks)
  - γ_ij-Matrix: paarweise Dilationsraten zwischen Mitgliedern
  - Drift-kompensierten Dispatcher: wer driftet am wenigsten?
  - Team-Kausalität: eine Team-Message trägt den Stand aller Mitglieder

TeamMessage erweitert Message um team_id, team_clock, gamma_matrix, team_state.
Team verwaltet Mitglieder, berechnet γ_ij aus Mission-History und empfiehlt
den nächsten Executor basierend auf drift_score.
"""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from backend.core.cdc import CausalDilationClock, CDCRelation
from backend.core.protocol import (
    Message, MessageType, new_msg_id, new_team_id,
)


# ── Team States ───────────────────────────────────────────────────────────────

class TeamState(str, Enum):
    FORMING      = "forming"       # Mitglieder werden registriert
    ACTIVE       = "active"        # Quorum erreicht, bereit für Tasks
    QUORUM_LOST  = "quorum_lost"   # Zu wenige Mitglieder erreichbar
    DISSOLVED    = "dissolved"     # Team aufgelöst


# ── TeamMessage ───────────────────────────────────────────────────────────────

@dataclass
class TeamMessage(Message):
    """CDC-Message mit Team-Kontext.

    team_clock    : max-merge aller Mitglieder-Clocks = Team-Eigenzeit
    gamma_matrix  : γ_ij für alle Paare zum Sendezeitpunkt
    team_state    : Zustand des Teams
    """
    team_id:      str                          = ""
    team_clock:   CausalDilationClock          = field(default_factory=CausalDilationClock)
    gamma_matrix: dict[str, dict[str, float]]  = field(default_factory=dict)
    team_state:   TeamState                    = TeamState.ACTIVE

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["team_id"]      = self.team_id
        d["team_clock"]   = self.team_clock.to_dict()
        d["gamma_matrix"] = self.gamma_matrix
        d["team_state"]   = self.team_state.value
        return d

    @classmethod
    def from_message(
        cls,
        msg: Message,
        team_id: str,
        team_clock: CausalDilationClock,
        gamma_matrix: dict[str, dict[str, float]],
        team_state: TeamState = TeamState.ACTIVE,
    ) -> "TeamMessage":
        """Upgrade einer normalen Message zu einer TeamMessage."""
        return cls(
            msg_id=msg.msg_id,
            mission_id=msg.mission_id,
            task_id=msg.task_id,
            parent_task_id=msg.parent_task_id,
            type=msg.type,
            sender=msg.sender,
            recipient=msg.recipient,
            payload=msg.payload,
            timestamp=msg.timestamp,
            clock=msg.clock,
            team_id=team_id,
            team_clock=team_clock,
            gamma_matrix=gamma_matrix,
            team_state=team_state,
        )


# ── MemberRecord ─────────────────────────────────────────────────────────────

@dataclass
class MemberRecord:
    agent_id:    str
    joined_at:   float = field(default_factory=time.time)
    last_seen:   float = field(default_factory=time.time)
    busy:        bool  = False
    clock:       CausalDilationClock = field(default_factory=CausalDilationClock)
    avg_rate:    float = 1.0   # ops/s aus Mission-History
    task_count:  int   = 0

    def mark_seen(self, clock: CausalDilationClock) -> None:
        self.last_seen = time.time()
        self.clock.merge(clock)

    @property
    def is_reachable(self) -> bool:
        return (time.time() - self.last_seen) < 60.0


# ── Team ─────────────────────────────────────────────────────────────────────

class Team:
    """Verwaltet Mitglieder, Team-Eigenzeit, γ_ij-Matrix und Dispatch-Empfehlung."""

    def __init__(self, team_id: str | None = None, name: str = "") -> None:
        self.team_id:  str         = team_id or new_team_id()
        self.name:     str         = name
        self.state:    TeamState   = TeamState.FORMING
        self._members: dict[str, MemberRecord] = {}
        self._rate_history: dict[str, list[float]] = defaultdict(list)

    # ── Mitgliedschaft ────────────────────────────────────────────────────────

    def add_member(self, agent_id: str) -> MemberRecord:
        if agent_id not in self._members:
            self._members[agent_id] = MemberRecord(agent_id=agent_id)
        self._check_quorum()
        return self._members[agent_id]

    def remove_member(self, agent_id: str) -> None:
        self._members.pop(agent_id, None)
        self._check_quorum()

    def update_member_clock(self, agent_id: str, clock: CausalDilationClock) -> None:
        if agent_id in self._members:
            self._members[agent_id].mark_seen(clock)
            rate = clock.dilation.get(agent_id, 0.0)
            if rate > 0:
                self._rate_history[agent_id].append(rate)
                self._members[agent_id].avg_rate = self._avg_rate(agent_id)

    def set_busy(self, agent_id: str, busy: bool) -> None:
        if agent_id in self._members:
            self._members[agent_id].busy = busy

    # ── Team-Eigenzeit ────────────────────────────────────────────────────────

    def merge_team_clock(self) -> CausalDilationClock:
        """Max-merge aller Mitglieder-Clocks = Team-Referenzzeit."""
        merged = CausalDilationClock()
        for m in self._members.values():
            merged.merge(m.clock)
        return merged

    # ── γ_ij-Matrix ───────────────────────────────────────────────────────────

    def compute_gamma_matrix(self) -> dict[str, dict[str, float]]:
        """Paarweise Dilationsraten γ_ij = avg_rate_i / avg_rate_j.

        γ_ij > 1: Agent i ist schneller als j (erlebt mehr Eigenzeit pro Wallclock).
        γ_ij < 1: Agent i ist langsamer als j.
        γ_ij = 1: Keine relative Dilation.
        """
        members = list(self._members.values())
        matrix: dict[str, dict[str, float]] = {}
        for mi in members:
            matrix[mi.agent_id] = {}
            for mj in members:
                if mi.agent_id == mj.agent_id:
                    matrix[mi.agent_id][mj.agent_id] = 1.0
                else:
                    rate_j = mj.avg_rate if mj.avg_rate > 0 else 1.0
                    matrix[mi.agent_id][mj.agent_id] = round(mi.avg_rate / rate_j, 4)
        return matrix

    # ── Drift-kompensierter Dispatcher ────────────────────────────────────────

    def recommend_next(
        self,
        candidates: list[str] | None = None,
    ) -> Optional[str]:
        """Empfiehlt das Mitglied mit kleinstem Drift-Score (nicht busy, erreichbar).

        drift_score = |γ_i - 1.0| — 0 bedeutet kein Drift gegenüber Referenz.
        recommendation_score = avg_rate / (1 + drift_score) - (0.5 if busy)
        """
        pool = candidates or list(self._members.keys())
        best_agent: Optional[str] = None
        best_score = -float("inf")

        for agent_id in pool:
            m = self._members.get(agent_id)
            if m is None or not m.is_reachable or m.busy:
                continue
            gamma = m.avg_rate  # relativ zur Referenz-Rate 1.0
            drift_score = abs(gamma - 1.0)
            score = m.avg_rate / (1.0 + drift_score)
            if score > best_score:
                best_score = score
                best_agent = agent_id

        return best_agent

    def recommend_details(
        self,
        candidates: list[str] | None = None,
    ) -> list[dict]:
        """Wie recommend_next, gibt aber Scores für alle Kandidaten zurück."""
        pool = candidates or list(self._members.keys())
        results = []
        for agent_id in pool:
            m = self._members.get(agent_id)
            if m is None:
                continue
            gamma      = m.avg_rate
            drift      = abs(gamma - 1.0)
            score      = m.avg_rate / (1.0 + drift) - (0.5 if m.busy else 0)
            results.append({
                "agent_id":           agent_id,
                "avg_rate":           round(m.avg_rate, 4),
                "gamma":              round(gamma, 4),
                "drift_score":        round(drift, 4),
                "recommendation_score": round(score, 4),
                "busy":               m.busy,
                "reachable":          m.is_reachable,
            })
        return sorted(results, key=lambda x: -x["recommendation_score"])

    # ── Quorum ────────────────────────────────────────────────────────────────

    def is_quorum(self) -> bool:
        """Mindestens ⌊n/2⌋ + 1 Mitglieder erreichbar."""
        n = len(self._members)
        if n == 0:
            return False
        reachable = sum(1 for m in self._members.values() if m.is_reachable)
        return reachable >= (n // 2 + 1)

    # ── Team-Message-Builder ──────────────────────────────────────────────────

    def wrap(self, msg: Message) -> TeamMessage:
        """Enriched eine normale Message mit aktuellem Team-Kontext."""
        return TeamMessage.from_message(
            msg=msg,
            team_id=self.team_id,
            team_clock=self.merge_team_clock(),
            gamma_matrix=self.compute_gamma_matrix(),
            team_state=self.state,
        )

    # ── Zustandsinformationen ─────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "team_id":     self.team_id,
            "name":        self.name,
            "state":       self.state.value,
            "members":     [
                {
                    "agent_id":  m.agent_id,
                    "busy":      m.busy,
                    "reachable": m.is_reachable,
                    "avg_rate":  round(m.avg_rate, 4),
                    "task_count": m.task_count,
                }
                for m in self._members.values()
            ],
            "quorum":      self.is_quorum(),
            "team_clock":  self.merge_team_clock().to_dict(),
            "gamma_matrix": self.compute_gamma_matrix(),
        }

    # ── Intern ────────────────────────────────────────────────────────────────

    def _check_quorum(self) -> None:
        if self.state == TeamState.DISSOLVED:
            return
        if len(self._members) == 0:
            self.state = TeamState.FORMING
        elif self.is_quorum():
            self.state = TeamState.ACTIVE
        else:
            self.state = TeamState.QUORUM_LOST

    def _avg_rate(self, agent_id: str) -> float:
        history = self._rate_history[agent_id]
        if not history:
            return 1.0
        recent = history[-20:]  # letzte 20 Werte
        return sum(recent) / len(recent)
