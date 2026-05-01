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
from datetime import datetime, time, timedelta, timezone
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
from nudgr.pending import expire_stale as expire_stale_pendings
from nudgr.quiet import defer_for_user
from nudgr.recurrence import advance_for_chain, next_occurrence

# Minutes between consecutive escalation pings, indexed by ping_count AFTER firing.
ESCALATION_OFFSETS_MIN: tuple[int, ...] = (5, 10, 20, 60)
MAX_PINGS = 1 + len(ESCALATION_OFFSETS_MIN)  # initial + N escalations


# ---------- callback data + keyboard ----------

CB_DONE = "done"
CB_SNOOZE_30 = "sn30"
CB_SNOOZE_2H = "sn2h"
CB_SNOOZE_TOM = "sn_tom"
CB_STOP = "stop"
CB_SKIP_NEXT = "skip"  # v2: only shown on recurring reminders

ALL_CALLBACK_PREFIXES: tuple[str, ...] = (
    CB_DONE,
    CB_SNOOZE_30,
    CB_SNOOZE_2H,
    CB_SNOOZE_TOM,
    CB_STOP,
    CB_SKIP_NEXT,
)


def make_keyboard(
    reminder_id: UUID, locale: str = "en", *, has_recurrence: bool = False
) -> InlineKeyboardMarkup:
    rid = str(reminder_id)
    rows = [
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
    # Only recurring reminders get the Skip-Next button — meaningless on one-shots.
    if has_recurrence:
        rows.append(
            [
                InlineKeyboardButton(
                    text=label("btn_skip_next", locale),
                    callback_data=f"{CB_SKIP_NEXT}:{rid}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
    has_recurrence = False
    with session_scope() as s:
        r = s.get(Reminder, reminder_id)
        if r is None or r.status != "active":
            return None
        is_escalation = r.ping_count > 0
        chat_id = r.chat_id
        user_id = r.user_id
        has_recurrence = bool(r.recurrence)
        text = _format_ping_text(r, is_escalation)

    locale = _user_locale(user_id)
    keyboard = make_keyboard(reminder_id, locale, has_recurrence=has_recurrence)
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
            raw_next = datetime.now(timezone.utc) + timedelta(minutes=gap_min)
            # Push escalations past quiet hours so we don't wake the user.
            r.next_ping_at = defer_for_user(raw_next, r.user_id)
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


def _chain_next_recurrence(
    reminder_id: UUID, *, after: datetime | None = None
) -> UUID | None:
    """If `reminder_id` has a recurrence rule and just hit a terminal state,
    create a fresh active reminder for the next occurrence. Returns the new id.

    Honours `until` (no chain past cutoff) and `count` (no chain past budget) —
    both enforced inside `next_occurrence`. `after` defaults to now() but the
    skip-next path passes the current reminder's fire_at so the next instance
    lands at the *next-after* slot rather than colliding with the just-cancelled one.
    """
    with session_scope() as s:
        r = s.get(Reminder, reminder_id)
        if r is None or not r.recurrence:
            return None
        rule = dict(r.recurrence)
        user = s.get(User, r.user_id)
        tz_name = (user.timezone if user else None) or rule.get("tz") or "UTC"
        # Bump fired_count so the chained instance reflects "we're on iteration N+1"
        # and so count-budget enforcement works.
        next_rule = advance_for_chain(rule) or rule
        starting_from = after or datetime.now(timezone.utc)
        next_at = next_occurrence(next_rule, starting_from, tz_name)
        if next_at is None:
            logger.info(
                f"scheduler: recurrence exhausted/cutoff for {reminder_id} — no chain"
            )
            return None
        # Defer the chained instance past quiet hours so a "9pm daily" reminder
        # doesn't fire at 9pm if user has quiet hours starting at 8pm.
        deferred = defer_for_user(next_at, r.user_id)
        new_reminder = Reminder(
            user_id=r.user_id,
            chat_id=r.chat_id,
            text=r.text,
            transcript=r.transcript,
            input_kind=r.input_kind,
            fire_at=next_at,           # canonical scheduled time (for skip-next anchoring)
            next_ping_at=deferred,     # actual delivery time (quiet-hours aware)
            ping_count=0,
            recurrence=next_rule,
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
            # v2.5: cheap maintenance — drop expired clarification pendings
            # before they can confuse a stale "in 5 minutes" reply hours later.
            try:
                expire_stale_pendings()
            except Exception:  # noqa: BLE001
                logger.exception("scheduler: pending sweep failed")
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


# ---------- digest scheduler ----------


def _todays_digest_moment_utc(
    digest_local: time, tz_name: str, now_utc: datetime
) -> datetime:
    """Resolve today's digest moment in `tz_name` to a UTC datetime."""
    try:
        tz = ZoneInfo(tz_name or "UTC")
    except Exception:
        tz = ZoneInfo("UTC")
    local_now = now_utc.astimezone(tz)
    today_local = local_now.replace(
        hour=digest_local.hour,
        minute=digest_local.minute,
        second=0,
        microsecond=0,
    )
    return today_local.astimezone(timezone.utc)


async def _digest_tick(bot: Bot) -> None:
    """One pass over users with `digest_local_time` set: push the digest if due."""
    from nudgr.i18n import label
    from nudgr.summary import render_summary_text

    now_utc = datetime.now(timezone.utc)
    candidates: list[tuple[UUID, int, str, str, datetime]] = []
    with session_scope() as s:
        rows = list(
            s.execute(
                select(User).where(User.digest_local_time.is_not(None)).where(User.is_active.is_(True))
            ).scalars()
        )
        for u in rows:
            assert u.digest_local_time is not None
            today_due = _todays_digest_moment_utc(u.digest_local_time, u.timezone, now_utc)
            # Has the moment passed today AND we haven't sent yet today?
            if now_utc < today_due:
                continue
            if u.last_digest_at is not None and u.last_digest_at >= today_due:
                continue
            # Resolve a chat_id: in single-chat mode, telegram_user_id == chat_id.
            chat_id = u.telegram_user_id
            candidates.append(
                (u.id, chat_id, u.preferred_locale or "en", u.timezone or "UTC", today_due)
            )

    for user_id, chat_id, locale, _tz_name, due_at in candidates:
        body = render_summary_text(user_id, locale)
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=f"{label('digest_header', locale)}\n\n{body}",
                parse_mode="HTML",
            )
        except TelegramAPIError as e:
            logger.warning(f"digest: send failed for user={user_id}: {e}")
            continue
        with session_scope() as s:
            u = s.get(User, user_id)
            if u is not None:
                u.last_digest_at = now_utc
        logger.info(f"digest: pushed for user={user_id} (due {due_at.isoformat(timespec='minutes')})")


async def run_digest_scheduler(bot: Bot) -> None:
    """Long-running digest tick. Runs alongside the main scheduler + polling."""
    interval = max(15, settings.digest_tick_interval_sec)
    logger.info(f"digest: starting (poll every {interval}s)")
    while True:
        try:
            await _digest_tick(bot)
        except asyncio.CancelledError:
            logger.info("digest: cancelled")
            raise
        except Exception:  # noqa: BLE001
            logger.exception("digest: tick error")
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
    """Reset escalation, push next ping by `minutes`. Returns the new fire time or None.

    Quiet hours are honoured — if the snooze would land inside the user's quiet
    window, we push to the next active edge instead.
    """
    with session_scope() as s:
        r = s.get(Reminder, reminder_id)
        if r is None or r.status != "active":
            return None
        raw = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        new_at = defer_for_user(raw, r.user_id)
        r.ping_count = 0
        r.next_ping_at = new_at
        return new_at


def snooze_until_tomorrow_9am(reminder_id: UUID) -> datetime | None:
    """Snooze until 09:00 in the user's local timezone tomorrow.

    9am sits outside typical quiet windows so this is usually a no-op for the
    quiet check, but we still pipe it through `defer_for_user` for consistency.
    """
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
        raw = target_local.astimezone(timezone.utc)
        new_at = defer_for_user(raw, r.user_id)
        r.ping_count = 0
        r.next_ping_at = new_at
        return new_at


def skip_next_recurrence(reminder_id: UUID) -> datetime | None:
    """Skip the upcoming instance of a recurring reminder.

    Cancels the current instance and immediately creates a chained instance
    one occurrence ahead. Returns the new fire time, or None if the reminder
    isn't recurring / already closed / the rule is exhausted.
    """
    starting_from: datetime | None = None
    with session_scope() as s:
        r = s.get(Reminder, reminder_id)
        if r is None or r.status != "active" or not r.recurrence:
            return None
        # Use the *scheduled* fire time as the chain anchor. This ensures the
        # new instance lands at the slot AFTER the one we're skipping, even if
        # the user taps Skip well in advance.
        starting_from = r.fire_at
        r.status = "cancelled"
        r.next_ping_at = None
    new_id = _chain_next_recurrence(reminder_id, after=starting_from)
    if new_id is None:
        return None
    with session_scope() as s:
        new_r = s.get(Reminder, new_id)
        return new_r.next_ping_at if new_r else None


def reschedule(reminder_id: UUID, new_fire_at: datetime) -> datetime | None:
    """Move an active reminder's fire time. Resets escalation count.

    Used by the `intent="edit"` path. Returns the actual scheduled fire time
    on success (post quiet-hours adjustment), or None if the reminder isn't active.
    """
    with session_scope() as s:
        r = s.get(Reminder, reminder_id)
        if r is None or r.status != "active":
            return None
        deferred = defer_for_user(new_fire_at, r.user_id)
        r.fire_at = new_fire_at
        r.next_ping_at = deferred
        r.ping_count = 0
        return deferred


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
