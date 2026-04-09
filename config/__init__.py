"""
Configuration package for AgentClaw.

Exports:
    - settings: Global settings singleton from pydantic
    - setup_logging: Function to initialize application logging
"""

from config.settings import settings
from config.logging_config import setup_logging

__all__ = ["settings", "setup_logging"]
