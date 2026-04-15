"""
Base Skill classes for AgentClaw skill system.

Provides BaseSkill abstract class and SkillResult dataclass for
implementing and executing skills with provider dependency management.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import logging
import re


logger = logging.getLogger(__name__)


@dataclass
class SkillResult:
    """
    Result of a skill execution.

    Attributes:
        text: Text output from the skill. None if no text output.
        image: Base64-encoded image or image URL. None if no image output.
        error: Error message if skill execution failed. None on success.
        skill_used: ID of the skill that was executed.
        metadata: Additional metadata about skill execution.
    """

    text: str | None = None
    image: str | None = None
    error: str | None = None
    skill_used: str = ""
    metadata: dict = field(default_factory=dict)

    @property
    def success(self) -> bool:
        """
        Check if skill execution was successful.

        Returns:
            True if no error occurred, False otherwise.
        """
        return self.error is None


class BaseSkill(ABC):
    """
    Abstract base class for all AgentClaw skills.

    Subclasses must implement the execute() method.
    Provides trigger matching and provider availability checking.

    Attributes:
        id: Unique skill identifier.
        name: Human-readable skill name.
        icon: Material Design icon name (default: "build").
        description: One-line skill description.
        triggers: List of regex patterns to auto-trigger the skill.
        requires: List of provider names required for this skill.
    """

    id: str = ""
    name: str = ""
    icon: str = "build"
    description: str = ""
    triggers: list[str] = []
    requires: list[str] = []

    @abstractmethod
    def execute(
        self,
        agent: dict,
        message: str,
        **context,
    ) -> SkillResult:
        """
        Execute the skill with given inputs.

        Subclasses must implement this method to perform the skill's logic.

        Args:
            agent: Agent configuration dictionary.
            message: User message or input text.
            **context: Additional context (task_id, user_id, etc.).

        Returns:
            SkillResult with execution output or error.
        """
        ...

    def matches(self, message: str) -> bool:
        """
        Check if message matches any skill triggers.

        Triggers are matched case-insensitively against the message.

        Args:
            message: Message to check against triggers.

        Returns:
            True if message matches any trigger pattern, False otherwise.

        Example:
            >>> skill.triggers = ["fetch", "url"]
            >>> skill.matches("Please fetch https://example.com")
            True
        """
        if not self.triggers:
            return False

        return any(
            re.search(trigger, message, re.IGNORECASE) for trigger in self.triggers
        )

    def longest_match(self, message: str) -> int:
        """
        Gibt die Länge des längsten Trigger-Matches zurück.
        Wird von SkillRegistry.find_matching() genutzt um den spezifischsten Skill zu wählen.

        Returns:
            Länge des längsten Matches, 0 wenn kein Trigger passt.
        """
        if not self.triggers:
            return 0
        best = 0
        for trigger in self.triggers:
            m = re.search(trigger, message, re.IGNORECASE)
            if m:
                best = max(best, len(m.group(0)))
        return best

    def is_available(self, providers: dict) -> bool:
        """
        Check if all required providers are configured.

        A provider is considered available if it has at least one of:
        - api_key: API authentication key
        - url: Service endpoint URL
        - bot_token: Bot authentication token

        Args:
            providers: Dictionary mapping provider names to their config dicts.

        Returns:
            True if all required providers are configured, False otherwise.

        Example:
            >>> skill.requires = ["openai", "google"]
            >>> providers = {
            ...     "openai": {"api_key": "sk-123"},
            ...     "google": {"api_key": "goog-456"}
            ... }
            >>> skill.is_available(providers)
            True
        """
        for required_provider in self.requires:
            provider_config = providers.get(required_provider, {})

            # Check if provider has at least one required credential
            has_api_key = bool(provider_config.get("api_key"))
            has_url = bool(provider_config.get("url"))
            has_bot_token = bool(provider_config.get("bot_token"))

            if not (has_api_key or has_url or has_bot_token):
                return False

        return True
