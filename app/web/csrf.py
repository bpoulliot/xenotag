"""Reject cross-origin state-changing requests (roadmap I1).

The session cookie is ``SameSite=lax``, which keeps it off cross-*site* POSTs.
It does nothing about a same-*site* one: behind a reverse proxy every other
``*.<domain>`` app is the same site, so a compromised sibling could POST to
xenotag with the admin's cookie attached. This closes that by the rule the
operator chose: a ``POST``/``PUT``/``PATCH``/``DELETE`` whose ``Origin`` (or,
absent that, ``Referer``) is not the request's own origin gets a 403.

* **The request's own origin** is what the client asked for, which behind a
  proxy is not what uvicorn sees on its socket. SWAG connects over plain http
  to ``xenotag:7755`` and uvicorn trusts forwarded headers only from 127.0.0.1,
  so ``request.url`` reads ``http://`` while the browser's ``Origin`` is
  ``https://``. So the scheme comes from ``X-Forwarded-Proto`` and the host
  from ``X-Forwarded-Host`` (then ``Host``) when present, the port from the
  host (then ``X-Forwarded-Port``, then the scheme's default).
  Trusting those headers here is safe even without a proxy in front: they are
  not CORS-safelisted, so a cross-origin page cannot make a browser send them
  without a preflight, and xenotag answers no preflight.
* **Neither header present** means a non-browser client -- browsers always
  send ``Origin`` on a cross-origin POST -- so the request is allowed. The
  session cookie is still required wherever it was before.
* ``/webhook/*`` is exempt: Sonarr/Radarr/Jellyfin post there server-to-server,
  and it authenticates by its own token.
"""

from __future__ import annotations

import json
import logging
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

CHECKED_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
EXEMPT_PREFIXES = ("/webhook/",)
_DEFAULT_PORTS = {"http": 80, "https": 443}

Origin = tuple[str, str, int]


def _first(value: str | None) -> str:
    """The client-most entry of a header a proxy chain may have appended to."""
    return (value or "").split(",")[0].strip()


def parse_origin(value: str) -> Origin | None:
    """``scheme://host[:port]`` (or a full URL, for ``Referer``) as a comparable triple.

    None for anything that is not an http(s) URL with a host -- including the
    literal ``null`` browsers send from sandboxed or privacy-redirected pages.
    """
    try:
        parts = urlsplit(value.strip())
        scheme = parts.scheme.lower()
        host = parts.hostname
        port = parts.port
    except ValueError:
        return None
    if scheme not in _DEFAULT_PORTS or not host:
        return None
    return scheme, host, port if port is not None else _DEFAULT_PORTS[scheme]


def own_origin(scheme: str, headers: dict[str, str]) -> Origin | None:
    """The origin the client addressed, from forwarded headers where a proxy set them.

    ``scheme`` is the one uvicorn saw; ``headers`` are lower-cased names.
    """
    scheme = (_first(headers.get("x-forwarded-proto")) or scheme).lower()
    host = _first(headers.get("x-forwarded-host")) or headers.get("host", "")
    try:
        parts = urlsplit(f"//{host}")
        hostname = parts.hostname
        port = parts.port
    except ValueError:
        return None
    if scheme not in _DEFAULT_PORTS or not hostname:
        return None
    if port is None:
        fwd_port = _first(headers.get("x-forwarded-port"))
        port = int(fwd_port) if fwd_port.isdigit() else _DEFAULT_PORTS[scheme]
    return scheme, hostname, port


def _printable(value: str) -> str:
    """A header value safe to put in a log line (no CR/LF or other controls)."""
    return "".join(ch if ch.isprintable() else "?" for ch in value)[:200]


class OriginCheckMiddleware:
    """Pure ASGI, so the 403 is produced before any route or body parsing runs."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope["method"] not in CHECKED_METHODS
            or scope["path"].startswith(EXEMPT_PREFIXES)
        ):
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        # An empty value is treated as absent: browsers omit the header, never blank it.
        claimed = headers.get("origin", "").strip()
        source = "Origin"
        if not claimed:
            claimed = headers.get("referer", "").strip()
            source = "Referer"
        if not claimed:
            await self.app(scope, receive, send)
            return

        expected = own_origin(scope.get("scheme", "http"), headers)
        if expected is not None and parse_origin(claimed) == expected:
            await self.app(scope, receive, send)
            return

        log.warning(
            "Rejected cross-origin %s %s: %s %s is not this request's origin %s",
            scope["method"],
            _printable(scope["path"]),
            source,
            _printable(claimed),
            "(unparseable)" if expected is None else "{}://{}:{}".format(*expected),
        )
        body = json.dumps({"detail": "Cross-origin request rejected"}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
