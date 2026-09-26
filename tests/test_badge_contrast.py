"""Rendered-contrast regression tests for badge overlays (roadmap B1).

Every assertion here samples a pixel out of a real pill-tile composite (since
B2 the probe renders through `_render_pill_tile()`, the body `_pill_tile()`
caches, so a measurement never touches `_PILL_CACHE`).
That is deliberate and is the whole point of the file: for the life of the B1
defect the badge colours *were* WCAG AAA as hex constants, and any test that
checked the constants would have passed the entire time while the shipped
render was 3.7-5.3:1. Only a rendered pixel can catch this class of bug.
"""

from __future__ import annotations

import inspect

import pytest
from PIL import Image

from app import overlay
from app.config import ImageConfig
from app.overlay import BadgeGroup, _parse_color, render_badge_groups
from scripts.measure_badge_contrast import (
    BACKDROPS,
    contrast_ratio,
    relative_luminance,
    render_over,
    sample_edge,
    sample_interior,
    self_test,
)
from scripts.measure_pill_cache_key import self_test as cache_key_self_test
from scripts.measure_pill_cache_key import sweep as cache_key_sweep

AA = 4.5
AAA = 7.0
WHITE = (255, 255, 255)


def _palette(cfg: ImageConfig) -> list[tuple[str, str]]:
    return [
        ("video", cfg.video_badge_color),
        ("audio", cfg.audio_badge_color),
        ("sub", cfg.sub_badge_color),
        ("rating", cfg.rating_badge_color),
    ]


@pytest.fixture(params=["system-font", "no-system-font"])
def font_env(request, monkeypatch):
    """Run every rendering test both with and without a system TTF available.

    CI runners do not necessarily ship DejaVu, and `_load_font()` silently falls
    back to Pillow's default. The contrast result must not depend on which one
    was found.
    """
    if request.param == "no-system-font":
        monkeypatch.setattr(overlay, "_FONT_PATHS", [])
    monkeypatch.setattr(overlay, "_font_cache", {})
    overlay.clear_pill_cache()
    yield request.param
    overlay.clear_pill_cache()


# ── The defect itself ────────────────────────────────────────────────────────
@pytest.mark.parametrize("backdrop", list(BACKDROPS))
@pytest.mark.parametrize("name,fill_hex", _palette(ImageConfig()))
def test_shipped_defaults_render_at_aaa(font_env, name, fill_hex, backdrop):
    """Every default badge must clear WCAG AAA as RENDERED, on every backdrop.

    Regression guard for `badge_opacity` drifting back down: at 0.65 the video
    badge rendered 3.7:1 on white.
    """
    cfg = ImageConfig()
    img, pill_wh = render_over(
        BACKDROPS[backdrop],
        fill_hex,
        cfg.badge_text_color,
        alpha=int(cfg.badge_opacity * 255),
    )
    ratio = contrast_ratio(_parse_color(cfg.badge_text_color), sample_interior(img, pill_wh))
    assert ratio >= AAA, f"{name} on {backdrop} rendered {ratio:.2f}:1, below AAA {AAA}:1"


@pytest.mark.parametrize("backdrop", list(BACKDROPS))
def test_glow_is_not_painted_behind_the_pill(font_env, backdrop):
    """The structural half of the fix, and the half the AAA test cannot see.

    An opaque pill hides whatever is behind it, so the test above stays green
    even if the white glow is restored under the fill. This one renders at a
    deliberately translucent alpha, where the glow is visible through the pill:
    the interior must never be LIGHTER than the opaque fill colour, because the
    only thing that could lighten it is glow the pill is sitting on.
    """
    fill_hex = ImageConfig().video_badge_color
    fill_rgb = _parse_color(fill_hex)
    img, pill_wh = render_over(BACKDROPS[backdrop], fill_hex, alpha=128)
    interior = sample_interior(img, pill_wh)
    backdrop_rgb = BACKDROPS[backdrop]
    ceiling = max(relative_luminance(fill_rgb), relative_luminance(backdrop_rgb))
    assert relative_luminance(interior) <= ceiling + 1e-3, (
        f"interior {interior} on {backdrop} is lighter than both the fill {fill_rgb} and the "
        f"backdrop {backdrop_rgb} — something white is being painted behind the pill"
    )


def test_translucency_lowers_the_rendered_ratio(font_env):
    """A translucent pill must measure BELOW its hex ratio, not equal to it.

    If this ever passes trivially, the measurement has stopped looking at
    pixels — which is exactly how B1 survived.
    """
    fill_hex = ImageConfig().video_badge_color
    hex_ratio = contrast_ratio(WHITE, _parse_color(fill_hex))
    img, pill_wh = render_over(BACKDROPS["white"], fill_hex, alpha=96)
    rendered = contrast_ratio(WHITE, sample_interior(img, pill_wh))
    assert rendered < hex_ratio - 1.0, f"rendered {rendered:.2f}:1 vs hex {hex_ratio:.2f}:1"


def test_opaque_pill_renders_exactly_its_hex_ratio(font_env):
    """At alpha=255 nothing behind the pill can reach the viewer."""
    for _, fill_hex in _palette(ImageConfig()):
        hex_ratio = contrast_ratio(WHITE, _parse_color(fill_hex))
        for backdrop in BACKDROPS.values():
            img, pill_wh = render_over(backdrop, fill_hex, alpha=255)
            rendered = contrast_ratio(WHITE, sample_interior(img, pill_wh))
            assert abs(rendered - hex_ratio) < 0.05, f"{fill_hex}: {rendered:.2f} vs {hex_ratio:.2f}"


# ── The measuring apparatus ──────────────────────────────────────────────────
def test_contrast_ratio_matches_wcag_reference_values():
    assert contrast_ratio(WHITE, (0, 0, 0)) == pytest.approx(21.0, abs=0.01)
    assert contrast_ratio(WHITE, WHITE) == pytest.approx(1.0, abs=1e-9)
    # #767676 is the canonical darkest grey that still clears AA against white.
    assert AA <= contrast_ratio(WHITE, (0x76, 0x76, 0x76)) < 4.6
    assert contrast_ratio(WHITE, (0x78, 0x78, 0x78)) < AA


def test_edge_sample_flatters_the_result(font_env):
    """Documents the sampling trap, so nobody 'simplifies' the sampler back.

    x=_GLOW_MARGIN is the pill's antialiased left edge, not its interior, and
    reading it there reported ~4.4:1 for a badge that rendered 3.7:1.
    """
    img, pill_wh = render_over(BACKDROPS["white"], ImageConfig().video_badge_color, alpha=165)
    interior = contrast_ratio(WHITE, sample_interior(img, pill_wh))
    edge = contrast_ratio(WHITE, sample_edge(img, pill_wh))
    assert edge > interior + 0.2, f"edge {edge:.2f}:1 vs interior {interior:.2f}:1"


def test_sampler_finds_a_planted_interior():
    planted = Image.new("RGB", (200, 100), (255, 0, 0))
    planted.paste(Image.new("RGB", (120, 50), (7, 11, 13)), (overlay._GLOW_MARGIN, overlay._GLOW_MARGIN))
    assert sample_interior(planted, (120, 50)) == (7, 11, 13)


def test_probe_self_test_passes(font_env):
    """`scripts/measure_badge_contrast.py --self-test` must stay green in CI."""
    assert self_test() == 0


# ── Cache key ────────────────────────────────────────────────────────────────
def test_pill_tile_takes_no_background_or_position_argument(font_env):
    """The tile depends on no poster — no position, no background.

    That is what lets one entry serve many posters at all. P6 (a
    background-aware palette) is the obvious way to break it: the same text
    would render differently per poster while the key stayed equal, and every
    poster after the first would get the first one's colours. Failing here is
    the cue to add a palette-selection term to the key, the way B3 added the
    padding.
    """
    params = list(inspect.signature(overlay._pill_tile).parameters)
    assert params == ["text", "fill_hex", "text_hex", "alpha", "font_size", "pad_h", "pad_v"]


def test_pill_tile_is_deterministic_for_identical_arguments(font_env):
    overlay.clear_pill_cache()
    first = overlay._pill_tile("1080p", "#134e4a", "#ffffff", 255, 40, 8, 5).tobytes()
    overlay.clear_pill_cache()
    second = overlay._pill_tile("1080p", "#134e4a", "#ffffff", 255, 40, 8, 5).tobytes()
    assert first == second


@pytest.mark.parametrize(
    ("term", "first", "second"),
    [
        ("pad_v", (36, 4, 2), (36, 4, 3)),
        ("pad_h", (36, 4, 2), (36, 5, 2)),
    ],
)
def test_cache_key_covers_padding(font_env, term, first, second):
    """B3, fixed. Two calls differing only in padding must not share an entry.

    `_compute_layout_params()` derives font_size and padding from the poster
    width by independent roundings, so two widths can agree on font_size and
    disagree on padding — 494px and 501px both give font_size 36, with pad_v 2
    and 3. While the key omitted padding, whichever poster was rendered first
    supplied the tile for both, 121 times over a 200-4000px sweep.

    `pad_h` is here by hand rather than by width. No poster width can produce a
    pad_h collision today, because every `_BADGE_SIZE_PX` value is an exact
    multiple of 8 and so every `round(8 * scale)` step lands on a
    `round(base * scale)` step as well. That is arithmetic luck in the layout
    constants, not a property of the cache, and a badge size that is not a
    multiple of 8 would spend it — so the key carries pad_h and this asserts it.
    """
    overlay.clear_pill_cache()
    a = overlay._pill_tile("1080p", "#134e4a", "#ffffff", 255, *first)
    b = overlay._pill_tile("1080p", "#134e4a", "#ffffff", 255, *second)
    assert a.size != b.size, f"same cache key served a tile built with another poster's {term}"
    assert a.tobytes() != b.tobytes()
    assert len(overlay._PILL_CACHE) == 2, f"{term} did not produce a distinct cache entry"


def test_cache_key_is_every_pill_tile_argument(font_env):
    """The defect class is "the key is not a superset of the renderer's inputs".

    Asserted structurally so a new argument cannot be added to `_pill_tile()`
    without either landing in the key or failing here. Everything else the body
    reads — `_GLOW_MARGIN`, `_GLOW_EXPAND`, `_GLOW_BLUR`, the corner radii, the
    glow's own colour — is a module constant, fixed for the life of a process;
    `_FONT_PATHS`/`_font_cache` are too, and `clear_pill_cache()` is the escape
    hatch the config-save routes already call when anything else moves.
    """
    params = list(inspect.signature(overlay._pill_tile).parameters)
    overlay.clear_pill_cache()
    overlay._pill_tile("1080p", "#134e4a", "#ffffff", 255, 36, 4, 2)
    key = next(iter(overlay._PILL_CACHE))
    assert len(key) == len(params), f"key covers {len(key)} of {len(params)} arguments"


def test_cache_key_probe_self_test_passes(font_env):
    """`scripts/measure_pill_cache_key.py --self-test` must stay green in CI."""
    assert cache_key_self_test() == 0


def test_no_poster_width_is_served_another_width_s_tile(font_env):
    """The acceptance criterion for B3, narrowed to the band that used to fail.

    494px and 501px were the first of 121 colliding widths over 200-4000px;
    the full sweep is `scripts/measure_pill_cache_key.py`, which exits non-zero
    on any collision. This keeps the cheap part of it in the suite.
    """
    r = cache_key_sweep(ImageConfig(), 480, 520)
    assert r["collisions"] == []
    assert r["cache_entries"] == r["ideal_entries"] == 4


@pytest.mark.parametrize(("first", "second"), [(494, 501), (501, 494)])
def test_a_poster_renders_the_same_whatever_went_through_the_cache_first(font_env, first, second):
    """B3 at the level a viewer meets it — a whole poster, through the real path.

    494px and 501px agree on font_size and differ on pad_v. Rendering one and
    then the other must give exactly the bytes the second produces on its own,
    in either order. This guards the defect *class* rather than B3's instance
    of it: P6's background-aware palette would fail here too, which is the cue
    to add a palette term to the key.

    Renders to bytes; it touches no file and no library.
    """
    cfg = ImageConfig()
    groups = [BadgeGroup(labels=["1080p"], fill_color=cfg.video_badge_color)]

    def render(width: int) -> bytes:
        base = Image.new("RGBA", (width, int(width * 1.5)), (0, 0, 0, 255))
        return render_badge_groups(base, groups, None, cfg).tobytes()

    overlay.clear_pill_cache()
    render(first)
    after = render(second)

    overlay.clear_pill_cache()
    alone = render(second)

    assert after == alone, f"{second}px wore {first}px's badge"


def test_widening_the_key_costs_only_the_entries_it_was_stealing():
    """B3's trap: a wider key buys correctness with cache hits. Quantify it.

    Over 200-4000px the fix adds 18 entries to 275 and moves the hit rate
    92.77% -> 92.29%. Those 18 are exactly the tiles the narrow key was serving
    wrong, so the fix costs nothing it was not already getting by cheating.
    """
    cfg = ImageConfig()
    seen: dict[tuple, set[tuple]] = {}
    for width in range(200, 4001):
        p = overlay._compute_layout_params(width, cfg)
        seen.setdefault((p["font_size"],), set()).add((p["pad_h"], p["pad_v"]))
    narrow = len(seen)
    wide = sum(len(v) for v in seen.values())
    assert (narrow, wide) == (275, 293)
    assert wide - narrow == sum(1 for v in seen.values() if len(v) > 1) == 18
