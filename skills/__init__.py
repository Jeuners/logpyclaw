"""
skills/__init__.py — Skill-Package.

Öffentliche API: BaseSkill, SkillResult, SkillRegistry.
Konkrete Skills werden über ihre Submodule importiert:

    from skills.comfyui import run_comfyui_sync
    from skills.registry import SkillRegistry
    from skills.base import BaseSkill

Alle früheren Legacy-Aliasse mit Unterstrich (_run_*, _make_*, FILE_TRIGGERS etc.)
wurden entfernt — keine aktive Call-Site benutzt sie mehr.
"""
from .base import BaseSkill, SkillResult
from .registry import SkillRegistry

__all__ = ["BaseSkill", "SkillResult", "SkillRegistry"]
