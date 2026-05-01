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
    # v0 single-tenant fallback. v3: prefer telegram_admin_ids; this is kept so
    # existing deployments keep working without an env edit.
    telegram_user_id: int = 0
    # v3: comma-separated Telegram user IDs auto-promoted to active+admin on
    # /start. Stored as a raw string and parsed by `admin_ids()` so
    # pydantic-settings doesn't try to JSON-decode it.
    #   TELEGRAM_ADMIN_IDS=12345,67890
    telegram_admin_ids: str = ""
    # v3.1: when True (default), any new user who messages the bot is
    # auto-activated on first sight — no invite code needed. Flip to False
    # to require explicit invite redemption (the v3-style private mode).
    # Admin allowlist + /invite continue to work in either mode.
    open_registration: bool = True

    # --- Scheduler ---
    scheduler_poll_interval_sec: int = 15
    max_audio_minutes: int = 10
    # v3: default lifetime for codes generated via /invite. 0 = no expiry.
    invite_default_ttl_days: int = 7
    # v2: digest tick interval. Lower => closer to user-configured time.
    digest_tick_interval_sec: int = 60

    def admin_ids(self) -> set[int]:
        """All Telegram user IDs treated as admins (env list ∪ legacy single-tenant)."""
        ids: set[int] = set()
        for piece in (self.telegram_admin_ids or "").split(","):
            piece = piece.strip()
            if not piece:
                continue
            try:
                ids.add(int(piece))
            except ValueError:
                # Skip junk gracefully — doctor will surface mismatches separately.
                continue
        if self.telegram_user_id:
            ids.add(self.telegram_user_id)
        return ids


settings = Settings()
