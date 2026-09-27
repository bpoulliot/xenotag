"""Roadmap I1: cross-origin state-changing requests are rejected by Origin/Referer.

The operator's rule (2026-09-26): a POST/PUT/PATCH/DELETE whose ``Origin`` (or,
absent that, ``Referer``) is not the request's own origin gets a 403;
``/webhook/*`` is exempt; a request with neither header is a non-browser client
and is allowed. Every test that shows a rejection has a partner showing the
same request is let through when the origin matches, so a middleware that
rejected (or allowed) everything would fail here.
"""

from __future__ import annotations

import logging
import threading

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.config import AppConfig, AuthConfig
from app.web.csrf import CHECKED_METHODS, own_origin, parse_origin

REJECTED = {"detail": "Cross-origin request rejected"}

# Re-listed from app/web/routes.py (2026-09-27). A new mutating route fails
# test_mutating_routes_are_the_known_set until it is added here, so it is
# looked at rather than silently covered or silently exempt.
MUTATING_ROUTES = {
    ("POST", "/login"),
    ("POST", "/logout"),
    ("POST", "/scan/full"),
    ("POST", "/scan/incremental"),
    ("POST", "/scan/cancel"),
    ("DELETE", "/api/scan-errors"),
    ("DELETE", "/api/legacy-tags"),
    ("POST", "/webhook/{source}"),
    ("PUT", "/api/settings"),
    ("POST", "/api/auth/change-password"),
    ("PUT", "/config"),
    ("POST", "/api/jellyfin/test"),
    ("POST", "/api/arr/test"),
    ("POST", "/api/arr/rootfolders"),
    ("POST", "/api/arr-sync/dry-run"),
    ("POST", "/api/deleted-items/report"),
}
CHECKED_ROUTES = sorted(r for r in MUTATING_ROUTES if not r[1].startswith("/webhook/"))

SELF = "http://testserver"  # TestClient's own origin
EVIL = "https://evil.example.org"


@pytest.fixture
def client(monkeypatch):
    from app.main import app
    from app.web import routes

    monkeypatch.setattr(routes._limiter, "enabled", False)
    monkeypatch.setattr(routes, "get_config", lambda: AppConfig())
    return TestClient(app)


def _send(client, method, path, headers=None):
    return client.request(method, path.replace("{source}", "sonarr"), headers=headers or {})


def _rejected(resp) -> bool:
    return resp.status_code == 403 and resp.json() == REJECTED


# ---------------------------------------------------------------------------
# The route list the middleware has to cover
# ---------------------------------------------------------------------------


def test_mutating_routes_are_the_known_set():
    from app.main import app

    found = {
        (method, route.path)
        for route in app.routes
        for method in getattr(route, "methods", None) or ()
        if method in CHECKED_METHODS
    }
    assert found == MUTATING_ROUTES


# ---------------------------------------------------------------------------
# Every checked route: cross-origin 403, same-origin and no-header pass
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method,path", CHECKED_ROUTES)
def test_cross_origin_is_rejected(client, method, path):
    assert _rejected(_send(client, method, path, {"Origin": EVIL}))


@pytest.mark.parametrize("method,path", CHECKED_ROUTES)
def test_same_origin_reaches_the_route(client, method, path):
    resp = _send(client, method, path, {"Origin": SELF})
    assert not _rejected(resp), resp.text


@pytest.mark.parametrize("method,path", CHECKED_ROUTES)
def test_cross_origin_referer_is_rejected_when_origin_is_absent(client, method, path):
    assert _rejected(_send(client, method, path, {"Referer": f"{EVIL}/attack.html"}))


@pytest.mark.parametrize("method,path", CHECKED_ROUTES)
def test_same_origin_referer_reaches_the_route(client, method, path):
    resp = _send(client, method, path, {"Referer": f"{SELF}/settings?tab=badges"})
    assert not _rejected(resp), resp.text


@pytest.mark.parametrize("method,path", CHECKED_ROUTES)
def test_neither_header_is_a_non_browser_client_and_passes(client, method, path):
    resp = _send(client, method, path)
    assert not _rejected(resp), resp.text


def test_origin_wins_over_referer(client):
    # A matching Referer does not rescue a foreign Origin, and vice versa.
    assert _rejected(_send(client, "POST", "/scan/full", {"Origin": EVIL, "Referer": f"{SELF}/"}))
    assert not _rejected(_send(client, "POST", "/scan/full", {"Origin": SELF, "Referer": f"{EVIL}/"}))


@pytest.mark.parametrize(
    "origin", ["null", "https://testserver", "http://testserver:8080", "http://testserver.evil.org"]
)
def test_near_misses_are_rejected(client, origin):
    # "null" (sandboxed iframe), wrong scheme, wrong port, suffix-extended host.
    assert _rejected(_send(client, "POST", "/scan/full", {"Origin": origin}))


def test_blank_headers_count_as_absent(client):
    assert not _rejected(_send(client, "POST", "/scan/full", {"Origin": "", "Referer": ""}))


def test_webhook_is_exempt(client):
    resp = client.post("/webhook/sonarr", content=b"{}", headers={"Origin": EVIL})
    assert not _rejected(resp)
    assert resp.json() != REJECTED
    # Control: the same foreign Origin on a checked route in the same client is refused.
    assert _rejected(client.post("/scan/full", headers={"Origin": EVIL}))


def test_safe_methods_are_not_checked(client):
    resp = client.get("/health", headers={"Origin": EVIL})
    assert resp.status_code == 200


def test_rejection_carries_the_security_headers(client):
    resp = _send(client, "POST", "/scan/full", {"Origin": EVIL})
    assert _rejected(resp)
    assert "frame-ancestors 'none'" in resp.headers["content-security-policy"]


def test_rejection_is_logged_with_what_was_expected(client, caplog):
    with caplog.at_level(logging.WARNING, logger="app.web.csrf"):
        _send(client, "PUT", "/api/settings", {"Origin": EVIL})
    assert "Origin https://evil.example.org" in caplog.text
    assert "http://testserver:80" in caplog.text


# ---------------------------------------------------------------------------
# The attack itself: a signed-in admin's cookie on a cross-origin request
# ---------------------------------------------------------------------------


@pytest.fixture
def admin(monkeypatch):
    from app.main import app
    from app.web import routes

    # Not a real hash: the session token only signs it, nothing verifies a password here.
    cfg = AppConfig(auth=AuthConfig(username="admin", password_hash="x", secret_key="k" * 64))  # noqa: S106
    monkeypatch.setattr(routes, "get_config", lambda: cfg)
    token = auth.create_session("admin", cfg.auth.secret_key, cfg.auth.password_hash)
    scans = threading.Event()  # the route starts the scan on a thread
    monkeypatch.setattr(routes, "run_full_scan", lambda *a, **k: scans.set())
    return TestClient(app), {"Cookie": f"{routes.SESSION_COOKIE}={token}"}, scans


def test_signed_in_cross_origin_scan_is_refused_and_nothing_runs(admin):
    client, cookie, scans = admin
    resp = client.post("/scan/full", headers={**cookie, "Origin": "https://sibling.example.org"})
    assert _rejected(resp)
    assert not scans.wait(0.5)


def test_signed_in_same_origin_scan_runs(admin):
    client, cookie, scans = admin
    resp = client.post("/scan/full", headers={**cookie, "Origin": SELF})
    assert resp.status_code == 200, resp.text
    assert scans.wait(5)


# ---------------------------------------------------------------------------
# Behind a reverse proxy: what SWAG actually sends
# ---------------------------------------------------------------------------

# SWAG's proxy.conf: Host $host; X-Forwarded-Host $host:$server_port;
# X-Forwarded-Proto $scheme -- over a plain-http hop to xenotag:7755.
SWAG = {
    "Host": "xenotag.example.org",
    "X-Forwarded-Host": "xenotag.example.org:443",
    "X-Forwarded-Proto": "https",
}


def test_behind_swag_the_public_https_origin_passes(client):
    resp = _send(client, "PUT", "/api/settings", {**SWAG, "Origin": "https://xenotag.example.org"})
    assert not _rejected(resp), resp.text


@pytest.mark.parametrize(
    "origin",
    ["http://xenotag.example.org", "https://other.example.org", "https://xenotag.example.org:8443"],
)
def test_behind_swag_a_different_origin_is_rejected(client, origin):
    assert _rejected(_send(client, "PUT", "/api/settings", {**SWAG, "Origin": origin}))


def test_without_forwarded_headers_the_public_origin_would_be_refused(client):
    # The naive comparison: uvicorn sees http:// and SWAG's Host. This is why the
    # forwarded headers are read -- without them every real save would be a 403.
    headers = {"Host": "xenotag.example.org", "Origin": "https://xenotag.example.org"}
    assert _rejected(_send(client, "PUT", "/api/settings", headers))


def test_host_is_used_when_the_proxy_sends_no_forwarded_host(client):
    headers = {"Host": "xenotag.example.org", "X-Forwarded-Proto": "https", "Origin": "https://xenotag.example.org"}
    assert not _rejected(_send(client, "PUT", "/api/settings", headers))


# ---------------------------------------------------------------------------
# The two parsers, directly
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("https://Example.org", ("https", "example.org", 443)),
        ("http://example.org", ("http", "example.org", 80)),
        ("http://127.0.0.1:7755", ("http", "127.0.0.1", 7755)),
        ("http://[::1]:7755", ("http", "::1", 7755)),
        ("https://example.org/settings?x=1#y", ("https", "example.org", 443)),
        ("null", None),
        ("", None),
        ("file:///etc/passwd", None),
        ("javascript:alert(1)", None),
        ("http://example.org:99999", None),
        ("http://", None),
    ],
)
def test_parse_origin(value, expected):
    assert parse_origin(value) == expected


@pytest.mark.parametrize(
    "scheme,headers,expected",
    [
        ("http", {"host": "127.0.0.1:7755"}, ("http", "127.0.0.1", 7755)),
        ("http", {"host": "localhost"}, ("http", "localhost", 80)),
        ("http", {"host": "[::1]:7755"}, ("http", "::1", 7755)),
        ("http", {"host": "internal", "x-forwarded-proto": "https"}, ("https", "internal", 443)),
        (
            "http",
            {"host": "internal", "x-forwarded-host": "Pub.Example.org:443", "x-forwarded-proto": "HTTPS"},
            ("https", "pub.example.org", 443),
        ),
        # Traefik style: host without port, the port on its own.
        (
            "http",
            {"host": "internal", "x-forwarded-host": "pub", "x-forwarded-proto": "https", "x-forwarded-port": "8443"},
            ("https", "pub", 8443),
        ),
        # A proxy chain appends; the client-most entry is the one the browser addressed.
        (
            "http",
            {"host": "internal", "x-forwarded-host": "pub, mid", "x-forwarded-proto": "https, http"},
            ("https", "pub", 443),
        ),
        ("http", {"host": ""}, None),
        ("http", {"host": "h:notaport"}, None),
        ("http", {"host": "h", "x-forwarded-proto": "gopher"}, None),
    ],
)
def test_own_origin(scheme, headers, expected):
    assert own_origin(scheme, headers) == expected
