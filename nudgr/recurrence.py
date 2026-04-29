"""Recurrence rules: daily / weekly / monthly RRULE-lite.

Rule shape stored in `Reminder.recurrence` (JSONB):

  {
    "kind": "daily" | "weekly" | "monthly",
    "time": "HH:MM",            # local wall-clock time in user's TZ
    "weekdays": [0..6],         # weekly only — Mon=0 … Sun=6
    "day_of_month": 1..31,      # monthly only — clamped to month length
    "tz": "Europe/Amsterdam",   # IANA name, used to resolve the wall-clock time
    "until": "<ISO datetime>",  # optional terminal cutoff (UTC). >= until → no chain.
    "count": <int>,             # optional max instance count (>=1)
    "fired_count": <int>        # tracked across chained instances; starts at 1
  }

`next_occurrence(rule, after, tz_name)` returns the next UTC datetime strictly
greater than `after`, or None if the rule is invalid or exhausted.
`should_chain(rule)` answers "are we still allowed to spawn another instance?"
based on count/until.

Kept narrow on purpose — daily + weekly + monthly cover ~all real use cases.
Custom intervals (every Nth) and end-of-week-of-month edge rules are not in
scope.
"""

from __future__ import annotations

import calendar
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def _coerce_tz(tz_name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name or "UTC")
    except Exception:
        return ZoneInfo("UTC")


def _parse_hhmm(s: str | None) -> time | None:
    if not s or not isinstance(s, str):
        return None
    try:
        hh, mm = s.split(":", 1)
        return time(int(hh), int(mm))
    except (ValueError, AttributeError):
        return None


def _parse_iso_utc(s) -> datetime | None:
    """Best-effort ISO → tz-aware UTC datetime. Returns None on failure."""
    if not s or not isinstance(s, str):
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def normalize_rule(rule: dict | None, default_tz: str = "UTC") -> dict | None:
    """Validate + normalize a recurrence rule. Returns None if invalid.

    Carries forward optional terminal fields (until/count) + tracking field
    (fired_count) untouched so chained instances preserve the budget.
    """
    if not isinstance(rule, dict):
        return None
    kind = str(rule.get("kind") or "").lower()
    if kind not in ("daily", "weekly", "monthly"):
        return None
    t = _parse_hhmm(rule.get("time"))
    if t is None:
        return None
    tz_name = str(rule.get("tz") or default_tz)
    try:
        ZoneInfo(tz_name)
    except Exception:
        tz_name = default_tz

    weekdays_raw = rule.get("weekdays") or []
    weekdays: list[int] = []
    if isinstance(weekdays_raw, (list, tuple)):
        for w in weekdays_raw:
            try:
                wi = int(w)
            except (TypeError, ValueError):
                continue
            if 0 <= wi <= 6 and wi not in weekdays:
                weekdays.append(wi)
    weekdays.sort()
    if kind == "weekly" and not weekdays:
        return None

    day_of_month: int | None = None
    if kind == "monthly":
        try:
            day_of_month = int(rule.get("day_of_month"))
        except (TypeError, ValueError):
            return None
        if not (1 <= day_of_month <= 31):
            return None

    out: dict = {
        "kind": kind,
        "time": f"{t.hour:02d}:{t.minute:02d}",
        "weekdays": weekdays,
        "tz": tz_name,
    }
    if day_of_month is not None:
        out["day_of_month"] = day_of_month

    # Optional terminal fields — preserve only if they're meaningful.
    until_dt = _parse_iso_utc(rule.get("until"))
    if until_dt is not None:
        out["until"] = until_dt.isoformat()
    count = rule.get("count")
    if isinstance(count, int) and count >= 1:
        out["count"] = count
    fired = rule.get("fired_count")
    if isinstance(fired, int) and fired >= 0:
        out["fired_count"] = fired
    return out


def _eom_clamped(year: int, month: int, target_day: int) -> int:
    """Clamp `target_day` to the last day of (year, month). Handles Feb / 30-day months."""
    last = calendar.monthrange(year, month)[1]
    return min(target_day, last)


def _next_monthly(after_local: datetime, day_of_month: int, target_t: time, tz: ZoneInfo) -> datetime:
    """Walk forward at most 60 days to find the next clamped DoM occurrence > after."""
    base = after_local
    # Try this month first; if its clamped day is already past, advance.
    for month_offset in range(0, 14):
        # Compute year/month after offsetting from base.
        month_idx = base.month - 1 + month_offset
        year = base.year + month_idx // 12
        month = month_idx % 12 + 1
        clamped = _eom_clamped(year, month, day_of_month)
        cand_local = datetime(year, month, clamped, target_t.hour, target_t.minute, tzinfo=tz)
        cand_utc = cand_local.astimezone(timezone.utc)
        if cand_utc > after_local.astimezone(timezone.utc):
            return cand_utc
    # Defensive — shouldn't happen with a sane rule.
    raise RuntimeError("recurrence: monthly walk exhausted 14 months")


def next_occurrence(
    rule: dict | None, after: datetime, tz_name: str | None = None
) -> datetime | None:
    """Return the next UTC datetime strictly greater than `after` for `rule`.

    Returns None if:
      - rule is invalid (unknown kind, bad time, etc.)
      - the rule's `until` cutoff is at-or-before `after`
      - the rule's `count` budget has been exhausted by `fired_count`
    """
    norm = normalize_rule(rule, default_tz=tz_name or "UTC")
    if norm is None:
        return None

    if after.tzinfo is None:
        after_utc = after.replace(tzinfo=timezone.utc)
    else:
        after_utc = after.astimezone(timezone.utc)

    # count budget: fired_count is the 1-indexed instance number we're about
    # to schedule. count=N means "N total instances" — we permit fired_count
    # up to and including N, refuse N+1 and above.
    if "count" in norm and isinstance(norm.get("fired_count"), int):
        if norm["fired_count"] > norm["count"]:
            return None

    tz = _coerce_tz(norm["tz"])
    target_t = _parse_hhmm(norm["time"]) or time(9, 0)
    after_local = after_utc.astimezone(tz)
    base_day = after_local.date()

    cand_utc: datetime | None = None
    if norm["kind"] == "daily":
        for offset in range(0, 8):
            cand_local = datetime.combine(
                base_day + timedelta(days=offset), target_t, tzinfo=tz
            )
            c = cand_local.astimezone(timezone.utc)
            if c > after_utc:
                cand_utc = c
                break
    elif norm["kind"] == "weekly":
        weekdays = set(norm["weekdays"])
        if not weekdays:
            return None
        for offset in range(0, 14):
            day = base_day + timedelta(days=offset)
            if day.weekday() not in weekdays:
                continue
            cand_local = datetime.combine(day, target_t, tzinfo=tz)
            c = cand_local.astimezone(timezone.utc)
            if c > after_utc:
                cand_utc = c
                break
    elif norm["kind"] == "monthly":
        cand_utc = _next_monthly(after_local, norm["day_of_month"], target_t, tz)

    if cand_utc is None:
        return None

    # until cutoff: candidate must be strictly before until (cutoff is exclusive).
    until_dt = _parse_iso_utc(norm.get("until"))
    if until_dt is not None and cand_utc >= until_dt:
        return None

    return cand_utc


def advance_for_chain(rule: dict | None) -> dict | None:
    """Return a copy of `rule` with `fired_count` incremented for the next
    chained instance. None if the rule is invalid."""
    norm = normalize_rule(rule)
    if norm is None:
        return None
    out = dict(norm)
    out["fired_count"] = int(norm.get("fired_count", 1)) + 1
    return out


def should_chain(rule: dict | None, after: datetime, tz_name: str | None = None) -> bool:
    """True if a chained next instance is allowed (count/until not exhausted)."""
    return next_occurrence(rule, after, tz_name) is not None


def rule_summary(rule: dict | None, locale: str = "en") -> str:
    """Short human-readable summary for the pinned summary / confirmations.

    Returns an empty string if the rule is invalid.
    """
    norm = normalize_rule(rule)
    if norm is None:
        return ""
    from nudgr.i18n import label  # local import: avoids any cycles

    t = norm["time"]
    suffix = ""
    if "count" in norm:
        remaining = norm["count"] - int(norm.get("fired_count", 1)) + 1
        if remaining > 0:
            suffix = f" ×{remaining}"
        else:
            suffix = " ✓"

    if norm["kind"] == "daily":
        return f"{label('wd_daily', locale)} {t}{suffix}"

    if norm["kind"] == "monthly":
        # Day-of-month abbreviation — same in en/ru ("on the Nth").
        dom = norm.get("day_of_month")
        return f"day {dom} @ {t}{suffix}" if locale == "en" else f"{dom}-го числа в {t}{suffix}"

    wd = sorted(norm["weekdays"])
    if wd == [0, 1, 2, 3, 4]:
        return f"{label('wd_weekdays', locale)} {t}{suffix}"
    if wd == [5, 6]:
        return f"{label('wd_weekends', locale)} {t}{suffix}"
    short = label("wd_short", locale).split(",")
    names = ",".join(short[i] for i in wd if 0 <= i < len(short))
    return f"{names} {t}{suffix}"
