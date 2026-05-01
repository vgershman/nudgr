"""Locale strings + per-message language detection.

Locale lifecycle:
  - First incoming message: detect_locale() classifies by Cyrillic presence.
  - We persist user.preferred_locale; subsequent messages re-detect and
    update the persisted value so a user can switch by switching language.
  - Bot replies + the pinned summary render via label(key, locale).
"""

from __future__ import annotations

import re

# Any Cyrillic letter → Russian. Cheap, ~always correct for ru/en split.
_CYRILLIC_RE = re.compile(r"[а-яА-ЯёЁ]")


def detect_locale(text: str) -> str:
    if not text:
        return "en"
    return "ru" if _CYRILLIC_RE.search(text) else "en"


_LABELS: dict[str, dict[str, str]] = {
    "en": {
        # Welcome / help
        "welcome": (
            "👋 <b>nudgr</b>\n\n"
            "Send me a voice, text, or video describing what to remind you about and when:\n"
            "  • <i>remind me to call mom in 15 minutes</i>\n"
            "  • <i>take meds at 9pm</i>\n"
            "  • <i>every weekday at 9am take meds</i>\n\n"
            "I'll fire the reminder and keep nudging until you tap Done.\n\n"
            "Commands: /list, /tz, /help"
        ),
        # Confirmations
        "got_it_in": "✓ Got it. I'll remind you in {eta}:",
        "got_it_at": "✓ Got it. I'll remind you at {at}:",
        "got_it_recurring_daily": "✓ Got it. Daily reminder at {time}:",
        "got_it_recurring_weekly": "✓ Got it. Recurring reminder ({days} at {time}):",
        # Status
        "summary_header": "Active reminders",
        "summary_empty": "No active reminders. Send me one to start.",
        "summary_recurring_marker": " ⟳",
        # Verdicts
        "decision_done": "✓ Done.",
        "decision_stopped": "⏹ Stopped.",
        "decision_snoozed_to": "💤 Snoozed. Next ping {at}.",
        "decision_already_closed": "Already closed.",
        # Cancel/done by name
        "cancelled_match": "✓ Cancelled: <b>{text}</b>",
        "done_match": "✓ Marked done: <b>{text}</b>",
        "no_match": "No active reminder matching '{hint}'.",
        # Errors / clarification
        "when_clarify": "❓ When? Try '30m', '2h', or 'tomorrow 9am'.",
        "parse_failed": "I couldn't parse that — try again?",
        "not_authorized": "Not authorized.",
        "audio_too_long": "That clip is {minutes}m {seconds}s — over the {max}-minute limit. Try a shorter recording or paste the text.",
        # /tz
        "tz_set": "Timezone set: <b>{tz}</b>",
        "tz_invalid": "Unknown timezone: <code>{input}</code>. Try a name like <code>Europe/Amsterdam</code> or <code>Asia/Tbilisi</code>.",
        "tz_current": "Your current timezone: <b>{tz}</b>. Change with /tz <i>Region/City</i>.",
        # ETA
        "eta_in_min": "in {n}m",
        "eta_in_hour_min": "in {h}h{m:02d}m",
        "eta_at_iso": "at {iso}",
        # Buttons
        "btn_done": "✅ Done",
        "btn_snooze_30": "💤 +30m",
        "btn_snooze_2h": "💤 +2h",
        "btn_snooze_tomorrow": "💤 Tomorrow 9am",
        "btn_stop": "⏹ Stop",
        # Weekday short names (for recurring summary)
        "wd_short": "Mon,Tue,Wed,Thu,Fri,Sat,Sun",
        "wd_weekdays": "weekdays",
        "wd_weekends": "weekends",
        "wd_daily": "daily",
        # Invites (v3)
        "invite_redeemed": "✓ Welcome! You're in.",
        "invite_already_active": "You're already activated — go ahead and send a reminder.",
        "invite_expired": "That invite code has expired. Ask your admin for a new one.",
        "invite_used": "That invite code has already been used.",
        "invite_unknown": "Unknown invite code. Try again or ask your admin.",
        "invite_required": (
            "🔒 This bot is private. Ask the admin for an invite code, then send "
            "<code>/start &lt;CODE&gt;</code>."
        ),
        "invite_admin_only": "Only admins can do that.",
        "invite_issued": (
            "🎟 New invite code:\n\n<code>{code}</code>\n\n"
            "Share this with the person you're inviting. They redeem it via "
            "<code>/start {code}</code>{expires}."
        ),
        "invite_issued_expires": " (expires {at})",
        "invite_list_empty": "No active invite codes. Issue one with /invite.",
        "invite_list_header": "Active invite codes:",
        # Quiet hours / digest (v2 — used by P3)
        "quiet_set": "🌙 Quiet hours: <b>{from_t}–{to_t}</b> (local). Pings landing inside this window are deferred.",
        "quiet_cleared": "🌙 Quiet hours cleared.",
        "quiet_current": "🌙 Quiet hours: <b>{from_t}–{to_t}</b> (local). Clear with <code>/quiet off</code>.",
        "quiet_none": "🌙 No quiet hours set. Try <code>/quiet 23:00 07:00</code>.",
        "quiet_invalid": "Couldn't read that — try <code>/quiet 23:00 07:00</code> or <code>/quiet off</code>.",
        "digest_set": "📰 Daily digest at <b>{at}</b> (local).",
        "digest_cleared": "📰 Daily digest off.",
        "digest_current": "📰 Daily digest at <b>{at}</b> (local). Clear with <code>/digest off</code>.",
        "digest_none": "📰 No daily digest set. Try <code>/digest 08:00</code>.",
        "digest_invalid": "Couldn't read that — try <code>/digest 08:00</code> or <code>/digest off</code>.",
        "digest_header": "📰 <b>Daily digest</b>",
        # Edit (v2 — used by P2)
        "edit_done": "✓ Updated <b>{text}</b> — next at {at}.",
        "edit_no_match": "No active reminder matches '{hint}'.",
        "btn_skip_next": "⏭ Skip next",
        "decision_skipped": "⏭ Skipped. Next at {at}.",
        # Clarification context (v2.5)
        "clarify_pending": "❓ {question}\n\n📌 <i>{task}</i>",
        "clarify_pending_default_q": "When should I remind you?",
        "btn_pending_cancel": "❌ Cancel",
        "pending_cancelled": "⏹ Cancelled — nothing scheduled.",
        "pending_already_gone": "Nothing pending right now.",
    },
    "ru": {
        "welcome": (
            "👋 <b>nudgr</b>\n\n"
            "Пришли голосовое, текст или видео — что напомнить и когда:\n"
            "  • <i>напомни через 15 минут позвонить маме</i>\n"
            "  • <i>принять таблетки в 21:00</i>\n"
            "  • <i>каждый будний день в 9 утра принять таблетки</i>\n\n"
            "Я напомню и буду напоминать снова, пока не отметишь «готово».\n\n"
            "Команды: /list, /tz, /help"
        ),
        "got_it_in": "✓ Понял. Напомню через {eta}:",
        "got_it_at": "✓ Понял. Напомню {at}:",
        "got_it_recurring_daily": "✓ Понял. Ежедневно в {time}:",
        "got_it_recurring_weekly": "✓ Понял. Повторяющееся напоминание ({days} в {time}):",
        "summary_header": "Активные напоминания",
        "summary_empty": "Активных напоминаний нет. Пришли мне что-нибудь.",
        "summary_recurring_marker": " ⟳",
        "decision_done": "✓ Готово.",
        "decision_stopped": "⏹ Остановлено.",
        "decision_snoozed_to": "💤 Отложено. Следующий пинг {at}.",
        "decision_already_closed": "Уже закрыто.",
        "cancelled_match": "✓ Отменено: <b>{text}</b>",
        "done_match": "✓ Отмечено как готово: <b>{text}</b>",
        "no_match": "Не нашёл активного напоминания по запросу «{hint}».",
        "when_clarify": "❓ Когда? Попробуй «30м», «2ч» или «завтра в 9 утра».",
        "parse_failed": "Не разобрал — попробуй ещё раз?",
        "not_authorized": "Не авторизовано.",
        "audio_too_long": "Запись слишком длинная: {minutes}м {seconds}с (лимит {max} минут). Сократи или пришли текстом.",
        "tz_set": "Часовой пояс установлен: <b>{tz}</b>",
        "tz_invalid": "Неизвестный часовой пояс: <code>{input}</code>. Попробуй формат <code>Europe/Moscow</code> или <code>Asia/Tbilisi</code>.",
        "tz_current": "Текущий часовой пояс: <b>{tz}</b>. Изменить: /tz <i>Region/City</i>.",
        "eta_in_min": "через {n}м",
        "eta_in_hour_min": "через {h}ч{m:02d}м",
        "eta_at_iso": "{iso}",
        "btn_done": "✅ Готово",
        "btn_snooze_30": "💤 +30м",
        "btn_snooze_2h": "💤 +2ч",
        "btn_snooze_tomorrow": "💤 Завтра 9:00",
        "btn_stop": "⏹ Стоп",
        "wd_short": "Пн,Вт,Ср,Чт,Пт,Сб,Вс",
        "wd_weekdays": "будни",
        "wd_weekends": "выходные",
        "wd_daily": "ежедневно",
        # Invites (v3)
        "invite_redeemed": "✓ Добро пожаловать! Доступ открыт.",
        "invite_already_active": "Уже активирован — пришли напоминание.",
        "invite_expired": "Срок действия кода истёк. Попроси новый у админа.",
        "invite_used": "Этот код уже использован.",
        "invite_unknown": "Неизвестный код приглашения. Попробуй ещё раз или попроси админа.",
        "invite_required": (
            "🔒 Бот приватный. Попроси админа выдать код приглашения и пришли "
            "<code>/start &lt;КОД&gt;</code>."
        ),
        "invite_admin_only": "Только администратор может это сделать.",
        "invite_issued": (
            "🎟 Новый код приглашения:\n\n<code>{code}</code>\n\n"
            "Передай его приглашённому — он введёт <code>/start {code}</code>{expires}."
        ),
        "invite_issued_expires": " (истекает {at})",
        "invite_list_empty": "Нет активных кодов. Создай через /invite.",
        "invite_list_header": "Активные коды приглашений:",
        # Quiet hours / digest (v2 — used by P3)
        "quiet_set": "🌙 Тихие часы: <b>{from_t}–{to_t}</b> (локально). Пинги в этом окне будут отложены.",
        "quiet_cleared": "🌙 Тихие часы отключены.",
        "quiet_current": "🌙 Тихие часы: <b>{from_t}–{to_t}</b> (локально). Отключить: <code>/quiet off</code>.",
        "quiet_none": "🌙 Тихие часы не заданы. Например: <code>/quiet 23:00 07:00</code>.",
        "quiet_invalid": "Не разобрал — попробуй <code>/quiet 23:00 07:00</code> или <code>/quiet off</code>.",
        "digest_set": "📰 Ежедневная сводка в <b>{at}</b> (локально).",
        "digest_cleared": "📰 Ежедневная сводка отключена.",
        "digest_current": "📰 Ежедневная сводка в <b>{at}</b> (локально). Отключить: <code>/digest off</code>.",
        "digest_none": "📰 Ежедневная сводка не настроена. Например: <code>/digest 08:00</code>.",
        "digest_invalid": "Не разобрал — попробуй <code>/digest 08:00</code> или <code>/digest off</code>.",
        "digest_header": "📰 <b>Ежедневная сводка</b>",
        # Edit (v2 — used by P2)
        "edit_done": "✓ Обновлено <b>{text}</b> — следующий пинг {at}.",
        "edit_no_match": "Нет активного напоминания по «{hint}».",
        "btn_skip_next": "⏭ Пропустить",
        "decision_skipped": "⏭ Пропущено. Следующий {at}.",
        # Clarification context (v2.5)
        "clarify_pending": "❓ {question}\n\n📌 <i>{task}</i>",
        "clarify_pending_default_q": "Когда напомнить?",
        "btn_pending_cancel": "❌ Отмена",
        "pending_cancelled": "⏹ Отменено — ничего не запланировано.",
        "pending_already_gone": "Ничего не ожидает уточнения.",
    },
}


def label(key: str, locale: str = "en", **kwargs) -> str:
    """Return a UI string for `locale`, falling back to English. Supports
    str.format kwargs for parameterized strings."""
    bundle = _LABELS.get(locale) or _LABELS["en"]
    template = bundle.get(key) or _LABELS["en"].get(key, key)
    if kwargs:
        try:
            return template.format(**kwargs)
        except (KeyError, IndexError):
            return template
    return template


def supported_locales() -> list[str]:
    return list(_LABELS.keys())
