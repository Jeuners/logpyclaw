"""
AgentClaw Configuration Settings

Pydantic-based configuration management with environment variable support.
"""

from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """
    Central configuration for AgentClaw application.

    Loads from environment variables with AGENTCLAW_ prefix.
    Defaults support local development setup.
    """

    # Server Configuration
    PORT: int = 5050
    HOST: str = "0.0.0.0"
    DEBUG: bool = False
    NATIVE_MODE: bool = False
    SECRET_KEY: str = "change-me-in-production"

    # CORS Configuration
    CORS_ORIGINS: list[str] = ["http://localhost:5050"]

    # Task Management
    TASK_TTL_SECONDS: int = 3600
    TASK_TIMEOUT_SECONDS: int = 3600
    MAX_HISTORY_PER_AGENT: int = 100
    # Watchdog: wie lange ein Task in "working" stecken darf bevor er als
    # hängend gilt und auto-failed wird (600s = 10 Min). ComfyUI-Renders
    # können mehrere Minuten dauern → konservativ halten.
    TASK_STALE_WORKING_SEC: int = 600
    # Operator-Supervisor: maximale Re-Entry-Runden bevor zwangsweise final
    # geantwortet werden muss. Verhindert Endlosschleifen bei schlechter
    # Acceptance-Krit definition.
    MAX_SUPERVISOR_TURNS: int = 5

    # Content & API Limits
    MAX_CONTENT_LENGTH: int = 32000
    RATE_LIMIT_CHAT: str = "20/minute"
    RATE_LIMIT_DEFAULT: str = "60/minute"

    # External Services
    OLLAMA_URL: str = "http://localhost:11434"
    COMFYUI_URL: str = "http://localhost:8188"
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379

    # TTS Providers
    MISTRAL_TTS_URL: str = "https://api.mistral.ai/v1/audio/speech"
    MISTRAL_VOICES_URL: str = "https://api.mistral.ai/v1/audio/voices"
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    GOOGLE_TTS_URL: str = "https://texttospeech.googleapis.com/v1/text:synthesize"

    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FILE: str = "agentclaw.log"
    LIVELOG: bool = True  # Live-Log Panel im Chat (Toggle via UI)

    # A2A Delegation
    A2A_OPENROUTER_REFERER: str = "http://localhost:5050"

    # API Keys (loaded from .env but not prefixed)
    MISTRAL_API_KEY: str = ""
    OPENROUTER_API_KEY: str = ""
    GOOGLE_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    OPENAI_API_KEY: str = ""

    model_config = {
        "env_file": ".env",
        "env_prefix": "AGENTCLAW_",
        "extra": "ignore",  # Ignore unknown fields from .env
    }


# Global settings singleton
settings = Settings()
