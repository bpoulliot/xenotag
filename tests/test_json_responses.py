"""The app's JSON responses (roadmap I10).

I10 dropped `default_response_class=ORJSONResponse`, which the pinned FastAPI
deprecates, so every route now renders through Starlette's `JSONResponse`.

Two things are pinned here, and both go through a real request, because
neither shows up any other way:

* FastAPI emits `FastAPIDeprecationWarning` when a response is RENDERED, not at
  import -- `import app.main` records nothing -- and the class subclasses
  `UserWarning`, not `DeprecationWarning`, so `-W error::DeprecationWarning`
  passes with the bug still present.
* `JSONResponse` renders with `allow_nan=False`: a NaN or an infinity that
  orjson quietly wrote as `null` now RAISES, turning the route into a 500. The
  one route computing ratios from caller input is the contrast check (B2).
"""

from __future__ import annotations

import json
import math
import warnings

import pytest
from fastapi.exceptions import FastAPIDeprecationWarning
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    from app.main import app
    from app.web import routes

    monkeypatch.setattr(routes, "_require_user", lambda request: "admin")
    # No `with`: the lifespan (config load, DB, scheduler) is not needed to
    # render a response, and must not touch anything outside the test.
    return TestClient(app)


def test_a_request_records_no_fastapi_deprecation_warning(client):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        resp = client.get("/health")
    assert resp.status_code == 200
    deprecations = [w for w in caught if issubclass(w.category, FastAPIDeprecationWarning)]
    assert not deprecations, [f"{w.filename}:{w.lineno}: {w.message}" for w in deprecations]


def _all_numbers(node):
    if isinstance(node, dict):
        for v in node.values():
            yield from _all_numbers(v)
    elif isinstance(node, list):
        for v in node:
            yield from _all_numbers(v)
    elif isinstance(node, (int, float)) and not isinstance(node, bool):
        yield node


def _reject_constant(name):
    raise AssertionError(f"non-finite JSON constant {name!r} in the response")


@pytest.mark.parametrize(
    "params",
    [
        {"opacity": "0"},
        {"opacity": "-1"},
        {"opacity": "1.5"},
        {"opacity": "1e308"},
        {"opacity": "nan"},
        {"opacity": "inf"},
        {"opacity": "-inf"},
        # identical fill and text: the ratio's floor, 1:1
        {"text_color": "#123456", "video_color": "#123456", "audio_color": "#123456"},
        {"text_color": "#ffffff", "sub_color": "#ffffff", "rating_color": "#ffffff", "opacity": "0"},
        {"text_color": "#000000", "video_color": "#000000"},
        # malformed colours parse to black (roadmap B6)
        {"text_color": "#fff", "video_color": "red"},
        {"text_color": "", "audio_color": "zzzzzz"},
        {"text_color": "#gggggg", "sub_color": "#12345"},
        {"show_video": "false", "show_audio": "false", "show_subs": "false", "show_rating": "false"},
    ],
)
def test_badge_contrast_renders_only_finite_numbers(client, params):
    resp = client.get("/api/badge-contrast", params=params)
    assert resp.status_code == 200, resp.text
    body = json.loads(resp.text, parse_constant=_reject_constant)
    numbers = list(_all_numbers(body))
    assert numbers, "premise: the report carries numbers"
    assert all(math.isfinite(n) for n in numbers)
    assert 0.1 <= body["opacity"] <= 1.0


def test_the_finite_check_can_fail():
    """Self-test: the checks above reject what `JSONResponse` would refuse."""
    with pytest.raises(AssertionError):
        json.loads('{"r": NaN}', parse_constant=_reject_constant)
    assert not all(math.isfinite(n) for n in _all_numbers({"a": [1.0, float("inf")]}))
