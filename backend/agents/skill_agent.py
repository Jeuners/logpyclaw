"""
backend/agents/skill_agent.py — SkillAgent wraps einen Skill als AsyncAgent.

Jeder registrierte Skill bekommt einen eigenen SkillAgent.
Agent-ID: "skill:<skill_id>"  (z.B. "skill:websearch")
"""
from __future__ import annotations

from backend.agents.base import AsyncAgent
from backend.core.protocol import Message
from backend.skills import Skill


class SkillAgent(AsyncAgent):
    def __init__(self, skill: Skill) -> None:
        super().__init__(f"skill:{skill.skill_id}", skill.skill_id.capitalize())
        self._skill = skill

    async def handle(self, msg: Message) -> Message:
        clock = self.advance_clock(msg.clock)
        query = msg.payload.get("content", "")
        try:
            result = await self._skill.execute(query)
            return Message.response(msg, result, clock=clock)
        except Exception as e:
            return Message.error(msg, f"[{self._skill.skill_id}] {e}", clock=clock)

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["skill_id"] = self._skill.skill_id
        d["faction"] = "gatherers"
        return d
