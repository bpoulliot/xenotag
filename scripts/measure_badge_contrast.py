#!/usr/bin/env python3
"""Measure the *rendered* WCAG contrast of badge text against the badge fill.

Roadmap item B1. The config's per-colour comments quote the contrast of the
opaque hex against white text. That is not what renders: `_pill_tile()` draws
the pill at `badge_opacity` alpha, so whatever is behind it shows through. This
script renders real `_pill_tile()` output over flat backdrops and samples the
badge interior, so the number it prints is the number a viewer actually sees.

    python3 scripts/measure_badge_contrast.py                 # shipped defaults
    python3 scripts/measure_badge_contrast.py --config FILE   # a real config.yml
    python3 scripts/measure_badge_contrast.py --opacity 1.0   # what-if
    python3 scripts/measure_badge_contrast.py --self-test     # probe self-check

Sampling method: the pill rectangle inside the tile is known
(`_GLOW_MARGIN.._GLOW_MARGIN+pill_w/h`); it is inset by `_INTERIOR_INSET` px and
the *modal* pixel of that region is taken. The inset matters — x=_GLOW_MARGIN is
the antialiased left edge of the pill, not its interior, and sampling there
flatters the result by about a point. `--show-edge` prints both.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import ImageConfig  # noqa: E402
from app.overlay import _GLOW_MARGIN, _load_font, _parse_color, _pill_tile, clear_pill_cache  # noqa: E402

# Pixels trimmed off every side of the pill rectangle before sampling, to stay
# clear of the antialiased rounded-rectangle boundary and the glow bleeding in.
_INTERIOR_INSET = 5

BACKDROPS: dict[str, tuple[int, int, int]] = {
    "black": (0, 0, 0),
    "white": (255, 255, 255),
    "grey": (128, 128, 128),
}


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


# ── Rendering + sampling ─────────────────────────────────────────────────────
def _pill_box(text: str, font_size: int, pad_h: int, pad_v: int) -> tuple[int, int]:
    """Return (pill_w, pill_h) exactly as _pill_tile computes them."""
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
    """Composite a real `_pill_tile()` onto a flat backdrop. Returns (image, pill_wh)."""
    clear_pill_cache()  # the cache is keyed on appearance only; never reuse across runs
    tile = _pill_tile(text, fill_hex, text_hex, alpha, font_size, pad_h, pad_v)
    base = Image.new("RGBA", tile.size, (*backdrop, 255))
    base.alpha_composite(tile)
    return base.convert("RGB"), _pill_box(text, font_size, pad_h, pad_v)


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


def sample_edge(img: Image.Image, pill_wh: tuple[int, int]) -> tuple[int, int, int]:
    """The trap: x=_GLOW_MARGIN is the pill's antialiased left EDGE, not its interior."""
    _, pill_h = pill_wh
    return img.convert("RGB").getpixel((_GLOW_MARGIN, _GLOW_MARGIN + pill_h // 2))


def rendered_ratio(
    backdrop: tuple[int, int, int],
    fill_hex: str,
    text_hex: str = "#ffffff",
    **kw: object,
) -> float:
    img, pill_wh = render_over(backdrop, fill_hex, text_hex, **kw)  # type: ignore[arg-type]
    return contrast_ratio(_parse_color(text_hex), sample_interior(img, pill_wh))


# ── Report ───────────────────────────────────────────────────────────────────
def _badges(cfg: ImageConfig) -> list[tuple[str, str]]:
    return [
        ("video", cfg.video_badge_color),
        ("audio", cfg.audio_badge_color),
        ("sub", cfg.sub_badge_color),
        ("rating", cfg.rating_badge_color),
    ]


def report(cfg: ImageConfig, *, show_edge: bool = False) -> int:
    alpha = int(cfg.badge_opacity * 255)
    text_rgb = _parse_color(cfg.badge_text_color)
    print(f"badge_opacity={cfg.badge_opacity} (alpha={alpha})  text={cfg.badge_text_color}")
    print()
    header = f"| {'badge':<6} | {'opaque hex':>10} |" + "".join(f" {name:>8} |" for name in BACKDROPS)
    print(header)
    print("|" + "-" * 8 + "|" + "-" * 12 + "|" + ("-" * 10 + "|") * len(BACKDROPS))

    worst = float("inf")
    for name, hex_ in _badges(cfg):
        claimed = contrast_ratio(text_rgb, _parse_color(hex_))
        cells = []
        for bd in BACKDROPS.values():
            r = rendered_ratio(bd, hex_, cfg.badge_text_color, alpha=alpha)
            worst = min(worst, r)
            cells.append(f" {r:>7.1f}: |")
        print(f"| {name:<6} | {claimed:>9.1f}: |" + "".join(cells))
        if show_edge:
            img, wh = render_over(next(iter(BACKDROPS.values())), hex_, cfg.badge_text_color, alpha=alpha)
            edge = contrast_ratio(text_rgb, sample_edge(img, wh))
            interior = contrast_ratio(text_rgb, sample_interior(img, wh))
            print(f"|   └─ on black: interior {interior:.1f}:1 vs EDGE-SAMPLE {edge:.1f}:1 (the trap)")

    print()
    print(
        f"worst rendered ratio: {worst:.2f}:1   AA(4.5) {'PASS' if worst >= 4.5 else 'FAIL'}"
        f"   AAA(7.0) {'PASS' if worst >= 7.0 else 'FAIL'}"
    )
    return 0


# ── Self-test — must be able to fail in BOTH directions ──────────────────────
def self_test() -> int:
    """Check the probe can (a) confirm a good render and (b) catch a bad one.

    A probe that can only confirm what we expect confirms it whether or not it
    is true, so each block below is paired: one case the probe must call good
    and one it must call bad. If the probe were wired to the hex constants (the
    bug this whole item is about) the alpha cases would pass identically and
    ALPHA-BLIND would fire.
    """
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str) -> None:
        print(f"  {'ok  ' if ok else 'FAIL'}  {name}: {detail}")
        if not ok:
            failures.append(name)

    # 1. contrast_ratio against WCAG's own reference values
    check(
        "white-on-black is 21:1",
        abs(contrast_ratio((255, 255, 255), (0, 0, 0)) - 21.0) < 0.01,
        f"{contrast_ratio((255, 255, 255), (0, 0, 0)):.4f}",
    )
    check(
        "white-on-white is 1:1",
        abs(contrast_ratio((255, 255, 255), (255, 255, 255)) - 1.0) < 1e-6,
        f"{contrast_ratio((255, 255, 255), (255, 255, 255)):.4f}",
    )
    # #767676 is the canonical darkest grey that still clears AA on white.
    aa_grey = contrast_ratio((255, 255, 255), (0x76, 0x76, 0x76))
    check("#767676-on-white clears AA by a hair", 4.5 <= aa_grey < 4.6, f"{aa_grey:.3f}")
    # ...and one shade lighter must NOT clear it (the negative direction).
    near_miss = contrast_ratio((255, 255, 255), (0x78, 0x78, 0x78))
    check("#787878-on-white misses AA", near_miss < 4.5, f"{near_miss:.3f}")

    # 2. positive direction — at alpha=255 the render must MATCH the opaque hex,
    #    because an opaque pill hides the glow and the backdrop completely.
    for hex_ in ("#134e4a", "#1e3a8a", "#7c2d12", "#4c1d95"):
        claimed = contrast_ratio((255, 255, 255), _parse_color(hex_))
        for bd_name, bd in BACKDROPS.items():
            got = rendered_ratio(bd, hex_, alpha=255)
            check(
                f"opaque {hex_} on {bd_name} == hex ratio",
                abs(got - claimed) < 0.05,
                f"rendered {got:.2f}:1 vs hex {claimed:.2f}:1",
            )

    # 3. negative direction — at a low alpha the render must NOT match the hex.
    #    A probe reading the constants instead of the pixels would pass block 2
    #    and this one identically; only this block can catch it.
    for hex_ in ("#134e4a", "#4c1d95"):
        claimed = contrast_ratio((255, 255, 255), _parse_color(hex_))
        got = rendered_ratio((255, 255, 255), hex_, alpha=96)
        check(
            f"ALPHA-BLIND check: translucent {hex_} != hex ratio",
            got < claimed - 1.0,
            f"rendered {got:.2f}:1 is well below hex {claimed:.2f}:1",
        )

    # 4. the sampler itself: interior vs the edge trap must disagree, and the
    #    interior must be the darker (lower-contrast, less flattering) reading.
    img, wh = render_over((255, 255, 255), "#134e4a", alpha=165)
    interior = contrast_ratio((255, 255, 255), sample_interior(img, wh))
    edge = contrast_ratio((255, 255, 255), sample_edge(img, wh))
    check(
        "edge sample flatters the result",
        edge > interior + 0.2,
        f"edge {edge:.2f}:1 vs interior {interior:.2f}:1",
    )

    # 5. the sampler must find a planted flat interior it did not choose.
    planted = Image.new("RGB", (200, 100), (255, 0, 0))
    planted.paste(Image.new("RGB", (120, 50), (7, 11, 13)), (_GLOW_MARGIN, _GLOW_MARGIN))
    check(
        "modal sampler finds a planted interior",
        sample_interior(planted, (120, 50)) == (7, 11, 13),
        f"{sample_interior(planted, (120, 50))}",
    )

    print()
    if failures:
        print(f"SELF-TEST FAILED: {len(failures)} check(s): {', '.join(failures)}")
        return 1
    print("SELF-TEST PASSED — the probe can both confirm a good render and catch a bad one.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, help="read the image: section of a real config.yml")
    ap.add_argument("--opacity", type=float, help="override badge_opacity (what-if)")
    ap.add_argument("--show-edge", action="store_true", help="also print the edge-sample trap")
    ap.add_argument("--self-test", action="store_true", help="run the probe's own self-test and exit")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    if args.config:
        import yaml

        data = yaml.safe_load(args.config.read_text()) or {}
        cfg = ImageConfig.model_validate(data.get("image", {}))
        print(f"palette from {args.config}")
    else:
        cfg = ImageConfig()
        print("shipped ImageConfig defaults")
    if args.opacity is not None:
        cfg = cfg.model_copy(update={"badge_opacity": args.opacity})
    return report(cfg, show_edge=args.show_edge)


if __name__ == "__main__":
    raise SystemExit(main())
