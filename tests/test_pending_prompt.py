"""Pending-context prompt rendering — exercised without a DB.

The DB-bound parts of `nudgr.pending` (upsert/get/clear/expire) are exercised
manually + by integration; this file pins down the prompt-shaping contract so
the LLM keeps getting the merge instructions when a pending exists.
"""

from __future__ import annotations

from nudgr.llm.prompts.parse_intent import render_user_prompt


def test_render_no_pending_omits_pending_block():
    out = render_user_prompt(
        user_text="remind me at 9pm",
        current_iso="2026-04-29T12:00:00+02:00",
        user_tz="Europe/Amsterdam",
    )
    assert "Pending context" not in out
    assert "remind me at 9pm" in out


def test_render_with_pending_includes_target_and_question():
    pc = {
        "target_text": "test new feature",
        "recurrence": None,
        "fire_at_iso": None,
        "clarification_question": "When?",
    }
    out = render_user_prompt(
        user_text="in 5 minutes",
        current_iso="2026-04-29T12:00:00+02:00",
        user_tz="Europe/Amsterdam",
        pending_context=pc,
    )
    assert "Pending context" in out
    assert "test new feature" in out
    assert "When?" in out
    assert "in 5 minutes" in out


def test_render_with_pending_renders_recurrence_repr():
    pc = {
        "target_text": "take meds",
        "recurrence": {"kind": "daily", "time": "21:00", "tz": "UTC"},
        "fire_at_iso": "2026-04-29T19:00:00+00:00",
        "clarification_question": None,
    }
    out = render_user_prompt(
        user_text="from tomorrow",
        current_iso="2026-04-29T12:00:00+00:00",
        user_tz="UTC",
        pending_context=pc,
    )
    assert "take meds" in out
    assert "daily" in out  # recurrence dict rendered via repr
    assert "21:00" in out
