"""
backend/skills/ — Skill-Interface und Registry.

Ein Skill ist eine ausführbare Fähigkeit (Tool), die ein Agent über den
SkillAgent aufrufen kann. Jeder Skill implementiert execute(query) → str.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class Skill(ABC):
    """Basis-Interface für alle Skills."""

    skill_id: str  # z.B. "websearch", "calculator"
    description: str = ""

    @abstractmethod
    async def execute(self, query: str) -> str:
        """Führt den Skill aus und gibt das Ergebnis als String zurück."""

    def to_dict(self) -> dict:
        return {"skill_id": self.skill_id, "description": self.description}
