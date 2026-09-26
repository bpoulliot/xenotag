"""WCAG contrast of a badge's label against its *rendered* fill (roadmap B1, B2).

The one implementation. `scripts/measure_badge_contrast.py` prints from it and
the Settings page's contrast warnings are computed by it, through
`GET /api/badge-contrast`, so the number beside a colour picker is the number
the probe prints for the same config.

Why render instead of doing the arithmetic on the hex: at `badge_opacity`
below 1.0 the poster shows through the fill, so the colour behind the label is
not the configured colour. B1 was exactly that mistake -- the config quoted
the opaque hex's ratio while the badge rendered at roughly half of it. So this
module composites a real pill tile over flat backdrops and samples the pixels.

At opacity < 1.0 there is no single ratio: it depends on the poster. The
figure reported is the WORST over black, white and grey backdrops. For light
text on a dark fill the white backdrop is the worst possible poster, and for
dark text on a light fill black is; for a text colour whose luminance falls
BETWEEN the fill-over-black and fill-over-white renders, some mid-tone poster
does worse than any of the three (roadmap B2 records the measurement).
"""

from __future__ import annotations

from PIL import Image

from .config import ImageConfig
from .overlay import _GLOW_MARGIN, _load_font, _parse_color, _render_pill_tile, badge_alpha

# WCAG 2.x thresholds for normal-size text. The large-text exemption (3:1 /
# 4.5:1) is not claimed: badges are sized against a 1000px reference poster,
# and at the thumbnail sizes a library grid shows posters at, the label is
# nowhere near 18pt.
AA = 4.5
AAA = 7.0

BACKDROPS: dict[str, tuple[int, int, int]] = {
    "black": (0, 0, 0),
    "white": (255, 255, 255),
    "grey": (128, 128, 128),
}

# (category, ImageConfig colour field, ImageConfig visibility field)
BADGES: tuple[tuple[str, str, str], ...] = (
    ("video", "video_badge_color", "show_video_badges"),
    ("audio", "audio_badge_color", "show_audio_badges"),
    ("sub", "sub_badge_color", "show_sub_badges"),
    ("rating", "rating_badge_color", "show_rating_badge"),
)

# Pixels trimmed off every side of the pill rectangle before sampling, to stay
# clear of the antialiased rounded-rectangle boundary and the glow bleeding in.
# x=_GLOW_MARGIN is the pill's EDGE: sampling there flatters by about a point.
_INTERIOR_INSET = 5


# ── WCAG 2.x relative luminance / contrast ratio ─────────────────────────────
def _linearize(channel_8bit: int) -> float:
    c = channel_8bit / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(rgb: tuple[int, int, int]) -> float:
    r, g, b = (_linearize(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    la, lb = relative_luminance(a), relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def format_ratio(ratio: float) -> str:
    """How a ratio is written everywhere it is shown -- the probe and the UI."""
    return f"{ratio:.2f}"


def grade(ratio: float) -> str:
    """'AAA', 'AA' or 'fail', judged on the unrounded ratio."""
    if ratio >= AAA:
        return "AAA"
    if ratio >= AA:
        return "AA"
    return "fail"


# ── Rendering + sampling ─────────────────────────────────────────────────────
def pill_box(text: str, font_size: int, pad_h: int, pad_v: int) -> tuple[int, int]:
    """Return (pill_w, pill_h) exactly as `_render_pill_tile()` computes them."""
    font = _load_font(font_size)
    ref_h = font.getbbox("AgfpQ")[3] - font.getbbox("AgfpQ")[1]
    bbox = font.getbbox(text)
    return (bbox[2] - bbox[0] + pad_h * 2, ref_h + pad_v * 2)


def render_over(
    backdrop: tuple[int, int, int],
    fill_hex: str,
    text_hex: str = "#ffffff",
    *,
    alpha: int = 165,
    font_size: int = 56,
    pad_h: int = 8,
    pad_v: int = 5,
    text: str = "1080p",
) -> tuple[Image.Image, tuple[int, int]]:
    """Composite a real pill tile onto a flat backdrop. Returns (image, pill_wh).

    The tile comes from the uncached renderer, so a measurement never reads a
    tile rendered for something else and never leaves one in `_PILL_CACHE`.
    """
    tile = _render_pill_tile(text, fill_hex, text_hex, alpha, font_size, pad_h, pad_v)
    base = Image.new("RGBA", tile.size, (*backdrop, 255))
    base.alpha_composite(tile)
    return base.convert("RGB"), pill_box(text, font_size, pad_h, pad_v)


def sample_interior(img: Image.Image, pill_wh: tuple[int, int]) -> tuple[int, int, int]:
    """Modal pixel of the pill interior, inset clear of the antialiased edge."""
    pill_w, pill_h = pill_wh
    gm = _GLOW_MARGIN
    inset = min(_INTERIOR_INSET, max(1, pill_w // 4), max(1, pill_h // 4))
    box = (gm + inset, gm + inset, gm + pill_w - inset, gm + pill_h - inset)
    region = img.crop(box)
    colors = region.getcolors(maxcolors=region.width * region.height)
    if not colors:
        raise RuntimeError("interior region is empty")
    return max(colors, key=lambda c: c[0])[1]


def rendered_ratio(
    backdrop: tuple[int, int, int],
    fill_hex: str,
    text_hex: str = "#ffffff",
    **kw: object,
) -> float:
    img, pill_wh = render_over(backdrop, fill_hex, text_hex, **kw)  # type: ignore[arg-type]
    return contrast_ratio(_parse_color(text_hex), sample_interior(img, pill_wh))


# ── The whole palette ────────────────────────────────────────────────────────
def badge_contrast(cfg: ImageConfig) -> dict:
    """Rendered contrast of every badge in `cfg` against its configured text colour.

    Per badge: the opaque-hex ratio (for reference only), the rendered ratio on
    each backdrop, and the worst of those, which is the figure to judge by.
    `worst` at the top level is the worst SHOWN badge, or None if none is.
    """
    alpha = badge_alpha(cfg.badge_opacity)
    text_rgb = _parse_color(cfg.badge_text_color)
    badges: dict[str, dict] = {}
    for name, colour_field, show_field in BADGES:
        fill = getattr(cfg, colour_field)
        rendered = {bd: rendered_ratio(rgb, fill, cfg.badge_text_color, alpha=alpha) for bd, rgb in BACKDROPS.items()}
        worst_bd = min(rendered, key=lambda bd: rendered[bd])
        worst = rendered[worst_bd]
        opaque = contrast_ratio(text_rgb, _parse_color(fill))
        badges[name] = {
            "color": fill,
            "shown": bool(getattr(cfg, show_field)),
            "opaque": opaque,
            "opaque_text": format_ratio(opaque),
            "rendered": rendered,
            "worst": worst,
            "worst_text": format_ratio(worst),
            # None when every backdrop renders the same (an opaque fill): the
            # figure then holds on any poster, not on a particular one.
            "worst_backdrop": (None if len({format_ratio(r) for r in rendered.values()}) == 1 else worst_bd),
            "grade": grade(worst),
        }

    shown = [(n, b) for n, b in badges.items() if b["shown"]]
    overall = None
    if shown:
        name, b = min(shown, key=lambda nb: nb[1]["worst"])
        overall = {
            "badge": name,
            "ratio": b["worst"],
            "text": b["worst_text"],
            "backdrop": b["worst_backdrop"],
            "grade": b["grade"],
        }
    return {
        "opacity": cfg.badge_opacity,
        "alpha": alpha,
        "text_color": cfg.badge_text_color,
        "thresholds": {"AA": AA, "AAA": AAA},
        "backdrops": list(BACKDROPS),
        "badges": badges,
        "worst": overall,
    }
