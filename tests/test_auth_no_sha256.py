"""B15: the unsalted sha256 fallback is gone; bcrypt is the only password hash.

Before B15, app/auth.py hashed the admin password as bare sha256 when
`import bcrypt` failed. An install that ran that way can still hold a
`sha256:` hash in config.yml; it must no longer verify, and bootstrap() must
say how to recover rather than leave the admin silently locked out.
"""

from __future__ import annotations

import hashlib
import logging
from types import SimpleNamespace

import bcrypt
import pytest

from app import auth


def _legacy_sha256(pw: str) -> str:
    """Exactly what the removed fallback stored."""
    return "sha256:" + hashlib.sha256(pw.encode()).hexdigest()


def test_a_sha256_hash_does_not_verify():
    assert not auth.verify_password("hunter2", _legacy_sha256("hunter2"))


def test_a_bcrypt_hash_verifies():
    hashed = bcrypt.hashpw(b"hunter2", bcrypt.gensalt(rounds=4)).decode()
    assert hashed.startswith("$2b$")
    assert auth.verify_password("hunter2", hashed)
    assert not auth.verify_password("hunter3", hashed)


def test_hashing_is_salted_bcrypt():
    a = auth.hash_password("hunter2")
    b = auth.hash_password("hunter2")
    assert a.startswith("$2b$") and b.startswith("$2b$")
    assert a != b  # a fresh salt each time
    assert auth.verify_password("hunter2", a) and auth.verify_password("hunter2", b)


def _cfg(password_hash: str):
    return SimpleNamespace(secret_key="k" * 64, username="admin", password_hash=password_hash)


def _errors(caplog):
    return [r for r in caplog.records if r.name == "app.auth" and r.levelno >= logging.ERROR]


def test_bootstrap_logs_the_recovery_for_a_sha256_hash(caplog):
    cfg = _cfg(_legacy_sha256("hunter2"))
    saved = []
    with caplog.at_level(logging.DEBUG, logger="app.auth"):
        auth.bootstrap(cfg, saved.append)
    errors = _errors(caplog)
    assert len(errors) == 1
    msg = errors[0].getMessage()
    assert "auth.password_hash" in msg and "config.yml" in msg and "restart" in msg
    # Not auto-reset: the stored hash is left for the operator to clear.
    assert cfg.password_hash == _legacy_sha256("hunter2")
    assert saved == []


@pytest.mark.parametrize("stored", ["bcrypt", ""], ids=["bcrypt-hash", "first-run"])
def test_bootstrap_is_quiet_for_bcrypt_and_first_run(caplog, monkeypatch, stored):
    monkeypatch.setenv("XENOTAG_PASSWORD", "hunter2")
    pw_hash = bcrypt.hashpw(b"hunter2", bcrypt.gensalt(rounds=4)).decode() if stored else ""
    cfg = _cfg(pw_hash)
    with caplog.at_level(logging.DEBUG, logger="app.auth"):
        auth.bootstrap(cfg, lambda _c: None)
    assert _errors(caplog) == []
    assert cfg.password_hash.startswith("$2b$")


def test_a_missing_bcrypt_fails_at_import(monkeypatch):
    """The downgrade itself: without bcrypt, auth must not load at all."""
    import importlib.util
    import sys

    monkeypatch.setitem(sys.modules, "bcrypt", None)  # makes `import bcrypt` raise
    spec = importlib.util.spec_from_file_location("_auth_without_bcrypt", auth.__file__)
    module = importlib.util.module_from_spec(spec)
    with pytest.raises(ImportError):
        spec.loader.exec_module(module)
