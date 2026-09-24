"""Roadmap B4/P10: the default badge palette, and the one-shot migration to it.

The old defaults cleared WCAG AAA on every badge while two of them -- audio
navy and rating violet -- were CIEDE2000 1.9 apart under deuteranopia, i.e. the
same colour to a colour-blind viewer. Palette 2 fixes that; these tests pin the
fix AND the migration's two promises:

  1. a colour still on its old shipped default moves to the new one, and
  2. a colour the operator chose -- including an old default chosen
     deliberately AFTER the migration -- is never touched.

Promise 2 is the one that is easy to break: a migration keyed only on "value
equals the old default" re-fires on every load, and silently reverts an
operator who picked the old navy on purpose.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from measure_palette_separation import SAME_COLOUR, worst_separation  # noqa: E402

from app.config import (  # noqa: E402
    _PALETTE_1_DEFAULTS,
    BADGE_PALETTE_VERSION,
    ImageConfig,
    load_config,
    save_config_from_dict,
)

COLOUR_FIELDS = ("video_badge_color", "audio_badge_color", "sub_badge_color", "rating_badge_color")
PALETTE_2 = {f: ImageConfig.model_fields[f].default for f in COLOUR_FIELDS}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("CONFIG_PATH", "JELLYFIN_API_KEY", "JELLYFIN_API_KEY_FILE", "XENOTAG_SECRET_KEY"):
        monkeypatch.delenv(var, raising=False)


def _legacy_file(tmp_path, image: dict) -> Path:
    """A config.yml as a pre-palette-2 install would have saved it: no version."""
    path = tmp_path / "config.yml"
    path.write_text(yaml.dump({"image": image, "log_level": "INFO"}))
    return path


# ---------------------------------------------------------------------------
# The palette itself
# ---------------------------------------------------------------------------


def test_default_palette_is_tellable_apart_under_every_simulated_vision_type():
    """The B4 acceptance criterion. This is the check the old palette failed."""
    cfg = ImageConfig()
    palette = {f.split("_")[0]: getattr(cfg, f) for f in COLOUR_FIELDS}
    worst, pair, kind = worst_separation(palette)
    assert worst >= SAME_COLOUR, f"{pair} collapses under {kind or 'normal'} vision (dE {worst:.1f})"


def test_the_old_palette_really_did_collapse():
    """Guards the test above against a probe that has stopped seeing anything."""
    old = {f.split("_")[0]: v for f, v in _PALETTE_1_DEFAULTS.items()}
    worst, pair, kind = worst_separation(old)
    assert worst < SAME_COLOUR
    assert pair == "audio/rating"


def test_a_new_install_is_on_the_current_palette():
    cfg = ImageConfig()
    assert cfg.badge_palette_version == BADGE_PALETTE_VERSION
    assert {f: getattr(cfg, f) for f in COLOUR_FIELDS} == PALETTE_2


# ---------------------------------------------------------------------------
# Promise 1: old defaults move
# ---------------------------------------------------------------------------


def test_every_old_default_migrates_to_its_new_default():
    cfg = ImageConfig(**_PALETTE_1_DEFAULTS)
    assert {f: getattr(cfg, f) for f in COLOUR_FIELDS} == PALETTE_2
    assert cfg.badge_palette_version == BADGE_PALETTE_VERSION


@pytest.mark.parametrize("spelling", ["#1E3A8A", " #1e3a8a ", "#1e3a8a"])
def test_the_match_ignores_case_and_surrounding_space(spelling):
    assert ImageConfig(audio_badge_color=spelling).audio_badge_color == PALETTE_2["audio_badge_color"]


# ---------------------------------------------------------------------------
# Promise 2: the operator's choices stay
# ---------------------------------------------------------------------------


def test_a_custom_colour_is_left_alone():
    cfg = ImageConfig(**{**_PALETTE_1_DEFAULTS, "sub_badge_color": "#ff0000"})
    assert cfg.sub_badge_color == "#ff0000"
    assert cfg.video_badge_color == PALETTE_2["video_badge_color"]


def test_the_match_is_per_field_not_any_old_default():
    """The old RATING violet placed in the video field is a choice, not a default."""
    cfg = ImageConfig(video_badge_color=_PALETTE_1_DEFAULTS["rating_badge_color"])
    assert cfg.video_badge_color == _PALETTE_1_DEFAULTS["rating_badge_color"]


def test_an_old_default_chosen_after_the_migration_is_kept():
    cfg = ImageConfig(audio_badge_color="#1e3a8a", badge_palette_version=BADGE_PALETTE_VERSION)
    assert cfg.audio_badge_color == "#1e3a8a"


def test_the_migration_runs_once_through_a_real_save_and_reload(tmp_path):
    """End to end, through the file: migrate, persist the marker, then honour a
    deliberate old-default choice on every later load."""
    path = _legacy_file(tmp_path, dict(_PALETTE_1_DEFAULTS))

    migrated = load_config(path)
    assert migrated.image.audio_badge_color == PALETTE_2["audio_badge_color"]

    # The operator saves Settings. The UI spreads the loaded image dict back
    # into its PUT body, so the version travels with it.
    body = migrated.model_dump()
    body["image"]["audio_badge_color"] = "#1e3a8a"  # chosen on purpose
    save_config_from_dict(body)

    on_disk = yaml.safe_load(path.read_text())["image"]
    assert on_disk["badge_palette_version"] == BADGE_PALETTE_VERSION
    assert on_disk["audio_badge_color"] == "#1e3a8a"

    for _ in range(2):  # and it survives any number of restarts
        assert load_config(path).image.audio_badge_color == "#1e3a8a"


def test_an_unparseable_version_is_treated_as_unmigrated():
    cfg = ImageConfig(badge_palette_version="garbage", audio_badge_color="#1e3a8a")
    assert cfg.audio_badge_color == PALETTE_2["audio_badge_color"]
    assert cfg.badge_palette_version == BADGE_PALETTE_VERSION


# ---------------------------------------------------------------------------
# The preview route builds an ImageConfig from query params
# ---------------------------------------------------------------------------


def test_previewing_an_old_default_renders_the_old_default(monkeypatch):
    """The route constructs ImageConfig from what the operator is looking at.
    Without an explicit version the migration would treat that as a legacy
    config and swap the colour being previewed for a different one."""
    from app.web import routes

    captured = {}

    def fake_render(groups, rating_group, cfg_img, base_image_bytes=None):
        captured["cfg"] = cfg_img
        return b"jpeg"

    monkeypatch.setattr(routes, "_require_user", lambda request: "admin")
    monkeypatch.setattr(routes, "generate_preview_bytes", fake_render)

    asyncio.run(routes.preview_image(None, audio_color="#1e3a8a"))
    assert captured["cfg"].audio_badge_color == "#1e3a8a"


def test_an_unset_preview_colour_falls_back_to_the_current_default(monkeypatch):
    from app.web import routes

    captured = {}
    monkeypatch.setattr(routes, "_require_user", lambda request: "admin")
    monkeypatch.setattr(
        routes,
        "generate_preview_bytes",
        lambda g, r, cfg_img, base_image_bytes=None: captured.setdefault("cfg", cfg_img) and b"",
    )

    asyncio.run(routes.preview_image(None))
    assert {f: getattr(captured["cfg"], f) for f in COLOUR_FIELDS} == PALETTE_2
