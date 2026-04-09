"""
Security utilities for AgentClaw.

Provides path validation, API key masking, and agent ID sanitization
to protect against path traversal, credential exposure, and injection attacks.
"""

from pathlib import Path
import re

from core.errors import ValidationError


# Safe base directory for downloads
SAFE_DOWNLOAD_BASE = Path("~/Downloads/AgentClaw").expanduser().resolve()


def safe_path(filename: str) -> Path:
    """
    Validate and resolve a filename to a safe download path.

    Prevents path traversal attacks by ensuring the resolved path
    remains within SAFE_DOWNLOAD_BASE.

    Args:
        filename: Filename or relative path to validate.

    Returns:
        Resolved Path object within SAFE_DOWNLOAD_BASE.

    Raises:
        ValueError: If the resolved path attempts to escape the safe base directory.

    Example:
        >>> path = safe_path("document.pdf")
        >>> path.parent == SAFE_DOWNLOAD_BASE
        True
    """
    try:
        # Resolve relative to safe base
        full_path = (SAFE_DOWNLOAD_BASE / filename).resolve()

        # Ensure it stays within the safe base
        full_path.relative_to(SAFE_DOWNLOAD_BASE)

        return full_path
    except ValueError as e:
        raise ValueError(
            f"Path traversal detected: '{filename}' resolves outside safe directory"
        ) from e


def mask_api_key(key: str) -> str:
    """
    Mask API key for safe logging and display.

    Shows only first 3 and last 4 characters with ellipsis.
    Requires minimum 8 characters for masking; shorter keys returned unchanged.

    Args:
        key: API key to mask.

    Returns:
        Masked key in format "sk-...1234" or unchanged if too short.

    Example:
        >>> mask_api_key("sk-proj-abcd1234efgh5678")
        'sk-...5678'
        >>> mask_api_key("short")
        'short'
    """
    if len(key) < 8:
        return key

    # Show first 3 chars + last 4 chars with ellipsis
    return f"{key[:3]}...{key[-4:]}"


def sanitize_agent_id(agent_id: str) -> str:
    """
    Sanitize agent ID to prevent injection attacks.

    Only allows alphanumeric characters and hyphens.
    Minimum 1 character, maximum 64 characters.

    Args:
        agent_id: Agent ID to sanitize.

    Returns:
        Sanitized agent ID.

    Raises:
        ValidationError: If agent ID contains invalid characters or invalid length.

    Example:
        >>> sanitize_agent_id("my-agent-123")
        'my-agent-123'
        >>> sanitize_agent_id("agent@123")
        Traceback (most recent call last):
            ...
        ValidationError: Invalid agent ID format
    """
    if not agent_id or not isinstance(agent_id, str):
        raise ValidationError("Agent ID must be a non-empty string")

    if len(agent_id) > 64:
        raise ValidationError("Agent ID must not exceed 64 characters")

    # Only allow alphanumeric and hyphens
    if not re.match(r"^[a-zA-Z0-9\-]+$", agent_id):
        raise ValidationError(
            "Agent ID can only contain alphanumeric characters and hyphens"
        )

    return agent_id
