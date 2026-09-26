"""A transport that refuses to send anything but a read (roadmap B5).

Wrapping a client's transport makes "this run is read-only" a property of the
wire rather than of every call site: a non-GET raises *before* the request is
handed to the network layer, so a wrong branch in the caller cannot reach the
server. Used for the *arr dry run, and for every *arr client while writes are
off.
"""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class ReadOnlyViolation(RuntimeError):
    """A write was attempted through a read-only transport and was not sent."""


class ReadOnlyTransport(httpx.BaseTransport):
    def __init__(self, inner: httpx.BaseTransport | None = None, *, quiet: bool = False) -> None:
        self._inner = inner or httpx.HTTPTransport()
        self._quiet = quiet  # the self-test's deliberate violations are not news
        self.sent: dict[str, int] = {}
        self.blocked: list[str] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.method not in SAFE_METHODS:
            # Path only: a query string can carry a token on some servers.
            what = f"{request.method} {request.url.path}"
            self.blocked.append(what)
            if not self._quiet:
                log.error("Read-only transport blocked %s — nothing was sent", what)
            raise ReadOnlyViolation(f"read-only transport refused {what}")
        self.sent[request.method] = self.sent.get(request.method, 0) + 1
        return self._inner.handle_request(request)

    def close(self) -> None:
        self._inner.close()


def self_test() -> list[str]:
    """Prove the guard in both directions; return the failures (empty = pass).

    A guard that can only confirm what we expect confirms it whether or not it
    works, so this checks both halves: a GET must reach the inner transport,
    and a write must raise *and* leave the inner transport untouched. The last
    check goes through a real ``HTTPTransport`` aimed at a closed port: if the
    guard let the POST through, it would fail with a connection error instead
    of ``ReadOnlyViolation``.
    """
    failures: list[str] = []
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.method)
        return httpx.Response(200, json={})

    with httpx.Client(transport=ReadOnlyTransport(httpx.MockTransport(handler), quiet=True)) as client:
        if client.get("http://guard.test/api/v3/tag").status_code != 200 or seen != ["GET"]:
            failures.append(f"GET did not pass through (inner saw {seen})")
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            try:
                client.request(method, "http://guard.test/api/v3/tag", json={"label": "x"})
                failures.append(f"{method} was NOT blocked")
            except ReadOnlyViolation:
                pass
        if seen != ["GET"]:
            failures.append(f"a write reached the inner transport (inner saw {seen})")

    with httpx.Client(transport=ReadOnlyTransport(quiet=True), timeout=2) as client:
        try:
            client.post("http://127.0.0.1:9/api/v3/tag", json={"label": "x"})
            failures.append("POST through a real transport was NOT blocked")
        except ReadOnlyViolation:
            pass
        except httpx.HTTPError as exc:
            failures.append(f"POST reached the network layer ({type(exc).__name__}) — guard not in the path")
    return failures
