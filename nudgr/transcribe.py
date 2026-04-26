"""Whisper transcription via OpenAI API.

Telegram delivers voice/audio/video messages as files we download to disk
first; this module handles that download path + the API call.
"""

from __future__ import annotations

from pathlib import Path

from aiogram import Bot
from aiogram.types import Audio, Voice
from openai import AsyncOpenAI

from nudgr.config import settings
from nudgr.observability.logging import logger

# Telegram caps file downloads at 20MB via Bot API. Whisper accepts up to 25MB.
MAX_FILE_BYTES = 20 * 1024 * 1024


async def download_telegram_file(bot: Bot, file_id: str, dest: Path) -> Path:
    """Download a Telegram file by file_id to `dest`."""
    file_info = await bot.get_file(file_id)
    if file_info.file_size and file_info.file_size > MAX_FILE_BYTES:
        raise ValueError(
            f"File too large ({file_info.file_size} bytes > {MAX_FILE_BYTES})"
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    await bot.download_file(file_info.file_path, destination=dest)
    return dest


async def transcribe_file(
    openai_client: AsyncOpenAI,
    audio_path: Path,
    *,
    language: str | None = None,
) -> str:
    """Transcribe a local audio/video file via Whisper. Returns the text."""
    with audio_path.open("rb") as fh:
        result = await openai_client.audio.transcriptions.create(
            model=settings.whisper_model,
            file=fh,
            language=language,  # None = auto-detect
            response_format="text",
        )
    # response_format="text" returns a plain string; the SDK gives a string back.
    text = str(result).strip()
    logger.info(f"transcribe: {len(text)} chars from {audio_path.name}")
    return text
