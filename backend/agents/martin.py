"""
backend/agents/martin.py — Martin, der Operator-Agent.

Martin ist der kanonische OPERATORS-Fraktions-Agent. Er:
- Empfängt komplexe Tasks und zerlegt sie (Intent-Detection)
- Delegiert an die richtige Fraktion/Agent über den Conductor
- Liest CDC llm_summary() und Faction-γ_ij für Routing-Entscheidungen
- Führt QC-Loops durch (Auditor-Delegation mit Score-Schwelle)
- Dient als Operator-Bridge für cross-faction ADVERSARIAL-Verkehr

Als einziger Agent hat Martin Meta-Sicht auf das Fraktionssystem.
Domain-Arbeit macht er nicht — nur Routing, Übersetzung und Korrektur.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field

from backend.agents.base import AsyncAgent
from backend.core.cdc import CausalDilationClock
from backend.core.faction_protocol import FactionRegistry
from backend.core.protocol import Message, MessageType

# ── QC-Konfiguration ──────────────────────────────────────────────────────────


@dataclass
class QCConfig:
    """Steuert den QC-Loop den Martin nach jeder Maker-Delegation durchführt."""

    enabled: bool = True
    min_score: int = 7  # 1-10 — unter diesem Wert → Retry
    max_retries: int = 2
    auditor_id: str = ""  # leer = kein QC (oder kein Auditor verfügbar)


# ── DelegationPlan ────────────────────────────────────────────────────────────


@dataclass
class DelegationStep:
    agent_id: str
    content: str
    depends_on: list[int] = field(default_factory=list)  # Indizes vorheriger Steps


# ── MartinAgent ───────────────────────────────────────────────────────────────


class MartinAgent(AsyncAgent):
    """Martin — CDC-bewusster Operator.

    Routing-Logik (einfach, kein LLM nötig für Demo):
    - @AgentName im Content → direkte Delegation
    - Faction-Annotation (#faction:makers) → Faction-Routing
    - Sonst: Echo-Fallback oder default_recipient

    Für echtes LLM-Routing: llm_router_fn injizieren.
    """

    AGENT_ID = "agent:martin"

    def __init__(
        self,
        conductor=None,
        qc: QCConfig | None = None,
        llm_router_fn=None,  # async fn(content) → agent_id | None  (legacy)
        llm_planner_fn=None,  # async fn(content) → list[DelegationStep] | None
        registry: FactionRegistry | None = None,
        model: str = "",
        temperature: float = 0.3,
    ) -> None:
        super().__init__(self.AGENT_ID, "Martin")
        self.conductor = conductor
        self.qc = qc or QCConfig()
        self._router_fn = llm_router_fn
        self._planner_fn = llm_planner_fn
        self._registry = registry or FactionRegistry.get()
        self.model = model
        self.temperature = temperature

    # ── Handle ────────────────────────────────────────────────────────────────

    async def handle(self, msg: Message) -> Message:
        clock = self.advance_clock(msg.clock)
        content = msg.payload.get("content", "")

        # 1. FactionEnvelope prüfen — Bridge-Request?
        envelope = msg.payload.get("_faction")
        if envelope and envelope.get("requires_bridge"):
            return await self._bridge(msg, content, clock)

        # 2. Planer aufrufen (multi-step) oder Router (single-step)
        if self._planner_fn:
            steps = await self._planner_fn(content)
            if not steps:
                return Message.response(
                    msg, f"[Martin] No plan found for: {content[:80]}", clock=clock
                )
            if len(steps) == 1:
                return await self._delegate_with_qc(msg, steps[0].agent_id, steps[0].content, clock)
            return await self._execute_plan(msg, steps, clock)

        # Legacy: single router
        target = await self._resolve_target(content, msg)
        if not target:
            return Message.response(
                msg, f"[Martin] No route found for: {content[:80]}", clock=clock
            )
        return await self._delegate_with_qc(msg, target, content, clock)

    # ── Routing ───────────────────────────────────────────────────────────────

    async def _resolve_target(self, content: str, msg: Message) -> str | None:
        # @AgentName Syntax
        m = re.search(r"@([\w:]+)", content)
        if m:
            ref = m.group(1)
            if ref.startswith("skill:") or ref.startswith("agent:"):
                return ref
            return f"agent:{ref}"

        # #skill:X Syntax → skill:<id>
        m = re.search(r"#skill:([\w]+)", content)
        if m:
            return f"skill:{m.group(1)}"

        # #faction:X Syntax
        m = re.search(r"#faction:([\w]+)", content)
        if m:
            faction_id = m.group(1)
            faction = self._registry.get_faction(faction_id)
            if faction and faction.members:
                return next(iter(faction.members))

        # LLM-Router falls injiziert
        if self._router_fn:
            return await self._router_fn(content)

        # Fallback: gleiche Mission weiterleiten an ersten Nicht-Martin-Agenten
        if self.conductor:
            for ag in self.conductor.list_agents():
                if ag.agent_id != self.AGENT_ID and ag.agent_id != "a2a:gateway":
                    return ag.agent_id

        return None

    # ── Delegation mit QC-Loop ────────────────────────────────────────────────

    async def _delegate_with_qc(
        self,
        original: Message,
        target_id: str,
        content: str,
        clock: CausalDilationClock,
    ) -> Message:
        if not self.conductor:
            return Message.error(original, "Martin has no conductor", clock=clock)

        for attempt in range(self.qc.max_retries + 1):
            # Delegation
            sub_msg = Message.request(
                mission_id=original.mission_id,
                sender=self.AGENT_ID,
                recipient=target_id,
                content=content,
                parent_task_id=original.task_id,
                clock=self.advance_clock(),
            )
            response = await self.conductor.dispatch(sub_msg)

            if response.type == MessageType.ERROR:
                return response

            result_text = str(response.payload.get("result", ""))

            # QC-Check — Skills sind deterministisch, kein Feedback-Loop sinnvoll
            is_skill = target_id.startswith("skill:")
            if not self.qc.enabled or not self.qc.auditor_id or is_skill:
                break

            score = await self._qc_check(original, result_text)
            if score >= self.qc.min_score:
                break

            if attempt < self.qc.max_retries:
                content = (
                    f"Vorherige Antwort war unzureichend (Score {score}/10). "
                    f"Verbessere und vervollständige: {content}\n\n"
                    f"Vorherige Antwort zur Referenz: {result_text[:300]}"
                )
            else:
                result_text = (
                    f"[QC failed after {attempt + 1} attempts, best score {score}/10] {result_text}"
                )

        return Message.response(
            original,
            result_text,
            clock=self.advance_clock(response.clock),
        )

    async def _execute_plan(
        self,
        original: Message,
        steps: list[DelegationStep],
        clock: CausalDilationClock,
    ) -> Message:
        """Führt mehrere DelegationSteps sequenziell aus und aggregiert die Ergebnisse."""
        results: list[str] = []
        step_results: dict[int, str] = {}

        for i, step in enumerate(steps):
            # Kontext aus Abhängigkeiten einbauen
            context = ""
            if step.depends_on:
                deps = "\n".join(step_results[j] for j in step.depends_on if j in step_results)
                context = f"[Vorherige Ergebnisse]\n{deps}\n\n"

            content = f"{context}{step.content}"
            resp = await self._delegate_with_qc(original, step.agent_id, content, clock)
            text = resp.payload.get("result", "")
            step_results[i] = text
            results.append(f"**Schritt {i + 1}/{len(steps)}** → `{step.agent_id}`\n{text}")

        combined = "\n\n".join(results)
        return Message.response(original, combined, clock=self.advance_clock())

    async def _qc_check(self, original: Message, result: str) -> int:
        """Delegiert an Auditor, extrahiert Score 1-10. Gibt 0 bei Fehler zurück."""
        if not self.conductor:
            return 10  # kein Auditor → durchlassen

        qc_msg = Message.request(
            mission_id=original.mission_id,
            sender=self.AGENT_ID,
            recipient=self.qc.auditor_id,
            content=f"Rate this result 1-10. Reply with only the number.\n\nResult: {result[:500]}",
            parent_task_id=original.task_id,
            clock=self.advance_clock(),
        )
        try:
            qc_resp = await asyncio.wait_for(
                self.conductor.dispatch(qc_msg),
                timeout=30.0,
            )
            if qc_resp.type == MessageType.RESPONSE:
                text = str(qc_resp.payload.get("result", "5"))
                nums = re.findall(r"\d+", text)
                if nums:
                    return min(10, max(1, int(nums[0])))
        except Exception:
            pass
        return 5  # Default bei Fehler

    # ── Operator-Bridge ───────────────────────────────────────────────────────

    async def _bridge(
        self,
        msg: Message,
        content: str,
        clock: CausalDilationClock,
    ) -> Message:
        """Übersetzt cross-faction ADVERSARIAL-Messages (Charter-aware Reformulierung)."""
        envelope = msg.payload.get("_faction", {})
        recipient_faction = self._registry.get_faction(envelope.get("recipient_faction", ""))

        if recipient_faction:
            lens = recipient_faction.charter.mission_lens
            translated = f"[Bridge via {self.name}] {lens}\n\nOriginal: {content}"
        else:
            translated = content

        target = await self._resolve_target(content, msg)
        if not target:
            return Message.error(msg, "Bridge: no target found", clock=clock)

        sub = Message.request(
            mission_id=msg.mission_id,
            sender=self.AGENT_ID,
            recipient=target,
            content=translated,
            parent_task_id=msg.task_id,
            clock=self.advance_clock(),
        )
        if self.conductor:
            return await self.conductor.dispatch(sub)
        return Message.error(msg, "Bridge: no conductor", clock=clock)

    # ── CDC-Kontext ───────────────────────────────────────────────────────────

    def cdc_context(self) -> str:
        """Gibt Martin's aktuelles Zeitgefühl als LLM-lesbaren String zurück."""
        return self._clock.llm_summary()

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["faction"] = "operators"
        if self.model:
            d["model"] = self.model
        d["temperature"] = self.temperature
        d["qc"] = {
            "enabled": self.qc.enabled,
            "min_score": self.qc.min_score,
            "max_retries": self.qc.max_retries,
            "auditor_id": self.qc.auditor_id,
        }
        return d
