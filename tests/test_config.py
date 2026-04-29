"""Config: admin_ids parsing handles CSV / legacy / garbage."""

from __future__ import annotations

import importlib

import pytest


def _reload_settings(monkeypatch, **env):
    """Clear the cached settings module + re-import with patched env."""
    # Default seed (matches conftest baseline).
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x:y@localhost/z")
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    import nudgr.config as cfg

    importlib.reload(cfg)
    return cfg.settings


def test_admin_ids_csv(monkeypatch):
    s = _reload_settings(monkeypatch, TELEGRAM_ADMIN_IDS="100,200,300", TELEGRAM_USER_ID="0")
    assert s.admin_ids() == {100, 200, 300}


def test_admin_ids_handles_whitespace(monkeypatch):
    s = _reload_settings(monkeypatch, TELEGRAM_ADMIN_IDS=" 1 ,  2,3 ", TELEGRAM_USER_ID="0")
    assert s.admin_ids() == {1, 2, 3}


def test_admin_ids_legacy_fallback(monkeypatch):
    s = _reload_settings(monkeypatch, TELEGRAM_ADMIN_IDS="", TELEGRAM_USER_ID="42")
    assert s.admin_ids() == {42}


def test_admin_ids_union_legacy_and_env_list(monkeypatch):
    s = _reload_settings(monkeypatch, TELEGRAM_ADMIN_IDS="1,2", TELEGRAM_USER_ID="3")
    assert s.admin_ids() == {1, 2, 3}


def test_admin_ids_skips_garbage_tokens(monkeypatch):
    s = _reload_settings(monkeypatch, TELEGRAM_ADMIN_IDS="1,not-an-id,3", TELEGRAM_USER_ID="0")
    assert s.admin_ids() == {1, 3}


def test_admin_ids_empty(monkeypatch):
    s = _reload_settings(monkeypatch, TELEGRAM_ADMIN_IDS="", TELEGRAM_USER_ID="0")
    assert s.admin_ids() == set()
