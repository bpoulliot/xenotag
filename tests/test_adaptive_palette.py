"""Background-aware badge palette (roadmap P6), through the real render path.

`render_badge_groups()` picks, per drawn row, between each pill's main fill and
its group's backup by the luminance of the poster strip that row covers. P7
lays a group over up to two rows and closes a row with "+N" pills counting
groups that had no row; both must follow the same rule. Every test here reads
the fill each pill was actually drawn with, by recording `_place_pill()`.
`scripts/measure_adaptive_palette.py` is the probe for the thresholds.
"""

from __future__ import annotations

import pytest
from PIL import Image

from app import overlay
from app.config import ImageConfig
from app.overlay import BadgeGroup, render_badge_groups

WHITE = (255, 255, 255, 255)
BLACK = (0, 0, 0, 255)
DEFAULT = ImageConfig()
VIDEO = (DEFAULT.video_badge_color, DEFAULT.backup_video_badge_color)
AUDIO = (DEFAULT.audio_badge_color, DEFAULT.backup_audio_badge_color)
SUB = (DEFAULT.sub_badge_color, DEFAULT.backup_sub_badge_color)
RATING = (DEFAULT.rating_badge_color, DEFAULT.backup_rating_badge_color)


def _group(labels: list[str], palette: tuple[str, str]) -> BadgeGroup:
    return BadgeGroup(labels, palette[0], DEFAULT.badge_text_color, palette[1])


def _cfg(adapt: bool, opacity: float = 0.65) -> ImageConfig:
    return DEFAULT.model_copy(update={"adapt_badge_colors": adapt, "badge_opacity": opacity})


@pytest.fixture
def drawn(monkeypatch):
    """Every pill drawn, as ``(text, fill_hex)``, in draw order."""
    calls: list[tuple[str, str]] = []
    real = overlay._place_pill

    def spy(layer, xy, text, fill_hex, *rest):
        calls.append((text, fill_hex))
        real(layer, xy, text, fill_hex, *rest)

    monkeypatch.setattr(overlay, "_place_pill", spy)
    return calls


def _split_poster(size=(600, 900)) -> Image.Image:
    """Top half black, bottom half white."""
    img = Image.new("RGBA", size, BLACK)
    img.paste(WHITE, (0, size[1] // 2, size[0], size[1]))
    return img


def test_default_is_off():
    assert DEFAULT.adapt_badge_colors is False


@pytest.mark.parametrize("adapt, opacity", [(False, 0.65), (True, 1.0)])
def test_off_or_opaque_renders_byte_identical_to_no_backup(adapt, opacity):
    """Off, or on at 100%, the backup is never read: the render matches groups that carry none."""
    poster = _split_poster()
    with_backup = [_group(["4K", "HEVC"], VIDEO), _group(["DTS EN"], AUDIO)]
    without = [BadgeGroup(g.labels, g.fill_color, g.text_color) for g in with_backup]
    rating = _group(["PG"], RATING)
    bare_rating = BadgeGroup(rating.labels, rating.fill_color, rating.text_color)
    got = render_badge_groups(poster, with_backup, rating, _cfg(adapt, opacity)).tobytes()
    assert got == render_badge_groups(poster, without, bare_rating, _cfg(False, opacity)).tobytes()


def test_light_region_takes_backup_dark_region_keeps_main(drawn):
    """Rating top-left over black keeps its main fill; tags bottom-left over white take the backup."""
    cfg = _cfg(True).model_copy(update={"rating_position": "top-left", "badge_position": "bottom-left"})
    render_badge_groups(_split_poster(), [_group(["1080p"], VIDEO)], _group(["PG"], RATING), cfg)
    assert drawn == [("PG", RATING[0]), ("1080p", VIDEO[1])]


def test_off_keeps_main_everywhere(drawn):
    cfg = _cfg(False).model_copy(update={"rating_position": "top-left", "badge_position": "bottom-left"})
    render_badge_groups(_split_poster(), [_group(["1080p"], VIDEO)], _group(["PG"], RATING), cfg)
    assert drawn == [("PG", RATING[0]), ("1080p", VIDEO[0])]


def test_each_wrap_row_decides_for_itself(drawn):
    """A group wrapped over two rows: only the row over the light strip takes the backup."""
    group = _group([f"CODEC{i} EN DE FR JA" for i in range(6)], AUDIO)
    cfg = _cfg(True).model_copy(update={"badge_position": "bottom-left"})
    placed: list = []
    render_badge_groups(Image.new("RGBA", (600, 900), BLACK), [group], None, cfg, placed=placed)
    ys = sorted({y for _, (_, y, _, _) in placed})
    assert len(ys) == 2, "the fixture must wrap onto exactly two rows"
    h = placed[0][1][3]

    # Light only under the bottom row.
    poster = Image.new("RGBA", (600, 900), BLACK)
    poster.paste(WHITE, (0, ys[1], 600, ys[1] + h))
    drawn.clear()
    placed2: list = []
    render_badge_groups(poster, [group], None, cfg, placed=placed2)
    assert [r for _, r in placed2] == [r for _, r in placed]
    fills_by_row = {
        y: {fill for (_, fill), (_, (_, yy, _, _)) in zip(drawn, placed2, strict=True) if yy == y} for y in ys
    }
    assert fills_by_row == {ys[0]: {AUDIO[0]}, ys[1]: {AUDIO[1]}}


def test_count_pill_follows_its_own_groups_backup(drawn):
    """A group with no row is counted as "+N" in its own colour; adapted, in its own backup."""
    cfg = _cfg(True).model_copy(update={"badge_position": "bottom-left"})
    p = overlay._compute_layout_params(600, cfg)
    pitch = overlay._measure_group_height(p["font_size"], p["pad_v"]) + p["row_gap"]
    height = 2 * p["margin"] + pitch  # room for exactly one row
    poster = Image.new("RGBA", (600, height), WHITE)
    render_badge_groups(poster, [_group(["4K"], VIDEO), _group(["PGS EN"], SUB)], None, cfg)
    assert drawn == [("4K", VIDEO[1]), ("+2", SUB[1])]


def test_count_pill_keeps_main_when_off(drawn):
    cfg = _cfg(False).model_copy(update={"badge_position": "bottom-left"})
    p = overlay._compute_layout_params(600, cfg)
    pitch = overlay._measure_group_height(p["font_size"], p["pad_v"]) + p["row_gap"]
    poster = Image.new("RGBA", (600, 2 * p["margin"] + pitch), WHITE)
    render_badge_groups(poster, [_group(["4K"], VIDEO), _group(["PGS EN"], SUB)], None, cfg)
    assert drawn == [("4K", VIDEO[0]), ("+2", SUB[0])]
