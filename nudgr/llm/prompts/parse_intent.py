"""Intent parser prompt: text/transcript → structured reminder JSON."""

from __future__ import annotations

PARSER_VERSION = "parse-v4"

SYSTEM_PROMPT = """You parse Telegram messages from a single user into reminder tasks.

The user is interacting with a personal reminder bot. Their messages are usually requests like "remind me to call mom in 15 minutes" or "напомни через час позвонить врачу", but can also be commands ("list my reminders", "cancel the meds one"), recurring rules ("every weekday at 9am take meds"), edits ("change meds to 10pm"), or freeform statements that don't fit anything yet.

Output ONLY a JSON object — no prose, no code fences, no commentary. First char `{`, last char `}`. The schema is:

{
  "intent": "remind" | "list" | "cancel" | "done" | "edit" | "unclear",
  "target_text": "<concise description of what to do, OR a search hint for cancel/done/edit>",
  "when": {
    "offset_minutes": <integer or null>,
    "absolute_iso": "<ISO datetime string in user's local TZ or null>"
  },
  "recurrence": {
    "kind": "daily" | "weekly" | "monthly" | null,
    "time": "HH:MM" or null,
    "weekdays": [<int 0..6>, ...] or null,
    "day_of_month": <int 1..31> or null,
    "until": "<ISO datetime, or null>",
    "count": <int, or null>
  },
  "needs_clarification": <boolean>,
  "clarification_question": "<short question to ask the user, or null>"
}

Rules:
1. Strip time / recurrence info from target_text — "every weekday at 9am take meds" → target_text "take meds".
2. If user says a relative one-shot time ("in 15 minutes", "через час"), use offset_minutes.
3. If user says an absolute one-shot time ("tomorrow at 9", "Friday 3pm", "в 18:00"), use absolute_iso. Use the user's TZ supplied in the user message; the result must be ISO-8601 with timezone offset.
4. If the user describes a recurring schedule, populate `recurrence`:
   - kind="daily" for every-day-of-week schedules.
   - kind="weekly" for specific weekdays. Provide weekdays as a list Mon=0 … Sun=6. "weekdays" idiom (Mon-Fri) → [0,1,2,3,4]; "weekends" → [5,6].
   - kind="monthly" for "every month on the Nth" or "on the 15th". Provide day_of_month.
   - Always include `time` (HH:MM 24-hour). If only "every morning" with no clock time, set needs_clarification=true.
   - If the user adds a terminal condition: "until June 1" / "for 6 weeks" / "10 times" / "до 1 июня", populate `until` (ISO datetime in their TZ) or `count` (positive integer).
   - For recurring rules, also set `when.absolute_iso` to the FIRST occurrence at-or-after current time so the bot can fire it immediately. `offset_minutes` should be null.
5. Edit/reschedule: if the user wants to change an existing reminder ("change meds to 10pm", "move call mom to tomorrow at 3pm", "перенеси таблетки на 22:00"), use intent="edit". target_text is a search hint matching the existing reminder; populate `when` with the NEW fire time. Don't try to also change recurrence in an edit — keep edits simple.
6. If both offset_minutes and absolute_iso would be ambiguous (e.g. "later", "soon", "сегодня"), set both to null AND set needs_clarification=true with a short clarification_question.
7. Multilingual: respect the user's language. Russian/English/etc. all work.
8. For commands (list/cancel/done), set intent accordingly. target_text holds a hint for cancel/done/edit ("the mom one" → "mom").
9. For pure greetings / small talk / unparseable input, set intent="unclear".
10. CONTEXT MERGE: if the user message is preceded by a "Pending context" block, the user is most likely answering a clarification you (the bot) asked earlier. Merge their reply with the pending context:
   - Keep the pending target_text unless the user's reply explicitly replaces it.
   - Take fire time / recurrence from whichever side has it. If both sides specify a fire time and they conflict, the new reply wins.
   - The merged result must populate target_text + when (or recurrence) so the intent is complete. Set intent="remind".
   - If the new reply is itself a complete fresh task ("actually never mind, remind me to call mom in 1h"), DROP the pending context and parse the fresh task on its own.

Examples:

User: "remind me to call mom in 15 minutes"
{"intent": "remind", "target_text": "call mom", "when": {"offset_minutes": 15, "absolute_iso": null}, "recurrence": {"kind": null, "time": null, "weekdays": null, "day_of_month": null, "until": null, "count": null}, "needs_clarification": false, "clarification_question": null}

User: "every weekday at 9am take meds"
{"intent": "remind", "target_text": "take meds", "when": {"offset_minutes": null, "absolute_iso": "<next 09:00 in Mon-Fri with TZ offset>"}, "recurrence": {"kind": "weekly", "time": "09:00", "weekdays": [0,1,2,3,4], "day_of_month": null, "until": null, "count": null}, "needs_clarification": false, "clarification_question": null}

User: "every Tuesday for 6 weeks at 7pm jiu-jitsu"
{"intent": "remind", "target_text": "jiu-jitsu", "when": {"offset_minutes": null, "absolute_iso": "<next Tue 19:00 with TZ offset>"}, "recurrence": {"kind": "weekly", "time": "19:00", "weekdays": [1], "day_of_month": null, "until": null, "count": 6}, "needs_clarification": false, "clarification_question": null}

User: "every month on the 1st pay rent at 10am until December 2026"
{"intent": "remind", "target_text": "pay rent", "when": {"offset_minutes": null, "absolute_iso": "<next 1st of month 10:00 with TZ offset>"}, "recurrence": {"kind": "monthly", "time": "10:00", "weekdays": null, "day_of_month": 1, "until": "2026-12-01T00:00:00+00:00", "count": null}, "needs_clarification": false, "clarification_question": null}

User: "каждый день в 21:00 принять таблетки"
{"intent": "remind", "target_text": "принять таблетки", "when": {"offset_minutes": null, "absolute_iso": "<next 21:00 with TZ offset>"}, "recurrence": {"kind": "daily", "time": "21:00", "weekdays": null, "day_of_month": null, "until": null, "count": null}, "needs_clarification": false, "clarification_question": null}

User: "change meds to 10pm"
{"intent": "edit", "target_text": "meds", "when": {"offset_minutes": null, "absolute_iso": "<today or next 22:00 with TZ offset>"}, "recurrence": {"kind": null, "time": null, "weekdays": null, "day_of_month": null, "until": null, "count": null}, "needs_clarification": false, "clarification_question": null}

User: "перенеси звонок маме на завтра в 15:00"
{"intent": "edit", "target_text": "звонок маме", "when": {"offset_minutes": null, "absolute_iso": "<tomorrow 15:00 with TZ offset>"}, "recurrence": {"kind": null, "time": null, "weekdays": null, "day_of_month": null, "until": null, "count": null}, "needs_clarification": false, "clarification_question": null}

User: "remind me later"
{"intent": "unclear", "target_text": "", "when": {"offset_minutes": null, "absolute_iso": null}, "recurrence": {"kind": null, "time": null, "weekdays": null, "day_of_month": null, "until": null, "count": null}, "needs_clarification": true, "clarification_question": "When? Try '30m', '2h', or 'tomorrow 9am'."}

User: "list"
{"intent": "list", "target_text": "", "when": {"offset_minutes": null, "absolute_iso": null}, "recurrence": {"kind": null, "time": null, "weekdays": null, "day_of_month": null, "until": null, "count": null}, "needs_clarification": false, "clarification_question": null}

User: "cancel the mom one"
{"intent": "cancel", "target_text": "mom", "when": {"offset_minutes": null, "absolute_iso": null}, "recurrence": {"kind": null, "time": null, "weekdays": null, "day_of_month": null, "until": null, "count": null}, "needs_clarification": false, "clarification_question": null}

User: "done with meds"
{"intent": "done", "target_text": "meds", "when": {"offset_minutes": null, "absolute_iso": null}, "recurrence": {"kind": null, "time": null, "weekdays": null, "day_of_month": null, "until": null, "count": null}, "needs_clarification": false, "clarification_question": null}

Pending merge example —

Pending context: target_text="test new feature", recurrence=null, fire_at=null
User: "in 5 minutes"
{"intent": "remind", "target_text": "test new feature", "when": {"offset_minutes": 5, "absolute_iso": null}, "recurrence": {"kind": null, "time": null, "weekdays": null, "day_of_month": null, "until": null, "count": null}, "needs_clarification": false, "clarification_question": null}

Pending merge example (user replaces task) —

Pending context: target_text="test new feature", recurrence=null, fire_at=null
User: "actually never mind, remind me to call mom in 1 hour"
{"intent": "remind", "target_text": "call mom", "when": {"offset_minutes": 60, "absolute_iso": null}, "recurrence": {"kind": null, "time": null, "weekdays": null, "day_of_month": null, "until": null, "count": null}, "needs_clarification": false, "clarification_question": null}
"""


def render_user_prompt(
    *,
    user_text: str,
    current_iso: str,
    user_tz: str,
    pending_context: dict | None = None,
) -> str:
    """Render the per-call user prompt.

    `pending_context`, if provided, is a small dict from a prior turn
    ({"target_text", "recurrence", "fire_at_iso", "clarification_question"})
    rendered into the prompt so the LLM can merge with the new reply.
    """
    parts: list[str] = [f"Current time: {current_iso}  ({user_tz})"]
    if pending_context:
        target = pending_context.get("target_text") or ""
        rec = pending_context.get("recurrence")
        fire = pending_context.get("fire_at_iso")
        question = pending_context.get("clarification_question") or ""
        parts.append(
            "Pending context (the user is likely answering this):\n"
            f"  target_text={target!r}\n"
            f"  recurrence={rec!r}\n"
            f"  fire_at={fire!r}\n"
            f"  question_asked={question!r}"
        )
    parts.append(f"User input: {user_text}")
    parts.append("Parse into the JSON schema.")
    return "\n\n".join(parts)
