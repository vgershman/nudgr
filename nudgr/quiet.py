"""Quiet hours: defer pings into the next active local-time window.

A user's quiet config lives on User.quiet_from / User.quiet_to (TIME columns).
Window semantics:
  * quiet_from < quiet_to: same-day window, e.g. 13:00–14:00 (afternoon nap).
  * quiet_from > quiet_to: window crosses midnight, e.g. 23:00–07:00 (sleep).
  * either NULL: no quiet hours — `defer` is a no-op.

`is_in_quiet_window(local_dt, qf, qt)` answers "would this fire be silenced?"
`defer_into_active_window(when_utc, tz_name, qf, qt)` returns a UTC datetime
guaranteed to be outside the quiet window — the closest forward active edge,
i.e. `quiet_to` on the relevant local day.

Used by the scheduler whenever it sets `next_ping_at` (initial fire, escalation,
snooze, reschedule, recurrence chain) so a user's "23:00–07:00 quiet" reliably
pushes pings to 07:00 instead of waking them up.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from nudgr.db.models import User
from nudgr.db.session import session_scope


def _coerce_tz(tz_name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name or "UTC")
    except Exception:
        return ZoneInfo("UTC")


def is_in_quiet_window(
    local_dt: datetime, quiet_from: time | None, quiet_to: time | None
) -> bool:
    """True iff `local_dt`'s wall-clock time falls inside [quiet_from, quiet_to)."""
    if quiet_from is None or quiet_to is None:
        return False
    t = local_dt.time()
    if quiet_from == quiet_to:
        # Degenerate — empty window. Treat as off.
        return False
    if quiet_from < quiet_to:
        return quiet_from <= t < quiet_to
    # Crosses midnight: window is t >= quiet_from OR t < quiet_to.
    return t >= quiet_from or t < quiet_to


def defer_into_active_window(
    when_utc: datetime,
    tz_name: str | None,
    quiet_from: time | None,
    quiet_to: time | None,
) -> datetime:
    """If `when_utc` lands inside quiet hours, push to the next quiet_to edge.

    Returns `when_utc` unchanged if there are no quiet hours or it's already
    outside the window. Always returns a UTC datetime.
    """
    if quiet_from is None or quiet_to is None or quiet_from == quiet_to:
        return when_utc

    if when_utc.tzinfo is None:
        when_utc = when_utc.replace(tzinfo=timezone.utc)
    else:
        when_utc = when_utc.astimezone(timezone.utc)

    tz = _coerce_tz(tz_name)
    local = when_utc.astimezone(tz)

    if not is_in_quiet_window(local, quiet_from, quiet_to):
        return when_utc

    # Compute the next quiet_to edge in local time.
    if quiet_from < quiet_to:
        # Same-day window: edge is today at quiet_to (we're inside, so today's
        # quiet_to is in the future).
        edge_local = local.replace(
            hour=quiet_to.hour, minute=quiet_to.minute, second=0, microsecond=0
        )
        if edge_local <= local:
            edge_local += timedelta(days=1)
    else:
        # Crosses midnight: if we're past quiet_from today (late evening), edge
        # is tomorrow's quiet_to. If we're before quiet_to (early morning), edge
        # is today's quiet_to.
        if local.time() < quiet_to:
            edge_local = local.replace(
                hour=quiet_to.hour, minute=quiet_to.minute, second=0, microsecond=0
            )
        else:
            edge_local = (local + timedelta(days=1)).replace(
                hour=quiet_to.hour, minute=quiet_to.minute, second=0, microsecond=0
            )
    return edge_local.astimezone(timezone.utc)


def defer_for_user(when_utc: datetime, user_id: UUID) -> datetime:
    """Convenience: load `user_id`'s quiet config and apply `defer_into_active_window`."""
    with session_scope() as s:
        u = s.get(User, user_id)
        if u is None:
            return when_utc
        return defer_into_active_window(when_utc, u.timezone, u.quiet_from, u.quiet_to)


def parse_hhmm(s: str) -> time | None:
    """Lenient HH:MM parser used by the /quiet and /digest commands."""
    s = (s or "").strip()
    if not s:
        return None
    # Accept "9", "09", "9:00", "09:00", "9.00".
    s = s.replace(".", ":")
    parts = s.split(":")
    try:
        if len(parts) == 1:
            hh = int(parts[0])
            mm = 0
        else:
            hh = int(parts[0])
            mm = int(parts[1])
    except ValueError:
        return None
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return time(hh, mm)
