"""Reminder scheduler: poll DB every N seconds, fire due reminders, escalate.

Single async loop. On each tick, find reminders with status='active' and
next_ping_at <= now. For each, send the reminder message + buttons via
Telegram and advance the ping schedule.

Escalation backoff (minutes after each ping):
  ping 1 (initial fire) → +5m
  ping 2 (escalation 1) → +10m
  ping 3 (escalation 2) → +20m
  ping 4 (escalation 3) → +60m
  ping 5+              → stop (status='expired')

Recurrence (v1): when a reminder reaches a terminal status (done/cancelled/
expired) AND has a `recurrence` rule, the helpers in `db_helpers` chain the
next instance using `recurrence.next_occurrence`.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select

from nudgr.config import settings
from nudgr.db.models import Reminder, User
from nudgr.db.session import session_scope
from nudgr.i18n import label
from nudgr.observability.logging import logger
from nudgr.recurrence import next_occurrence

# Minutes between consecutive escalation pings, indexed by ping_count AFTER firing.
ESCALATION_OFFSETS_MIN: tuple[int, ...] = (5, 10, 20, 60)
MAX_PINGS = 1 + len(ESCALATION_OFFSETS_MIN)  # initial + N escalations


# ---------- callback data + keyboard ----------

CB_DONE = "done"
CB_SNOOZE_30 = "sn30"
CB_SNOOZE_2H = "sn2h"
CB_SNOOZE_TOM = "sn_tom"
CB_STOP = "stop"

ALL_CALLBACK_PREFIXES: tuple[str, ...] = (
    CB_DONE,
    CB_SNOOZE_30,
    CB_SNOOZE_2H,
    CB_SNOOZE_TOM,
    CB_STOP,
)


def make_keyboard(reminder_id: UUID, locale: str = "en") -> InlineKeyboardMarkup:
    rid = str(reminder_id)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=label("btn_done", locale), callback_data=f"{CB_DONE}:{rid}"
                ),
                InlineKeyboardButton(
                    text=label("btn_snooze_30", locale),
                    callback_data=f"{CB_SNOOZE_30}:{rid}",
                ),
                InlineKeyboardButton(
                    text=label("btn_snooze_2h", locale),
                    callback_data=f"{CB_SNOOZE_2H}:{rid}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=label("btn_snooze_tomorrow", locale),
                    callback_data=f"{CB_SNOOZE_TOM}:{rid}",
                ),
                InlineKeyboardButton(
                    text=label("btn_stop", locale), callback_data=f"{CB_STOP}:{rid}"
                ),
            ],
        ]
    )


# ---------- scheduler core ----------


def _next_ping_after(ping_count: int) -> datetime | None:
    """`ping_count` is the count BEFORE we just sent. Returns the next time
    we should ping after this fire, or None to stop escalating."""
    idx = ping_count
    if idx >= len(ESCALATION_OFFSETS_MIN):
        return None
    return datetime.now(timezone.utc) + timedelta(minutes=ESCALATION_OFFSETS_MIN[idx])


def _format_ping_text(reminder: Reminder, is_escalation: bool) -> str:
    body = reminder.text
    if is_escalation:
        return f"🔔 <b>still pending</b> — {body}"
    return f"🔔 <b>{body}</b>"


def _user_locale(user_id: UUID) -> str:
    with session_scope() as s:
        user = s.get(User, user_id)
        return (user.preferred_locale if user else "en") or "en"


async def _fire_reminder(bot: Bot, reminder_id: UUID) -> tuple[int, UUID, str] | None:
    """Send (or re-send) the reminder. Update ping_count + next_ping_at.

    Returns (chat_id, user_id, locale) on a successful send (so the bot can
    refresh the pinned summary), or None on no-op / failure.
    """
    with session_scope() as s:
        r = s.get(Reminder, reminder_id)
        if r is None or r.status != "active":
            return None
        is_escalation = r.ping_count > 0
        chat_id = r.chat_id
        user_id = r.user_id
        text = _format_ping_text(r, is_escalation)

    locale = _user_locale(user_id)
    keyboard = make_keyboard(reminder_id, locale)
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )
    except TelegramAPIError as e:
        logger.error(f"scheduler: send failed for {reminder_id}: {e}")
        return None

    # Advance the schedule.
    expired_with_recurrence = False
    with session_scope() as s:
        r = s.get(Reminder, reminder_id)
        if r is None:
            return None
        new_count = r.ping_count + 1
        if new_count - 1 < len(ESCALATION_OFFSETS_MIN):
            gap_min = ESCALATION_OFFSETS_MIN[new_count - 1]
            r.next_ping_at = datetime.now(timezone.utc) + timedelta(minutes=gap_min)
            r.status = "active"
        else:
            r.next_ping_at = None
            r.status = "expired"
            expired_with_recurrence = bool(r.recurrence)
            logger.info(f"scheduler: {reminder_id} expired after {new_count} pings")
        r.ping_count = new_count

    # If this expiration completes a recurring reminder, chain the next one.
    if expired_with_recurrence:
        _chain_next_recurrence(reminder_id)

    return chat_id, user_id, locale


def _chain_next_recurrence(reminder_id: UUID) -> UUID | None:
    """If `reminder_id` has a recurrence rule and just hit a terminal state,
    create a fresh active reminder for the next occurrence. Returns the new id."""
    with session_scope() as s:
        r = s.get(Reminder, reminder_id)
        if r is None or not r.recurrence:
            return None
        rule = dict(r.recurrence)
        # Use the user's TZ as the fallback if the rule didn't carry one.
        user = s.get(User, r.user_id)
        tz_name = (user.timezone if user else None) or rule.get("tz") or "UTC"
        next_at = next_occurrence(rule, datetime.now(timezone.utc), tz_name)
        if next_at is None:
            logger.warning(f"scheduler: recurrence rule yielded no next time for {reminder_id}")
            return None
        new_reminder = Reminder(
            user_id=r.user_id,
            chat_id=r.chat_id,
            text=r.text,
            transcript=r.transcript,
            input_kind=r.input_kind,
            fire_at=next_at,
            next_ping_at=next_at,
            ping_count=0,
            recurrence=rule,
            status="active",
        )
        s.add(new_reminder)
        s.flush()
        logger.info(
            f"scheduler: chained recurring reminder {reminder_id} → {new_reminder.id} "
            f"at {next_at.isoformat(timespec='minutes')}"
        )
        return new_reminder.id


async def _tick(bot: Bot) -> list[tuple[int, UUID, str]]:
    """One scheduler tick: find due reminders + fire them.

    Returns the list of (chat_id, user_id, locale) tuples that we successfully
    pinged this tick. The bot uses this to refresh pinned summaries.
    """
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        stmt = (
            select(Reminder.id)
            .where(Reminder.status == "active")
            .where(Reminder.next_ping_at.is_not(None))
            .where(Reminder.next_ping_at <= now)
            .order_by(Reminder.next_ping_at)
            .limit(50)
        )
        due_ids = list(s.execute(stmt).scalars())
    if not due_ids:
        return []
    logger.info(f"scheduler: firing {len(due_ids)} due reminder(s)")
    fired: list[tuple[int, UUID, str]] = []
    for rid in due_ids:
        try:
            res = await _fire_reminder(bot, rid)
            if res is not None:
                fired.append(res)
        except Exception:  # noqa: BLE001 — never let one bad reminder kill the loop
            logger.exception(f"scheduler: error firing {rid}")
    return fired


async def run_scheduler(bot: Bot) -> None:
    """Long-running scheduler loop. Runs as a task alongside dp.start_polling."""
    interval = max(5, settings.scheduler_poll_interval_sec)
    logger.info(f"scheduler: starting (poll every {interval}s)")
    # Lazy import to avoid a hard cycle: summary imports nothing scheduler-side,
    # but bot imports both. Keep this import-lazy so module import order is loose.
    from nudgr.summary import update_pinned_summary

    while True:
        try:
            fired = await _tick(bot)
            # After firing, refresh each affected user's pinned summary so the
            # ETA / overdue marker stays fresh.
            seen: set[tuple[int, UUID]] = set()
            for chat_id, user_id, locale in fired:
                key = (chat_id, user_id)
                if key in seen:
                    continue
                seen.add(key)
                try:
                    await update_pinned_summary(bot, user_id, chat_id, locale)
                except Exception:  # noqa: BLE001
                    logger.exception(f"scheduler: summary refresh failed for {user_id}")
        except asyncio.CancelledError:
            logger.info("scheduler: cancelled")
            raise
        except Exception:  # noqa: BLE001
            logger.exception("scheduler: tick error")
        await asyncio.sleep(interval)


# ---------- DB helpers used by bot handlers ----------


def _terminal_chain(reminder_id: UUID) -> None:
    """If the just-terminated reminder has a recurrence rule, chain the next one."""
    with session_scope() as s:
        r = s.get(Reminder, reminder_id)
        has_rec = bool(r and r.recurrence)
    if has_rec:
        _chain_next_recurrence(reminder_id)


def mark_done(reminder_id: UUID) -> bool:
    with session_scope() as s:
        r = s.get(Reminder, reminder_id)
        if r is None or r.status not in ("active",):
            return False
        r.status = "done"
        r.next_ping_at = None
        r.done_at = datetime.now(timezone.utc)
    _terminal_chain(reminder_id)
    return True


def mark_stopped(reminder_id: UUID) -> bool:
    with session_scope() as s:
        r = s.get(Reminder, reminder_id)
        if r is None or r.status != "active":
            return False
        r.status = "cancelled"
        r.next_ping_at = None
    # Stopping a recurring reminder cancels just this instance — the user can
    # /list and see the freshly-chained next one. Chain it.
    _terminal_chain(reminder_id)
    return True


def snooze(reminder_id: UUID, minutes: int) -> datetime | None:
    """Reset escalation, push next ping by `minutes`. Returns the new fire time or None."""
    with session_scope() as s:
        r = s.get(Reminder, reminder_id)
        if r is None or r.status != "active":
            return None
        new_at = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        r.ping_count = 0
        r.next_ping_at = new_at
        return new_at


def snooze_until_tomorrow_9am(reminder_id: UUID) -> datetime | None:
    """Snooze until 09:00 in the user's local timezone tomorrow."""
    with session_scope() as s:
        r = s.get(Reminder, reminder_id)
        if r is None or r.status != "active":
            return None
        user = s.get(User, r.user_id)
        tz_name = (user.timezone if user else "UTC") or "UTC"
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("UTC")
        local_now = datetime.now(tz)
        target_local = (local_now + timedelta(days=1)).replace(
            hour=9, minute=0, second=0, microsecond=0
        )
        new_at = target_local.astimezone(timezone.utc)
        r.ping_count = 0
        r.next_ping_at = new_at
        return new_at


def parse_callback_data(data: str) -> tuple[str, UUID] | None:
    parts = (data or "").split(":", 1)
    if len(parts) != 2:
        return None
    action, rid_str = parts
    try:
        rid = UUID(rid_str)
    except ValueError:
        return None
    return action, rid
