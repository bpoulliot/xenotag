#!/usr/bin/env python3
"""Measure the adaptive badge palette: its threshold, its backup colours, the pair.

Roadmap item P6. With `image.adapt_badge_colors` on, every badge row samples the
poster under it and, where that region is lighter than
`overlay.ADAPT_LUMINANCE_THRESHOLD` (for a light label), draws in the backup
palette. This probe re-derives both defaults from real renders and checks them:

 1. **The threshold.** For each opacity, the lightest flat grey region over which
    the MAIN palette still renders AAA (7:1) -- B1's method: a real
    `_pill_tile()` over the backdrop, modal pixel of the flat interior. The
    shipped threshold must sit at or below that boundary at the design opacity,
    so the main palette is AAA wherever it is still used.
 2. **The backup palette -- contrast.** It must hold AA (4.5:1) over every flat
    region at or above the threshold at the design opacity. AAA is out of reach
    there for ANY colour: pure black renders 6.90:1 on white at 65%.
 3. **The backup palette -- separation.** CIEDE2000 under normal, protan, deutan
    and tritan vision (B4's probe, imported) between every pair that can share a
    poster: backup/backup AND backup/main of different categories, because rows
    choose independently. B4's bar: dE 5.
 4. **What the pair buys.** Worst label contrast over every flat grey poster,
    main palette alone vs adapted, per opacity.

The design opacity is 65%: the only value below 100% with a history -- the
default until B1, and still what any config saved before B1 carries.

    python3 scripts/measure_adaptive_palette.py                # shipped defaults: acceptance
    python3 scripts/measure_adaptive_palette.py --config FILE  # a real config.yml
    python3 scripts/measure_adaptive_palette.py --search       # re-derive the backup palette (~5 min)
    python3 scripts/measure_adaptive_palette.py --self-test    # probe self-check

Exit status is non-zero if any acceptance check fails. Only flat regions are
modelled; how a textured region's mean luminance maps onto its legibility is not.
"""

from __future__ import annotations

import argparse
import itertools
import math
import random
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from measure_palette_separation import SAME_COLOUR, VIEWS, ciede2000, hex_to_lab, separation, simulate  # noqa: E402

from app.config import ImageConfig  # noqa: E402
from app.contrast import AA, AAA, rendered_ratio, sample_interior  # noqa: E402
from app.overlay import (  # noqa: E402
    _GLOW_MARGIN,
    ADAPT_LUMINANCE_THRESHOLD,
    BadgeGroup,
    adapted_fill,
    badge_alpha,
    region_luminance,
    render_badge_groups,
)
from app.wcag import relative_luminance  # noqa: E402

CATEGORIES = ("video", "audio", "sub", "rating")
DESIGN_OPACITY = 0.65
OPACITIES = (0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0)
HUE_TOLERANCE = 30.0  # degrees of CIELAB hue a backup may stray from its category's main colour
MIN_CHROMA = 4.0  # below this a colour is a grey, and its category's hue is gone


def grey_lum(g: int) -> float:
    return relative_luminance((g, g, g))


def worst_over(palette: dict[str, str], backdrop: tuple[int, int, int], text: str, alpha: int) -> float:
    return min(rendered_ratio(backdrop, fill, text, alpha=alpha) for fill in palette.values())


def grey_sweep(palette: dict[str, str], text: str, alpha: int) -> list[float]:
    """Worst label contrast of `palette` over each flat grey poster 0..255."""
    return [worst_over(palette, (g, g, g), text, alpha) for g in range(256)]


def lightest_grey_holding(sweep: list[float], bar: float) -> int | None:
    """Lightest g such that EVERY grey from black up to g clears `bar`."""
    last = None
    for g, r in enumerate(sweep):
        if r < bar:
            break
        last = g
    return last


def label_is_light(text: str) -> bool:
    # The overlay's own rule, asked through the overlay: which side of the
    # threshold a white and a black region fall on for this label.
    return adapted_fill(Image.new("RGB", (1, 1), (255, 255, 255)), (0, 0, 1, 1), "main", "backup", text) == "backup"


def uses_backup(g: int, text: str, threshold: float) -> bool:
    light = grey_lum(g) >= threshold
    return light == label_is_light(text)


# ── Colour identity (CIELAB hue / chroma) ────────────────────────────────────
def lch(colour: str) -> tuple[float, float, float]:
    lab_l, a, b = hex_to_lab(colour)
    return lab_l, math.hypot(a, b), math.degrees(math.atan2(b, a)) % 360


def hue_gap(h1: float, h2: float) -> float:
    return abs((h1 - h2 + 180) % 360 - 180)


def co_occurring_pairs(main: dict[str, str], backup: dict[str, str]) -> list[tuple[str, str, str, str]]:
    """Every (label, colour, label, colour) pair that can appear on one poster
    with adaptation on: backup/backup, and backup/main across categories."""
    pairs = [(f"b.{x}", backup[x], f"b.{y}", backup[y]) for x, y in itertools.combinations(CATEGORIES, 2)]
    pairs += [(f"b.{x}", backup[x], f"m.{y}", main[y]) for x in CATEGORIES for y in CATEGORIES if x != y]
    return pairs


def worst_pair(pairs) -> tuple[float, str, str | None]:
    worst = (float("inf"), "", None)
    for la, ca, lb, cb in pairs:
        for kind in VIEWS:
            d = separation(ca, cb, kind)
            if d < worst[0]:
                worst = (d, f"{la}/{lb}", kind)
    return worst


# ── Checks (each returns (ok, detail)); the self-test plants failures in them ─
def check_threshold(main: dict[str, str], text: str, threshold: float) -> tuple[bool, str]:
    sweep = grey_sweep(main, text, badge_alpha(DESIGN_OPACITY))
    bad = [g for g in range(256) if not uses_backup(g, text, threshold) and sweep[g] < AAA]
    if bad:
        g = bad[-1] if label_is_light(text) else bad[0]
        return False, f"main palette used over grey {g} (L {grey_lum(g):.4f}) renders {sweep[g]:.2f}:1 < AAA"
    return True, "main palette clears AAA over every flat region it is still used on"


def check_backup_contrast(backup: dict[str, str], text: str, threshold: float) -> tuple[bool, str]:
    alpha = badge_alpha(DESIGN_OPACITY)
    served = [g for g in range(256) if uses_backup(g, text, threshold)]
    if not served:
        return False, "the threshold leaves the backup no region to serve"
    worst_g = min(served, key=lambda g: worst_over(backup, (g, g, g), text, alpha))
    r = worst_over(backup, (worst_g, worst_g, worst_g), text, alpha)
    if r < AA:
        return False, f"backup over grey {worst_g} renders {r:.2f}:1 < AA at {DESIGN_OPACITY:.0%}"
    return True, f"backup worst {r:.2f}:1 (grey {worst_g}) >= AA over every region it serves"


def check_separation(main: dict[str, str], backup: dict[str, str]) -> tuple[bool, str]:
    d, pair, kind = worst_pair(co_occurring_pairs(main, backup))
    if d < SAME_COLOUR:
        return False, f"{pair} at {kind or 'normal'} vision -> dE {d:.1f} < {SAME_COLOUR:.0f}"
    return True, f"worst co-occurring pair {pair} at {kind or 'normal'} vision -> dE {d:.1f}"


# ── Report ───────────────────────────────────────────────────────────────────
def palettes(cfg: ImageConfig) -> tuple[dict[str, str], dict[str, str]]:
    main = {c: getattr(cfg, f"{c}_badge_color") for c in CATEGORIES}
    backup = {c: getattr(cfg, f"backup_{c}_badge_color") for c in CATEGORIES}
    return main, backup


def report(cfg: ImageConfig, threshold: float) -> int:
    main, backup = palettes(cfg)
    text = cfg.badge_text_color
    print(f"text {text}  threshold L {threshold}  design opacity {DESIGN_OPACITY:.0%}")
    print(f"main   {main}\nbackup {backup}\n")

    print("1. Lightest flat grey over which the MAIN palette still clears each bar (rendered, flat interior)")
    print(f"  {'opacity':>7} | {'AAA grey':>8} {'L':>7} | {'AA grey':>7} {'L':>7}")
    for op in OPACITIES:
        sw = grey_sweep(main, text, badge_alpha(op))
        g3, g2 = lightest_grey_holding(sw, AAA), lightest_grey_holding(sw, AA)
        fmt = lambda g: (f"{g:>7}  {grey_lum(g):.4f}" if g is not None else f"{'none':>7}  {'-':>6}")  # noqa: E731
        print(f"  {op:>7.0%} | {fmt(g3)} | {fmt(g2)}")

    rnd = random.Random(0)  # noqa: S311 -- a seeded sample, not a secret
    alpha = badge_alpha(DESIGN_OPACITY)
    below, above = [], []
    while len(below) < 150 or len(above) < 150:
        c = tuple(rnd.randrange(256) for _ in range(3))
        side = above if uses_backup_rgb(c, text, threshold) else below
        if len(side) < 150:
            side.append(c)
    main_col = min(worst_over(main, c, text, alpha) for c in below)
    back_col = min(worst_over(backup, c, text, alpha) for c in above)
    print(
        f"\n  coloured regions at {DESIGN_OPACITY:.0%} (150 random RGB each side of the threshold, seed 0):"
        f"\n    main on its side  worst {main_col:.2f}:1    backup on its side  worst {back_col:.2f}:1"
    )

    print("\n2. Backup palette: rendered contrast on a white poster by opacity (B2's worst case for a light label)")
    print(f"  {'opacity':>7} | " + " ".join(f"{c:>7}" for c in CATEGORIES))
    for op in OPACITIES:
        a = badge_alpha(op)
        print(
            f"  {op:>7.0%} | "
            + " ".join(f"{rendered_ratio((255, 255, 255), f, text, alpha=a):>7.2f}" for f in backup.values())
        )
    print(
        f"  lowest opacity holding AA on white: {opacity_floor(backup, text, AA)}%"
        f"   AAA: {opacity_floor(backup, text, AAA)}%"
        f"   (main palette alone: AA {opacity_floor(main, text, AA)}%, AAA {opacity_floor(main, text, AAA)}%)"
    )

    print("\n  identity: CIELAB lightness / chroma / hue, and hue gap to the category's main colour")
    max_chroma = max(lch(c)[1] for c in main.values())
    for c in CATEGORIES:
        bl, bc, bh = lch(backup[c])
        print(f"    {c:6} {backup[c]}  L*{bl:5.1f}  C {bc:5.1f}  h {bh:5.0f}  gap {hue_gap(bh, lch(main[c])[2]):4.0f}")
    print(f"    (main palette's largest chroma: {max_chroma:.1f})")

    print("\n3. Separation, every pair that can share a poster (CIEDE2000; ! = below dE 5)")
    print(f"  {'pair':18}" + "".join(f"{(k or 'normal'):>14}" for k in VIEWS))
    for la, ca, lb, cb in co_occurring_pairs(main, backup):
        cells = ""
        for kind in VIEWS:
            d = separation(ca, cb, kind)
            cells += f"{d:13.1f}{'!' if d < SAME_COLOUR else ' '}"
        print(f"  {la + '/' + lb:18}{cells}")
    d_main, pair_main, kind_main = worst_pair(
        [(f"m.{x}", main[x], f"m.{y}", main[y]) for x, y in itertools.combinations(CATEGORIES, 2)]
    )
    print(f"  (main palette's own worst pair: {pair_main} at {kind_main or 'normal'} -> dE {d_main:.1f})")

    print("\n4. Worst label contrast over every flat grey poster: main alone vs adapted")
    print(f"  {'opacity':>7} | {'main':>7} | {'adapted':>7}")
    for op in OPACITIES:
        a = badge_alpha(op)
        ms = grey_sweep(main, text, a)
        bs = grey_sweep(backup, text, a)
        # At 100% the overlay does not adapt (an opaque badge hides the poster).
        adapted = ms if op >= 1.0 else [bs[g] if uses_backup(g, text, threshold) else ms[g] for g in range(256)]
        print(f"  {op:>7.0%} | {min(ms):>7.2f} | {min(adapted):>7.2f}")

    print("\nAcceptance")
    fails = 0
    for name, (ok, detail) in (
        ("threshold", check_threshold(main, text, threshold)),
        ("backup contrast", check_backup_contrast(backup, text, threshold)),
        ("separation", check_separation(main, backup)),
    ):
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
        fails += not ok
    return 1 if fails else 0


def opacity_floor(palette: dict[str, str], text: str, bar: float) -> int | None:
    """Lowest slider percent such that every percent from 100 down to it keeps
    `palette` at or above `bar` on a white poster."""
    last = None
    for pct in range(100, 9, -1):
        if worst_over(palette, (255, 255, 255), text, badge_alpha(pct / 100)) < bar:
            break
        last = pct
    return last


def uses_backup_rgb(c: tuple[int, int, int], text: str, threshold: float) -> bool:
    return (relative_luminance(c) >= threshold) == label_is_light(text)


# ── Search: re-derive the backup palette ─────────────────────────────────────
def search(cfg: ImageConfig, *, step: int = 3, starts: int = 12, iters: int = 6000, seed: int = 0) -> dict[str, str]:
    """Maximise the worst co-occurring CVD separation, subject to: AA on a white
    poster at the design opacity (checked analytically, then by render), hue
    within HUE_TOLERANCE of the category's main colour, chroma between
    MIN_CHROMA and the main palette's largest. Hill-climbing with restarts, then
    a 1-step polish; seeded, so a re-run reproduces the result."""
    main, _ = palettes(cfg)
    text_l = relative_luminance(tuple(int(cfg.badge_text_color[i : i + 2], 16) for i in (1, 3, 5)))
    alpha = badge_alpha(DESIGN_OPACITY)
    max_chroma = max(lch(c)[1] for c in main.values())

    def hx(c):
        return "#{:02x}{:02x}{:02x}".format(*c)

    def ok(cat, c):
        mixed = tuple(round((ch * alpha + 255 * (255 - alpha)) / 255) for ch in c)
        lo, hi = sorted((relative_luminance(mixed), text_l))
        if (hi + 0.05) / (lo + 0.05) < AA:
            return False
        _, chroma, hue = lch(hx(c))
        return MIN_CHROMA <= chroma <= max_chroma and hue_gap(hue, lch(main[cat])[2]) <= HUE_TOLERANCE

    grid = range(0, 256, step)
    cands = {cat: [c for c in itertools.product(grid, grid, grid) if ok(cat, c)] for cat in CATEGORIES}
    for cat, cs in cands.items():
        print(f"  {cat}: {len(cs)} candidates", flush=True)
        if not cs:
            raise SystemExit(f"no {cat} colour meets the constraints")
    labs: dict[tuple, list] = {}

    def lab_views(c):
        if c not in labs:
            labs[c] = [hex_to_lab(simulate(hx(c), v)) for v in VIEWS]
        return labs[c]

    main_rgb = {cat: tuple(int(main[cat][i : i + 2], 16) for i in (1, 3, 5)) for cat in CATEGORIES}

    def score(p):
        s = min(
            min(ciede2000(a, b) for a, b in zip(lab_views(p[x]), lab_views(p[y]), strict=True))
            for x, y in itertools.combinations(CATEGORIES, 2)
        )
        for x in CATEGORIES:
            for y in CATEGORIES:
                if x != y:
                    s = min(
                        s, min(ciede2000(a, b) for a, b in zip(lab_views(p[x]), lab_views(main_rgb[y]), strict=True))
                    )
        return s

    best, best_p = -1.0, None
    for s in range(starts):
        rnd = random.Random(seed + s)  # noqa: S311 -- seeded so a re-run reproduces
        p = {cat: rnd.choice(cands[cat]) for cat in CATEGORIES}
        sc = score(p)
        for _ in range(iters):
            cat = rnd.choice(CATEGORIES)
            q = dict(p)
            q[cat] = rnd.choice(cands[cat])
            s2 = score(q)
            if s2 >= sc:
                p, sc = q, s2
        if sc > best:
            best, best_p = sc, p
        print(f"  start {s}: dE {sc:.2f}", flush=True)

    improved = True
    while improved:
        improved = False
        for cat in CATEGORIES:
            for ch in range(3):
                for delta in (-3, -2, -1, 1, 2, 3):
                    c = list(best_p[cat])
                    c[ch] = min(255, max(0, c[ch] + delta))
                    c = tuple(c)
                    if c == best_p[cat] or not ok(cat, c):
                        continue
                    q = dict(best_p)
                    q[cat] = c
                    s2 = score(q)
                    if s2 > best:
                        best, best_p, improved = s2, q, True
    result = {cat: hx(c) for cat, c in best_p.items()}
    print(f"  best: dE {best:.2f}  {result}")
    return result


# ── Self-test ────────────────────────────────────────────────────────────────
def self_test() -> int:
    """Every check must fail on a planted defect and pass on a known-good input."""
    failures: list[str] = []

    def expect(name: str, cond: bool, detail: str = "") -> None:
        if not cond:
            failures.append(f"{name} {detail}")

    cfg = ImageConfig()
    main, backup = palettes(cfg)
    white, black = "#ffffff", "#000000"

    # region_luminance: exact on flat greys, 0.5 on half black / half white,
    # None off the image -- degenerate inputs give degenerate outputs.
    for g in (0, 98, 255):
        img = Image.new("RGB", (40, 20), (g, g, g))
        got = region_luminance(img, (0, 0, 40, 20))
        expect("region_luminance flat", abs(got - grey_lum(g)) < 1e-12, f"g={g} got {got}")
    half = Image.new("RGB", (40, 20), (0, 0, 0))
    half.paste((255, 255, 255), (20, 0, 40, 20))
    expect("region_luminance half", abs(region_luminance(half, (0, 0, 40, 20)) - 0.5) < 1e-12)
    expect("region_luminance clip", region_luminance(half, (100, 100, 120, 120)) is None)
    expect("region_luminance sub-box", region_luminance(half, (20, 0, 40, 20)) == 1.0)

    # The selector: a light label switches on light regions, a dark one on dark.
    wimg, bimg = Image.new("RGB", (4, 4), (255,) * 3), Image.new("RGB", (4, 4), (0,) * 3)
    box = (0, 0, 4, 4)
    expect("select light/light", adapted_fill(wimg, box, "#111111", "#222222", white) == "#222222")
    expect("select light/dark", adapted_fill(bimg, box, "#111111", "#222222", white) == "#111111")
    expect("select dark/dark", adapted_fill(bimg, box, "#eeeeee", "#dddddd", black) == "#dddddd")
    expect("select dark/light", adapted_fill(wimg, box, "#eeeeee", "#dddddd", black) == "#eeeeee")

    # Threshold check: the shipped one passes, a planted too-light one fails.
    ok, d = check_threshold(main, white, ADAPT_LUMINANCE_THRESHOLD)
    expect("threshold shipped", ok, d)
    ok, d = check_threshold(main, white, 0.5)
    expect("threshold planted 0.5", not ok, d)
    # ...and the boundary it measures must move the right way: a black palette
    # holds AAA over lighter greys than the main one does.
    a = badge_alpha(DESIGN_OPACITY)
    g_main = lightest_grey_holding(grey_sweep(main, white, a), AAA)
    g_black = lightest_grey_holding(grey_sweep({c: black for c in CATEGORIES}, white, a), AAA)
    expect("boundary moves", g_main is not None and g_black is not None and g_black > g_main, f"{g_main} {g_black}")

    # Backup contrast: the shipped one passes, a mid-grey one fails.
    ok, d = check_backup_contrast(backup, white, ADAPT_LUMINANCE_THRESHOLD)
    expect("backup contrast shipped", ok, d)
    ok, d = check_backup_contrast({c: "#9a9a9a" for c in CATEGORIES}, white, ADAPT_LUMINANCE_THRESHOLD)
    expect("backup contrast planted grey", not ok, d)

    # Separation: shipped passes; four identical colours fail; a backup that
    # collides with ANOTHER category's main colour fails (the cross pair).
    ok, d = check_separation(main, backup)
    expect("separation shipped", ok, d)
    ok, d = check_separation(main, {c: "#101010" for c in CATEGORIES})
    expect("separation planted identical", not ok, d)
    ok, d = check_separation(main, {**backup, "video": main["audio"]})
    expect("separation planted cross", not ok, d)

    # End to end through render_badge_groups: top half black, bottom half white,
    # rating top-left, tags bottom-left, 65%. Adapted: the bottom row is the
    # backup over white, the rating the main over black. Off: main everywhere.
    poster = Image.new("RGBA", (600, 900), (0, 0, 0, 255))
    poster.paste((255, 255, 255, 255), (0, 450, 600, 900))
    groups = [BadgeGroup(["1080p"], main["video"], white, backup["video"])]
    rating = BadgeGroup(["PG"], main["rating"], white, backup["rating"])

    def interiors(adapt: bool, opacity: float) -> dict[str, tuple]:
        c = cfg.model_copy(update={"adapt_badge_colors": adapt, "badge_opacity": opacity})
        placed: list = []
        img = render_badge_groups(poster, groups, rating, c, placed=placed).convert("RGB")
        out = {}
        for kind, (x, y, w, h) in placed:
            tile = img.crop((x - _GLOW_MARGIN, y - _GLOW_MARGIN, x + w + _GLOW_MARGIN, y + h + _GLOW_MARGIN))
            out[kind] = sample_interior(tile, (w, h))
        return out

    def expected(fill: str, backdrop: tuple, opacity: float) -> tuple:
        from app.contrast import render_over

        # 5px inset clear of the text: the modal pixel is the fill.
        img, wh = render_over(backdrop, fill, white, alpha=badge_alpha(opacity), text="1080p")
        return sample_interior(img, wh)

    on, off = interiors(True, 0.65), interiors(False, 0.65)
    expect("e2e adapted tags", on["tags"] == expected(backup["video"], (255,) * 3, 0.65), f"{on}")
    expect("e2e adapted rating", on["rating"] == expected(main["rating"], (0,) * 3, 0.65), f"{on}")
    expect("e2e off tags", off["tags"] == expected(main["video"], (255,) * 3, 0.65), f"{off}")
    expect("e2e on != off", on["tags"] != off["tags"])
    opaque_on, opaque_off = interiors(True, 1.0), interiors(False, 1.0)
    expect("e2e opaque ignores adapt", opaque_on == opaque_off, f"{opaque_on} {opaque_off}")

    for f in failures:
        print(f"FAIL: {f}")
    print("self-test: PASS" if not failures else "self-test: FAIL")
    return 0 if not failures else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, help="read the image: section of a real config.yml")
    ap.add_argument("--search", action="store_true", help="re-derive the backup palette (slow)")
    ap.add_argument("--self-test", action="store_true", help="run the probe's own self-test and exit")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    if args.config:
        import yaml

        data = yaml.safe_load(args.config.read_text()) or {}
        cfg = ImageConfig(**(data.get("image") or {}))
        print(f"source: {args.config}\n")
    else:
        cfg = ImageConfig()
        print("source: shipped defaults (app/config.py)\n")

    if args.search:
        search(cfg)
        return 0
    return report(cfg, ADAPT_LUMINANCE_THRESHOLD)


if __name__ == "__main__":
    raise SystemExit(main())
