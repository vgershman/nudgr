"""Shared pytest fixtures + env bootstrapping.

Most modules pull in `nudgr.config.settings` at import time, which in turn
needs the env vars below to construct successfully. We seed safe placeholders
here so tests can `from nudgr.foo import bar` without a real .env on the box.

Tests that need DB access (invites, scheduler helpers) are kept narrow — most
of the suite focuses on pure-functional modules (recurrence, quiet, i18n,
parser helpers) so it runs offline and fast.
"""

from __future__ import annotations

import os

# Seed minimal config BEFORE the first nudgr.* import. pytest collects + imports
# test modules eagerly, so this has to happen at module load time of conftest.
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test")
os.environ.setdefault("TELEGRAM_ADMIN_IDS", "1001")
os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://nudgr:nudgr_dev@localhost:5433/nudgr"
)
os.environ.setdefault("TIMEZONE", "Europe/Amsterdam")
