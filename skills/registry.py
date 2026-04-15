"""
Skill Registry for AgentClaw.

Manages skill discovery, registration, and availability checking.
Provides querying capabilities for matching skills based on agent config
and provider availability.
"""

import logging
from typing import Optional

from skills.base import BaseSkill


logger = logging.getLogger(__name__)


class SkillRegistry:
    """
    Central registry for managing and querying AgentClaw skills.

    Handles skill registration, discovery by matching agent message,
    and availability filtering based on provider configuration.
    """

    def __init__(self):
        """Initialize an empty skill registry."""
        self._skills: dict[str, BaseSkill] = {}

    def register(self, skill: BaseSkill) -> None:
        """
        Register a skill in the registry.

        Args:
            skill: BaseSkill instance to register.

        Raises:
            ValueError: If skill ID is empty or duplicate.
        """
        if not skill.id:
            raise ValueError("Skill must have a non-empty id")

        if skill.id in self._skills:
            logger.warning(f"Overwriting existing skill: {skill.id}")

        self._skills[skill.id] = skill
        logger.debug(f"Registered skill: {skill.id} ({skill.name})")

    def get(self, skill_id: str) -> Optional[BaseSkill]:
        """
        Get a skill by ID.

        Args:
            skill_id: Skill identifier.

        Returns:
            Skill instance or None if not found.
        """
        return self._skills.get(skill_id)

    def all(self) -> list[BaseSkill]:
        """
        Get all registered skills.

        Returns:
            List of all BaseSkill instances.
        """
        return list(self._skills.values())

    def find_matching(
        self,
        agent: dict,
        message: str,
    ) -> Optional[BaseSkill]:
        """
        Find the BEST matching skill for agent and message.

        Statt beim ersten Treffer zu stoppen (first-match), werden alle
        passenden Skills gesammelt und der mit dem LÄNGSTEN Regex-Match
        zurückgegeben. Längerer Match = spezifischere Regel = bessere Wahl.

        Beispiel: chrome_browser matched "https://linkedin.com/in/hgod" mit
        Länge 28, url_fetch nur "https://" mit Länge 8 → chrome_browser gewinnt.

        Args:
            agent: Agent configuration dictionary.
            message: User message to match against triggers.

        Returns:
            Best matching BaseSkill or None if no match found.
        """
        enabled_skill_ids = agent.get("skills", [])
        if not enabled_skill_ids:
            return None  # Agent hat keine Skills konfiguriert
        best_skill: Optional[BaseSkill] = None
        best_match_len = 0

        for skill in self._skills.values():
            if enabled_skill_ids and skill.id not in enabled_skill_ids:
                continue

            match_len = skill.longest_match(message)
            if match_len > 0 and match_len >= best_match_len:
                best_match_len = match_len
                best_skill = skill

        if best_skill:
            logger.debug(
                "Best skill match: %s (match_len=%d) for: %s",
                best_skill.id, best_match_len, message[:50]
            )
        return best_skill

    def available_for(
        self,
        agent: dict,
        providers: dict,
    ) -> list[BaseSkill]:
        """
        Get all available skills for an agent given provider configuration.

        A skill is available if:
        1. It's enabled for this agent (or agent has no restrictions)
        2. All its required providers are configured

        Args:
            agent: Agent configuration dictionary.
            providers: Provider configuration dictionary.

        Returns:
            List of available BaseSkill instances.
        """
        enabled_skill_ids = agent.get("skills", [])
        available_skills = []

        for skill in self._skills.values():
            # Check skill is enabled for this agent
            if enabled_skill_ids and skill.id not in enabled_skill_ids:
                continue

            # Check all required providers are available
            if skill.is_available(providers):
                available_skills.append(skill)
                logger.debug(f"Skill available: {skill.id}")

        return available_skills

    def list_for_api(self) -> list[dict]:
        """
        Get skill list formatted for API responses.

        Returns:
            List of skill dictionaries with id, name, icon, description, requires.
        """
        return [
            {
                "id": skill.id,
                "name": skill.name,
                "icon": skill.icon,
                "description": skill.description,
                "requires": skill.requires,
            }
            for skill in self._skills.values()
        ]
