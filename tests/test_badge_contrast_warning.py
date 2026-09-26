"""The Settings page's contrast warning (roadmap B2).

DECIDED 2026-09-23: warn, do not prevent. A configured colour that fails
contrast renders as asked and is reported -- beside the picker, at the moment
of choosing. What these tests pin is that the reported number is the right
one: the RENDERED ratio (B1's lesson), against the CONFIGURED text colour,
from the same code the probe prints, and that nothing refuses the colour.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
import yaml

from app import overlay
from app.config import BADGE_PALETTE_VERSION, ImageConfig, load_config
from app.contrast import AA, AAA, badge_contrast, contrast_ratio
from app.overlay import _parse_color
from scripts.measure_badge_contrast import report

TEMPLATE = Path(__file__).resolve().parent.parent / "app" / "web" / "templates" / "index.html"

# B2's original live-config sub colour: 4.76:1 as an opaque hex (AA), but at
# the 0.65 opacity it was configured with, 2.58:1 on a white poster.
OLD_SUB = "#a86200"


def _cfg(**kw) -> ImageConfig:
    return ImageConfig(badge_palette_version=BADGE_PALETTE_VERSION, **kw)


@pytest.fixture
def routes(monkeypatch):
    from app.web import routes as r

    monkeypatch.setattr(r, "_require_user", lambda request: "admin")
    return r


# ── The number is the rendered one ───────────────────────────────────────────
def test_a_translucent_colour_is_judged_on_what_renders_not_on_its_hex():
    """The check that would pass if the warning read the opaque hex.

    `#a86200` is 4.76:1 as a hex -- AA. At 0.65 the poster shows through, and
    on a white poster it renders 2.58:1. An opaque-hex check grades it AA and
    this test fails; only a rendered check grades it a fail.
    """
    sub = badge_contrast(_cfg(sub_badge_color=OLD_SUB, badge_opacity=0.65))["badges"]["sub"]
    assert AA <= sub["opaque"] < AAA, "premise: the hex alone clears AA"
    assert sub["worst"] < AA
    assert sub["grade"] == "fail"
    assert sub["worst_text"] == "2.58"
    assert sub["worst_backdrop"] == "white"


def test_an_opaque_colour_renders_at_its_hex_ratio_on_every_poster():
    """At 1.0 the fill hides the poster, so rendered == hex and no backdrop is worst."""
    sub = badge_contrast(_cfg(sub_badge_color=OLD_SUB, badge_opacity=1.0))["badges"]["sub"]
    for bd, ratio in sub["rendered"].items():
        assert ratio == pytest.approx(sub["opaque"], abs=0.05), bd
    assert sub["worst_backdrop"] is None
    assert sub["grade"] == "AA"


def test_the_shipped_defaults_are_all_aaa():
    m = badge_contrast(ImageConfig())
    assert {b["grade"] for b in m["badges"].values()} == {"AAA"}
    assert m["worst"]["badge"] == "rating"
    assert m["worst"]["text"] == "7.48"


def test_the_worst_poster_is_computed_not_assumed_white():
    """Dark text on a light fill is at its worst over BLACK, not white."""
    fill = badge_contrast(_cfg(badge_text_color="#000000", video_badge_color="#e2ddcf", badge_opacity=0.65))
    assert fill["badges"]["video"]["worst_backdrop"] == "black"


def test_the_ratio_is_against_the_configured_text_colour():
    """A check hard-coded to white text is wrong once the setting changes."""
    fill = "#203a30"
    for text in ("#ffffff", "#b7b2a6", "#708675"):
        video = badge_contrast(_cfg(badge_text_color=text, video_badge_color=fill))["badges"]["video"]
        assert video["worst"] == pytest.approx(contrast_ratio(_parse_color(text), _parse_color(fill)), abs=0.05)
    sage = badge_contrast(_cfg(badge_text_color="#708675", video_badge_color=fill))["badges"]["video"]
    assert sage["grade"] == "fail"


def test_the_overall_figure_ignores_hidden_badges():
    shown = _cfg(sub_badge_color=OLD_SUB, badge_opacity=0.65)
    assert badge_contrast(shown)["worst"]["badge"] == "sub"
    hidden = badge_contrast(shown.model_copy(update={"show_sub_badges": False}))
    assert hidden["worst"]["badge"] != "sub"
    assert hidden["badges"]["sub"]["grade"] == "fail", "a hidden badge is still measured, just not summarised"


def test_no_badge_shown_means_no_overall_figure():
    off = {"show_video_badges": False, "show_audio_badges": False, "show_sub_badges": False, "show_rating_badge": False}
    assert badge_contrast(_cfg(**off))["worst"] is None


def test_measuring_leaves_the_pill_cache_alone():
    """A colour the operator is only trying out must not land in the render cache."""
    overlay.clear_pill_cache()
    overlay._pill_tile("1080p", "#203a30", "#ffffff", 255, 36, 4, 2)
    before = dict(overlay._PILL_CACHE)
    badge_contrast(_cfg(sub_badge_color=OLD_SUB, badge_opacity=0.65))
    assert overlay._PILL_CACHE == before
    overlay.clear_pill_cache()


# ── The UI's number is the probe's number ────────────────────────────────────
_ROW = re.compile(r"^\| (\w+)\s+\|.*\|\s+([\d.]+): \|$")
_WORST = re.compile(r"^worst rendered ratio: ([\d.]+):1")


@pytest.mark.parametrize(
    "opacity,text,sub",
    [(0.65, "#ffffff", OLD_SUB), (1.0, "#ffffff", OLD_SUB), (0.8, "#e2ddcf", "#50532f")],
)
def test_the_settings_page_shows_what_the_probe_prints(routes, capsys, opacity, text, sub):
    """Same config through the endpoint and through the probe: same digits."""
    ui = asyncio.run(routes.badge_contrast_check(None, opacity=opacity, text_color=text, sub_color=sub))

    report(_cfg(badge_opacity=opacity, badge_text_color=text, sub_badge_color=sub))
    lines = capsys.readouterr().out.splitlines()
    probe = {m[1]: m[2] for m in map(_ROW.match, lines) if m}
    assert set(probe) == {"video", "audio", "sub", "rating"}
    assert probe == {name: b["worst_text"] for name, b in ui["badges"].items()}
    worst_line = next(m[1] for m in map(_WORST.match, lines) if m)
    assert worst_line == ui["worst"]["text"]


def test_the_endpoint_does_not_migrate_the_colour_being_checked(routes):
    """The palette-1 navy chosen on purpose must be measured as navy, not indigo."""
    m = asyncio.run(routes.badge_contrast_check(None, audio_color="#1e3a8a"))
    assert m["badges"]["audio"]["color"] == "#1e3a8a"


def test_the_endpoint_and_the_preview_describe_the_same_badge(routes, monkeypatch):
    """One query->ImageConfig mapping for both, so the warning judges the badge
    the preview shows -- including the clamped opacity."""
    captured = {}
    monkeypatch.setattr(
        routes,
        "generate_preview_bytes",
        lambda g, r, cfg_img, base_image_bytes=None: captured.setdefault("cfg", cfg_img) and b"",
    )
    asyncio.run(routes.preview_image(None, opacity=0.05, sub_color=OLD_SUB))
    m = asyncio.run(routes.badge_contrast_check(None, opacity=0.05, sub_color=OLD_SUB))
    assert m["opacity"] == captured["cfg"].badge_opacity == 0.1
    assert m["badges"]["sub"]["color"] == captured["cfg"].sub_badge_color


# ── Warn, do not prevent ─────────────────────────────────────────────────────
def test_a_failing_colour_still_saves(routes, monkeypatch, tmp_path):
    path = tmp_path / "config.yml"
    path.write_text(yaml.dump({"auth": {"username": "admin", "password_hash": "x", "secret_key": "s"}}))
    load_config(path)
    monkeypatch.setattr(routes, "reschedule", lambda *a, **k: None)

    body = {"image": {"sub_badge_color": OLD_SUB, "badge_opacity": 0.65}, "auth": {"username": "admin"}}
    assert badge_contrast(_cfg(**body["image"]))["badges"]["sub"]["grade"] == "fail"

    class _Req:
        async def json(self):
            return body

    assert asyncio.run(routes.save_settings(_Req())) == {"status": "saved"}
    image = yaml.safe_load(path.read_text())["image"]
    assert image["sub_badge_color"] == OLD_SUB
    assert image["badge_opacity"] == 0.65


def test_the_page_has_no_formula_of_its_own_and_never_blocks_saving():
    """The browser asks the server; it does not carry a copy of the formula
    (a second implementation is how the hex figure and the render drifted),
    and nothing on the page disables Save on the strength of a ratio."""
    html = TEMPLATE.read_text()
    assert "/api/badge-contrast" in html
    assert "0.2126" not in html and "0.04045" not in html
    assert not re.search(r"btn-save-preview[\s\S]{0,200}\.disabled\s*=", html)
    assert not re.search(r"saveBtn\.disabled", html)
    for anchor in ("cc-video", "cc-audio", "cc-sub", "cc-rating", "cc-opacity"):
        assert f'id="{anchor}"' in html
