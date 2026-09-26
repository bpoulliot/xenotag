#!/usr/bin/env python3
"""Measure the *rendered* WCAG contrast of badge text against the badge fill.

Roadmap item B1. The config's per-colour comments quote the contrast of the
opaque hex against white text. That is not what renders: `_pill_tile()` draws
the pill at `badge_opacity` alpha, so whatever is behind it shows through. This
script renders real pill tiles over flat backdrops and samples the badge
interior, so the number it prints is the number a viewer actually sees.

Roadmap item B2 moved the renderer, sampler and formula into `app/contrast.py`,
which is also what the Settings page's contrast warnings are computed by. The
`worst` column is the figure shown beside each colour picker.

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

# The formula, the renderer and the sampler live in app/contrast.py -- the same
# code the Settings page's contrast warning is computed by (roadmap B2). The
# probe re-exports them so tests and later sessions can keep importing here.
from app.config import ImageConfig  # noqa: E402
from app.contrast import (  # noqa: E402, F401
    AA,
    AAA,
    BACKDROPS,
    badge_contrast,
    contrast_ratio,
    format_ratio,
    relative_luminance,
    render_over,
    rendered_ratio,
    sample_interior,
)
from app.overlay import _GLOW_MARGIN, _parse_color  # noqa: E402


def sample_edge(img: Image.Image, pill_wh: tuple[int, int]) -> tuple[int, int, int]:
    """The trap: x=_GLOW_MARGIN is the pill's antialiased left EDGE, not its interior."""
    _, pill_h = pill_wh
    return img.convert("RGB").getpixel((_GLOW_MARGIN, _GLOW_MARGIN + pill_h // 2))


# ── Report ───────────────────────────────────────────────────────────────────
def report(cfg: ImageConfig, *, show_edge: bool = False) -> int:
    """Print the table. `worst` is the figure the Settings page shows beside
    each colour picker; the test suite checks the two agree to the digit."""
    m = badge_contrast(cfg)
    text_rgb = _parse_color(cfg.badge_text_color)
    print(f"badge_opacity={cfg.badge_opacity} (alpha={m['alpha']})  text={cfg.badge_text_color}")
    print()
    header = (
        f"| {'badge':<6} | {'opaque hex':>10} |" + "".join(f" {name:>8} |" for name in BACKDROPS) + f" {'worst':>8} |"
    )
    print(header)
    print("|" + "-" * 8 + "|" + "-" * 12 + "|" + ("-" * 10 + "|") * (len(BACKDROPS) + 1))

    for name, b in m["badges"].items():
        cells = "".join(f" {b['rendered'][bd]:>7.1f}: |" for bd in BACKDROPS)
        print(f"| {name:<6} | {b['opaque']:>9.1f}: |" + cells + f" {b['worst_text']:>7}: |")
        if show_edge:
            img, wh = render_over(next(iter(BACKDROPS.values())), b["color"], cfg.badge_text_color, alpha=m["alpha"])
            edge = contrast_ratio(text_rgb, sample_edge(img, wh))
            interior = contrast_ratio(text_rgb, sample_interior(img, wh))
            print(f"|   └─ on black: interior {interior:.1f}:1 vs EDGE-SAMPLE {edge:.1f}:1 (the trap)")

    worst = min(b["worst"] for b in m["badges"].values())
    print()
    print(
        f"worst rendered ratio: {format_ratio(worst)}:1   AA({AA}) {'PASS' if worst >= AA else 'FAIL'}"
        f"   AAA({AAA}) {'PASS' if worst >= AAA else 'FAIL'}"
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
