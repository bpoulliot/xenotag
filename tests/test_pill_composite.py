"""The pill lands on the poster at the opacity the chips measure (roadmap B21).

Until B21, `_render_group()` placed each tile with one masked paste, which put
a translucent fill at a**3 over (1 - a**2) of the poster while `app.contrast`
modelled a. The fix (operator decision (b)) alpha-composites the pill and keeps
the glow's paste, on the premise that a 100% poster -- production's -- does not
move by a single byte. Every assertion here is on rendered pixels, through the
real `render_badge_groups()`; `scripts/measure_pill_composite.py` is the probe.
"""

from __future__ import annotations

import hashlib

import pytest

from app import overlay
from app.config import AppConfig, ImageConfig
from app.contrast import BACKDROPS
from app.pipeline import _make_badge_groups
from scripts.generate_readme_images import _OVERLAYS, _RATING
from scripts.measure_pill_composite import (
    SHIPPED,
    SYNTHETIC,
    algebra_expected,
    algebra_rendered,
    chip_interior,
    floors,
    glow_band,
    identity_rows,
    legacy_place,
    poster_interior,
    render,
    self_test,
    tile_alpha_levels,
)
from scripts.measure_pill_composite import _readme_media as readme_media

# sha256 of the RGB bytes of every README overlay config over every synthetic
# poster at the shipped 100%, rendered by origin/main 5476f24 -- the code
# before B21 -- with Pillow 12.3.0 and DejaVu Sans Bold. A stored reference,
# so "100% did not move" does not rest only on the probe's copy of the old paste.
# The tv, tv_plus and stacked rows were re-taken at P7 containment (2026-10-06):
# their single audio row used to hide `DTS-HD JA`, which the 2-row wrap now
# draws. The desktop rows, which hid nothing, did not move and are 5476f24's.
REFERENCE_100 = {
    ("overlay-desktop.jpg", "white"): "70ebbc0aca6e44a4a97c1623888965565bb559be8b39bcd79032534858ee2999",
    ("overlay-desktop.jpg", "black"): "188f5a99a302d6b10dd1d96853dcc3fe4fad2b0e512fdd48f7c67c360670f590",
    ("overlay-desktop.jpg", "grey128"): "f0f349fd6e880203fa5fd4366e52eb4d16d51b271f3250d784f3e38c86a19f8e",
    ("overlay-desktop.jpg", "gradient"): "12dc2b649e9a7321698dda5d0be0cb514bd2fed5cf4cb26ac930c88ed01fb661",
    ("overlay-tv.jpg", "white"): "d72e1951fffd56dddaf0209a0c9e3596bc967422fd2213322c4b1c692818aa1a",
    ("overlay-tv.jpg", "black"): "bffe6dd609961f071c226e422bbb8053646db19b4066a9b721d54511238055f2",
    ("overlay-tv.jpg", "grey128"): "362c7dbc2977795bd616188b9bd23e044ebe32b3e70d4ac92867968a96a107a4",
    ("overlay-tv.jpg", "gradient"): "9b6c2c76d53b4e9de281fb4cb8e77718b280337e9ba01aeba78a149f223b1549",
    ("overlay-tv-plus.jpg", "white"): "bb7dd730fb47f491895a552fe4e718a28a010da1fc4f04295fb3c184e3879093",
    ("overlay-tv-plus.jpg", "black"): "0d6f8af1181ca58bcd43564be31242b720fe6d49b7bb34c82b754de705426bc0",
    ("overlay-tv-plus.jpg", "grey128"): "e4661aa3a44432737477216ad1bd85accc8ee45f67532bd8d468e6607ff2bf1f",
    ("overlay-tv-plus.jpg", "gradient"): "fc5272a999c4c8a20ba79cd883025cd0178e9df3e6892b8620e0891c01a2135c",
    ("overlay-stacked.jpg", "white"): "c6c42fdea6a0404dedfaa8bfebde0bc74125885af76b1268881a3cdf155adf3d",
    ("overlay-stacked.jpg", "black"): "00184d0e858217e5d6b34e7ce3e6d7aff9362fef9e403bb537527f07e45ae29c",
    ("overlay-stacked.jpg", "grey128"): "cefc9cca45b9e50d6d362cf873be2ab9fa76609ea0f78090aaeb1a6ea1cc3a65",
    ("overlay-stacked.jpg", "gradient"): "5a7379f85f05272de902022b79e13a74915897dddcc9e640ec7c7eadf4c1ef4c",
}


@pytest.fixture(autouse=True)
def _fresh_cache():
    overlay.clear_pill_cache()
    yield
    overlay.clear_pill_cache()


# ── The fix ─────────────────────────────────────────────────────────────────
def test_algebra_case_fill_lands_at_a_not_a_cubed():
    """B21's own case: rating #73485b over black at 65%.

    a = 165/255; the fill must land at fill*a, which is the `(74, 47, 59)`
    the chip has always reported. Before the fix the poster got fill*a**3.
    """
    a = overlay.badge_alpha(0.65) / 255
    expected = tuple(round(c * a) for c in (0x73, 0x48, 0x5B))
    assert expected == algebra_expected(1) == (74, 47, 59)
    assert algebra_rendered(SHIPPED) == expected
    # and the old paste really did what the filing said
    assert algebra_rendered(legacy_place) == algebra_expected(3) == (31, 20, 25)


@pytest.mark.parametrize("opacity", [0.65, 0.80, 0.90, 1.00])
@pytest.mark.parametrize("backdrop", ["white", "black"])
@pytest.mark.parametrize("field", ["video_badge_color", "audio_badge_color", "sub_badge_color", "rating_badge_color"])
def test_chip_and_poster_agree(opacity, backdrop, field):
    """The Settings chip and the poster read the same interior pixel.

    Tolerance: zero levels on every channel. They go through the same Pillow
    composite, so anything above zero means they have come apart again.
    """
    fill = getattr(ImageConfig(), field)
    chip = chip_interior(BACKDROPS[backdrop], fill, "#ffffff", opacity)
    poster = poster_interior(SHIPPED, BACKDROPS[backdrop], fill, "#ffffff", opacity)
    assert poster == chip, f"{field} at {opacity} on {backdrop}: poster {poster} vs chip {chip}"


def test_chip_and_poster_disagreed_before_the_fix():
    """The other direction: the same comparison must SEE B21 under the old paste."""
    fill = ImageConfig().rating_badge_color
    chip = chip_interior(BACKDROPS["white"], fill, "#ffffff", 0.80)
    assert poster_interior(legacy_place, BACKDROPS["white"], fill, "#ffffff", 0.80) != chip


# ── What must not move ──────────────────────────────────────────────────────
def test_100_percent_matches_the_stored_pre_b21_reference():
    if overlay._load_font(20).getname() != ("DejaVu Sans", "Bold"):
        pytest.skip("the reference was rendered with DejaVu Sans Bold")
    media = readme_media()
    moved = []
    for name, _sample, overrides in _OVERLAYS:
        cfg = AppConfig()
        cfg.image = cfg.image.model_copy(update=overrides)
        assert cfg.image.badge_opacity == 1.0
        groups, rating = _make_badge_groups(media, _RATING, cfg)
        for bname, make in SYNTHETIC.items():
            img = render(SHIPPED, make(), cfg.image, groups, rating)
            if hashlib.sha256(img.tobytes()).hexdigest() != REFERENCE_100[(name, bname)]:
                moved.append((name, bname))
    assert not moved, f"100% renders moved: {moved}"


def test_100_percent_is_byte_identical_to_the_old_paste():
    """Font-independent twin of the stored reference: old and new paste, same run."""
    rows = identity_rows(1.0)
    assert len(rows) == 20
    assert [(n, d) for n, d in rows if d["pixels"]] == []


def test_below_100_percent_the_render_does_move():
    """Otherwise the identity above would prove nothing about the swap."""
    assert all(d["pixels"] for _, d in identity_rows(0.65))


def test_pill_tiles_are_binary_in_alpha_at_100_percent():
    """Why the identity holds: no pixel is partly pill, so paste and composite agree."""
    assert tile_alpha_levels() == {0, 255}


@pytest.mark.parametrize("opacity", [1.0, 0.65])
def test_glow_band_left_of_a_pill_is_unchanged(opacity):
    """Decision (b) keeps today's glow: 1-8 px left of a pill on grey 128, old == new."""
    old, new = glow_band(legacy_place, opacity), glow_band(SHIPPED, opacity)
    assert new == old
    assert any(p != (128, 128, 128) for p in new), "the band must actually be glow, not bare poster"


# ── The numbers the docs quote ──────────────────────────────────────────────
def test_opacity_floors_quoted_in_config_and_readme_hold_on_the_poster():
    """config.py and the README say 0.98 holds AAA and 0.80 holds AA. Before B21
    that was true of the chip and false of the poster (0.99 / 0.87)."""
    assert floors(SHIPPED) == {"AA": 0.80, "AAA": 0.98}


def test_probe_self_test_passes():
    """`scripts/measure_pill_composite.py --self-test` must stay green in CI."""
    assert self_test() == 0
