"""Quiet-hours window detection + deferral semantics."""

from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from nudgr.quiet import defer_into_active_window, is_in_quiet_window, parse_hhmm


AMS = ZoneInfo("Europe/Amsterdam")


def _utc(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


# ---------- is_in_quiet_window ----------


def test_quiet_off_when_either_endpoint_none():
    assert is_in_quiet_window(_utc(2026, 4, 25, 22, 0), None, time(7, 0)) is False
    assert is_in_quiet_window(_utc(2026, 4, 25, 22, 0), time(23, 0), None) is False


def test_quiet_same_day_window():
    qf, qt = time(13, 0), time(14, 0)
    # Inside
    assert is_in_quiet_window(datetime(2026, 4, 25, 13, 30, tzinfo=AMS), qf, qt) is True
    # On the start edge — included
    assert is_in_quiet_window(datetime(2026, 4, 25, 13, 0, tzinfo=AMS), qf, qt) is True
    # On the end edge — excluded (half-open)
    assert is_in_quiet_window(datetime(2026, 4, 25, 14, 0, tzinfo=AMS), qf, qt) is False
    # Well outside
    assert is_in_quiet_window(datetime(2026, 4, 25, 12, 0, tzinfo=AMS), qf, qt) is False


def test_quiet_cross_midnight_window():
    qf, qt = time(23, 0), time(7, 0)
    assert is_in_quiet_window(datetime(2026, 4, 25, 22, 30, tzinfo=AMS), qf, qt) is False
    assert is_in_quiet_window(datetime(2026, 4, 25, 23, 30, tzinfo=AMS), qf, qt) is True
    assert is_in_quiet_window(datetime(2026, 4, 26, 6, 30, tzinfo=AMS), qf, qt) is True
    assert is_in_quiet_window(datetime(2026, 4, 26, 7, 0, tzinfo=AMS), qf, qt) is False
    assert is_in_quiet_window(datetime(2026, 4, 26, 12, 0, tzinfo=AMS), qf, qt) is False


def test_quiet_degenerate_window_treated_as_off():
    # quiet_from == quiet_to is meaningless and should not block anything.
    qf = qt = time(9, 0)
    assert is_in_quiet_window(datetime(2026, 4, 25, 9, 0, tzinfo=AMS), qf, qt) is False


# ---------- defer_into_active_window ----------


def test_defer_no_op_outside_window():
    fire = _utc(2026, 4, 26, 12, 0)  # 14:00 Ams — outside 23:00–07:00
    out = defer_into_active_window(fire, "Europe/Amsterdam", time(23, 0), time(7, 0))
    assert out == fire


def test_defer_no_op_when_no_quiet():
    fire = _utc(2026, 4, 26, 2, 0)
    out = defer_into_active_window(fire, "Europe/Amsterdam", None, None)
    assert out == fire


def test_defer_pushes_late_evening_to_next_morning():
    # 23:30 Ams (= 21:30 UTC, CEST) → next quiet_to (07:00 next day Ams)
    fire = _utc(2026, 4, 25, 21, 30)
    out = defer_into_active_window(fire, "Europe/Amsterdam", time(23, 0), time(7, 0))
    local = out.astimezone(AMS)
    assert local.date() == datetime(2026, 4, 26).date()
    assert local.hour == 7 and local.minute == 0


def test_defer_pushes_early_morning_to_same_day_morning():
    # 02:00 Ams next day cross-midnight → same-day 07:00
    fire = _utc(2026, 4, 26, 0, 0)  # 02:00 Ams Apr 26 (CEST = +02:00)
    out = defer_into_active_window(fire, "Europe/Amsterdam", time(23, 0), time(7, 0))
    local = out.astimezone(AMS)
    assert local.date() == datetime(2026, 4, 26).date()
    assert local.hour == 7


def test_defer_same_day_window_pushes_to_window_end():
    # 13:30 Ams inside 13:00-14:00 → 14:00 same day
    fire = _utc(2026, 4, 25, 11, 30)  # 13:30 CEST
    out = defer_into_active_window(fire, "Europe/Amsterdam", time(13, 0), time(14, 0))
    local = out.astimezone(AMS)
    assert local.hour == 14 and local.minute == 0
    assert local.date() == datetime(2026, 4, 25).date()


# ---------- parse_hhmm ----------


def test_parse_hhmm_accepts_variants():
    assert parse_hhmm("9") == time(9, 0)
    assert parse_hhmm("09") == time(9, 0)
    assert parse_hhmm("09:30") == time(9, 30)
    assert parse_hhmm("9.30") == time(9, 30)
    assert parse_hhmm("23:59") == time(23, 59)


def test_parse_hhmm_rejects_garbage():
    assert parse_hhmm("") is None
    assert parse_hhmm("not a time") is None
    assert parse_hhmm("25:00") is None
    assert parse_hhmm("12:60") is None
    assert parse_hhmm("-1:30") is None
