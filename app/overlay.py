from __future__ import annotations

import io
import logging
import shutil
import string
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .config import ImageConfig
from .wcag import LIGHT_DARK_MIDPOINT, LINEAR_8BIT, relative_luminance

log = logging.getLogger(__name__)

_FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
]

_font_cache: dict[int, ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if size in _font_cache:
        return _font_cache[size]
    for path in _FONT_PATHS:
        if Path(path).exists():
            try:
                font = ImageFont.truetype(path, size)
                _font_cache[size] = font
                return font
            except Exception:  # noqa: S110
                pass
    font = ImageFont.load_default()
    _font_cache[size] = font
    return font


# ── Pill tile cache ──────────────────────────────────────────────────────────
_PILL_CACHE: dict[tuple, Image.Image] = {}
_GLOW_MARGIN = 14
_GLOW_EXPAND = 4
_GLOW_BLUR = 6

_REFERENCE_WIDTH = 1000
_BADGE_SIZE_PX: dict[str, int] = {"desktop": 56, "tv": 72, "tv_plus": 88}


# Roadmap P6: with image.adapt_badge_colors on, a badge row whose poster region
# is on the LABEL's side of this relative luminance (lighter than it, for a light
# label) is drawn in the backup palette instead. Measured, not picked: it is the
# lightest flat region over which the shipped main palette still renders AAA
# (7:1) at 65% opacity -- and that boundary barely moves with opacity (grey 94
# at 50%, 98 at 65%, 109 at 80%), which is what lets one threshold serve the
# whole slider. scripts/measure_adaptive_palette.py re-derives it.
# PROVISIONAL (HOLD, roadmap B21): measured with B1's instrument, which the
# poster path does not match below 100% -- _render_group() pastes each tile with
# itself as the mask, so the fill lands at a^3 over (1 - a^2) of the poster.
# Re-derive once B21 is resolved.
ADAPT_LUMINANCE_THRESHOLD = 0.12


@dataclass
class BadgeGroup:
    labels: list[str]
    fill_color: str
    text_color: str = "#ffffff"
    # Roadmap P6: the fill this row uses instead when the poster under it would
    # wash the main one out. Read only with image.adapt_badge_colors on.
    backup_fill_color: str | None = None


def prefer_order(langs: list[str], prefer: list[str]) -> list[str]:
    """``langs`` with the codes in ``prefer`` first, in ``prefer``'s order.

    Everything else follows in its original order. Order only: the result holds
    exactly the items of ``langs`` (roadmap P7, ``image.prefer_languages``).
    """
    if not prefer:
        return list(langs)
    rank = {code: i for i, code in enumerate(prefer)}
    # sorted() is stable, so unlisted codes keep today's relative order.
    return sorted(langs, key=lambda code: rank.get(code, len(prefer)))


def order_pills_by_language(pills: dict[str, list[str]], prefer: list[str]) -> list[str]:
    """Pill labels for ``{codec_or_format: [langs]}``, preferred languages first.

    Within each pill its languages are put in ``prefer`` order; the pills are
    then ranked by the best-preferred language each carries, pills carrying none
    keeping today's order after them. With ``prefer`` empty this is exactly the
    label list the row builders produced before P7.
    """
    labels: list[tuple[int, str]] = []
    for key, langs in pills.items():
        ordered = prefer_order(langs, prefer)
        rank = min((prefer.index(c) for c in ordered if c in prefer), default=len(prefer))
        labels.append((rank, f"{key} {' '.join(ordered)}" if ordered else key))
    return [label for _, label in sorted(labels, key=lambda t: t[0])]


def clear_pill_cache() -> None:
    _PILL_CACHE.clear()


def _pill_tile(
    text: str,
    fill_hex: str,
    text_hex: str,
    alpha: int,
    font_size: int,
    pad_h: int,
    pad_v: int,
) -> Image.Image:
    # Every argument, not just the ones that looked like "appearance". The
    # padding changes the tile's size, and _compute_layout_params() rounds
    # font_size and padding out of the poster width independently -- 494px and
    # 501px both give font_size 36 with pad_v 2 and 3 (roadmap B3). A key that
    # is not a superset of the renderer's inputs serves the first poster's tile
    # to every later one that hashes to it.
    key = (text, fill_hex, text_hex, alpha, font_size, pad_h, pad_v)
    if key in _PILL_CACHE:
        return _PILL_CACHE[key]
    tile = _render_pill_tile(text, fill_hex, text_hex, alpha, font_size, pad_h, pad_v)
    _PILL_CACHE[key] = tile
    return tile


def _render_pill_tile(
    text: str,
    fill_hex: str,
    text_hex: str,
    alpha: int,
    font_size: int,
    pad_h: int,
    pad_v: int,
) -> Image.Image:
    """Render one pill tile, bypassing the cache.

    `_pill_tile()` is the only caller on the poster path. The contrast check in
    `app.contrast` calls this directly, so measuring a colour the operator is
    only trying out neither reads a cached tile nor leaves one behind.
    """
    font = _load_font(font_size)
    fill_rgb = _parse_color(fill_hex)
    text_rgb = _parse_color(text_hex)

    ref_h = font.getbbox("AgfpQ")[3] - font.getbbox("AgfpQ")[1]
    pill_h = ref_h + pad_v * 2
    bbox = font.getbbox(text)
    pill_w = bbox[2] - bbox[0] + pad_h * 2

    gm = _GLOW_MARGIN
    tile = Image.new("RGBA", (pill_w + 2 * gm, pill_h + 2 * gm), (0, 0, 0, 0))

    glow = Image.new("RGBA", tile.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.rounded_rectangle(
        [(gm - _GLOW_EXPAND, gm - _GLOW_EXPAND), (gm + pill_w + _GLOW_EXPAND, gm + pill_h + _GLOW_EXPAND)],
        radius=8 + _GLOW_EXPAND,
        fill=(255, 255, 255, 210),
    )
    glow = glow.filter(ImageFilter.GaussianBlur(radius=_GLOW_BLUR))

    # Clear the glow out from under the pill footprint. Behind a translucent
    # fill the glow is what the viewer sees THROUGH the badge, which pinned
    # every badge's rendered contrast near white whatever the poster was
    # (roadmap B1: 3.7-5.3:1 against text the config claimed was 9.4-11.0:1).
    # Punched out, the glow is what it was meant to be -- a halo AROUND the
    # pill -- and the rendered fill is the configured colour. Deflated by 1px
    # so the pill's own antialiased edge still lands on glow, not a hard cut.
    # NOTE: filter() returns a new image, so the Draw handle must be rebound.
    gd = ImageDraw.Draw(glow)
    gd.rounded_rectangle(
        [(gm + 1, gm + 1), (gm + pill_w - 1, gm + pill_h - 1)],
        radius=7,
        fill=(0, 0, 0, 0),
    )

    pill = Image.new("RGBA", tile.size, (0, 0, 0, 0))
    pd = ImageDraw.Draw(pill)
    pd.rounded_rectangle([(gm, gm), (gm + pill_w, gm + pill_h)], radius=8, fill=(*fill_rgb, alpha))
    text_h = bbox[3] - bbox[1]
    ty = gm + pad_v + (ref_h - text_h) // 2 - bbox[1]
    pd.text((gm + pad_h - bbox[0], ty), text, font=font, fill=(*text_rgb, 255))

    return Image.alpha_composite(glow, pill)


def badge_alpha(opacity: float) -> int:
    """The fill alpha a `badge_opacity` renders at. One mapping, shared with
    `app.contrast`, so the measured ratio is the ratio of what renders."""
    return int(opacity * 255)


def _parse_color(hex_str: str) -> tuple[int, int, int]:
    """``#rrggbb`` to an RGB tuple. Anything else raises ValueError.

    It used to return black for every other spelling, silently (roadmap B6).
    ImageConfig now normalises every colour to ``#rrggbb`` at validation, so a
    value reaching here in another form is a bug upstream, and a loud one is
    better than a poster full of black badges.
    """
    if not (len(hex_str) == 7 and hex_str[0] == "#" and all(c in string.hexdigits for c in hex_str[1:])):
        raise ValueError(f"not a #rrggbb colour: {hex_str!r}")
    return (int(hex_str[1:3], 16), int(hex_str[3:5], 16), int(hex_str[5:7], 16))


def _find_image(folder: Path, targets: list[str]) -> Path | None:
    for name in targets:
        p = folder / name
        if p.exists():
            return p
    return None


def _backup_path(image_path: Path, suffix: str) -> Path:
    return image_path.with_name(image_path.name + suffix)


def _compute_layout_params(img_w: int, cfg: ImageConfig) -> dict:
    scale = img_w / _REFERENCE_WIDTH
    base_px = _BADGE_SIZE_PX.get(cfg.badge_size, _BADGE_SIZE_PX["tv"])
    return {
        "font_size": max(8, round(base_px * scale)),
        "pad_h": max(3, round(8 * scale)),
        "pad_v": max(2, round(5 * scale)),
        "col_gap": max(4, round(10 * scale)),
        "row_gap": max(4, round(12 * scale)),
        "margin": max(4, round(12 * scale)),
        "alpha": badge_alpha(cfg.badge_opacity),
    }


def region_luminance(img: Image.Image, box: tuple[int, int, int, int]) -> float | None:
    """Mean WCAG relative luminance of ``img`` inside ``box`` (clipped to the image).

    Averaged in linear light, per channel, from the region's histogram -- exact,
    and independent of the region's size. None if the box misses the image.
    """
    x0, y0 = max(0, box[0]), max(0, box[1])
    x1, y1 = min(img.width, box[2]), min(img.height, box[3])
    if x1 <= x0 or y1 <= y0:
        return None
    hist = img.crop((x0, y0, x1, y1)).convert("RGB").histogram()
    n = (x1 - x0) * (y1 - y0)
    r, g, b = (
        sum(count * LINEAR_8BIT[v] for v, count in enumerate(hist[i * 256 : (i + 1) * 256])) / n for i in range(3)
    )
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def adapted_fill(
    poster: Image.Image,
    box: tuple[int, int, int, int],
    fill_color: str,
    backup_fill_color: str,
    text_color: str,
) -> str:
    """The fill a badge row gets over ``box`` of ``poster`` (roadmap P6).

    The backup palette is for the regions that wash the label out: under a
    translucent fill the poster pulls the rendered colour toward itself, which
    hurts exactly when the poster is on the label's side of mid-grey -- light
    posters for a light label, dark ones for a dark label. One threshold,
    ADAPT_LUMINANCE_THRESHOLD, decides which side the region is on.
    """
    lum = region_luminance(poster, box)
    if lum is None:
        return fill_color
    region_is_light = lum >= ADAPT_LUMINANCE_THRESHOLD
    label_is_light = relative_luminance(_parse_color(text_color)) >= LIGHT_DARK_MIDPOINT
    return backup_fill_color if region_is_light == label_is_light else fill_color


def _measure_group_height(
    font_size: int,
    pad_v: int,
) -> int:
    """Return the pixel height of one badge row."""
    font = _load_font(font_size)
    ref_h = font.getbbox("AgfpQ")[3] - font.getbbox("AgfpQ")[1]
    return ref_h + pad_v * 2


def _truncate_label(label: str, max_w: int, font, pad_h: int) -> str:
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


def _render_group(
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
    backup_fill: str | None = None,
    poster: Image.Image | None = None,
) -> tuple[Image.Image, list[tuple[int, int, int, int]]]:
    """Render one badge group as a single row onto base. Overflow replaced with … pill.

    ``reserve_w`` narrows the row by that many pixels from the far side, for a row
    that shares its band with another group across the poster (roadmap B10).
    With ``backup_fill`` and ``poster`` given, the row's fill is chosen by the
    poster region the laid-out row covers (roadmap P6).
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
        badge = _truncate_label(badge, max_row_w, font, pad_h)
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

    # Roadmap P6: sample exactly the strip the row's pills will cover, from the
    # bare poster -- not `base`, where a neighbouring row's glow may already sit.
    # The choice reaches _pill_tile() as its fill_hex argument, so it is part of
    # the cache key like every other input and a tile rendered over a light
    # region is never served over a dark one.
    if backup_fill is not None and poster is not None:
        fill_color = adapted_fill(poster, (x, y, x + row_w, y + pill_h), fill_color, backup_fill, text_color)

    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    gm = _GLOW_MARGIN
    rects = []
    for badge, bw in row:
        tile = _pill_tile(badge, fill_color, text_color, alpha, font_size, pad_h, pad_v)
        overlay.paste(tile, (x - gm, y - gm), tile)
        rects.append((x, y, bw, pill_h))
        x += bw + col_gap

    return Image.alpha_composite(base, overlay), rects


def render_badge_groups(
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
    # Roadmap P6. Off (the default) passes no backup, so no row samples the
    # poster and every render is byte-identical to one made before P6 existed.
    # Nor at 100% opacity: an opaque badge hides the poster, so the region
    # under it cannot change what renders -- which is what the checkbox says.
    adapt = cfg.adapt_badge_colors and p["alpha"] < 255
    poster = result

    def backup_of(group: BadgeGroup) -> dict:
        return {"backup_fill": group.backup_fill_color, "poster": poster} if adapt else {}

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
        result, rating_rects = _render_group(
            result,
            rating_group.labels,
            cfg.rating_position,
            rating_group.fill_color,
            rating_group.text_color,
            *common,
            **backup_of(rating_group),
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
        result, rects = _render_group(
            result,
            group.labels,
            cfg.badge_position,
            group.fill_color,
            group.text_color,
            *common,
            y_offset=cumulative_offset,
            reserve_w=rating_w + p["col_gap"] if shares_band else 0,
            **backup_of(group),
        )
        if placed is not None:
            placed.extend(("tags", r) for r in rects)
        cumulative_offset += row_h + p["row_gap"]

    return result


def _make_placeholder(width: int, height: int, font_size: int) -> Image.Image:
    base = Image.new("RGBA", (width, height))
    draw = ImageDraw.Draw(base)
    for y in range(height):
        t = y / height
        r = int(28 + (45 - 28) * t)
        g = int(35 + (52 - 35) * t)
        b = int(51 + (80 - 51) * t)
        draw.line([(0, y), (width, y)], fill=(r, g, b, 255))
    label_font = _load_font(max(font_size - 2, 10))
    draw.text((width // 2, height // 2), "PREVIEW", fill=(80, 95, 120, 160), anchor="mm", font=label_font)
    return base


_PORTRAIT_RATIO = 2 / 3  # target width:height for portrait posters


def _pad_to_portrait(img: Image.Image) -> Image.Image:
    """Extend a square or landscape image downward to a 2:3 portrait ratio.

    The added area is filled with a blurred sample of the bottom edge so the
    extension blends naturally rather than showing a hard black bar.
    """
    w, h = img.size
    target_h = round(w / _PORTRAIT_RATIO)
    if target_h <= h:
        return img  # already portrait

    pad_h = target_h - h
    # Sample a thin strip from the bottom of the image and blur it to fill the pad
    strip_src_h = min(h, max(4, round(h * 0.05)))  # up to 5% of image height
    strip = img.crop((0, h - strip_src_h, w, h)).resize((w, pad_h), Image.LANCZOS)
    strip = strip.filter(ImageFilter.GaussianBlur(radius=max(8, pad_h // 6)))

    canvas = Image.new("RGBA", (w, target_h), (0, 0, 0, 255))
    canvas.paste(img, (0, 0))
    canvas.paste(strip, (0, h))
    return canvas


def apply_overlay(
    item_folder: Path,
    groups: list[BadgeGroup],
    rating_group: BadgeGroup | None,
    cfg: ImageConfig,
) -> Path | None:
    """
    Find poster image in item_folder, back up original on first run,
    render badge overlay, and save back in place.
    Returns the modified image path, or None if no image found.
    """
    image_path = _find_image(item_folder, cfg.targets)
    if image_path is None:
        log.debug("No poster image found in %s", item_folder)
        return None

    backup = _backup_path(image_path, cfg.backup_suffix)
    if not backup.exists():
        shutil.copy2(image_path, backup)
        log.debug("Backed up original: %s", backup)

    try:
        base = Image.open(backup).convert("RGBA")
    except Exception as exc:
        log.warning("Cannot open image %s: %s", backup, exc)
        return None

    w, h = base.size
    if w > 10000 or h > 10000:
        log.warning("Image too large to overlay (%dx%d): %s", w, h, image_path)
        return None

    if cfg.normalize_portrait and w / h > _PORTRAIT_RATIO + 0.05:
        log.debug("Padding %dx%d image to portrait: %s", w, h, image_path)
        base = _pad_to_portrait(base)

    composited = render_badge_groups(base, groups, rating_group, cfg).convert("RGB")

    try:
        composited.save(str(image_path), format="JPEG", quality=92)
    except Exception:
        composited.save(str(image_path))

    log.info("Overlay applied: %s", image_path)
    return image_path


def generate_preview_bytes(
    groups: list[BadgeGroup],
    rating_group: BadgeGroup | None,
    cfg: ImageConfig,
    width: int = 280,
    height: int = 420,
    base_image_bytes: bytes | None = None,
) -> bytes:
    """Generate a poster JPEG with badge overlay for UI preview."""
    font_size = _BADGE_SIZE_PX.get(cfg.badge_size, _BADGE_SIZE_PX["tv"])

    if base_image_bytes:
        try:
            base = Image.open(io.BytesIO(base_image_bytes)).convert("RGBA")
            bw, bh = base.size
            if bw > 10000 or bh > 10000:
                log.warning("Preview image too large (%dx%d), using placeholder", bw, bh)
                base = _make_placeholder(width, height, font_size)
            else:
                base = base.resize((width, height), Image.LANCZOS)
        except Exception:
            base = _make_placeholder(width, height, font_size)
    else:
        base = _make_placeholder(width, height, font_size)

    result = render_badge_groups(base, groups, rating_group, cfg).convert("RGB")
    buf = io.BytesIO()
    result.save(buf, format="JPEG", quality=88)
    return buf.getvalue()
