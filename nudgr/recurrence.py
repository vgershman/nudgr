"""Recurrence rules: daily/weekly RRULE-lite.

Rule shape stored in `Reminder.recurrence` (JSONB):

  {
    "kind": "daily" | "weekly",
    "time": "HH:MM",            # local wall-clock time in user's TZ
    "weekdays": [0..6],         # Mon=0 … Sun=6; only used for kind=weekly
    "tz": "Europe/Amsterdam",   # IANA name, used to resolve the wall-clock time
  }

`next_occurrence(rule, after, tz_name)` returns the next UTC datetime strictly
greater than `after`. Used by the scheduler to chain a recurring reminder onto
its next instance once the current one reaches a terminal status.

Kept deliberately narrow — daily + weekly cover ~all v1 use cases. Anything
fancier (every Nth, monthly, custom interval) can ship later as a new `kind`.
"""

from __future__ import annotations

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


def normalize_rule(rule: dict | None, default_tz: str = "UTC") -> dict | None:
    """Validate + normalize a recurrence rule. Returns None if invalid."""
    if not isinstance(rule, dict):
        return None
    kind = str(rule.get("kind") or "").lower()
    if kind not in ("daily", "weekly"):
        return None
    t = _parse_hhmm(rule.get("time"))
    if t is None:
        return None
    tz_name = str(rule.get("tz") or default_tz)
    # Best-effort: if the supplied TZ is unknown, fall back to default.
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
        # Weekly rule with no weekdays is meaningless — reject.
        return None
    return {
        "kind": kind,
        "time": f"{t.hour:02d}:{t.minute:02d}",
        "weekdays": weekdays,
        "tz": tz_name,
    }


def next_occurrence(
    rule: dict | None, after: datetime, tz_name: str | None = None
) -> datetime | None:
    """Return the next UTC datetime strictly greater than `after` for `rule`.

    `after` may be naive or tz-aware; we always normalize to UTC. `tz_name` is
    used only as a fallback when the rule itself lacks a tz field.
    """
    norm = normalize_rule(rule, default_tz=tz_name or "UTC")
    if norm is None:
        return None

    if after.tzinfo is None:
        after_utc = after.replace(tzinfo=timezone.utc)
    else:
        after_utc = after.astimezone(timezone.utc)

    tz = _coerce_tz(norm["tz"])
    target_t = _parse_hhmm(norm["time"]) or time(9, 0)

    # Walk in local-tz days. Compare using UTC values (DST-safe).
    after_local = after_utc.astimezone(tz)
    # Start scan at the user's local "today".
    base_day = after_local.date()

    if norm["kind"] == "daily":
        for offset in range(0, 8):
            cand_local = datetime.combine(
                base_day + timedelta(days=offset), target_t, tzinfo=tz
            )
            cand_utc = cand_local.astimezone(timezone.utc)
            if cand_utc > after_utc:
                return cand_utc
        return None

    if norm["kind"] == "weekly":
        weekdays = set(norm["weekdays"])
        if not weekdays:
            return None
        for offset in range(0, 14):
            day = base_day + timedelta(days=offset)
            if day.weekday() not in weekdays:
                continue
            cand_local = datetime.combine(day, target_t, tzinfo=tz)
            cand_utc = cand_local.astimezone(timezone.utc)
            if cand_utc > after_utc:
                return cand_utc
        return None

    return None


def rule_summary(rule: dict | None, locale: str = "en") -> str:
    """Short human-readable summary for the pinned summary / confirmations.

    Returns an empty string if the rule is invalid. Locale-aware enough for
    the en/ru bundles in i18n.py — falls back to English if the locale is
    unknown.
    """
    norm = normalize_rule(rule)
    if norm is None:
        return ""
    from nudgr.i18n import label  # local import: avoids any cycles

    t = norm["time"]
    if norm["kind"] == "daily":
        return f"{label('wd_daily', locale)} {t}"

    wd = sorted(norm["weekdays"])
    if wd == [0, 1, 2, 3, 4]:
        return f"{label('wd_weekdays', locale)} {t}"
    if wd == [5, 6]:
        return f"{label('wd_weekends', locale)} {t}"

    short = label("wd_short", locale).split(",")
    names = ",".join(short[i] for i in wd if 0 <= i < len(short))
    return f"{names} {t}"
