"""The background-aware palette's Settings UI (roadmap P6).

Three paths carry the five keys -- ``image.adapt_badge_colors`` and the four
``image.backup_*_badge_color`` -- and each is pinned here: the structured save
writes them and the next load reads them back; the page loads, saves and sends
them (the page is tested by its text, as the B2 and B12(b) tests do); and the
Preview route hands them to the renderer, which draws a different poster with
the checkbox on than off.
"""

from __future__ import annotations

import asyncio
import inspect
import io
import re
from pathlib import Path

import pytest
import yaml
from PIL import Image

from app.config import ImageConfig, config_as_dict_safe, load_config

TEMPLATE = Path(__file__).resolve().parent.parent / "app" / "web" / "templates" / "index.html"

CATS = ("video", "audio", "sub", "rating")
BACKUP_KEYS = tuple(f"backup_{c}_badge_color" for c in CATS)
SHIPPED_BACKUPS = {
    "backup_video_badge_color": "#0c332d",
    "backup_audio_badge_color": "#120c2a",
    "backup_sub_badge_color": "#332d0c",
    "backup_rating_badge_color": "#1f0001",
}
# Distinct from the shipped backups and from each other, so a key that
# round-trips into the wrong slot, or not at all, is told apart.
PICKED = {
    "backup_video_badge_color": "#102030",
    "backup_audio_badge_color": "#203040",
    "backup_sub_badge_color": "#304050",
    "backup_rating_badge_color": "#405060",
}


@pytest.fixture
def routes(monkeypatch):
    from app.web import routes as r

    monkeypatch.setattr(r, "_require_user", lambda request: "admin")
    monkeypatch.setattr(r, "reschedule", lambda *a, **k: None)
    monkeypatch.setattr(r, "reschedule_reconcile", lambda *a, **k: None)
    return r


@pytest.fixture
def cfg_path(tmp_path):
    path = tmp_path / "config.yml"
    path.write_text(yaml.dump({"auth": {"username": "admin", "password_hash": "x", "secret_key": "s"}}))
    load_config(path)
    return path


def _save(routes, image: dict) -> None:
    body = {"image": image, "auth": {"username": "admin"}}

    class _Req:
        async def json(self):
            return body

    assert asyncio.run(routes.save_settings(_Req())) == {"status": "saved"}


# ── Defaults ─────────────────────────────────────────────────────────────────
def test_the_default_config_has_adapt_off_and_the_shipped_backups(cfg_path):
    image = load_config(cfg_path).image
    assert image.adapt_badge_colors is False
    assert {k: getattr(image, k) for k in BACKUP_KEYS} == SHIPPED_BACKUPS
    assert config_as_dict_safe()["image"]["adapt_badge_colors"] is False


# ── The structured save round-trips the five keys ───────────────────────────
@pytest.mark.parametrize("adapt", [True, False])
def test_the_five_keys_survive_save_and_reload(routes, cfg_path, adapt):
    """Saved -> written to config.yml -> loaded again -> the same values, and
    the backups are kept with the checkbox OFF too (they must not be lost
    while the pickers are hidden)."""
    _save(routes, {"adapt_badge_colors": adapt, **PICKED})

    on_disk = yaml.safe_load(cfg_path.read_text())["image"]
    assert on_disk["adapt_badge_colors"] is adapt
    assert {k: on_disk[k] for k in BACKUP_KEYS} == PICKED

    reloaded = load_config(cfg_path).image
    assert reloaded.adapt_badge_colors is adapt
    assert {k: getattr(reloaded, k) for k in BACKUP_KEYS} == PICKED

    # ... and the page's GET reads them back the same.
    page = asyncio.run(routes.get_settings(None))["image"]
    assert page["adapt_badge_colors"] is adapt
    assert {k: page[k] for k in BACKUP_KEYS} == PICKED


# ── The page loads, saves and sends them ─────────────────────────────────────
def _js_function(html: str, name: str) -> str:
    """The body of a top-level ``[async] function name() {...}`` in the page script."""
    m = re.search(rf"\n(?:async )?function {name}\([^)]*\) \{{\n(.*?)\n\}}\n", html, re.S)
    assert m, f"function {name} not found"
    return m.group(1)


def test_the_page_has_the_checkbox_off_with_its_note_and_four_pickers_with_chips():
    html = TEMPLATE.read_text()
    box = re.search(r'<input type="checkbox" id="prev-adapt"[^>]*>', html)
    assert box and "checked" not in box.group(0).replace("onchange", "")
    assert "Adapt badge colours to the poster" in html
    assert "Only matters below 100% opacity" in html
    for cat in CATS:
        assert f'id="prev-backup-{cat}-color"' in html
        assert f'id="prev-backup-{cat}-hex"' in html
        # The same chip the main picker carries, filled by the same renderer.
        assert f'id="cc-backup-{cat}"' in html
    checks = _js_function(html, "runContrastCheck")
    assert "renderChip(document.getElementById('cc-' + cat), m.badges[cat], t)" in checks
    assert "renderChip(document.getElementById('cc-backup-' + cat), m.backup[cat], t)" in checks


def test_both_save_buttons_write_the_five_keys():
    html = TEMPLATE.read_text()
    keys = _js_function(html, "adaptSettings")
    assert "adapt_badge_colors:" in keys and "getElementById('prev-adapt')?.checked" in keys
    for cat in CATS:
        assert f"backup_{cat}_badge_color:" in keys and f"'prev-backup-{cat}-color'" in keys
    assert "...adaptSettings()" in _js_function(html, "collectForm")
    assert "...adaptSettings()" in _js_function(html, "collectPreviewSettings")


def test_the_page_loads_the_five_keys_from_the_saved_settings():
    html = TEMPLATE.read_text()
    assert "setChk('prev-adapt',       s.image?.adapt_badge_colors ?? false)" in html
    for cat in CATS:
        assert re.search(rf"setColor\('prev-backup-{cat}',\s+s\.image\?\.backup_{cat}_badge_color", html)


def test_preview_and_the_chips_send_what_the_routes_read(routes):
    """Every param the page sends is one the routes take, and both the Preview
    and the contrast check send all five."""
    html = TEMPLATE.read_text()
    sent = re.findall(r"&(\w+)=\$\{", _js_function(html, "adaptParams"))
    assert sent == ["adapt", "backup_video_color", "backup_audio_color", "backup_sub_color", "backup_rating_color"]
    for route in (routes.preview_image, routes.badge_contrast_check):
        assert set(sent) <= set(inspect.signature(route).parameters)
    assert "+ adaptParams()" in _js_function(html, "refreshPreviews")
    assert "+ adaptParams()" in _js_function(html, "contrastParams")


# ── The Preview route hands them to the renderer ─────────────────────────────
# A sample with a pill in every category, so every group's backup is checked.
SAMPLE = {"resolution": "1080p", "audio": "EN AAC", "subtitles": "EN SRT", "rating": "PG-13"}


def test_the_preview_params_reach_the_renderer(routes, cfg_path, monkeypatch):
    captured = {}

    def fake(groups, rating_group, cfg_img, base_image_bytes=None):
        captured.update(cfg=cfg_img, groups=groups, rating=rating_group)
        return b""

    monkeypatch.setattr(routes, "generate_preview_bytes", fake)
    asyncio.run(
        routes.preview_image(
            None,
            **SAMPLE,
            adapt="true",
            backup_video_color=PICKED["backup_video_badge_color"],
            backup_audio_color=PICKED["backup_audio_badge_color"],
            backup_sub_color=PICKED["backup_sub_badge_color"],
            backup_rating_color=PICKED["backup_rating_badge_color"],
        )
    )
    cfg = captured["cfg"]
    assert cfg.adapt_badge_colors is True
    assert {k: getattr(cfg, k) for k in BACKUP_KEYS} == PICKED
    # Each badge group carries its category's backup, as a scan builds it.
    by_main = {g.fill_color: g.backup_fill_color for g in [*captured["groups"], captured["rating"]]}
    for cat in CATS:
        assert by_main[getattr(cfg, f"{cat}_badge_color")] == PICKED[f"backup_{cat}_badge_color"]

    # Without the params: off, and the shipped backups.
    asyncio.run(routes.preview_image(None, **SAMPLE))
    assert captured["cfg"].adapt_badge_colors is False
    assert {k: getattr(captured["cfg"], k) for k in BACKUP_KEYS} == SHIPPED_BACKUPS


def test_the_preview_renders_a_different_poster_with_adapt_on(routes, cfg_path, monkeypatch, tmp_path):
    """On a white sample at 65%, adapt on draws the backup palette; off, or on
    with the backups set to the main colours, draws exactly what off draws."""
    white = io.BytesIO()
    Image.new("RGB", (280, 420), (255, 255, 255)).save(white, "JPEG", quality=95)
    (tmp_path / "white.jpg").write_bytes(white.getvalue())
    monkeypatch.setattr(routes, "_PREVIEW_CACHE", tmp_path)

    def render(**kw) -> bytes:
        resp = asyncio.run(routes.preview_image(None, **SAMPLE, opacity=0.65, sample="white.jpg", **kw))
        return resp.body

    off = render()
    on = render(adapt="true")
    assert on != off
    main = ImageConfig()
    same_as_main = render(
        adapt="true",
        backup_video_color=main.video_badge_color,
        backup_audio_color=main.audio_badge_color,
        backup_sub_color=main.sub_badge_color,
        backup_rating_color=main.rating_badge_color,
    )
    assert same_as_main == off

    # An opaque badge hides the poster: the checkbox changes nothing at 100%.
    def opaque(**kw) -> bytes:
        return asyncio.run(routes.preview_image(None, **SAMPLE, opacity=1.0, sample="white.jpg", **kw)).body

    assert opaque(adapt="true") == opaque()
