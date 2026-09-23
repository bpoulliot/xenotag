from __future__ import annotations

import io
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .config import ImageConfig

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


@dataclass
class BadgeGroup:
    labels: list[str]
    fill_color: str
    text_color: str = "#ffffff"


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
    key = (text, fill_hex, text_hex, alpha, font_size)
    if key in _PILL_CACHE:
        return _PILL_CACHE[key]

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

    tile = Image.alpha_composite(glow, pill)
    _PILL_CACHE[key] = tile
    return tile


def _parse_color(hex_str: str) -> tuple[int, int, int]:
    h = hex_str.lstrip("#")
    if len(h) != 6:
        return (0, 0, 0)
    try:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except ValueError:
        return (0, 0, 0)


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
        "alpha": int(cfg.badge_opacity * 255),
    }


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
) -> Image.Image:
    """Render one badge group as a single row onto base. Overflow replaced with … pill."""
    if not labels:
        return base

    font = _load_font(font_size)
    img_w, img_h = base.size

    ref_h = font.getbbox("AgfpQ")[3] - font.getbbox("AgfpQ")[1]
    pill_h = ref_h + pad_v * 2
    max_row_w = img_w - 2 * margin

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
        return base

    row_w = sum(bw for _, bw in row) + col_gap * (len(row) - 1)
    is_bottom = "bottom" in position
    is_right = "right" in position

    y = (img_h - margin - pill_h - y_offset) if is_bottom else (margin + y_offset)
    x = (img_w - margin - row_w) if is_right else margin

    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    gm = _GLOW_MARGIN
    for badge, bw in row:
        tile = _pill_tile(badge, fill_color, text_color, alpha, font_size, pad_h, pad_v)
        overlay.paste(tile, (x - gm, y - gm), tile)
        x += bw + col_gap

    return Image.alpha_composite(base, overlay)


def render_badge_groups(
    base: Image.Image,
    groups: list[BadgeGroup],
    rating_group: BadgeGroup | None,
    cfg: ImageConfig,
) -> Image.Image:
    """Composite all badge groups onto base and return the result."""
    img_w, _ = base.size
    p = _compute_layout_params(img_w, cfg)

    result = base.convert("RGBA") if base.mode != "RGBA" else base

    # Main badge groups rendered at cfg.badge_position, bottom-to-top
    cumulative_offset = 0
    for group in reversed(groups):
        if not group.labels:
            continue
        result = _render_group(
            result,
            group.labels,
            cfg.badge_position,
            group.fill_color,
            group.text_color,
            p["alpha"],
            p["font_size"],
            p["pad_h"],
            p["pad_v"],
            p["col_gap"],
            p["margin"],
            y_offset=cumulative_offset,
        )
        group_h = _measure_group_height(p["font_size"], p["pad_v"])
        cumulative_offset += group_h + p["row_gap"]

    # Top-left rating (independent)
    if rating_group and rating_group.labels:
        result = _render_group(
            result,
            rating_group.labels,
            "top-left",
            rating_group.fill_color,
            rating_group.text_color,
            p["alpha"],
            p["font_size"],
            p["pad_h"],
            p["pad_v"],
            p["col_gap"],
            p["margin"],
        )

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
