"""Pinned active-tasks summary.

Each user gets one bot-pinned message in their chat showing their current
active reminders. The id of that message lives on `User.pinned_summary_message_id`.

Lifecycle:
  * First state change after /start → create message, pin it, store the id.
  * Every subsequent state change (new reminder, done, cancel, snooze, fire,
    expire) → edit the existing message in place.
  * If the user deletes the pinned message or Telegram returns "message to edit
    not found", we transparently re-send + re-pin and update the stored id.

Kept resilient to Telegram quirks: any failure here is logged and swallowed —
the summary is a nice-to-have and must never break the main reply path.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from sqlalchemy import select

from nudgr.db.models import Reminder, User
from nudgr.db.session import session_scope
from nudgr.i18n import label
from nudgr.observability.logging import logger
from nudgr.recurrence import rule_summary


def _format_eta(next_at: datetime | None, now: datetime, tz: ZoneInfo, locale: str) -> str:
    if next_at is None:
        return "—"
    delta = next_at - now
    mins = int(delta.total_seconds() // 60)
    if mins < 0:
        # Overdue — show absolute local time. Better than negative ETAs.
        local = next_at.astimezone(tz)
        return label("eta_at_iso", locale, iso=local.strftime("%H:%M"))
    if mins < 60:
        return label("eta_in_min", locale, n=mins)
    if mins < 60 * 24:
        return label("eta_in_hour_min", locale, h=mins // 60, m=mins % 60)
    local = next_at.astimezone(tz)
    return label("eta_at_iso", locale, iso=local.strftime("%a %H:%M"))


def render_summary_text(user_id: UUID, locale: str = "en") -> str:
    """Render the current pinned-summary HTML for `user_id`."""
    with session_scope() as s:
        user = s.get(User, user_id)
        tz_name = user.timezone if user else "UTC"
        rows = list(
            s.execute(
                select(Reminder)
                .where(Reminder.user_id == user_id)
                .where(Reminder.status == "active")
                .order_by(Reminder.next_ping_at.nulls_last(), Reminder.created_at)
                .limit(50)
            ).scalars()
        )
        # Snapshot the fields we need before the session closes.
        items = [
            {
                "text": r.text,
                "next_ping_at": r.next_ping_at,
                "ping_count": r.ping_count,
                "recurrence": r.recurrence,
            }
            for r in rows
        ]

    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")

    if not items:
        return f"📌 <b>{label('summary_header', locale)}</b>\n\n{label('summary_empty', locale)}"

    now = datetime.now(timezone.utc)
    lines = [f"📌 <b>{label('summary_header', locale)}</b>"]
    for it in items:
        eta = _format_eta(it["next_ping_at"], now, tz, locale)
        marker = label("summary_recurring_marker", locale) if it["recurrence"] else ""
        rec = ""
        if it["recurrence"]:
            r = rule_summary(it["recurrence"], locale)
            if r:
                rec = f" · {r}"
        pings = f" · 🔁{it['ping_count']}" if it["ping_count"] > 0 else ""
        text = (it["text"] or "").strip()
        lines.append(f"• {text}{marker}  ({eta}{rec}{pings})")
    return "\n".join(lines)


async def update_pinned_summary(
    bot: Bot, user_id: UUID, chat_id: int, locale: str = "en"
) -> None:
    """Refresh (or create) the pinned summary in `chat_id` for `user_id`.

    Never raises — logs and swallows. The summary is a UX bonus, not a critical
    path, and Telegram has a lot of edge cases (no permission to pin, message
    deleted, etc.).
    """
    text = render_summary_text(user_id, locale)
    with session_scope() as s:
        user = s.get(User, user_id)
        if user is None:
            return
        existing_msg_id = user.pinned_summary_message_id

    if existing_msg_id is not None:
        try:
            await bot.edit_message_text(
                text=text,
                chat_id=chat_id,
                message_id=existing_msg_id,
                parse_mode=ParseMode.HTML,
            )
            return
        except TelegramBadRequest as e:
            msg = str(e).lower()
            if "message is not modified" in msg:
                return
            if (
                "message to edit not found" in msg
                or "message can't be edited" in msg
                or "message_id_invalid" in msg
            ):
                logger.info(f"summary: stored message {existing_msg_id} gone, re-creating")
                # fall through to recreate
            else:
                logger.warning(f"summary: edit failed: {e}")
                return
        except TelegramAPIError as e:
            logger.warning(f"summary: edit failed: {e}")
            return

    # Create + pin a new summary message.
    try:
        sent = await bot.send_message(
            chat_id=chat_id, text=text, parse_mode=ParseMode.HTML
        )
    except TelegramAPIError as e:
        logger.warning(f"summary: send failed: {e}")
        return
    new_id = sent.message_id

    try:
        await bot.pin_chat_message(
            chat_id=chat_id, message_id=new_id, disable_notification=True
        )
    except TelegramAPIError as e:
        # Pinning may fail (e.g. group with no permission). The message itself
        # is still useful, so we keep the id and use edit_message_text next time.
        logger.info(f"summary: pin failed (non-fatal): {e}")

    with session_scope() as s:
        user = s.get(User, user_id)
        if user is not None:
            user.pinned_summary_message_id = new_id
