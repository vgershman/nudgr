"""Parser helpers — JSON cleanup, fire-time resolution, recurrence extraction.

The full `parse()` path goes through Anthropic, so we test just the pure-
functional pieces here. The LLM-shaped JSON is constructed manually to lock
down the schema contract.
"""

from __future__ import annotations

from datetime import datetime, timezone

from nudgr.parser import _clean_json, _extract_recurrence, _resolve_fire_at


# ---------- _clean_json ----------


def test_clean_json_passthrough_for_plain_object():
    assert _clean_json('{"intent":"remind"}') == '{"intent":"remind"}'


def test_clean_json_strips_code_fences():
    raw = '```json\n{"intent":"remind"}\n```'
    assert _clean_json(raw) == '{"intent":"remind"}'


def test_clean_json_strips_bare_fences():
    raw = '```\n{"intent":"remind"}\n```'
    assert _clean_json(raw) == '{"intent":"remind"}'


def test_clean_json_finds_object_in_chatty_response():
    raw = 'Here is your JSON:\n{"intent":"remind"}\n— hope this helps'
    assert _clean_json(raw) == '{"intent":"remind"}'


# ---------- _resolve_fire_at ----------


def test_resolve_offset_minutes_yields_future_utc():
    out = _resolve_fire_at({"offset_minutes": 30}, "Europe/Amsterdam")
    assert out is not None
    delta = (out - datetime.now(timezone.utc)).total_seconds()
    assert 25 * 60 < delta < 35 * 60


def test_resolve_offset_minutes_zero_or_negative_returns_none():
    # Defensive — offset=0 / negative makes no semantic sense for "in N minutes".
    assert _resolve_fire_at({"offset_minutes": 0}, "UTC") is None
    assert _resolve_fire_at({"offset_minutes": -10}, "UTC") is None


def test_resolve_absolute_iso_with_offset():
    out = _resolve_fire_at(
        {"absolute_iso": "2026-04-26T09:00:00+02:00"}, "Europe/Amsterdam"
    )
    assert out == datetime(2026, 4, 26, 7, 0, tzinfo=timezone.utc)


def test_resolve_absolute_iso_naive_uses_user_tz():
    # Naive ISO is interpreted as the user's local TZ.
    out = _resolve_fire_at(
        {"absolute_iso": "2026-04-26T09:00:00"}, "Europe/Amsterdam"
    )
    # 09:00 Amsterdam in late April = CEST = +02:00 = 07:00 UTC
    assert out == datetime(2026, 4, 26, 7, 0, tzinfo=timezone.utc)


def test_resolve_absolute_iso_garbage_returns_none():
    assert _resolve_fire_at({"absolute_iso": "not-a-date"}, "UTC") is None


def test_resolve_empty_when_returns_none():
    assert _resolve_fire_at({}, "UTC") is None
    assert _resolve_fire_at({"offset_minutes": None, "absolute_iso": None}, "UTC") is None


# ---------- _extract_recurrence ----------


def test_extract_recurrence_weekly():
    raw = {
        "recurrence": {
            "kind": "weekly",
            "time": "09:00",
            "weekdays": [0, 1, 2, 3, 4],
        }
    }
    out = _extract_recurrence(raw, "Europe/Amsterdam")
    assert out is not None
    assert out["kind"] == "weekly"
    assert out["time"] == "09:00"
    assert out["weekdays"] == [0, 1, 2, 3, 4]
    assert out["tz"] == "Europe/Amsterdam"


def test_extract_recurrence_monthly_carries_dom():
    raw = {
        "recurrence": {
            "kind": "monthly",
            "time": "10:00",
            "day_of_month": 15,
        }
    }
    out = _extract_recurrence(raw, "UTC")
    assert out is not None
    assert out["day_of_month"] == 15


def test_extract_recurrence_with_terminal_fields():
    raw = {
        "recurrence": {
            "kind": "weekly",
            "time": "19:00",
            "weekdays": [1],
            "count": 6,
            "until": "2026-12-01T00:00:00+00:00",
        }
    }
    out = _extract_recurrence(raw, "UTC")
    assert out["count"] == 6
    assert out["until"].startswith("2026-12-01")


def test_extract_recurrence_returns_none_for_missing_kind():
    assert _extract_recurrence({"recurrence": {"kind": None}}, "UTC") is None
    assert _extract_recurrence({"recurrence": {}}, "UTC") is None
    assert _extract_recurrence({}, "UTC") is None


def test_extract_recurrence_returns_none_for_invalid_rule():
    # Weekly without weekdays is invalid.
    raw = {"recurrence": {"kind": "weekly", "time": "09:00", "weekdays": []}}
    assert _extract_recurrence(raw, "UTC") is None
