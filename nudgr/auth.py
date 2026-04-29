"""Auth: who is allowed to talk to the bot?

v3 multi-user model:
  - Admins (Telegram IDs in settings.admin_ids()) are *always* authorized and
    auto-promoted to active on /start. They can /invite.
  - Anyone else needs to redeem an invite code via `/start <code>`. Once active,
    the user has full bot access (no admin powers — they can't /invite further).
  - Inactive users see a "Not authorized." reply with no detail leak.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from nudgr.config import settings
from nudgr.db.models import User
from nudgr.db.session import session_scope


def is_admin_telegram_id(telegram_user_id: int | None) -> bool:
    if telegram_user_id is None:
        return False
    return telegram_user_id in settings.admin_ids()


def is_active_user(user_id: UUID) -> bool:
    with session_scope() as s:
        u = s.get(User, user_id)
        return bool(u and u.is_active)


def lookup_telegram_user(telegram_user_id: int) -> User | None:
    """Read-only fetch (detached) for a telegram user id."""
    with session_scope() as s:
        u = s.execute(
            select(User).where(User.telegram_user_id == telegram_user_id)
        ).scalar_one_or_none()
        if u is not None:
            s.expunge(u)
        return u


def is_authorized_telegram_id(telegram_user_id: int | None) -> bool:
    """True iff the Telegram user is an admin OR an existing active user."""
    if telegram_user_id is None:
        return False
    if is_admin_telegram_id(telegram_user_id):
        return True
    u = lookup_telegram_user(telegram_user_id)
    return bool(u and u.is_active)
