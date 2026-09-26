"""Password hashing must survive the bcrypt 4 -> 5 upgrade (dependabot #40).

bcrypt only uses the first 72 bytes of a password. 4.x truncated longer input
silently; 5.x raises ValueError. verify_password() swallows exceptions as
"wrong password", so without the guard in app/auth.py an operator with a
>72-byte password -- hashed under 4.x -- is silently locked out of the UI the
moment 5.x is installed.

These tests hold under both versions: CI runs them on whichever bcrypt
requirements.txt pins.
"""

from __future__ import annotations

import bcrypt
import pytest

from app import auth

LONG_ASCII = "correct horse battery staple " * 4  # 116 bytes
# Multibyte, with a 2-byte character straddling the 72-byte boundary: 71 bytes
# of 'a' then 'é' (2 bytes), so a naive character-level cut and a byte-level cut
# disagree. bcrypt 4.x cut BYTES.
STRADDLE = "a" * 71 + "é" + "tail"


def _hashed_the_bcrypt4_way(pw: str) -> str:
    """What bcrypt 4.x stored for `pw`: it silently used only the first 72 bytes."""
    return bcrypt.hashpw(pw.encode()[:72], bcrypt.gensalt(rounds=4)).decode()


@pytest.mark.parametrize("pw", [LONG_ASCII, STRADDLE], ids=["ascii", "multibyte-straddle"])
def test_a_long_password_hashed_before_the_upgrade_still_verifies(pw):
    """The lockout this guards against. Fails under bcrypt 5 without the guard."""
    assert len(pw.encode()) > 72
    assert auth.verify_password(pw, _hashed_the_bcrypt4_way(pw))


@pytest.mark.parametrize("pw", [LONG_ASCII, STRADDLE], ids=["ascii", "multibyte-straddle"])
def test_a_long_password_can_be_set_and_verified(pw):
    """Setting one must not 500: under bcrypt 5 hashpw raises without the guard."""
    assert auth.verify_password(pw, auth.hash_password(pw))


def test_a_wrong_password_is_still_rejected():
    hashed = auth.hash_password(LONG_ASCII)
    assert not auth.verify_password("not the password", hashed)


def test_the_guard_does_not_widen_what_matches():
    """Truncation is bcrypt's own behaviour, not something this adds: two
    passwords sharing their first 72 bytes already matched under 4.x. Pin that,
    so nobody mistakes it for a regression introduced here."""
    hashed = auth.hash_password("p" * 72 + "first-ending")
    assert auth.verify_password("p" * 72 + "a-different-ending", hashed)


def test_a_short_password_is_untouched():
    hashed = auth.hash_password("hunter2")
    assert auth.verify_password("hunter2", hashed)
    assert not auth.verify_password("hunter3", hashed)
