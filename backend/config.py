"""
backend/config.py — Typisierte Konfiguration via pydantic-settings.

Lädt Werte aus .env (falls vorhanden), dann aus Umgebungsvariablen.
Singleton via get_settings() — wird einmal beim Import initialisiert.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── LLM ───────────────────────────────────────────────────────────────────
    ollama_url: str = Field("http://localhost:11434", description="Ollama base URL")
    ollama_model: str = Field("gemma4:e4b", description="Default Ollama model")
    anthropic_api_key: str = Field("", description="Anthropic API key")
    openai_api_key: str = Field("", description="OpenAI API key")
    openrouter_api_key: str = Field("", description="OpenRouter API key")
    openrouter_default_model: str = Field("deepseek/deepseek-v4-flash:free", description="Default OpenRouter model")
    groq_api_key: str = Field("", description="Groq API key (einzeln oder als Fallback)")
    groq_api_keys: str = Field("", description="Groq API key-Pool (kommagetrennt)")

    @property
    def groq_key_pool(self) -> list[str]:
        """Alle konfigurierten Groq-Keys, dedupliziert."""
        keys = [k.strip() for k in self.groq_api_keys.split(",") if k.strip()]
        if self.groq_api_key and self.groq_api_key not in keys:
            keys.insert(0, self.groq_api_key)
        return keys

    # ── Storage ───────────────────────────────────────────────────────────────
    db_url: str = Field("sqlite:///./logpyclaw.db", description="SQLAlchemy DB URL")

    # ── Auth ──────────────────────────────────────────────────────────────────
    web_bridge_token: str = Field("", description="Auth token for /ext/dilles/v1/*")

    # ── Skills ────────────────────────────────────────────────────────────────
    comfyui_url: str = Field("http://192.168.4.15:8000", description="ComfyUI endpoint")

    # ── Server ────────────────────────────────────────────────────────────────
    host: str = Field("0.0.0.0", description="Bind host")
    port: int = Field(6060, description="HTTP port")

    # ── Martin QC ─────────────────────────────────────────────────────────────
    martin_qc_enabled: bool = Field(True, description="Enable Martin's QC loop")
    martin_qc_min_score: int = Field(7, description="Minimum QC score (1-10)")
    martin_qc_max_retries: int = Field(2, description="Max retries in QC loop")
    martin_qc_auditor_id: str = Field("", description="Auditor agent ID for QC (empty = disabled)")


@lru_cache
def get_settings() -> Settings:
    return Settings()
