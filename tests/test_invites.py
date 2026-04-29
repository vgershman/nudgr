"""Invite-code generator: format + character set + uniqueness over many draws.

Redemption logic hits the DB and is exercised manually / in integration —
this file locks down the pure-functional pieces.
"""

from __future__ import annotations

from nudgr.invites import CODE_LEN, _ALPHABET, _new_code


def test_new_code_length():
    for _ in range(50):
        assert len(_new_code()) == CODE_LEN


def test_new_code_alphabet():
    allowed = set(_ALPHABET)
    for _ in range(50):
        c = _new_code()
        assert all(ch in allowed for ch in c), f"bad chars in {c!r}"


def test_new_code_no_confusable_glyphs():
    # 0/O/1/I/L are deliberately excluded — verify none ever leak in.
    forbidden = set("01IL")
    for _ in range(200):
        assert not (set(_new_code()) & forbidden)


def test_new_code_high_entropy():
    """Sanity: many fresh codes should rarely collide. 1000 draws over ~31^8
    space gives essentially-zero collision probability."""
    codes = {_new_code() for _ in range(1000)}
    assert len(codes) == 1000
