"""Long-running aiogram bot. Polls Telegram, handles messages + callbacks.

Message flow (text/voice/video):
  user → handle_message
       → if voice/audio/video_note/video: download + Whisper transcribe
       → detect_locale + persist preferred_locale
       → parser.parse(text, tz_name=user.timezone) → ParsedIntent
       → dispatch by intent (remind / list / cancel / done / unclear)
       → confirm to user, persist Reminder if intent=remind
       → refresh pinned active-tasks summary

Callback flow:
  user taps Done/Snooze/Stop on a fired reminder
       → cb_action → mark_done/snooze/mark_stopped
       → edit message to remove buttons + show outcome
       → refresh pinned summary
"""

from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from nudgr.config import settings
from nudgr.db.models import Reminder, User
from nudgr.db.session import session_scope
from nudgr.i18n import detect_locale, label, supported_locales
from nudgr.llm.router import LLMRouter
from nudgr.observability.logging import logger
from nudgr.parser import ParsedIntent, parse
from nudgr.recurrence import rule_summary
from nudgr.scheduler import (
    ALL_CALLBACK_PREFIXES,
    CB_DONE,
    CB_SNOOZE_2H,
    CB_SNOOZE_30,
    CB_SNOOZE_TOM,
    CB_STOP,
    mark_done,
    mark_stopped,
    parse_callback_data,
    run_scheduler,
    snooze,
    snooze_until_tomorrow_9am,
)
from nudgr.summary import update_pinned_summary
from nudgr.transcribe import download_telegram_file, transcribe_file


# ---------- auth + user upsert ----------


def _is_authorized(user_id: int | None) -> bool:
    return user_id is not None and user_id == settings.telegram_user_id


def _upsert_user(
    telegram_user_id: int,
    telegram_username: str | None,
    *,
    detected_locale: str | None = None,
) -> tuple[UUID, str, str]:
    """Find-or-create a User row. Returns (internal_uuid, locale, tz_name).

    If `detected_locale` is provided AND differs from the stored value, persist
    it. v1: locale auto-switches with the user's language.
    """
    with session_scope() as s:
        existing = s.execute(
            select(User).where(User.telegram_user_id == telegram_user_id)
        ).scalar_one_or_none()
        if existing is not None:
            if telegram_username and existing.telegram_username != telegram_username:
                existing.telegram_username = telegram_username
            if detected_locale and detected_locale in supported_locales():
                if existing.preferred_locale != detected_locale:
                    existing.preferred_locale = detected_locale
            return existing.id, existing.preferred_locale or "en", existing.timezone or "UTC"
        user = User(
            telegram_user_id=telegram_user_id,
            telegram_username=telegram_username,
            timezone=settings.timezone,
            preferred_locale=detected_locale if detected_locale in supported_locales() else "en",
        )
        s.add(user)
        s.flush()
        return user.id, user.preferred_locale, user.timezone


def _get_user_state(user_id: UUID) -> tuple[str, str]:
    """Return (locale, tz_name) for an existing user. Defaults if missing."""
    with session_scope() as s:
        user = s.get(User, user_id)
        if user is None:
            return "en", settings.timezone
        return user.preferred_locale or "en", user.timezone or "UTC"


# ---------- safe answer helper ----------


async def _safe_answer(query: CallbackQuery, text: str = "", *, alert: bool = False) -> None:
    try:
        await query.answer(text, show_alert=alert)
    except TelegramBadRequest as e:
        msg = str(e).lower()
        if "query is too old" in msg or "query id is invalid" in msg:
            logger.info(f"cb answer skipped (stale): {e}")
            return
        logger.warning(f"cb answer failed: {e}")
    except TelegramAPIError as e:
        logger.warning(f"cb answer failed: {e}")


async def _refresh_summary(bot: Bot, user_id: UUID, chat_id: int, locale: str) -> None:
    """Best-effort pinned summary refresh — never propagates errors."""
    try:
        await update_pinned_summary(bot, user_id, chat_id, locale)
    except Exception:  # noqa: BLE001
        logger.exception(f"summary refresh failed for user={user_id}")


# ---------- transcription helpers ----------


async def _transcribe_message(
    bot: Bot, message: Message, router: LLMRouter, locale: str
) -> str | None:
    """Download voice/audio/video file from a Telegram message and transcribe it.
    Returns the transcript or None if the message has no audio attached."""
    file_id: str | None = None
    kind: str = "text"
    if message.voice:
        file_id = message.voice.file_id
        kind = "voice"
        duration = message.voice.duration or 0
    elif message.audio:
        file_id = message.audio.file_id
        kind = "audio"
        duration = message.audio.duration or 0
    elif message.video_note:
        file_id = message.video_note.file_id
        kind = "video_note"
        duration = message.video_note.duration or 0
    elif message.video:
        file_id = message.video.file_id
        kind = "video"
        duration = message.video.duration or 0
    else:
        return None

    if duration > settings.max_audio_minutes * 60:
        await message.answer(
            label(
                "audio_too_long",
                locale,
                minutes=duration // 60,
                seconds=duration % 60,
                max=settings.max_audio_minutes,
            )
        )
        return ""

    # Map media type to a file extension Whisper accepts.
    ext_map = {"voice": ".ogg", "audio": ".mp3", "video_note": ".mp4", "video": ".mp4"}
    ext = ext_map.get(kind, ".ogg")
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / f"{file_id}{ext}"
        try:
            await download_telegram_file(bot, file_id, dest)
        except ValueError as e:
            await message.answer(f"Couldn't download that — {e}")
            return ""
        text = await transcribe_file(router.openai, dest)
    logger.info(f"transcribed {kind} ({duration}s) → {len(text)} chars")
    return text


# ---------- formatting helpers ----------


def _format_eta(fire_at: datetime, locale: str, tz_name: str) -> str:
    delta = fire_at - datetime.now(timezone.utc)
    mins = int(delta.total_seconds() / 60)
    if mins < 60:
        return label("eta_in_min", locale, n=max(0, mins))
    if mins < 60 * 24:
        return label("eta_in_hour_min", locale, h=mins // 60, m=mins % 60)
    try:
        tz = ZoneInfo(tz_name)
        local = fire_at.astimezone(tz)
        return label("eta_at_iso", locale, iso=local.strftime("%Y-%m-%d %H:%M"))
    except Exception:
        return label("eta_at_iso", locale, iso=fire_at.strftime("%Y-%m-%d %H:%M UTC"))


def _format_local_time(dt: datetime, tz_name: str) -> str:
    try:
        tz = ZoneInfo(tz_name)
        return dt.astimezone(tz).strftime("%H:%M")
    except Exception:
        return dt.strftime("%H:%M UTC")


# ---------- message handlers ----------


async def cmd_start(message: Message, bot: Bot) -> None:
    uid = message.from_user.id if message.from_user else 0
    if not _is_authorized(uid):
        await message.answer("Not authorized.")
        return
    username = message.from_user.username if message.from_user else None
    detected = detect_locale(message.text or "")
    user_id, locale, _tz = _upsert_user(uid, username, detected_locale=detected)
    await message.answer(label("welcome", locale), parse_mode=ParseMode.HTML)
    await _refresh_summary(bot, user_id, message.chat.id, locale)


async def cmd_help(message: Message, bot: Bot) -> None:
    await cmd_start(message, bot)


async def cmd_list(message: Message, bot: Bot) -> None:
    if not _is_authorized(message.from_user.id if message.from_user else None):
        return
    detected = detect_locale(message.text or "")
    user_id, locale, _tz = _upsert_user(
        message.from_user.id,
        message.from_user.username if message.from_user else None,
        detected_locale=detected,
    )
    # /list refreshes the pinned summary instead of dumping a separate message.
    await _refresh_summary(bot, user_id, message.chat.id, locale)


async def cmd_tz(message: Message, bot: Bot) -> None:
    """`/tz` shows current timezone; `/tz <Region/City>` sets it."""
    if not _is_authorized(message.from_user.id if message.from_user else None):
        return
    detected = detect_locale(message.text or "")
    user_id, locale, current_tz = _upsert_user(
        message.from_user.id,
        message.from_user.username if message.from_user else None,
        detected_locale=detected,
    )

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer(
            label("tz_current", locale, tz=current_tz), parse_mode=ParseMode.HTML
        )
        return
    candidate = parts[1].strip()
    try:
        ZoneInfo(candidate)
    except Exception:
        await message.answer(
            label("tz_invalid", locale, input=candidate), parse_mode=ParseMode.HTML
        )
        return
    with session_scope() as s:
        user = s.get(User, user_id)
        if user is not None:
            user.timezone = candidate
    await message.answer(
        label("tz_set", locale, tz=candidate), parse_mode=ParseMode.HTML
    )
    await _refresh_summary(bot, user_id, message.chat.id, locale)


async def handle_message(message: Message, bot: Bot, router: LLMRouter) -> None:
    """Main entry for free-form text/voice/video messages."""
    if not _is_authorized(message.from_user.id if message.from_user else None):
        return
    raw_text = message.text or message.caption or ""
    detected = detect_locale(raw_text)
    user_id, locale, tz_name = _upsert_user(
        message.from_user.id,
        message.from_user.username if message.from_user else None,
        detected_locale=detected,
    )
    chat_id = message.chat.id

    # Resolve text — either from the message body or from a transcript.
    transcript: str | None = None
    if message.text and not message.text.startswith("/"):
        text = message.text.strip()
        kind = "text"
    else:
        transcript = await _transcribe_message(bot, message, router, locale)
        if transcript is None:
            return  # no media, nothing to do
        if not transcript:
            return  # already errored out (size limit, download fail)
        text = transcript
        kind = (
            "voice" if message.voice
            else "audio" if message.audio
            else "video_note" if message.video_note
            else "video" if message.video
            else "text"
        )
    if not text:
        return

    # Re-detect locale from the resolved text (matters most for transcripts).
    locale = detect_locale(text) or locale
    with session_scope() as s:
        user = s.get(User, user_id)
        if user is not None and user.preferred_locale != locale and locale in supported_locales():
            user.preferred_locale = locale

    parsed: ParsedIntent
    try:
        parsed = await parse(text, router, tz_name=tz_name)
    except Exception as e:  # noqa: BLE001
        logger.exception(f"parse failed: {e}")
        await message.answer(label("parse_failed", locale))
        return

    if parsed.intent == "list":
        await cmd_list(message, bot)
        return

    if parsed.intent == "unclear" or parsed.needs_clarification:
        prompt = parsed.clarification_question or label("when_clarify", locale)
        await message.answer(f"❓ {prompt}")
        return

    if parsed.intent == "cancel":
        await _handle_cancel_or_done(
            message, bot, user_id, parsed.target_text, mark="cancelled", locale=locale
        )
        return
    if parsed.intent == "done":
        await _handle_cancel_or_done(
            message, bot, user_id, parsed.target_text, mark="done", locale=locale
        )
        return

    if parsed.intent == "remind":
        if parsed.fire_at is None:
            await message.answer(label("when_clarify", locale))
            return
        # Persist reminder.
        with session_scope() as s:
            reminder = Reminder(
                user_id=user_id,
                chat_id=chat_id,
                text=parsed.target_text or text[:120],
                transcript=transcript,
                input_kind=kind,
                fire_at=parsed.fire_at,
                next_ping_at=parsed.fire_at,
                recurrence=parsed.recurrence,
                status="active",
            )
            s.add(reminder)
            s.flush()
            reminder_text = reminder.text
        # Confirm — different copy for one-shot vs recurring.
        if parsed.recurrence:
            kind_rec = parsed.recurrence.get("kind")
            t = parsed.recurrence.get("time", "")
            if kind_rec == "daily":
                confirm = label("got_it_recurring_daily", locale, time=t)
            else:
                wd = parsed.recurrence.get("weekdays") or []
                # Friendly weekday rendering (uses the rule_summary helper for
                # weekday list / shorthand idioms).
                rs = rule_summary(parsed.recurrence, locale)
                # `rs` already reads "weekdays HH:MM" / "Mon,Tue HH:MM" — strip the
                # trailing time to feed `days` separately.
                days_part = rs.rsplit(" ", 1)[0] if rs else ",".join(str(d) for d in wd)
                confirm = label("got_it_recurring_weekly", locale, days=days_part, time=t)
        else:
            eta = _format_eta(parsed.fire_at, locale, tz_name)
            # Prefer the eta-style "in 15m" for short horizons; absolute for the rest.
            delta_min = int((parsed.fire_at - datetime.now(timezone.utc)).total_seconds() / 60)
            if delta_min < 60 * 24:
                confirm = label("got_it_in", locale, eta=eta)
            else:
                confirm = label("got_it_at", locale, at=eta)
        await message.answer(
            f"{confirm}\n📌 <b>{reminder_text}</b>", parse_mode=ParseMode.HTML
        )
        await _refresh_summary(bot, user_id, chat_id, locale)
        return

    # Should be unreachable.
    await message.answer(label("parse_failed", locale))


async def _handle_cancel_or_done(
    message: Message,
    bot: Bot,
    user_id: UUID,
    hint: str,
    *,
    mark: str,
    locale: str,
) -> None:
    """Find an active reminder loosely matching `hint` and mark it done/cancelled.

    Simple matching: case-insensitive substring on `text`. If multiple match,
    pick the soonest. If none match, ask the user to be more specific.
    """
    if not hint:
        await message.answer(label("when_clarify", locale))
        return
    needle = hint.strip().lower()
    target_id: UUID | None = None
    text_out: str | None = None
    with session_scope() as s:
        candidates = list(
            s.execute(
                select(Reminder)
                .where(Reminder.user_id == user_id)
                .where(Reminder.status == "active")
                .order_by(Reminder.next_ping_at)
            ).scalars()
        )
        match = next((r for r in candidates if needle in (r.text or "").lower()), None)
        if match is None:
            await message.answer(label("no_match", locale, hint=hint))
            return
        target_id = match.id
        text_out = match.text

    # Use the same path as the buttons so recurrence chaining triggers.
    if mark == "done":
        ok = mark_done(target_id)
        key = "done_match"
    else:
        ok = mark_stopped(target_id)
        key = "cancelled_match"
    if not ok:
        await message.answer(label("decision_already_closed", locale))
        return
    await message.answer(
        label(key, locale, text=text_out or ""), parse_mode=ParseMode.HTML
    )
    await _refresh_summary(bot, user_id, message.chat.id, locale)


# ---------- callback handlers ----------


async def cb_action(query: CallbackQuery, bot: Bot) -> None:
    if not _is_authorized(query.from_user.id if query.from_user else None):
        await _safe_answer(query, "Not authorized.", alert=True)
        return
    parsed = parse_callback_data(query.data or "")
    if parsed is None:
        await _safe_answer(query, "Bad callback.", alert=True)
        return
    action, reminder_id = parsed

    # Resolve the reminder owner so we can refresh their summary + use locale.
    with session_scope() as s:
        r = s.get(Reminder, reminder_id)
        owner_id = r.user_id if r else None
        chat_id = r.chat_id if r else (query.message.chat.id if query.message else 0)
    if owner_id is None:
        await _safe_answer(query, "Gone.", alert=True)
        return
    locale, tz_name = _get_user_state(owner_id)

    if action == CB_DONE:
        ok = mark_done(reminder_id)
        footer = f"\n\n<i>{label('decision_done', locale)}</i>" if ok else ""
        await _strip_buttons_and_append(query, footer)
        await _safe_answer(
            query, label("decision_done", locale) if ok else label("decision_already_closed", locale)
        )
    elif action == CB_STOP:
        ok = mark_stopped(reminder_id)
        footer = f"\n\n<i>{label('decision_stopped', locale)}</i>" if ok else ""
        await _strip_buttons_and_append(query, footer)
        await _safe_answer(
            query,
            label("decision_stopped", locale) if ok else label("decision_already_closed", locale),
        )
    elif action == CB_SNOOZE_30:
        new_at = snooze(reminder_id, 30)
        await _emit_snooze_outcome(query, new_at, locale, tz_name)
    elif action == CB_SNOOZE_2H:
        new_at = snooze(reminder_id, 120)
        await _emit_snooze_outcome(query, new_at, locale, tz_name)
    elif action == CB_SNOOZE_TOM:
        new_at = snooze_until_tomorrow_9am(reminder_id)
        await _emit_snooze_outcome(query, new_at, locale, tz_name)
    else:
        await _safe_answer(query, "Unknown action.", alert=True)
        return

    await _refresh_summary(bot, owner_id, chat_id, locale)


async def _emit_snooze_outcome(
    query: CallbackQuery, new_at: datetime | None, locale: str, tz_name: str
) -> None:
    if new_at is None:
        await _strip_buttons_and_append(query, "")
        await _safe_answer(query, label("decision_already_closed", locale))
        return
    at_local = _format_local_time(new_at, tz_name)
    msg = label("decision_snoozed_to", locale, at=at_local)
    await _strip_buttons_and_append(query, f"\n\n<i>{msg}</i>")
    await _safe_answer(query, msg)


async def _strip_buttons_and_append(query: CallbackQuery, footer: str) -> None:
    """Edit the message: keep its body, remove keyboard, append a confirmation footer."""
    try:
        original = query.message.html_text or query.message.text or ""
    except Exception:
        original = ""
    new_text = (original + footer).strip()
    try:
        await query.message.edit_text(
            text=new_text,
            parse_mode=ParseMode.HTML,
            reply_markup=None,
        )
    except TelegramAPIError as e:
        logger.warning(f"edit_text failed: {e}")


# ---------- bot lifecycle ----------


def _build_dispatcher(router: LLMRouter, bot: Bot) -> Dispatcher:
    dp = Dispatcher()
    # Make LLMRouter available to handlers via aiogram dependency injection.
    dp["router"] = router

    dp.message.register(cmd_start, CommandStart())
    dp.message.register(cmd_help, Command("help"))
    dp.message.register(cmd_list, Command("list"))
    dp.message.register(cmd_tz, Command("tz"))

    # Voice / audio / video / video_note → transcribe + parse
    dp.message.register(
        handle_message,
        F.voice | F.audio | F.video_note | F.video,
    )
    # Free-form text (not a slash command)
    dp.message.register(
        handle_message,
        F.text & ~F.text.startswith("/"),
    )

    dp.callback_query.register(
        cb_action,
        F.data.func(
            lambda d: any(
                (d or "").startswith(prefix + ":") for prefix in ALL_CALLBACK_PREFIXES
            )
        ),
    )
    return dp


async def run_bot() -> None:
    token = settings.telegram_bot_token.get_secret_value()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set — can't start bot")
    if settings.telegram_user_id == 0:
        logger.warning("TELEGRAM_USER_ID is 0 — all messages will be rejected as unauthorized")

    bot = Bot(token=token)
    router = LLMRouter()
    dp = _build_dispatcher(router, bot)
    scheduler_task = asyncio.create_task(run_scheduler(bot))

    try:
        logger.info("nudgr bot starting (polling)…")
        # drop_pending_updates=True: ignore queued updates from when the bot was offline.
        await dp.start_polling(bot, drop_pending_updates=True)
    finally:
        scheduler_task.cancel()
        try:
            await scheduler_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        await bot.session.close()
