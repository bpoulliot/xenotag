"""The sanitisers behind three CodeQL alerts that roadmap I13 triaged as false positives.

Each test drives the real route through ``TestClient`` with hostile input and
shows the property CodeQL cannot see. Each also carries a control that fails in
the other direction -- proof the input really is hostile, or that the probe
would see a leak if there were one -- so a green run is not a probe that can
only agree with itself.

* ``py/path-injection`` #3, #4, #5 (``preview_image``'s ``sample``): the query
  has no barrier for ``pathlib.PurePath.name``, so it cannot see that the
  joined path is always ``_PREVIEW_CACHE / <one component>``.
* ``py/cookie-injection`` #1 (``login``'s ``set_cookie``): the username reaches
  the cookie only inside a base64url-encoded, HMAC-signed token, and only when
  it equals the configured username. CodeQL propagates taint through encoding.
* ``py/stack-trace-exposure`` #9 (``/api/arr-sync/report``): the route returns
  ``str(exc)`` of a failed dry run -- the message, never a traceback -- to the
  signed-in admin. CodeQL counts the exception object itself as stack-trace
  information.
* ``py/stack-trace-exposure`` #10 (``/api/deleted-items/report``, roadmap B29):
  the stored report's ``reason`` and ``halted`` carry text from the three
  exceptions CodeQL names (``deleted_items.py`` 364, 366, 539). Measured, not
  dismissed: no traceback and no API key reaches the response, but an HTTP
  error's message carries the request URL -- with any ``user:password@`` in it.
"""

from __future__ import annotations

import http.server
import pathlib
import re
import threading
import traceback

import bcrypt
import httpx
import pytest
from fastapi.testclient import TestClient

from app import pipeline
from app.clients.jellyfin import JellyfinClient
from app.config import AppConfig, AuthConfig, JellyfinConfig

# ---------------------------------------------------------------------------
# py/path-injection #3-5 -- preview_image(sample=...)
# ---------------------------------------------------------------------------


@pytest.fixture
def preview(monkeypatch, tmp_path):
    """The preview route over a scratch cache, recording what it read and stat'd."""
    from app.main import app
    from app.web import routes

    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "legit.jpg").write_bytes(b"IN-CACHE")
    (tmp_path / "secret.jpg").write_bytes(b"OUTSIDE")
    monkeypatch.setattr(routes, "_PREVIEW_CACHE", cache)
    monkeypatch.setattr(routes, "_require_user", lambda request: "admin")

    seen: dict = {"base": [], "stat": []}

    def fake_render(groups, rating_group, cfg_img, base_image_bytes=None):
        seen["base"].append(base_image_bytes)
        return b"jpeg"

    monkeypatch.setattr(routes, "generate_preview_bytes", fake_render)

    for name in ("exists", "is_file"):
        real = getattr(pathlib.Path, name)

        def spy(self, *a, _real=real, **kw):
            if str(self).startswith(str(tmp_path)):
                seen["stat"].append(self)
            return _real(self, *a, **kw)

        monkeypatch.setattr(pathlib.Path, name, spy)

    return TestClient(app), cache, tmp_path, seen


def _escapes(tmp_path):
    """Inputs whose NAIVE join ``cache / sample`` resolves to the file outside the cache."""
    outside = tmp_path / "secret.jpg"
    return ["../secret.jpg", str(outside), f"../../{tmp_path.name}/secret.jpg", "legit.jpg/../../secret.jpg"]


def test_path_injection_inputs_really_escape_a_naive_join(tmp_path):
    """Control: without `.name`, every one of these reads the file outside the cache."""
    cache = tmp_path / "cache"
    cache.mkdir()
    (tmp_path / "secret.jpg").write_bytes(b"OUTSIDE")
    for sample in _escapes(tmp_path):
        assert (cache / sample).resolve() == (tmp_path / "secret.jpg").resolve(), sample


def test_preview_sample_cannot_read_outside_the_cache(preview):
    client, cache, tmp_path, seen = preview
    for sample in _escapes(tmp_path):
        seen["base"].clear()
        resp = client.get("/preview/image", params={"sample": sample})
        assert resp.status_code == 200, sample
        assert seen["base"] == [None], sample  # nothing read, and certainly not OUTSIDE


@pytest.mark.parametrize(
    "raw_query",
    [
        "sample=..%2Fsecret.jpg",  # URL-encoded slash, decoded once by the server
        "sample=%2E%2E%2Fsecret.jpg",  # URL-encoded dots too
        "sample=..%252Fsecret.jpg",  # double-encoded: arrives as a literal '%2F'
        "sample=..%5Csecret.jpg",  # '..\\secret.jpg' -- one filename on POSIX
        "sample=..%5C..%5Csecret.jpg",
        "sample=..%2Fsecret.jpg%00.png",  # NUL: pathlib reports False, never raises
        "sample=legit.jpg%00",
        "sample=..",  # the one name that is itself a step up: the cache's parent, a directory
        "sample=.",
        "sample=%2F",
    ],
)
def test_preview_sample_hostile_encodings_read_nothing(preview, raw_query):
    client, cache, tmp_path, seen = preview
    resp = client.get(f"/preview/image?{raw_query}")
    assert resp.status_code == 200
    assert seen["base"] == [None]
    # Nothing outside the cache is even stat'd: every probed path is the cache
    # itself ('.' and '/' have an empty name) or cache/<one name>.
    assert all(p == cache or p.parent == cache for p in seen["stat"]), seen["stat"]


def test_preview_sample_still_reads_a_file_in_the_cache(preview):
    """Control the other way: the probe does see a read when one happens."""
    client, cache, tmp_path, seen = preview
    client.get("/preview/image", params={"sample": "legit.jpg"})
    # A path prefix is dropped, not honoured: this is still the cache's own file.
    client.get("/preview/image", params={"sample": "../elsewhere/legit.jpg"})
    assert seen["base"] == [b"IN-CACHE", b"IN-CACHE"]
    assert seen["stat"] and all(p.parent == cache for p in seen["stat"])


# ---------------------------------------------------------------------------
# py/cookie-injection #1 -- login()'s set_cookie
# ---------------------------------------------------------------------------

HOSTILE_USERNAMES = [
    "admin; Domain=evil.example; Path=/",
    "admin\r\nSet-Cookie: pwned=1",
    'admin"; HttpOnly=false; x="',
    "admin, other=1",
    "ädmin\x00=",
]
# The stdlib cookie writer wraps a value holding "=" (base64 padding) in double
# quotes; Starlette unquotes it on the way back in.
_B64URL = re.compile(r'^(?P<q>"?)[A-Za-z0-9_-]+=*(?P=q)$')


@pytest.fixture
def login(monkeypatch):
    from app.main import app
    from app.web import routes

    monkeypatch.setattr(routes._limiter, "enabled", False)
    # A throwaway credential, hashed for this test only.
    password = "correct horse"  # noqa: S105
    state = {"cfg": None}
    monkeypatch.setattr(routes, "get_config", lambda: state["cfg"])

    def configure(username: str) -> None:
        pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=4)).decode()
        state["cfg"] = AppConfig(auth=AuthConfig(username=username, password_hash=pw_hash, secret_key="k" * 64))

    return TestClient(app), configure, password


@pytest.mark.parametrize("username", HOSTILE_USERNAMES)
def test_login_cookie_is_one_base64url_token_whatever_the_username(login, username):
    from app import auth
    from app.web import routes

    client, configure, password = login
    # Control: written raw, each of these would break a Set-Cookie header.
    assert set(username) & set(';,"\r\n\x00')
    configure(username)
    resp = client.post("/login", data={"username": username, "password": password}, follow_redirects=False)
    assert resp.status_code == 302
    cookies = resp.headers.get_list("set-cookie")
    assert len(cookies) == 1, cookies
    name, _, rest = cookies[0].partition("=")
    value, *attrs = [part.strip() for part in rest.split(";")]
    assert name == routes.SESSION_COOKIE
    assert _B64URL.match(value), value
    value = value.strip('"')
    assert {a.split("=")[0].lower() for a in attrs} <= {"httponly", "max-age", "path", "samesite", "secure"}
    assert "pwned" not in cookies[0] and "evil.example" not in cookies[0]
    # The username is carried, encoded, and comes back out intact.
    cfg = routes.get_config()
    assert auth.get_session_user(value, cfg.auth.secret_key, cfg.auth.password_hash) == username


def test_login_sets_no_cookie_for_a_username_that_is_not_configured(login):
    client, configure, password = login
    configure("admin")
    resp = client.post("/login", data={"username": HOSTILE_USERNAMES[0], "password": password}, follow_redirects=False)
    assert resp.status_code == 401
    assert resp.headers.get_list("set-cookie") == []


# ---------------------------------------------------------------------------
# py/stack-trace-exposure #9 -- /api/arr-sync/report's "error"
# ---------------------------------------------------------------------------

_KEY = "SENTINEL-JELLYFIN-KEY-5c1f"


class _Refuse(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(401)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def refusing_jellyfin():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Refuse)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def report(monkeypatch):
    from app.main import app
    from app.web import routes

    monkeypatch.setattr(routes, "load_report", lambda: None)
    monkeypatch.setitem(pipeline.arr_dry_run_state, "error", None)
    monkeypatch.setitem(pipeline.arr_dry_run_state, "running", False)
    return TestClient(app)


def _failed_dry_run(url: str) -> tuple[AppConfig, BaseException]:
    """Run the REAL dry run against a Jellyfin that refuses the key; return what it raised."""
    cfg = AppConfig(jellyfin=JellyfinConfig(url=url, api_key=_KEY))
    try:
        pipeline.run_arr_dry_run(cfg, store=False)
    except Exception as exc:  # the exception is the specimen
        return cfg, exc
    raise AssertionError("the dry run did not fail")


def test_arr_report_carries_the_message_never_the_traceback(monkeypatch, report, refusing_jellyfin):
    from app.web import routes

    cfg, exc = _failed_dry_run(refusing_jellyfin)
    formatted = "".join(traceback.format_exception(exc))
    # Control: the traceback exists and would be recognisable if it leaked.
    assert "Traceback" in formatted and 'File "' in formatted and "pipeline.py" in formatted

    monkeypatch.setattr(routes, "get_config", lambda: cfg)
    pipeline.run_arr_dry_run_background(cfg)  # the real thread target, run inline
    monkeypatch.setattr(routes, "_require_user", lambda request: "admin")
    resp = report.get("/api/arr-sync/report")
    assert resp.status_code == 200
    assert resp.json()["error"] == str(exc)
    for leak in ("Traceback", 'File "', ".py", _KEY):
        assert leak not in resp.text, leak


def test_arr_report_is_not_served_without_a_session(report):
    pipeline.arr_dry_run_state["error"] = "marker-9f2d"
    resp = report.get("/api/arr-sync/report")
    assert resp.status_code == 401
    assert "marker-9f2d" not in resp.text


# ---------------------------------------------------------------------------
# py/stack-trace-exposure #10 -- /api/deleted-items/report's "report" (B29)
# ---------------------------------------------------------------------------
#
# CodeQL's three sources, each forced through the REAL pass and read back
# through the REAL route (the pass stores its report in ``_last_report``; the
# route returns ``load_report()``):
#
#   364  ``except PassAborted``  -> report["reason"]   (an *arr preload fails)
#   366  ``except Exception``    -> report["reason"]   (the Jellyfin listing fails)
#   539  a strip's ``except``    -> report["halted"]   (an *arr write fails)

_JF_KEY = "SENTINEL-JF-KEY-0a7e"
_ARR_KEY = "SENTINEL-ARR-KEY-3b9d"
# A sentinel, never a credential: the probe looks for it in the response.
_URL_PASSWORD = "SENTINEL-URL-PW-61c2"  # noqa: S105
_TRACE_MARKERS = ("Traceback", 'File "', ".py")


def _strings(value) -> list[str]:
    """Every string in a decoded JSON body. Checking the raw body would miss
    ``File "``, which JSON escapes to ``File \\"``."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in (*_strings(k), *_strings(v))]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def _leaks(body: dict, secrets=(_JF_KEY, _ARR_KEY, _URL_PASSWORD)) -> list[str]:
    text = "\n".join(_strings(body))
    return [m for m in (*_TRACE_MARKERS, *secrets) if m in text]


@pytest.fixture
def deleted_route(monkeypatch, tmp_path):
    """The real deleted-items pass and route, over fakes, with sentinel keys in the clients."""
    from app import deleted_items
    from app.clients.radarr import RadarrClient
    from app.main import app
    from app.web import routes
    from tests.test_deleted_items import FakeJellyfin, cfg, scenario

    monkeypatch.setattr(deleted_items, "_last_report", None)
    monkeypatch.setattr(deleted_items, "state", {"running": False, "error": None})
    monkeypatch.setattr(deleted_items, "report_path", lambda: tmp_path / "deleted-items-report.json")
    monkeypatch.setattr(routes, "_require_user", lambda request: "admin")

    jf, db, _sonarr, radarr = scenario(tmp_path)
    urls = {"jf": "http://jf", "arr": "http://r0"}

    def build(cfg_, *, arr_writable, guards):
        client = JellyfinClient(urls["jf"], _JF_KEY, transport=httpx.MockTransport(jf.handle))
        arr = RadarrClient(urls["arr"], _ARR_KEY, "r0", transport=httpx.MockTransport(radarr.handle))
        return client, [], [arr]

    monkeypatch.setattr(deleted_items, "_build_clients", build)

    def run(mode: str = "report") -> dict:
        c = cfg(mode, "live")
        monkeypatch.setattr(routes, "get_config", lambda: c)
        deleted_items.run_deleted_items(c, db_path=db, store=True)  # the real pass, stored as a scan stores it
        resp = TestClient(app).get("/api/deleted-items/report")
        assert resp.status_code == 200
        return resp.json()

    return run, jf, radarr, urls, FakeJellyfin


def _force(source: str, jf, radarr) -> str:
    """Make one CodeQL source fire; return the pass mode it needs."""
    if source == "364":
        radarr.fail_status = {"GET": 503}  # preload fails -> PassAborted("could not read ...")
        return "report"
    if source == "366":
        jf.fail_on_page = 1  # listing answers 500 -> httpx.HTTPStatusError
        return "report"
    radarr.fail_status = {"PUT": 500}  # 539: the strip's write fails -> _halt(...)
    return "remove"


def _fired(source: str, report: dict) -> str:
    """The text the source wrote; asserts the source really fired."""
    if source == "539":
        assert report["halted"], report
        return report["halted"]
    assert report["status"] == "aborted" and report["reason"], report
    return report["reason"]


SOURCES = ["364", "366", "539"]


@pytest.mark.parametrize("source", SOURCES)
def test_deleted_items_report_carries_no_traceback_and_no_key(deleted_route, source):
    run, jf, radarr, urls, _ = deleted_route
    body = run(_force(source, jf, radarr))
    text = _fired(source, body["report"])
    assert _leaks(body) == [], (source, text)


@pytest.mark.parametrize("source", SOURCES)
def test_deleted_items_probe_sees_a_leak_when_there_is_one(deleted_route, monkeypatch, source):
    """Control the other way: a pass that DID put the traceback and the keys in its
    report is caught by the same probe, through the same route."""
    from app import deleted_items

    def leaky(original):
        def wrapper(self, reason):
            original(self, f"{reason}\n{traceback.format_exc()} {_JF_KEY} {_ARR_KEY} {_URL_PASSWORD}")

        return wrapper

    monkeypatch.setattr(deleted_items.DeletedItemsPass, "_abort", leaky(deleted_items.DeletedItemsPass._abort))
    monkeypatch.setattr(deleted_items.DeletedItemsPass, "_halt", leaky(deleted_items.DeletedItemsPass._halt))
    run, jf, radarr, urls, _ = deleted_route
    body = run(_force(source, jf, radarr))
    _fired(source, body["report"])
    assert _leaks(body) == [*_TRACE_MARKERS, _JF_KEY, _ARR_KEY, _URL_PASSWORD]


def test_deleted_items_route_survives_an_unreadable_report_file(deleted_route, monkeypatch, tmp_path):
    """No report in memory and a report path that cannot be read (a directory):
    ``load_report()`` swallows the OSError and the route answers ``report: null``."""
    from app import deleted_items
    from app.main import app
    from app.web import routes
    from tests.test_deleted_items import cfg

    blocked = tmp_path / "unreadable"
    blocked.mkdir()
    monkeypatch.setattr(deleted_items, "report_path", lambda: blocked)
    monkeypatch.setattr(routes, "get_config", lambda: cfg())
    with pytest.raises(IsADirectoryError):
        blocked.read_text()  # control: the read really fails
    body = TestClient(app).get("/api/deleted-items/report").json()
    assert body["report"] is None and body["error"] is None and _leaks(body) == []


# What B29's fix changes. These pin the residual the probe found, so the fix
# must flip them on purpose: an HTTP error's message is the request URL.


@pytest.mark.parametrize("source", ["366", "539"])
def test_cq10_residual_an_http_error_puts_the_request_url_in_the_report(deleted_route, source):
    run, jf, radarr, urls, _ = deleted_route
    urls["jf"], urls["arr"] = "http://jf-internal.example:8096", "http://r0-internal.example:7878"
    body = run(_force(source, jf, radarr))
    text = _fired(source, body["report"])
    host = "jf-internal.example:8096" if source == "366" else "r0-internal.example:7878"
    assert "HTTPStatusError" in text and f"for url 'http://{host}/" in text, text


@pytest.mark.parametrize("source", ["366", "539"])
def test_cq10_residual_a_password_in_the_configured_url_reaches_the_response(deleted_route, source):
    """httpx quotes the URL with its userinfo, so a ``http://user:password@host``
    Jellyfin or *arr URL puts the password in the report -- and in the ntfy for a halt."""
    run, jf, radarr, urls, _ = deleted_route
    urls["jf"] = f"http://xt:{_URL_PASSWORD}@jf"
    urls["arr"] = f"http://xt:{_URL_PASSWORD}@r0"
    body = run(_force(source, jf, radarr))
    _fired(source, body["report"])
    assert _leaks(body) == [_URL_PASSWORD]


def test_cq10_source_364_messages_are_built_by_the_pass_not_copied_from_an_error(deleted_route):
    """364's text is PassAborted's own message: instance labels, counts, page numbers.
    Even with a password in the *arr URL it is not there (the label is the instance name)."""
    run, jf, radarr, urls, _ = deleted_route
    urls["arr"] = f"http://xt:{_URL_PASSWORD}@r0"
    body = run(_force("364", jf, radarr))
    assert body["report"]["reason"] == "could not read radarr/r0: rows owned there cannot be judged"
    assert body["report"]["instances"]["radarr/r0"]["error"] == "HTTPStatusError"
    assert _leaks(body) == []
