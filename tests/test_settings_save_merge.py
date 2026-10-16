"""
B27 and B28: a structured Settings save must merge, not replace.

`PUT /api/settings` rebuilds the whole config from the page's body. The page
sends `auth: {username}` only, so `auth.secret_key` (the session-signing key,
which the page never shows) validated to its default "" and was written to
config.yml and installed as the live config (B27). `buildSettings()` likewise
sent `scan` without the key the page does not show, `max_workers` (B28).

All values here are invented.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app import auth
from app.config import get_config, load_config

HASH = "$2b$12$notarealhashjustastringforthetest"
KEY = "disk-sentinel-signing-key-" + "a" * 38
ENV_KEY = "env-sentinel-signing-key-" + "b" * 39
TEMPLATE = Path(__file__).resolve().parent.parent / "app" / "web" / "templates" / "index.html"


@pytest.fixture
def cfg_file(tmp_path, monkeypatch):
    for var in ("XENOTAG_SECRET_KEY", "XENOTAG_SECRET_KEY_FILE", "JELLYFIN_API_KEY", "JELLYFIN_API_KEY_FILE"):
        monkeypatch.delenv(var, raising=False)
    path = tmp_path / "config.yml"
    path.write_text(
        yaml.dump(
            {
                "auth": {"username": "admin", "password_hash": HASH, "secret_key": KEY},
                "scan": {"max_workers": 9},
            }
        )
    )
    return path


@pytest.fixture
def client(cfg_file, monkeypatch):
    from app.main import app
    from app.web import routes

    load_config(cfg_file)
    monkeypatch.setattr(routes, "_require_user", lambda request: "admin")
    monkeypatch.setattr(routes, "reschedule", lambda *a, **k: None)
    return TestClient(app)


def _page_body() -> dict:
    """What the Settings page sends for `auth`: the username and nothing else."""
    return {"auth": {"username": "admin"}}


def _keys_named(node, name: str) -> list:
    """Every value stored under `name`, at any depth of decoded JSON."""
    found = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k == name:
                found.append(v)
            found.extend(_keys_named(v, name))
    elif isinstance(node, list):
        for item in node:
            found.extend(_keys_named(item, name))
    return found


# ── B27a: the save keeps the signing key ─────────────────────────────────────
def test_structured_save_keeps_the_signing_key_on_disk_and_in_memory(client, cfg_file):
    assert client.put("/api/settings", json=_page_body()).status_code == 200

    assert yaml.safe_load(cfg_file.read_text())["auth"]["secret_key"] == KEY
    assert get_config().auth.secret_key == KEY


def test_a_session_minted_before_the_save_verifies_after_it(client):
    cfg = get_config()
    token = auth.create_session("admin", cfg.auth.secret_key, cfg.auth.password_hash)
    assert auth.get_session_user(token, cfg.auth.secret_key, cfg.auth.password_hash) == "admin"

    assert client.put("/api/settings", json=_page_body()).status_code == 200

    after = get_config().auth
    assert auth.get_session_user(token, after.secret_key, after.password_hash) == "admin"


def test_an_env_supplied_key_is_still_never_written_back(client, cfg_file, monkeypatch):
    monkeypatch.setenv("XENOTAG_SECRET_KEY", ENV_KEY)
    load_config(cfg_file)
    assert get_config().auth.secret_key == ENV_KEY

    assert client.put("/api/settings", json=_page_body()).status_code == 200

    text = cfg_file.read_text()
    assert ENV_KEY not in text
    assert "secret_key" not in yaml.safe_load(text)["auth"]
    assert get_config().auth.secret_key == ENV_KEY


# ── B27b: the key never reaches the browser ──────────────────────────────────
def test_get_settings_carries_no_secret_key(client):
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    body = resp.json()
    assert _keys_named(body, "secret_key") == []
    assert _keys_named(body, "password_hash") == []
    assert KEY not in json.dumps(body)
    assert body["auth"]["username"] == "admin"


# ── B28: a structured save keeps scan.max_workers ────────────────────────────
def test_the_page_spreads_the_loaded_scan_settings():
    """
    The repo has no way to run the page's JavaScript (no node or browser in the
    suite), so the page half is asserted on the template text, as the other
    template tests do; the server half is the test below.
    """
    html = TEMPLATE.read_text()
    assert re.search(r"scan:\s*\{\s*\.\.\.\(settings\.scan \|\| \{\}\),", html)
    # ...and it comes before the fields the page shows, so those still win.
    assert html.index("...(settings.scan || {}),") < html.index("reconcile_schedule: document.getElementById")


def test_a_save_whose_body_carries_max_workers_keeps_it(client, cfg_file):
    """What the page now sends: the loaded `scan` object, `max_workers` included."""
    scan = client.get("/api/settings").json()["scan"]
    assert scan["max_workers"] == 9

    assert client.put("/api/settings", json={**_page_body(), "scan": scan}).status_code == 200

    assert yaml.safe_load(cfg_file.read_text())["scan"]["max_workers"] == 9
    assert get_config().scan.max_workers == 9
