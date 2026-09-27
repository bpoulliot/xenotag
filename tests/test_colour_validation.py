"""Roadmap B6: a colour that is not six-digit hex rendered BLACK, silently.

`ImageConfig`'s five colour fields were plain strings and `_parse_color()`
returned `(0, 0, 0)` for anything but `#rrggbb`, so `badge_text_color: "#fff"`
painted black labels. OPERATOR DECISION 2026-09-26, option (a):

  * every colour Pillow's `ImageColor.getrgb()` reads as RGB is normalised to
    `#rrggbb` at validation;
  * anything else LOADS as that field's default with a WARNING naming the field
    and the value (the B10 precedent: a cosmetic typo must not stop the app);
  * a Settings or raw-YAML save of it is REFUSED, and config.yml is unchanged.

A value Pillow reads with an alpha channel is unusable here -- opacity has its
own field -- so it is treated as unparseable rather than having its alpha
dropped. Six bare hex digits (`203a30`), which Pillow rejects, are kept: the old
renderer drew them correctly, and refusing them would change a working poster.
"""

from __future__ import annotations

import logging

import pytest
import yaml

from app.config import ImageConfig, load_config, normalize_color, save_config, save_config_from_dict
from app.contrast import render_over, sample_interior
from app.overlay import _parse_color

FIELDS = ("badge_text_color", "video_badge_color", "audio_badge_color", "sub_badge_color", "rating_badge_color")
DEFAULTS = {f: ImageConfig.model_fields[f].default for f in FIELDS}

NORMALISED = [
    ("#fff", "#ffffff"),
    ("#FFF", "#ffffff"),
    ("#FFFFFF", "#ffffff"),
    ("#fc0", "#ffcc00"),
    ("red", "#ff0000"),
    ("RED", "#ff0000"),
    ("rgb(255,0,0)", "#ff0000"),
    ("rgb(100%,0%,0%)", "#ff0000"),
    ("hsl(120,100%,25%)", "#008000"),
    ("#203a30", "#203a30"),
    # Pillow rejects this spelling; kept because it rendered correctly pre-B6.
    ("203a30", "#203a30"),
]

UNPARSEABLE = [
    "#gggggg",
    "#12345",
    "#1234567",
    "",
    "zzz",
    # Pillow rejects a bare 3-digit form, and it rendered black before -- pinned.
    "fff",
    " #fff ",
    # Parsed by Pillow WITH alpha: refused rather than silently dropping it.
    "#ffff",
    "#ffffff80",
    "rgba(255,0,0,128)",
    # YAML hands over a non-string for these spellings (`000000` is an int).
    0,
    None,
]


@pytest.mark.parametrize("value,expected", NORMALISED)
def test_every_spelling_pillow_reads_is_normalised(value, expected):
    assert normalize_color(value) == expected
    for field in FIELDS:
        assert getattr(ImageConfig(**{field: value}), field) == expected


@pytest.mark.parametrize("value", UNPARSEABLE)
def test_an_unparseable_colour_loads_as_the_default_with_a_warning(value, caplog):
    assert normalize_color(value) is None
    for field in FIELDS:
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="app.config"):
            cfg = ImageConfig(**{field: value})
        assert getattr(cfg, field) == DEFAULTS[field]
        assert f"image.{field}" in caplog.text
        assert repr(value) in caplog.text


def test_a_valid_colour_logs_nothing(caplog):
    with caplog.at_level(logging.WARNING, logger="app.config"):
        ImageConfig(badge_text_color="#fff", video_badge_color="red")
    assert caplog.text == ""


# ── Loading never writes; saving refuses ─────────────────────────────────────
@pytest.fixture
def cfg_file(tmp_path, monkeypatch):
    for var in ("JELLYFIN_API_KEY", "JELLYFIN_API_KEY_FILE", "XENOTAG_SECRET_KEY", "XENOTAG_SECRET_KEY_FILE"):
        monkeypatch.delenv(var, raising=False)
    path = tmp_path / "config.yml"
    path.write_text(
        yaml.dump({"image": {"badge_text_color": "#fff", "video_badge_color": "#gggggg", "audio_badge_color": "red"}})
    )
    return path


def test_loading_normalises_in_memory_and_does_not_rewrite_the_file(cfg_file, caplog):
    before = cfg_file.read_bytes()
    with caplog.at_level(logging.WARNING, logger="app.config"):
        cfg = load_config(cfg_file)
    assert cfg.image.badge_text_color == "#ffffff"
    assert cfg.image.audio_badge_color == "#ff0000"
    assert cfg.image.video_badge_color == DEFAULTS["video_badge_color"]
    assert "image.video_badge_color '#gggggg'" in caplog.text
    assert cfg_file.read_bytes() == before


def test_a_raw_yaml_save_of_an_unparseable_colour_is_refused(cfg_file):
    load_config(cfg_file)
    before = cfg_file.read_bytes()
    with pytest.raises(ValueError, match=r"image\.rating_badge_color '#gggggg' is not a colour"):
        save_config("image:\n  rating_badge_color: '#gggggg'\n")
    assert cfg_file.read_bytes() == before


def test_a_settings_save_of_an_unparseable_colour_is_refused(cfg_file):
    load_config(cfg_file)
    before = cfg_file.read_bytes()
    with pytest.raises(ValueError, match=r"image\.badge_text_color '#gggggg' is not a colour"):
        save_config_from_dict({"image": {"badge_text_color": "#gggggg"}})
    assert cfg_file.read_bytes() == before


def test_a_save_with_an_alpha_colour_is_refused(cfg_file):
    load_config(cfg_file)
    with pytest.raises(ValueError, match="no alpha"):
        save_config_from_dict({"image": {"sub_badge_color": "#ffffff80"}})


def test_a_save_writes_the_normalised_value(cfg_file):
    load_config(cfg_file)
    save_config_from_dict({"image": {"badge_text_color": "#FFF", "video_badge_color": "rgb(0,128,0)"}})
    written = yaml.safe_load(cfg_file.read_text())["image"]
    assert written["badge_text_color"] == "#ffffff"
    assert written["video_badge_color"] == "#008000"


@pytest.fixture
def client(cfg_file, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.web import routes

    load_config(cfg_file)
    monkeypatch.setattr(routes, "_require_user", lambda request: "admin")
    monkeypatch.setattr(routes, "reschedule", lambda *a, **k: None)
    return TestClient(app)


def test_put_config_refuses_with_a_message_and_leaves_the_file(client, cfg_file):
    before = cfg_file.read_bytes()
    resp = client.put("/config", json={"yaml": "image:\n  badge_text_color: '#gggggg'\n"})
    assert resp.status_code == 400
    assert "image.badge_text_color '#gggggg' is not a colour" in resp.json()["detail"]
    assert cfg_file.read_bytes() == before


def test_put_settings_refuses_with_a_message_and_leaves_the_file(client, cfg_file):
    before = cfg_file.read_bytes()
    resp = client.put("/api/settings", json={"image": {"video_badge_color": "#gggggg"}})
    assert resp.status_code == 400
    assert "image.video_badge_color '#gggggg' is not a colour" in resp.json()["detail"]
    assert cfg_file.read_bytes() == before


def test_the_contrast_chip_uses_the_same_normaliser(client):
    """`/api/badge-contrast` must measure the colour that renders, not black."""
    short = client.get("/api/badge-contrast", params={"text_color": "#fff", "video_color": "#fc0"}).json()
    canon = client.get("/api/badge-contrast", params={"text_color": "#ffffff", "video_color": "#ffcc00"}).json()
    assert short["text_color"] == "#ffffff"
    assert short["badges"]["video"]["color"] == "#ffcc00"
    assert short["badges"]["video"]["rendered"] == canon["badges"]["video"]["rendered"]


# ── The render: the B6 table's cases, sampled in the flat interior ───────────
TABLE = [
    ("#fff", (255, 255, 255)),
    ("#FFF", (255, 255, 255)),
    ("red", (255, 0, 0)),
    ("#ffffff", (255, 255, 255)),
    # Unparseable: the text-colour default, #ffffff -- NOT black as before.
    ("fff", (255, 255, 255)),
    ("#12345", (255, 255, 255)),
    ("#1234567", (255, 255, 255)),
    ("#gggggg", (255, 255, 255)),
]


def _interior_colours(img, pill_wh):
    from app.contrast import _GLOW_MARGIN

    w, h = pill_wh
    inset = 5
    box = (_GLOW_MARGIN + inset, _GLOW_MARGIN + inset, _GLOW_MARGIN + w - inset, _GLOW_MARGIN + h - inset)
    return {c for _, c in img.crop(box).getcolors(maxcolors=w * h)}


@pytest.mark.parametrize("text_value,text_rgb", TABLE)
def test_the_b6_table_renders_the_colour_it_names(text_value, text_rgb):
    """`#fc0` fill under the configured text: before B6 this was black on black."""
    cfg = ImageConfig(badge_text_color=text_value, video_badge_color="#fc0")
    img, wh = render_over((0, 0, 0), cfg.video_badge_color, cfg.badge_text_color, alpha=255)
    assert sample_interior(img, wh) == (255, 204, 0), "the fill is the flat interior's modal pixel"
    colours = _interior_colours(img, wh)
    assert text_rgb in colours, "the label renders in the configured text colour"
    assert (0, 0, 0) not in colours, "no black anywhere inside the pill"
    ref, _ = render_over((0, 0, 0), "#ffcc00", "#" + "".join(f"{c:02x}" for c in text_rgb), alpha=255)
    assert img.tobytes() == ref.tobytes()


def test_parse_color_refuses_rather_than_returning_black():
    assert _parse_color("#FFcc00") == (255, 204, 0)
    for bad in ("#fff", "ffffff", "#gggggg", "#+1+2+3", "#ff ff0", "red", ""):
        with pytest.raises(ValueError):
            _parse_color(bad)
