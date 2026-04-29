"""Text → structured reminder intent via Haiku."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from nudgr.config import settings
from nudgr.llm.prompts.parse_intent import SYSTEM_PROMPT, render_user_prompt
from nudgr.llm.router import LLMRouter
from nudgr.observability.logging import logger
from nudgr.recurrence import next_occurrence, normalize_rule


@dataclass
class ParsedIntent:
    intent: str  # "remind" | "list" | "cancel" | "done" | "unclear"
    target_text: str
    fire_at: datetime | None  # already resolved to a UTC datetime
    # v1: optional recurrence rule (already validated/normalized). Null = one-shot.
    recurrence: dict | None
    needs_clarification: bool
    clarification_question: str | None
    raw: dict


def _user_tz(tz_name: str | None = None) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name or settings.timezone)
    except Exception:
        return ZoneInfo("UTC")


def _clean_json(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1]
        if s.startswith("json"):
            s = s[4:]
        s = s.strip()
        if s.endswith("```"):
            s = s[:-3].strip()
    if not s.startswith("{"):
        start = s.find("{")
        end = s.rfind("}")
        if start >= 0 and end > start:
            s = s[start : end + 1]
    return s


def _resolve_fire_at(when: dict, tz_name: str) -> datetime | None:
    """Convert the parser's `when` dict into a UTC datetime, or None if unclear."""
    if not isinstance(when, dict):
        return None
    offset_min = when.get("offset_minutes")
    if isinstance(offset_min, (int, float)) and offset_min > 0:
        return datetime.now(timezone.utc) + timedelta(minutes=int(offset_min))
    abs_iso = when.get("absolute_iso")
    if isinstance(abs_iso, str) and abs_iso:
        try:
            dt = datetime.fromisoformat(abs_iso)
            if dt.tzinfo is None:
                # Bare ISO with no offset — assume user's TZ.
                dt = dt.replace(tzinfo=_user_tz(tz_name))
            return dt.astimezone(timezone.utc)
        except ValueError:
            logger.warning(f"parser: bad absolute_iso {abs_iso!r}")
            return None
    return None


def _extract_recurrence(raw: dict | None, tz_name: str) -> dict | None:
    """Pull `recurrence` out of the parser response and normalize it."""
    if not isinstance(raw, dict):
        return None
    rec_raw = raw.get("recurrence")
    if not isinstance(rec_raw, dict):
        return None
    if not rec_raw.get("kind"):
        return None
    # Inject tz so the rule is self-contained when stored.
    rec_raw = {**rec_raw, "tz": rec_raw.get("tz") or tz_name}
    return normalize_rule(rec_raw, default_tz=tz_name)


async def parse(
    user_text: str, router: LLMRouter, *, tz_name: str | None = None
) -> ParsedIntent:
    """Parse a user message into a structured ParsedIntent.

    `tz_name` is the user's IANA timezone (resolved from the User row by the
    caller). Falls back to the global settings.timezone when omitted.
    """
    effective_tz = tz_name or settings.timezone
    tz = _user_tz(effective_tz)
    now_local = datetime.now(tz)
    current_iso = now_local.isoformat(timespec="seconds")

    user_prompt = render_user_prompt(
        user_text=user_text, current_iso=current_iso, user_tz=effective_tz
    )
    response = await router.chat(
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
        max_tokens=512,
        temperature=0.1,
    )
    raw_text = response.text
    try:
        data = json.loads(_clean_json(raw_text))
    except json.JSONDecodeError as e:
        logger.warning(f"parser: JSON decode failed ({e}); raw={raw_text[:200]!r}")
        return ParsedIntent(
            intent="unclear",
            target_text=user_text[:200],
            fire_at=None,
            recurrence=None,
            needs_clarification=True,
            clarification_question="I didn't catch that — can you rephrase?",
            raw={},
        )

    intent = str(data.get("intent") or "unclear").lower()
    if intent not in ("remind", "list", "cancel", "done", "edit", "unclear"):
        intent = "unclear"

    target_text = str(data.get("target_text") or "").strip()[:300]
    needs_clar = bool(data.get("needs_clarification") or False)
    clar_q = data.get("clarification_question")
    clar_q = str(clar_q).strip() if clar_q else None
    fire_at = _resolve_fire_at(data.get("when") or {}, effective_tz)
    recurrence = _extract_recurrence(data, effective_tz)

    # If the recurrence is valid but the model forgot to fill `when`, derive the
    # first fire from the rule itself. This is the common case for "every day at 9am".
    if recurrence is not None and fire_at is None:
        # Seed fired_count=1 so chained instances start counting from 2.
        seeded = {**recurrence, "fired_count": int(recurrence.get("fired_count", 1))}
        recurrence = seeded
        fire_at = next_occurrence(recurrence, datetime.now(timezone.utc), effective_tz)

    # Stamp fired_count=1 on freshly-created recurring rules — chained instances
    # increment via advance_for_chain in the scheduler.
    if recurrence is not None and "fired_count" not in recurrence:
        recurrence = {**recurrence, "fired_count": 1}

    # Sanity: if intent=remind but no fire_at and no clarification, force clarification.
    if intent == "remind" and fire_at is None and not needs_clar:
        needs_clar = True
        clar_q = clar_q or "When should I remind you? Try '30m', '2h', or 'tomorrow 9am'."

    # Edit intent must come with a new `when`. Otherwise force clarification.
    if intent == "edit" and fire_at is None and not needs_clar:
        needs_clar = True
        clar_q = clar_q or "What time should I move it to?"

    return ParsedIntent(
        intent=intent,
        target_text=target_text,
        fire_at=fire_at,
        recurrence=recurrence,
        needs_clarification=needs_clar,
        clarification_question=clar_q,
        raw=data,
    )
