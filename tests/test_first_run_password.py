"""B16: the generated first-run password goes to a 0600 file, never to the log.

Before B16, bootstrap() logged the generated admin password at WARNING (CodeQL
#8), where anyone with `docker logs` could read a working credential. Now it is
written to `initial-password` beside config.yml, created 0600 in one step, and
only that path is logged; the Settings password change deletes the file.
"""

from __future__ import annotations

import logging
import stat

import pytest
import yaml

from app import auth
from app.config import INITIAL_PASSWORD_FILE, get_config, initial_password_path, load_config, save_auth


@pytest.fixture
def cfg_file(tmp_path, monkeypatch):
    for var in ("XENOTAG_USERNAME", "XENOTAG_PASSWORD", "XENOTAG_SECRET_KEY", "XENOTAG_SECRET_KEY_FILE"):
        monkeypatch.delenv(var, raising=False)
    path = tmp_path / "config.yml"
    path.write_text(yaml.dump({"auth": {"username": "admin"}}))
    load_config(path)
    return path


def _first_start(caplog):
    """What main.py's lifespan does, with every log record captured."""
    cfg = get_config()
    with caplog.at_level(logging.DEBUG):
        auth.bootstrap(cfg.auth, save_auth, initial_password_path())
    return cfg


def _logged(caplog) -> str:
    """Every record's rendered message, plus any exception text attached to it."""
    out = []
    for r in caplog.records:
        out.append(r.getMessage())
        if r.exc_info:
            out.append(logging.Formatter().formatException(r.exc_info))
    return "\n".join(out)


def test_first_run_writes_a_0600_file_and_logs_only_its_path(cfg_file, caplog):
    cfg = _first_start(caplog)
    pw_file = cfg_file.parent / INITIAL_PASSWORD_FILE
    assert initial_password_path() == pw_file

    assert pw_file.is_file()
    assert stat.S_IMODE(pw_file.stat().st_mode) == 0o600
    password = pw_file.read_text().strip()
    assert len(password) >= 12

    # The file holds the credential that was stored -- in memory and on disk.
    assert auth.verify_password(password, cfg.auth.password_hash)
    on_disk = yaml.safe_load(cfg_file.read_text())["auth"]["password_hash"]
    assert auth.verify_password(password, on_disk)

    text = _logged(caplog)
    assert str(pw_file) in text
    assert password not in text


def test_the_file_is_created_0600_whatever_the_umask(cfg_file, caplog):
    """Mode comes from os.open(), not a later chmod: a permissive umask cannot widen it."""
    import os

    old = os.umask(0)
    try:
        _first_start(caplog)
    finally:
        os.umask(old)
    assert stat.S_IMODE((cfg_file.parent / INITIAL_PASSWORD_FILE).stat().st_mode) == 0o600


def test_the_env_var_route_creates_no_file(cfg_file, caplog, monkeypatch):
    monkeypatch.setenv("XENOTAG_PASSWORD", "a-long-enough-env-password")
    cfg = _first_start(caplog)
    assert not (cfg_file.parent / INITIAL_PASSWORD_FILE).exists()
    assert auth.verify_password("a-long-enough-env-password", cfg.auth.password_hash)
    assert "a-long-enough-env-password" not in _logged(caplog)


def test_an_existing_hash_creates_no_file(cfg_file, caplog):
    get_config().auth.password_hash = auth.hash_password("already-set-password")
    _first_start(caplog)
    assert not (cfg_file.parent / INITIAL_PASSWORD_FILE).exists()


def test_a_second_start_with_the_file_present_neither_overwrites_nor_relogs(cfg_file, caplog):
    _first_start(caplog)
    pw_file = cfg_file.parent / INITIAL_PASSWORD_FILE
    before = pw_file.read_bytes()
    password = before.decode().strip()
    caplog.clear()

    load_config(cfg_file)  # a restart: the hash is now in config.yml
    _first_start(caplog)
    assert pw_file.read_bytes() == before
    text = _logged(caplog)
    assert password not in text
    # Said once: the file is still there, and how it goes away.
    assert text.count(str(pw_file)) == 1


def test_a_lost_hash_adopts_the_file_rather_than_overwriting_it(cfg_file, caplog):
    """A first run whose hash never reached config.yml: the file is still the password."""
    pw_file = cfg_file.parent / INITIAL_PASSWORD_FILE
    pw_file.write_text("left-by-an-earlier-start\n")
    pw_file.chmod(0o600)
    cfg = _first_start(caplog)
    assert pw_file.read_text() == "left-by-an-earlier-start\n"
    assert auth.verify_password("left-by-an-earlier-start", cfg.auth.password_hash)
    assert "left-by-an-earlier-start" not in _logged(caplog)


def test_an_empty_file_is_not_overwritten_and_sets_no_hash(cfg_file, caplog):
    pw_file = cfg_file.parent / INITIAL_PASSWORD_FILE
    pw_file.write_text("")
    cfg = _first_start(caplog)
    assert pw_file.read_text() == ""
    assert cfg.auth.password_hash == ""
    assert any(r.levelno >= logging.ERROR and str(pw_file) in r.getMessage() for r in caplog.records)


def test_an_unwritable_config_dir_logs_no_password_and_sets_no_hash(cfg_file, caplog, monkeypatch):
    seen = []

    def boom(path, password):
        seen.append(password)
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(auth, "_write_initial_password", boom)
    cfg = _first_start(caplog)
    assert cfg.auth.password_hash == ""
    assert seen and seen[0] not in _logged(caplog)
    assert "XENOTAG_PASSWORD" in _logged(caplog)


def test_the_old_message_would_fail_this_suite(caplog):
    """Self-test of the log check: a password logged the pre-B16 way IS caught."""
    with caplog.at_level(logging.DEBUG):
        logging.getLogger("app.auth").warning("  Password : %s", "the-secret-value")
    assert "the-secret-value" in _logged(caplog)


@pytest.fixture
def client(cfg_file, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.web import routes

    monkeypatch.setattr(routes, "_require_user", lambda request: "admin")
    return TestClient(app)


def test_a_password_change_removes_the_file(cfg_file, caplog, client):
    _first_start(caplog)
    pw_file = cfg_file.parent / INITIAL_PASSWORD_FILE
    password = pw_file.read_text().strip()

    r = client.post(
        "/api/auth/change-password",
        json={"current_password": password, "new_password": "a-brand-new-password"},
    )
    assert r.status_code == 200, r.text
    assert not pw_file.exists()
    assert auth.verify_password("a-brand-new-password", get_config().auth.password_hash)


def test_a_refused_change_keeps_the_file(cfg_file, caplog, client):
    _first_start(caplog)
    pw_file = cfg_file.parent / INITIAL_PASSWORD_FILE
    r = client.post(
        "/api/auth/change-password",
        json={"current_password": "not-the-password", "new_password": "a-brand-new-password"},
    )
    assert r.status_code == 400
    assert pw_file.exists()


def test_a_change_without_the_file_is_fine(cfg_file, client):
    get_config().auth.password_hash = auth.hash_password("set-by-the-env-var")
    r = client.post(
        "/api/auth/change-password",
        json={"current_password": "set-by-the-env-var", "new_password": "a-brand-new-password"},
    )
    assert r.status_code == 200, r.text
    assert not (cfg_file.parent / INITIAL_PASSWORD_FILE).exists()
