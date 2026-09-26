"""Roadmap B10: the rating badge and the tag rows must never draw over each other.

The rating used to be hardwired to top-left while the tags went wherever
badge_position said, so choosing top-left for the tags drew them on top of the
rating. The rating now has its own corner (``rating_position``), and every one
of the 16 combinations must render without overlap:

  same corner          -> stack, rating nearest the corner
  same edge, opposite  -> the tag row sharing the rating's band is narrowed
                          (a tag row can otherwise span the full poster width)
  different edges      -> independent

These tests read pill rectangles from the real render path
(``render_badge_groups(..., placed=[])``), not from a re-implementation of it.
"""

from __future__ import annotations

import itertools

import pytest
from PIL import Image

from app.config import ImageConfig
from app.overlay import BadgeGroup, _compute_layout_params, render_badge_groups

CORNERS = ("top-left", "top-right", "bottom-left", "bottom-right")
W, H = 1000, 1500

# Enough labels that every tag row runs to the full poster width, which is the
# case that collides across the poster when rating and tags share an edge.
TAG_GROUPS = [
    BadgeGroup(labels=["2160p", "HEVC", "DV", "HDR10+", "10bit", "REMUX", "IMAX"], fill_color="#203a30"),
    BadgeGroup(labels=["TRUEHD", "ATMOS", "EN", "JA", "DE", "FR", "ES", "IT"], fill_color="#312c4c"),
    BadgeGroup(labels=["PGS", "EN", "JA", "DE", "FR", "ES", "IT", "PT", "NL"], fill_color="#50532f"),
]
RATING = BadgeGroup(labels=["PG-13"], fill_color="#73485b")


def _layout(badge_position: str, rating_position: str, groups=TAG_GROUPS, rating=RATING):
    cfg = ImageConfig(badge_position=badge_position, rating_position=rating_position)
    placed: list = []
    render_badge_groups(Image.new("RGBA", (W, H)), groups, rating, cfg, placed=placed)
    return cfg, [r for k, r in placed if k == "rating"], [r for k, r in placed if k == "tags"]


def _intersects(a, b) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


@pytest.mark.parametrize("badge_position,rating_position", list(itertools.product(CORNERS, CORNERS)))
def test_no_combination_draws_the_rating_under_a_tag(badge_position, rating_position):
    _, rating, tags = _layout(badge_position, rating_position)
    assert rating and tags, "the scenario must actually draw both"
    for r in rating:
        for t in tags:
            assert not _intersects(r, t), f"rating {r} overlaps tag {t}"


def test_the_reported_bug_top_left_tags_no_longer_cover_the_rating():
    """The operator's report, as it was filed: tags set to top-left."""
    _, rating, tags = _layout("top-left", "top-left")
    assert not any(_intersects(r, t) for r in rating for t in tags)


@pytest.mark.parametrize("corner", CORNERS)
def test_a_shared_corner_puts_the_rating_nearest_the_corner(corner):
    cfg, rating, tags = _layout(corner, corner)
    p = _compute_layout_params(W, cfg)
    ry = rating[0][1]
    if corner.startswith("top"):
        assert ry == p["margin"]
        assert min(t[1] for t in tags) > ry
    else:
        assert ry + rating[0][3] == H - p["margin"]
        assert max(t[1] + t[3] for t in tags) < ry


def test_same_edge_opposite_sides_narrows_only_the_row_in_the_ratings_band():
    cfg, rating, tags = _layout("top-right", "top-left")
    band_y = rating[0][1]
    in_band = [t for t in tags if t[1] == band_y]
    outside = [t for t in tags if t[1] != band_y]
    rating_right = max(x + w for x, _, w, _ in rating)
    assert in_band and min(t[0] for t in in_band) > rating_right
    # rows further from the edge are not in the rating's way and keep the full width
    assert outside and min(t[0] for t in outside) < rating_right


def test_the_default_layout_is_unchanged():
    """Defaults are tags bottom-left, rating top-left: exactly where both went
    before B10, so no existing poster moves."""
    cfg = ImageConfig()
    assert (cfg.badge_position, cfg.rating_position) == ("bottom-left", "top-left")
    cfg, rating, tags = _layout(cfg.badge_position, cfg.rating_position)
    p = _compute_layout_params(W, cfg)
    assert (rating[0][0], rating[0][1]) == (p["margin"], p["margin"])
    nearest_row = max(t[1] for t in tags)
    assert nearest_row + tags[0][3] == H - p["margin"]


def test_no_rating_means_no_offset():
    """With nothing in the corner, the tags must not leave a hole for it."""
    cfg, _, with_rating = _layout("top-left", "top-left")
    cfg, _, without = _layout("top-left", "top-left", rating=BadgeGroup(labels=[], fill_color="#73485b"))
    assert min(t[1] for t in without) < min(t[1] for t in with_rating)


def test_an_unknown_rating_position_falls_back_instead_of_refusing_to_start():
    assert ImageConfig(rating_position="centre").rating_position == "top-left"


def test_the_preview_route_clamps_an_unknown_corner():
    from app.web import routes

    cfg = routes._image_config_from_params(rating_position="nowhere")
    assert cfg.rating_position == "top-left"
    assert routes._image_config_from_params(rating_position="bottom-right").rating_position == "bottom-right"
