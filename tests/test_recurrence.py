"""Recurrence math: daily / weekly / monthly + until/count + DST + clamping."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from nudgr.recurrence import (
    advance_for_chain,
    next_occurrence,
    normalize_rule,
    rule_summary,
    should_chain,
)


def _utc(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


# ---------- normalize_rule ----------


def test_normalize_rejects_bad_kind():
    assert normalize_rule({"kind": "monthly_q3", "time": "09:00"}) is None
    assert normalize_rule({"kind": None, "time": "09:00"}) is None
    assert normalize_rule({}) is None
    assert normalize_rule(None) is None


def test_normalize_rejects_bad_time():
    assert normalize_rule({"kind": "daily", "time": "abc"}) is None
    assert normalize_rule({"kind": "daily", "time": ""}) is None


def test_normalize_rejects_weekly_without_weekdays():
    assert normalize_rule({"kind": "weekly", "time": "09:00", "weekdays": []}) is None


def test_normalize_rejects_monthly_with_bad_dom():
    assert normalize_rule({"kind": "monthly", "time": "09:00", "day_of_month": 0}) is None
    assert normalize_rule({"kind": "monthly", "time": "09:00", "day_of_month": 32}) is None
    assert normalize_rule({"kind": "monthly", "time": "09:00"}) is None  # missing dom


def test_normalize_falls_back_to_default_tz_for_unknown():
    norm = normalize_rule(
        {"kind": "daily", "time": "09:00", "tz": "Mars/Olympus"}, default_tz="UTC"
    )
    assert norm is not None
    assert norm["tz"] == "UTC"


def test_normalize_preserves_count_and_until():
    norm = normalize_rule(
        {
            "kind": "daily",
            "time": "09:00",
            "count": 5,
            "until": "2026-12-31T00:00:00+00:00",
            "fired_count": 2,
        }
    )
    assert norm["count"] == 5
    assert norm["fired_count"] == 2
    assert norm["until"].startswith("2026-12-31")


def test_normalize_drops_garbage_until():
    norm = normalize_rule({"kind": "daily", "time": "09:00", "until": "not-a-date"})
    assert norm is not None
    assert "until" not in norm


def test_normalize_drops_negative_count():
    norm = normalize_rule({"kind": "daily", "time": "09:00", "count": -1})
    assert "count" not in norm


# ---------- next_occurrence: daily ----------


def test_daily_basic_local_time():
    # 09:00 Amsterdam, asked from Sat 12:00 UTC → Sun 09:00 Ams (07:00 UTC)
    rule = {"kind": "daily", "time": "09:00", "tz": "Europe/Amsterdam"}
    n = next_occurrence(rule, _utc(2026, 4, 25, 12, 0))
    assert n == _utc(2026, 4, 26, 7, 0)


def test_daily_handles_dst_spring_forward():
    """Spring-forward 02:00→03:00 in Amsterdam (2026-03-29). A daily 02:30 rule
    asked from the prior evening must still return a valid future UTC datetime
    — zoneinfo resolves the non-existent local 02:30 by shifting the wall-clock
    forward, which is acceptable behavior. We just verify we don't crash, return
    None, or return a stale time."""
    rule = {"kind": "daily", "time": "02:30", "tz": "Europe/Amsterdam"}
    asked_at = _utc(2026, 3, 28, 23, 0)
    n = next_occurrence(rule, asked_at)
    assert n is not None
    assert n.tzinfo == timezone.utc
    assert n > asked_at


def test_daily_handles_dst_fall_back():
    """Fall-back 03:00→02:00 in Amsterdam (2026-10-25). A daily 02:30 rule on
    that date is technically ambiguous — zoneinfo picks one. We just want a
    valid future UTC datetime."""
    rule = {"kind": "daily", "time": "02:30", "tz": "Europe/Amsterdam"}
    asked_at = _utc(2026, 10, 24, 12, 0)
    n = next_occurrence(rule, asked_at)
    assert n is not None
    assert n > asked_at


def test_daily_count_exhausted_returns_none():
    # fired_count > count means we've already scheduled N+1 — refuse.
    rule = {"kind": "daily", "time": "09:00", "tz": "UTC", "count": 3, "fired_count": 4}
    assert next_occurrence(rule, _utc(2026, 4, 25)) is None


def test_daily_count_at_boundary_still_returns_last():
    # fired_count == count means we're scheduling the LAST instance — permit.
    rule = {"kind": "daily", "time": "09:00", "tz": "UTC", "count": 3, "fired_count": 3}
    assert next_occurrence(rule, _utc(2026, 4, 25)) == _utc(2026, 4, 25, 9, 0)


def test_daily_count_remaining_returns_next():
    rule = {"kind": "daily", "time": "09:00", "tz": "UTC", "count": 3, "fired_count": 1}
    assert next_occurrence(rule, _utc(2026, 4, 25)) == _utc(2026, 4, 25, 9, 0)


def test_daily_until_cutoff_short():
    rule = {
        "kind": "daily",
        "time": "09:00",
        "tz": "UTC",
        "until": "2026-04-26T00:00:00+00:00",
    }
    # Asked from Apr 25 12:00 — next is Apr 26 09:00 — but until=Apr 26 00:00,
    # so next is past cutoff → None.
    assert next_occurrence(rule, _utc(2026, 4, 25, 12, 0)) is None


def test_daily_until_cutoff_ok():
    rule = {
        "kind": "daily",
        "time": "09:00",
        "tz": "UTC",
        "until": "2026-04-30T00:00:00+00:00",
    }
    assert next_occurrence(rule, _utc(2026, 4, 25, 12, 0)) == _utc(2026, 4, 26, 9, 0)


# ---------- next_occurrence: weekly ----------


def test_weekly_picks_correct_weekday():
    # Mon-Fri 09:00 Ams, asked from Sat noon UTC → Mon 09:00 Ams = 07:00 UTC
    rule = {
        "kind": "weekly",
        "time": "09:00",
        "weekdays": [0, 1, 2, 3, 4],
        "tz": "Europe/Amsterdam",
    }
    n = next_occurrence(rule, _utc(2026, 4, 25, 12, 0))
    assert n == _utc(2026, 4, 27, 7, 0)


def test_weekly_skips_to_next_week_when_appropriate():
    # Only Sundays at 09:00. Asked Mon morning → next Sun.
    rule = {"kind": "weekly", "time": "09:00", "weekdays": [6], "tz": "UTC"}
    n = next_occurrence(rule, _utc(2026, 4, 27, 8, 0))  # Mon
    assert n.weekday() == 6


# ---------- next_occurrence: monthly ----------


def test_monthly_basic():
    rule = {
        "kind": "monthly",
        "time": "10:00",
        "day_of_month": 1,
        "tz": "Europe/Amsterdam",
    }
    n = next_occurrence(rule, _utc(2026, 4, 25, 12, 0))
    # May 1 10:00 Ams = 08:00 UTC
    assert n == _utc(2026, 5, 1, 8, 0)


def test_monthly_clamps_day_of_month():
    # day_of_month=31 in February → clamp to last day of Feb (28 in 2027)
    rule = {"kind": "monthly", "time": "09:00", "day_of_month": 31, "tz": "UTC"}
    n = next_occurrence(rule, _utc(2027, 1, 31, 23, 0))
    assert n == _utc(2027, 2, 28, 9, 0)


def test_monthly_with_count_exhaustion():
    rule = {
        "kind": "monthly",
        "time": "09:00",
        "day_of_month": 1,
        "tz": "UTC",
        "count": 6,
        "fired_count": 7,  # one past the budget — refuse
    }
    assert next_occurrence(rule, _utc(2026, 4, 25)) is None


# ---------- advance_for_chain ----------


def test_advance_increments_fired_count():
    rule = {"kind": "daily", "time": "09:00", "tz": "UTC", "fired_count": 1}
    nxt = advance_for_chain(rule)
    assert nxt["fired_count"] == 2


def test_advance_starts_from_one_when_missing():
    rule = {"kind": "daily", "time": "09:00", "tz": "UTC"}
    nxt = advance_for_chain(rule)
    assert nxt["fired_count"] == 2  # 1 (default) + 1


def test_advance_returns_none_for_invalid_rule():
    assert advance_for_chain({"kind": "garbage"}) is None


# ---------- should_chain ----------


def test_should_chain_true_when_remaining_budget():
    rule = {"kind": "daily", "time": "09:00", "tz": "UTC", "count": 5, "fired_count": 2}
    assert should_chain(rule, _utc(2026, 4, 25)) is True


def test_should_chain_false_when_exhausted():
    rule = {"kind": "daily", "time": "09:00", "tz": "UTC", "count": 1, "fired_count": 2}
    assert should_chain(rule, _utc(2026, 4, 25)) is False


# ---------- rule_summary ----------


def test_rule_summary_daily():
    assert rule_summary({"kind": "daily", "time": "09:00", "tz": "UTC"}) == "daily 09:00"


def test_rule_summary_weekdays_idiom():
    s = rule_summary(
        {"kind": "weekly", "time": "09:00", "weekdays": [0, 1, 2, 3, 4], "tz": "UTC"}
    )
    assert s == "weekdays 09:00"


def test_rule_summary_weekends_idiom():
    s = rule_summary({"kind": "weekly", "time": "10:00", "weekdays": [5, 6], "tz": "UTC"})
    assert s == "weekends 10:00"


def test_rule_summary_explicit_weekdays():
    s = rule_summary(
        {"kind": "weekly", "time": "08:00", "weekdays": [0, 2, 4], "tz": "UTC"}
    )
    assert s == "Mon,Wed,Fri 08:00"


def test_rule_summary_monthly_en_ru():
    en = rule_summary(
        {"kind": "monthly", "time": "09:00", "day_of_month": 15, "tz": "UTC"}
    )
    ru = rule_summary(
        {"kind": "monthly", "time": "09:00", "day_of_month": 15, "tz": "UTC"}, "ru"
    )
    assert "15" in en and "09:00" in en
    assert "15" in ru and "09:00" in ru


def test_rule_summary_with_count_remaining():
    s = rule_summary(
        {
            "kind": "daily",
            "time": "09:00",
            "tz": "UTC",
            "count": 6,
            "fired_count": 1,
        }
    )
    assert "×6" in s


def test_rule_summary_invalid_rule_returns_empty():
    assert rule_summary({"kind": "garbage"}) == ""
    assert rule_summary(None) == ""


# ---------- end-to-end count chain simulation ----------


def test_count_budget_chain_simulation():
    """Simulate creating + firing a 'count=3' weekly rule and confirm it stops
    after exactly 3 instances."""
    rule = {
        "kind": "weekly",
        "time": "09:00",
        "weekdays": [1],  # Tuesday
        "tz": "UTC",
        "count": 3,
        "fired_count": 1,
    }
    instances = [next_occurrence(rule, _utc(2026, 4, 25))]  # 1st = next Tue
    for _ in range(8):  # try 8 chains; should stop after 2 more
        rule = advance_for_chain(rule)
        nxt = next_occurrence(rule, instances[-1])
        if nxt is None:
            break
        instances.append(nxt)
    assert len(instances) == 3
    for inst in instances:
        assert inst.weekday() == 1
