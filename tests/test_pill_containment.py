"""Roadmap P7 containment, decision (d) of 2026-10-05: pills always inside the poster.

Each tag group wraps onto at most 2 rows; what still does not fit is counted in
a ``+N`` pill of the group's colour; the whole stack is clamped to the canvas,
and the rating is never covered. The pre-P7 renderer kept one row per group,
cut what did not fit with a ``…`` that did not say how much, and let the stack
run off a 2.39:1 canvas with ``normalize_portrait`` off.

Every assertion reads the real render: rectangles through ``placed=``, pill
text by wrapping ``overlay._pill_tile``, and the probe's own ``check()``
(``scripts/measure_pill_containment.py``), whose full grid is the acceptance.
"""

from __future__ import annotations

import hashlib
import itertools
import random

import pytest
from PIL import Image

from app import overlay as ov
from app.config import AppConfig, ImageConfig
from app.overlay import (
    _GLOW_MARGIN,
    BadgeGroup,
    _compute_layout_params,
    _load_font,
    _measure_group_height,
    _place_pill,
    render_badge_groups,
)
from app.pipeline import _make_badge_groups
from app.scanner import AudioTrack, MediaInfo, SubTrack
from scripts.measure_pill_containment import (
    CORNERS,
    SIZES,
    TAG_SETS,
    VIOLATIONS,
    atoms,
    badge_groups,
    canvas_for,
    check,
    render,
)

LANGS = "EN JA DE FR ES IT PT NL SV NO DA FI PL CS HU RO BG EL TR RU UK HE AR FA HI TH VI ID MS KO ZH".split()


@pytest.fixture(autouse=True)
def _fresh_cache():
    ov.clear_pill_cache()
    yield
    ov.clear_pill_cache()


# ── The pre-P7 renderer, verbatim (origin/main 2c57c39), as the reference ──
# Only renamed (_old_*), so that "a poster that hid nothing did not move" is
# checked against the code that drew it, not against a description of it.


def _old_truncate_label(label: str, max_w: int, font, pad_h: int) -> str:
    """Trim trailing space-separated tokens from label until its pill fits within max_w px."""

    def _pw(s: str) -> int:
        bb = font.getbbox(s)
        return bb[2] - bb[0] + pad_h * 2

    if _pw(label) <= max_w:
        return label
    tokens = label.split()
    for i in range(len(tokens) - 1, 0, -1):
        candidate = " ".join(tokens[:i]) + "…"
        if _pw(candidate) <= max_w:
            return candidate
    return tokens[0]  # first token alone can't be truncated further


def _old_render_group(
    base: Image.Image,
    labels: list[str],
    position: str,
    fill_color: str,
    text_color: str,
    alpha: int,
    font_size: int,
    pad_h: int,
    pad_v: int,
    col_gap: int,
    margin: int,
    y_offset: int = 0,
    reserve_w: int = 0,
) -> tuple[Image.Image, list[tuple[int, int, int, int]]]:
    """Render one badge group as a single row onto base. Overflow replaced with … pill.

    ``reserve_w`` narrows the row by that many pixels from the far side, for a row
    that shares its band with another group across the poster (roadmap B10).
    Returns the image and the ``(x, y, w, h)`` of every pill placed.
    """
    if not labels:
        return base, []

    font = _load_font(font_size)
    img_w, img_h = base.size

    ref_h = font.getbbox("AgfpQ")[3] - font.getbbox("AgfpQ")[1]
    pill_h = ref_h + pad_v * 2
    max_row_w = img_w - 2 * margin - reserve_w

    # Truncate any label whose pill would alone exceed the row width, then compute sizes
    badge_sizes: list[tuple[str, int]] = []
    for badge in labels:
        badge = _old_truncate_label(badge, max_row_w, font, pad_h)
        bbox = font.getbbox(badge)
        w = bbox[2] - bbox[0] + pad_h * 2
        badge_sizes.append((badge, w))

    e_text = "…"
    e_bbox = font.getbbox(e_text)
    e_w = e_bbox[2] - e_bbox[0] + pad_h * 2

    # Greedy single-row pack; append … when overflow
    row: list[tuple[str, int]] = []
    used_w = 0
    overflowed = False
    for badge, bw in badge_sizes:
        needed = bw if not row else bw + col_gap
        if used_w + needed <= max_row_w:
            row.append((badge, bw))
            used_w += needed
        else:
            overflowed = True
            break

    if overflowed:
        e_needed = col_gap + e_w
        # Trim tail to make room for …, but always keep at least 1 real badge
        while len(row) > 1 and used_w + e_needed > max_row_w:
            _, removed_bw = row.pop()
            used_w -= removed_bw + col_gap
        if used_w + e_needed <= max_row_w:
            row.append((e_text, e_w))
        # else: exactly 1 badge fills the row — silently omit ellipsis, badge presence implies content

    if not row:
        return base, []

    row_w = sum(bw for _, bw in row) + col_gap * (len(row) - 1)
    is_bottom = "bottom" in position
    is_right = "right" in position

    y = (img_h - margin - pill_h - y_offset) if is_bottom else (margin + y_offset)
    x = (img_w - margin - row_w) if is_right else margin

    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    gm = _GLOW_MARGIN
    rects = []
    for badge, bw in row:
        _place_pill(overlay, (x - gm, y - gm), badge, fill_color, text_color, alpha, font_size, pad_h, pad_v)
        rects.append((x, y, bw, pill_h))
        x += bw + col_gap

    return Image.alpha_composite(base, overlay), rects


def old_render_badge_groups(
    base: Image.Image,
    groups: list[BadgeGroup],
    rating_group: BadgeGroup | None,
    cfg: ImageConfig,
    placed: list | None = None,
) -> Image.Image:
    """Composite all badge groups onto base and return the result.

    If ``placed`` is given, it is extended with ``(kind, (x, y, w, h))`` for every
    pill drawn, ``kind`` being "rating" or "tags" -- so tests can check layout
    against the real render path rather than a re-implementation of it.
    """
    img_w, _ = base.size
    p = _compute_layout_params(img_w, cfg)

    result = base.convert("RGBA") if base.mode != "RGBA" else base
    common = (p["alpha"], p["font_size"], p["pad_h"], p["pad_v"], p["col_gap"], p["margin"])
    row_h = _measure_group_height(p["font_size"], p["pad_v"])

    # Roadmap B10. The rating and the tag rows each have their own corner, and
    # every combination must render without overlap:
    #   same corner          -> stack: the rating sits nearest the corner and
    #                           the tag rows continue past it;
    #   same edge, opposite  -> the one tag row sharing the rating's band is
    #   sides                   narrowed so it stops short of the rating (a tag
    #                           row may otherwise span the poster's full width);
    #   different edges      -> independent.
    # The rating is placed first because it owns its corner.
    rating_rects: list[tuple[int, int, int, int]] = []
    if rating_group and rating_group.labels:
        result, rating_rects = _old_render_group(
            result,
            rating_group.labels,
            cfg.rating_position,
            rating_group.fill_color,
            rating_group.text_color,
            *common,
        )
        if placed is not None:
            placed.extend(("rating", r) for r in rating_rects)

    same_corner = bool(rating_rects) and cfg.rating_position == cfg.badge_position
    same_edge = bool(rating_rects) and cfg.rating_position.split("-")[0] == cfg.badge_position.split("-")[0]
    rating_w = max(x + w for x, _, w, _ in rating_rects) - min(x for x, _, _, _ in rating_rects) if rating_rects else 0

    # Tag groups at cfg.badge_position, stacked away from the edge.
    cumulative_offset = row_h + p["row_gap"] if same_corner else 0
    for group in reversed(groups):
        if not group.labels:
            continue
        shares_band = same_edge and not same_corner and cumulative_offset == 0
        result, rects = _old_render_group(
            result,
            group.labels,
            cfg.badge_position,
            group.fill_color,
            group.text_color,
            *common,
            y_offset=cumulative_offset,
            reserve_w=rating_w + p["col_gap"] if shares_band else 0,
        )
        if placed is not None:
            placed.extend(("tags", r) for r in rects)
        cumulative_offset += row_h + p["row_gap"]

    return result


def _texts_and_image(render_fn, canvas, groups, rating, cfg):
    texts: list[str] = []
    real = ov._pill_tile

    def recording(text, *rest):
        texts.append(text)
        return real(text, *rest)

    ov._pill_tile = recording
    try:
        img = render_fn(canvas.copy(), groups, rating, cfg)
    finally:
        ov._pill_tile = real
    return texts, img


def _random_case(rng: random.Random):
    heavy = rng.random() < 0.3
    audio = [
        AudioTrack(rng.choice(LANGS), rng.choice(["AAC", "DD+", "DTS-HD", "TrueHD Atmos", "DD+ Atmos", "FLAC"]))
        for _ in range(rng.randint(1, 6 if heavy else 2))
    ]
    subs = [
        SubTrack(rng.choice(LANGS), rng.choice(["SRT", "PGS", "SSA", "VOB"]), True)
        for _ in range(rng.randint(0, 30 if heavy else 3))
    ]
    info = MediaInfo(
        resolution=rng.choice(["4K", "1080p", "720p", "SD"]),
        languages=[],
        raw_audio_langs=[],
        video_codec=rng.choice(["H.264", "H.265", "AV1", None]),
        hdr_type=rng.choice([None, "HDR10", "DV"]),
        audio_tracks=audio,
        subtitle_tracks=subs,
    )
    cfg = AppConfig()
    cfg.image = cfg.image.model_copy(
        update={
            "badge_size": rng.choice(SIZES),
            "badge_position": rng.choice(CORNERS),
            "rating_position": rng.choice(CORNERS),
        }
    )
    groups, rating = _make_badge_groups(info, rng.choice([None, "R", "PG-13", "Not Rated", "TV-Y7-FV"]), cfg)
    return groups, rating, cfg.image


def _gradient(w: int, h: int) -> Image.Image:
    img = Image.new("RGBA", (w, h))
    img.putdata([(x * 255 // w, y * 255 // h, 128, 255) for y in range(h) for x in range(w)])
    return img


def test_a_poster_that_hid_nothing_is_byte_identical():
    """The decision changes only the posters that wrapped or clipped (1.29%)."""
    rng = random.Random(20261006)  # noqa: S311 -- a reproducible test spread, not a secret
    bases = {w: _gradient(w, w * 3 // 2) for w in (300, 600)}
    clean = hid = 0
    for _ in range(80):
        groups, rating, cfg = _random_case(rng)
        base = bases[rng.choice(list(bases))]
        old_texts, old_img = _texts_and_image(old_render_badge_groups, base, groups, rating, cfg)
        new_texts, new_img = _texts_and_image(render_badge_groups, base, groups, rating, cfg)
        wanted = sum(len(label.split()) for g in [*groups, rating] if g for label in g.labels)
        if "…" not in "".join(old_texts) and sum(len(t.split()) for t in old_texts) == wanted:
            clean += 1
            assert old_texts == new_texts
            assert hashlib.sha256(old_img.tobytes()).digest() == hashlib.sha256(new_img.tobytes()).digest()
        else:
            hid += 1
            # The other direction: where the old row hid, the new render differs.
            assert old_img.tobytes() != new_img.tobytes()
    assert clean >= 30 and hid >= 30, (clean, hid)  # 37 / 43 with this seed


@pytest.mark.parametrize("size", SIZES)
def test_scope_aspect_with_padding_off_is_contained(size):
    """The shape that failed before P7: 2.39:1, normalize_portrait off (192 of 256 at tv_plus)."""
    canvas = canvas_for(("2.39:1", 239, 100), 1000, False)
    for (bp, rp), tag_set in itertools.product(itertools.product(CORNERS, CORNERS), TAG_SETS):
        cfg = ImageConfig(badge_size=size, badge_position=bp, rating_position=rp, normalize_portrait=False)
        groups, rating = badge_groups(TAG_SETS[tag_set], cfg)
        c = check(render(canvas, groups, rating, cfg))
        assert not {k: c[k] for k in VIOLATIONS if c[k]}, (size, bp, rp, tag_set, c)
        assert c["hook_mismatch"] == 0


@pytest.mark.parametrize("tag_set", list(TAG_SETS))
def test_every_hidden_badge_is_counted_and_no_ellipsis_is_drawn(tag_set):
    for width, size in itertools.product((300, 1000), SIZES):
        cfg = ImageConfig(badge_size=size)
        groups, rating = badge_groups(TAG_SETS[tag_set], cfg)
        r = render((width, width * 3 // 2), groups, rating, cfg)
        c = check(r)
        assert c["miscounted"] == 0 and c["ellipsis"] == 0 and c["order"] == 0, c
        assert not any("…" in p.text for p in r.pills)
        if tag_set == "max":
            assert c["tokens_hidden"] > 0 and c["counted"] == c["tokens_hidden"]


def test_a_group_takes_at_most_two_rows():
    cfg = ImageConfig(badge_size="tv_plus")
    groups, rating = badge_groups(TAG_SETS["max"], cfg)
    r = render((1000, 1500), groups, rating, cfg)
    for g in groups:
        rows = {p.rect[1] for p in r.pills if p.kind == "tags" and p.fill == g.fill_color}
        assert 1 <= len(rows) <= 2, (g.fill_color, rows)
    subs = [p.text for p in r.pills if p.fill == ImageConfig().sub_badge_color]
    assert subs[-1].startswith("+"), subs


def test_p99_shows_every_badge_by_wrapping():
    """The roadmap's case for (d): p99 (SRT + 12 languages) wraps rather than cuts.

    Its `--budget` table: the subtitles need 2 rows at desktop and tv, 3 at
    tv_plus -- so tv_plus shows two rows of them and counts the rest."""
    for size in SIZES:
        cfg = ImageConfig(badge_size=size)
        groups, rating = badge_groups(TAG_SETS["p99"], cfg)
        c = check(render((1000, 1500), groups, rating, cfg))
        if size == "tv_plus":
            assert 0 < c["tokens_hidden"] == c["counted"], c
        else:
            assert c["tokens_hidden"] == 0, (size, c)


def test_preferred_languages_survive_the_budget():
    langs = LANGS[:30]
    info = MediaInfo(
        resolution="1080p",
        languages=[],
        raw_audio_langs=[],
        video_codec="H.264",
        hdr_type=None,
        audio_tracks=[AudioTrack("EN", "AAC")],
        subtitle_tracks=[SubTrack(code, "PGS", True) for code in langs],
    )
    cfg = AppConfig()
    cfg.image = cfg.image.model_copy(update={"badge_size": "tv_plus", "prefer_languages": ["KO", "HU"]})
    groups, rating = _make_badge_groups(info, None, cfg)
    r = render((600, 900), groups, rating, cfg.image)
    c = check(r)
    assert c["tokens_hidden"] > 0 and c["miscounted"] == 0
    shown = [p.text for p in r.pills if p.fill == cfg.image.sub_badge_color]
    assert shown[0].startswith("PGS KO HU"), shown


def test_a_two_word_codec_is_never_split():
    """`TrueHD Atmos` is one badge: it never ends a row as `TrueHD` with `Atmos ...` below."""
    group = BadgeGroup(["DD+ EN", "TrueHD Atmos " + " ".join(LANGS[:20])], ImageConfig().audio_badge_color)
    for width in range(300, 1001, 50):
        for size in SIZES:
            cfg = ImageConfig(badge_size=size)
            r = render((width, width * 3 // 2), [group], None, cfg)
            texts = [p.text for p in r.pills]
            assert "TrueHD" not in texts and not any(t.startswith("Atmos") for t in texts), texts
            assert check(r)["miscounted"] == 0
    assert atoms("TrueHD Atmos EN") == ov._atoms("TrueHD Atmos EN") == ["TrueHD Atmos", "EN"]


def test_groups_with_no_row_are_counted_in_their_own_colour():
    """A canvas with fewer rows than groups: the lowest-priority group (subtitles)
    loses its row first and is counted, in its colour, on the last row drawn."""
    cfg = ImageConfig(badge_size="tv_plus", normalize_portrait=False)
    groups, rating = badge_groups(TAG_SETS["p50"], cfg)
    p = _compute_layout_params(1000, cfg)
    pitch = _measure_group_height(p["font_size"], p["pad_v"]) + p["row_gap"]
    height = 2 * p["margin"] + 3 * pitch - p["row_gap"]  # rating + two tag rows
    r = render((1000, height), groups, rating, cfg)
    c = check(r)
    assert not any(c[k] for k in VIOLATIONS), c
    subs = [p.text for p in r.pills if p.fill == ImageConfig().sub_badge_color]
    assert subs == ["+2"], subs  # SRT EN
    assert c["counted"] == c["tokens_hidden"] == 2


def test_wrapped_rows_read_top_to_bottom_at_every_corner():
    cfg_sizes = ImageConfig(badge_size="tv_plus")
    groups, _ = badge_groups(TAG_SETS["p99"], cfg_sizes)
    for corner in CORNERS:
        cfg = ImageConfig(badge_size="tv_plus", badge_position=corner)
        r = render((1000, 1500), groups, None, cfg)
        subs = [p for p in r.pills if p.fill == cfg.sub_badge_color]
        assert len({p.rect[1] for p in subs}) == 2
        assert [p.rect[1] for p in subs] == sorted(p.rect[1] for p in subs)
        assert subs[0].text.startswith("SRT EN")


def test_the_reference_copy_is_still_the_pre_p7_code():
    """The reference must keep drawing the old `…` pill, or it references nothing."""
    cfg = ImageConfig(badge_size="tv_plus")
    groups, rating = badge_groups(TAG_SETS["p99.9"], cfg)
    texts, _ = _texts_and_image(old_render_badge_groups, Image.new("RGBA", (300, 450)), groups, rating, cfg)
    assert any("…" in t for t in texts), texts
    _ = (_GLOW_MARGIN, _load_font, _place_pill)  # the reference's own dependencies


def test_a_label_with_legacy_numeric_languages_is_counted_exactly():
    """A stale index row can still hold numeric "languages" (`10`, `12`) from file
    names. Split mid-label, a continuation pill such as `AR 10 UK` must not be
    read back as one head: the renderer carries each pill's badges instead."""
    codes = LANGS[:9] + ["10"] + LANGS[9:17] + ["12"] + LANGS[17:22] + ["11"]
    group = BadgeGroup(["SRT " + " ".join(codes)], ImageConfig().sub_badge_color)
    for width, size in itertools.product((300, 600, 1000, 2000), SIZES):
        cfg = ImageConfig(badge_size=size)
        c = check(render((width, width * 3 // 2), [group], None, cfg))
        assert c["miscounted"] == 0 and c["order"] == 0, (width, size, c)


def test_trimming_for_the_count_pill_counts_carried_badges():
    """`_close_row()` trims by the badges each pill carries. Trimmed to nothing, a
    continuation pill `AR 10` is two badges, though its text reads as one head."""
    font = _load_font(40)
    fill = ImageConfig().sub_badge_color
    pill = ("AR 10", ov._pill_width(font, "AR 10", 8), fill, "#ffffff", ("AR", "10"))
    plus = ov._pill_width(font, "+5", 8)
    row, left = ov._close_row([pill], plus, [(3, fill, "#ffffff")], font, 8, 10)
    assert [p[0] for p in row] == ["+5"] and left == []
