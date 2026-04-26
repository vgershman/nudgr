"""Environment-driven settings."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Environment ---
    environment: Literal["development", "staging", "production"] = "development"
    log_level: str = "INFO"
    timezone: str = "UTC"

    # --- Database ---
    database_url: str = Field(
        default="postgresql+psycopg://nudgr:nudgr_dev@localhost:5433/nudgr"
    )

    # --- LLM ---
    anthropic_api_key: SecretStr = SecretStr("")
    openai_api_key: SecretStr = SecretStr("")
    llm_model_intent: str = "claude-haiku-4-5-20251001"
    whisper_model: str = "whisper-1"

    # --- Telegram ---
    telegram_bot_token: SecretStr = SecretStr("")
    telegram_user_id: int = 0

    # --- Scheduler ---
    scheduler_poll_interval_sec: int = 15
    max_audio_minutes: int = 10


settings = Settings()
