"""Roadmap B24: `/health` must not fabricate a Jellyfin verdict, and it must
be reachable WITHOUT a session -- `docker-compose.yml`'s healthcheck curls it
with no cookie at all.

The operator's decided fix: exempt `/health` from the session gate. An
unauthenticated caller gets only overall up/down and no dependency detail
(never a fabricated "unreachable" for a dependency it never contacted); an
authenticated caller (the dashboard UI) still gets full detail, and `status`
is computed from the real checks instead of being hardcoded.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import AppConfig


class _FakeJF:
    def __init__(self, url, api_key):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def health(self):
        return {"ok": True, "status": "healthy", "message": "Healthy"}


class _FakeJFDown(_FakeJF):
    def health(self):
        return {"ok": False, "status": "auth_error", "message": "Invalid API key"}


@pytest.fixture
def client(monkeypatch):
    from app.main import app
    from app.web import routes

    # Isolate from whatever config a sibling test module left behind: no
    # sonarr/radarr instances, so an authenticated call never makes a real
    # network call for those.
    monkeypatch.setattr(routes, "get_config", lambda: AppConfig())
    return TestClient(app)


def test_no_session_is_reachable_and_carries_no_dependency_detail(client):
    # The exact shape docker-compose.yml's `curl -f http://localhost:7755/health`
    # produces: no Cookie header at all.
    resp = client.get("/health")
    assert "cookie" not in resp.request.headers
    assert resp.status_code == 200
    body = resp.json()
    assert body["jellyfin"] is None
    assert body["sonarr"] is None
    assert body["radarr"] is None


def test_no_session_never_claims_a_jellyfin_verdict_it_did_not_measure(client):
    resp = client.get("/health")
    body = resp.json()
    # The old bug: jellyfin={"ok": False, "status": "unreachable", "message":
    # "Not authenticated"} for a dependency that was never contacted.
    assert body.get("jellyfin") is None
    assert "unreachable" not in str(body)


def test_no_session_status_is_not_a_hardcoded_ok(client):
    # Nothing was checked, so "ok" (a claim of health) would be fabricated.
    resp = client.get("/health")
    assert resp.json()["status"] != "ok"


def test_authenticated_status_follows_a_failing_jellyfin_check(client, monkeypatch):
    from app.web import routes

    monkeypatch.setattr(routes, "_current_user", lambda request: "admin")
    monkeypatch.setattr(routes, "JellyfinClient", _FakeJFDown)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["jellyfin"] == {"ok": False, "status": "auth_error", "message": "Invalid API key"}
    assert body["status"] != "ok"


def test_authenticated_status_is_ok_when_jellyfin_check_passes(client, monkeypatch):
    from app.web import routes

    monkeypatch.setattr(routes, "_current_user", lambda request: "admin")
    monkeypatch.setattr(routes, "JellyfinClient", _FakeJF)
    resp = client.get("/health")
    body = resp.json()
    assert body["jellyfin"] == {"ok": True, "status": "healthy", "message": "Healthy"}
    assert body["sonarr"] == []
    assert body["radarr"] == []
    assert body["status"] == "ok"
