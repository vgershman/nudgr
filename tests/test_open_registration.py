"""OPEN_REGISTRATION flag — auth gate behavior in open vs private mode.

Covers the pure-functional auth helpers; the actual user-row activation
runs through `_upsert_user` and is exercised manually + integration.
"""

from __future__ import annotations

import importlib

import pytest


def _reload_auth(monkeypatch, **env):
    """Reload nudgr.config + nudgr.auth with patched env."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg://x:y@localhost/z"
    )
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    import nudgr.config as cfg

    importlib.reload(cfg)
    import nudgr.auth as auth

    importlib.reload(auth)
    return cfg, auth


def test_open_mode_lets_anyone_in(monkeypatch):
    _, auth = _reload_auth(
        monkeypatch, TELEGRAM_ADMIN_IDS="1001", OPEN_REGISTRATION="true"
    )
    # Random non-admin id passes auth in open mode without a DB row.
    assert auth.is_authorized_telegram_id(9999) is True


def test_open_mode_rejects_zero_or_none(monkeypatch):
    _, auth = _reload_auth(monkeypatch, OPEN_REGISTRATION="true")
    assert auth.is_authorized_telegram_id(0) is False
    assert auth.is_authorized_telegram_id(None) is False


def test_private_mode_rejects_unknown_non_admin(monkeypatch):
    _, auth = _reload_auth(
        monkeypatch, TELEGRAM_ADMIN_IDS="1001", OPEN_REGISTRATION="false"
    )
    # Stub the DB lookup so this runs without a live postgres. In private mode
    # a non-admin with no User row is rejected.
    monkeypatch.setattr(auth, "lookup_telegram_user", lambda _id: None)
    assert auth.is_authorized_telegram_id(9999) is False


def test_admin_always_passes(monkeypatch):
    for mode in ("true", "false"):
        _, auth = _reload_auth(
            monkeypatch, TELEGRAM_ADMIN_IDS="1001", OPEN_REGISTRATION=mode
        )
        assert auth.is_authorized_telegram_id(1001) is True, f"admin failed in mode={mode}"


def test_default_is_open(monkeypatch):
    cfg, auth = _reload_auth(monkeypatch, TELEGRAM_ADMIN_IDS="1001")
    # OPEN_REGISTRATION not set → defaults to True.
    assert cfg.settings.open_registration is True
    assert auth.is_authorized_telegram_id(9999) is True
