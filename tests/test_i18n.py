"""Locale detection + label fallback + format kwargs."""

from __future__ import annotations

from nudgr.i18n import detect_locale, label, supported_locales


def test_detect_locale_english():
    assert detect_locale("hello world") == "en"
    assert detect_locale("remind me at 9pm") == "en"


def test_detect_locale_russian():
    assert detect_locale("напомни через час") == "ru"
    assert detect_locale("ёлка") == "ru"  # ё counts


def test_detect_locale_mixed_picks_russian():
    # Any Cyrillic → ru. Pragmatic for a bilingual user.
    assert detect_locale("call мама") == "ru"


def test_detect_locale_empty_defaults_english():
    assert detect_locale("") == "en"
    assert detect_locale(None) == "en"  # type: ignore[arg-type]


def test_label_fallback_to_english_for_unknown_locale():
    assert label("decision_done", "fr") == label("decision_done", "en")


def test_label_passes_through_kwargs():
    out = label("got_it_in", "en", eta="15m")
    assert "15m" in out


def test_label_returns_template_on_missing_kwargs():
    # Format kwargs missing → returns template instead of raising.
    out = label("got_it_in", "en")  # missing eta
    assert "{eta}" in out


def test_label_falls_back_to_key_for_unknown_key():
    assert label("definitely_not_a_real_key", "en") == "definitely_not_a_real_key"


def test_label_invite_strings_present_both_locales():
    for loc in ("en", "ru"):
        for key in (
            "invite_redeemed",
            "invite_expired",
            "invite_used",
            "invite_unknown",
            "invite_required",
            "invite_admin_only",
        ):
            v = label(key, loc)
            assert v and v != key, f"{loc}/{key} missing"


def test_label_quiet_digest_strings_present_both_locales():
    for loc in ("en", "ru"):
        for key in (
            "quiet_set",
            "quiet_cleared",
            "quiet_invalid",
            "digest_set",
            "digest_cleared",
            "digest_invalid",
            "digest_header",
        ):
            v = label(key, loc)
            assert v and v != key, f"{loc}/{key} missing"


def test_supported_locales_contains_en_ru():
    assert "en" in supported_locales()
    assert "ru" in supported_locales()
