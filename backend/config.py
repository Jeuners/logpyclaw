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

    # ── Storage ───────────────────────────────────────────────────────────────
    db_url: str = Field("sqlite:///./logpyclaw.db", description="SQLAlchemy DB URL")

    # ── Auth ──────────────────────────────────────────────────────────────────
    web_bridge_token: str = Field("", description="Auth token for /ext/dilles/v1/*")

    # ── Martin QC ─────────────────────────────────────────────────────────────
    martin_qc_enabled: bool = Field(True, description="Enable Martin's QC loop")
    martin_qc_min_score: int = Field(7, description="Minimum QC score (1-10)")
    martin_qc_max_retries: int = Field(2, description="Max retries in QC loop")
    martin_qc_auditor_id: str = Field("", description="Auditor agent ID for QC (empty = disabled)")


@lru_cache
def get_settings() -> Settings:
    return Settings()
