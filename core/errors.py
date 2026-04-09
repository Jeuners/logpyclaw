"""
Custom Exception Hierarchy for AgentClaw

Provides specific exception types for different error scenarios with
appropriate HTTP status codes for Flask response handling.
"""


class AgentClawError(Exception):
    """
    Base exception for all AgentClaw errors.

    Attributes:
        status_code: HTTP status code for API responses
        message: Human-readable error message
    """

    status_code = 500
    message = "Internal server error"

    def __init__(self, message: str | None = None, status_code: int | None = None):
        """
        Initialize AgentClawError.

        Args:
            message: Custom error message. Uses class default if None.
            status_code: Custom HTTP status code. Uses class default if None.
        """
        if message is not None:
            self.message = message
        if status_code is not None:
            self.status_code = status_code
        super().__init__(self.message)

    def to_dict(self) -> dict:
        """
        Serialize error to dictionary for API responses.

        Returns:
            Dictionary with error details.
        """
        return {
            "error": self.__class__.__name__,
            "message": self.message,
            "status_code": self.status_code,
        }


class AgentNotFoundError(AgentClawError):
    """Raised when an agent cannot be found."""

    status_code = 404
    message = "Agent not found"


class SkillExecutionError(AgentClawError):
    """Raised when skill execution fails."""

    status_code = 500
    message = "Skill execution failed"


class TaskNotFoundError(AgentClawError):
    """Raised when a task cannot be found."""

    status_code = 404
    message = "Task not found"


class TaskTimeoutError(AgentClawError):
    """Raised when a task exceeds timeout duration."""

    status_code = 408
    message = "Task timeout exceeded"


class ValidationError(AgentClawError):
    """Raised when input validation fails."""

    status_code = 422
    message = "Validation error"


class ProviderNotConfiguredError(AgentClawError):
    """Raised when a required provider is not configured."""

    status_code = 503
    message = "Provider not configured"


class M2MError(AgentClawError):
    """Raised when Machine-to-Machine (A2A) delegation fails."""

    status_code = 502
    message = "Agent-to-Agent delegation failed"
