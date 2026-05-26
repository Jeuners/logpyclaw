"""
backend/skills/ — Skill-Interface und Registry.

Ein Skill ist eine ausführbare Fähigkeit (Tool), die ein Agent über den
SkillAgent aufrufen kann. Jeder Skill implementiert execute(query) → str.

CONFIG_FIELDS-System:
  Subklassen deklarieren CONFIG_FIELDS = (SkillConfigField(...), ...).
  Der Skill.__init__ befüllt self.config aus kwargs → ENV-Var → default.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SkillConfigField:
    name: str
    env: str = ""
    default: Any = None
    required: bool = False
    secret: bool = False


class Skill(ABC):
    """Basis-Interface für alle Skills."""

    skill_id: str
    description: str = ""
    CONFIG_FIELDS: tuple[SkillConfigField, ...] = ()

    def __init__(self, **kwargs: Any) -> None:
        self.config: dict[str, Any] = {}
        for f in self.CONFIG_FIELDS:
            val = kwargs.get(f.name)
            if not val and f.env:
                val = os.environ.get(f.env, "")
            if not val and f.default is not None:
                val = f.default
            self.config[f.name] = val or ""

    @abstractmethod
    async def execute(self, query: str) -> str:
        """Führt den Skill aus und gibt das Ergebnis als String zurück."""

    def to_dict(self) -> dict:
        return {"skill_id": self.skill_id, "description": self.description}
