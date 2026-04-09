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
        Find the first skill matching agent config and message triggers.

        Checks:
        1. Skill is in agent's skill list (or agent has no skill restrictions)
        2. Skill triggers match the message

        Args:
            agent: Agent configuration dictionary.
            message: User message to match against triggers.

        Returns:
            First matching BaseSkill or None if no match found.
        """
        # Get agent's enabled skills (empty list = all skills allowed)
        enabled_skill_ids = agent.get("skills", [])

        for skill in self._skills.values():
            # If agent has skill restrictions, check if this skill is enabled
            if enabled_skill_ids and skill.id not in enabled_skill_ids:
                continue

            # Check if skill triggers match the message
            if skill.matches(message):
                logger.debug(
                    f"Found matching skill: {skill.id} for message: {message[:50]}"
                )
                return skill

        return None

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
