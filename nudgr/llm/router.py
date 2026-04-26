"""Provider router for Anthropic chat + OpenAI Whisper."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import anthropic
import httpx
from openai import AsyncOpenAI

from nudgr.config import settings


@dataclass
class LLMResponse:
    text: str
    model: str
    input_tokens: int
    output_tokens: int


class LLMRouter:
    """One-process router. Anthropic for chat, OpenAI for Whisper."""

    def __init__(self) -> None:
        self._anthropic = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key.get_secret_value(),
            max_retries=4,
            timeout=httpx.Timeout(connect=10.0, read=60.0, write=15.0, pool=10.0),
        )
        self._openai = AsyncOpenAI(
            api_key=settings.openai_api_key.get_secret_value(),
            max_retries=3,
        )

    @property
    def openai(self) -> AsyncOpenAI:
        return self._openai

    async def chat(
        self,
        *,
        system: str | None = None,
        messages: list[dict[str, Any]],
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> LLMResponse:
        model = model or settings.llm_model_intent
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        response = await self._anthropic.messages.create(**kwargs)
        text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        )
        usage = response.usage
        return LLMResponse(
            text=text,
            model=model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )

    async def ping_anthropic(self) -> str:
        resp = await self.chat(
            messages=[{"role": "user", "content": "Reply with the single word: pong"}],
            max_tokens=10,
        )
        return resp.text.strip()

    async def ping_openai(self) -> bool:
        # Cheapest call: list models. Avoids burning Whisper quota on a healthcheck.
        await self._openai.models.list()
        return True
