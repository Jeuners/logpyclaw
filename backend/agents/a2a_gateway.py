"""
backend/agents/a2a_gateway.py — A2A-Gateway-Agent (Dolmetscher).

Nimmt externe Google-A2A-Tasks entgegen, übersetzt sie in CDC-Messages,
leitet sie intern weiter und verpackt das Ergebnis als A2A-Artifact zurück.

Externe Agenten sehen nur Standard-A2A. Das interne CDC-Protokoll bleibt verborgen.

Phase 5: vollständige Google A2A 2025 Spec. Hier: funktionaler Stub.
"""
from __future__ import annotations

import time
import uuid
from typing import Optional

from backend.agents.base import AsyncAgent
from backend.core.cdc import CausalDilationClock
from backend.core.protocol import (
    Message, MessageType, new_mission_id, external_ref, agent_ref,
)


class A2AGatewayAgent(AsyncAgent):
    """Übersetzt Google A2A ↔ internes CDC-Protokoll."""

    def __init__(
        self,
        agent_id: str = "a2a:gateway",
        name: str = "A2A Gateway",
        default_recipient: Optional[str] = None,
        conductor=None,
    ) -> None:
        super().__init__(agent_id, name)
        self.default_recipient = default_recipient
        self.conductor = conductor    # wird von app.py injiziert

    # ── AsyncAgent handle ─────────────────────────────────────────────────────

    async def handle(self, msg: Message) -> Message:
        # Gateway verarbeitet keine eingehenden CDC-Messages direkt —
        # er sendet nur aus. Falls doch aufgerufen: Echo.
        clock = self.advance_clock(msg.clock)
        return Message.response(msg, f"gateway echo: {msg.payload}", clock=clock)

    # ── A2A → CDC ─────────────────────────────────────────────────────────────

    def wrap_a2a_task(self, a2a_task: dict) -> Message:
        """Konvertiert einen eingehenden A2A-Task zu einer CDC-Message.

        Neutrale Clock — externer Agent kennt CDC nicht.
        """
        text = self._extract_text(a2a_task)
        recipient = self._route(a2a_task)
        mission_id = new_mission_id()
        return Message.request(
            mission_id=mission_id,
            sender=external_ref("a2a"),
            recipient=recipient,
            content=text,
            clock=CausalDilationClock(),  # neutral
        )

    def unwrap_cdc_response(self, response: Message, a2a_task_id: str) -> dict:
        """Konvertiert eine CDC-Response zurück zu einem A2A-Artifact."""
        if response.type == MessageType.RESPONSE:
            status = "completed"
            parts  = [{"type": "text", "text": str(response.payload.get("result", ""))}]
        else:
            status = "failed"
            parts  = [{"type": "text", "text": str(response.payload.get("reason", "error"))}]
        return {
            "id":     a2a_task_id,
            "status": {"state": status},
            "artifacts": [{"parts": parts}],
            "metadata": {
                "cdc_clock":      response.clock.to_dict(),
                "cdc_llm_summary": response.clock.llm_summary(),
            },
        }

    # ── Agent-Card (Google A2A Discovery) ─────────────────────────────────────

    @staticmethod
    def agent_card(base_url: str = "http://localhost:5050") -> dict:
        return {
            "name":        "AgentClaw v3",
            "description": "CDC-native multi-agent system with time-dilation awareness",
            "url":         base_url,
            "version":     "3.0.0",
            "capabilities": {
                "streaming":          False,
                "pushNotifications":  False,
                "stateTransitionHistory": True,
            },
            "skills": [
                {
                    "id":          "chat",
                    "name":        "Chat",
                    "description": "Send a message to an AgentClaw agent",
                    "inputModes":  ["text"],
                    "outputModes": ["text"],
                }
            ],
        }

    # ── Intern ────────────────────────────────────────────────────────────────

    def _extract_text(self, a2a_task: dict) -> str:
        try:
            return a2a_task["message"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError):
            return str(a2a_task)

    def _route(self, a2a_task: dict) -> str:
        skill = a2a_task.get("skill", {}).get("id", "")
        if skill and self.conductor:
            for ag in self.conductor.list_agents():
                if skill in getattr(ag, "skills", []):
                    return ag.agent_id
        return self.default_recipient or agent_ref("default")
