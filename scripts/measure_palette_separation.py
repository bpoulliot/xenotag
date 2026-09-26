#!/usr/bin/env python3
"""Measure whether the badge palette's categories are tellable apart.

Roadmap item B4 / P10. `measure_badge_contrast.py` answers "can you read the
label against the fill". This answers the other half: **can you tell a video
badge from an audio badge at a glance** — which is the whole reason the four
categories carry different colours.

Contrast is a *within-badge* property; separation is a *between-badge* one, and
a palette can pass the first while failing the second. It reports CIEDE2000
between every pair, under normal colour vision and under simulated protanopia,
deuteranopia and tritanopia (Machado et al. 2009, severity 1.0).

Rules of thumb for CIEDE2000: ~1.0 is a just-noticeable difference under ideal
side-by-side conditions; badges are small, separated, and sit on arbitrary
poster art, so treat **<5 as effectively the same colour** and aim well above.

    python3 scripts/measure_palette_separation.py                # shipped defaults
    python3 scripts/measure_palette_separation.py --config FILE  # a real config.yml
    python3 scripts/measure_palette_separation.py --self-test    # probe self-check
"""

from __future__ import annotations

import argparse
import itertools
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import ImageConfig  # noqa: E402
from app.contrast import _linearize  # noqa: E402
from app.contrast import contrast_ratio as _wcag_contrast_ratio  # noqa: E402

CATEGORIES = ("video", "audio", "sub", "rating")

# Machado et al. (2009) CVD simulation matrices, severity 1.0, linear RGB.
CVD_MATRICES = {
    "protanopia": (
        (0.152286, 1.052583, -0.204868),
        (0.114503, 0.786281, 0.099216),
        (-0.003882, -0.048116, 1.051998),
    ),
    "deuteranopia": (
        (0.367322, 0.860646, -0.227968),
        (0.280085, 0.672501, 0.047413),
        (-0.011820, 0.042940, 0.968881),
    ),
    "tritanopia": (
        (1.255528, -0.076749, -0.178779),
        (-0.078411, 0.930809, 0.147602),
        (0.004733, 0.691367, 0.303900),
    ),
}
VIEWS = (None, *CVD_MATRICES)

# Below this, two badges are not reliably tellable apart in situ.
SAME_COLOUR = 5.0


def _from_linear(value: float) -> int:
    c = max(0.0, min(1.0, value))
    s = 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055
    return round(s * 255)


def hex_to_rgb(colour: str) -> tuple[int, int, int]:
    h = colour.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def hex_to_linear_rgb(colour: str) -> tuple[float, float, float]:
    return tuple(_linearize(c) for c in hex_to_rgb(colour))  # type: ignore[return-value]


def contrast_ratio(a: str, b: str) -> float:
    """WCAG ratio of two hexes -- app.contrast's formula, not a copy of it."""
    return _wcag_contrast_ratio(hex_to_rgb(a), hex_to_rgb(b))


def simulate(colour: str, kind: str | None) -> str:
    """Return `colour` as seen with the given colour-vision deficiency."""
    if kind is None:
        return colour
    v = hex_to_linear_rgb(colour)
    m = CVD_MATRICES[kind]
    return "#{:02x}{:02x}{:02x}".format(*(_from_linear(sum(m[i][j] * v[j] for j in range(3))) for i in range(3)))


def hex_to_lab(colour: str) -> tuple[float, float, float]:
    r, g, b = hex_to_linear_rgb(colour)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 216 / 24389 else (841 / 108) * t + 4 / 29

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def ciede2000(lab1: tuple[float, float, float], lab2: tuple[float, float, float]) -> float:
    """CIE Delta-E 2000. Perceptual distance; bigger means easier to tell apart."""
    l1, a1, b1 = lab1
    l2, a2, b2 = lab2
    c1, c2 = math.hypot(a1, b1), math.hypot(a2, b2)
    c_bar = (c1 + c2) / 2
    g = 0.5 * (1 - math.sqrt(c_bar**7 / (c_bar**7 + 25**7))) if c_bar else 0.0
    a1p, a2p = (1 + g) * a1, (1 + g) * a2
    c1p, c2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360 if (a1p or b1) else 0.0
    h2p = math.degrees(math.atan2(b2, a2p)) % 360 if (a2p or b2) else 0.0

    dlp = l2 - l1
    dcp = c2p - c1p
    if c1p * c2p == 0:
        dhp = 0.0
    else:
        raw = h2p - h1p
        dhp = raw - 360 if raw > 180 else (raw + 360 if raw < -180 else raw)
    dhp_big = 2 * math.sqrt(c1p * c2p) * math.sin(math.radians(dhp) / 2)

    lbp, cbp = (l1 + l2) / 2, (c1p + c2p) / 2
    if c1p * c2p == 0:
        hbp = h1p + h2p
    elif abs(h1p - h2p) > 180:
        hbp = (h1p + h2p + 360) / 2 if h1p + h2p < 360 else (h1p + h2p - 360) / 2
    else:
        hbp = (h1p + h2p) / 2

    t = (
        1
        - 0.17 * math.cos(math.radians(hbp - 30))
        + 0.24 * math.cos(math.radians(2 * hbp))
        + 0.32 * math.cos(math.radians(3 * hbp + 6))
        - 0.20 * math.cos(math.radians(4 * hbp - 63))
    )
    sl = 1 + (0.015 * (lbp - 50) ** 2) / math.sqrt(20 + (lbp - 50) ** 2)
    sc = 1 + 0.045 * cbp
    sh = 1 + 0.015 * cbp * t
    rt = -math.sin(math.radians(60 * math.exp(-(((hbp - 275) / 25) ** 2)))) * (
        2 * math.sqrt(cbp**7 / (cbp**7 + 25**7)) if cbp else 0.0
    )
    return math.sqrt((dlp / sl) ** 2 + (dcp / sc) ** 2 + (dhp_big / sh) ** 2 + rt * (dcp / sc) * (dhp_big / sh))


def separation(a: str, b: str, kind: str | None = None) -> float:
    return ciede2000(hex_to_lab(simulate(a, kind)), hex_to_lab(simulate(b, kind)))


def worst_separation(palette: dict[str, str]) -> tuple[float, str, str | None]:
    """Smallest pairwise distance across all simulated vision types."""
    worst = (float("inf"), "", None)
    for x, y in itertools.combinations(palette, 2):
        for kind in VIEWS:
            d = separation(palette[x], palette[y], kind)
            if d < worst[0]:
                worst = (d, f"{x}/{y}", kind)
    return worst


def report(palette: dict[str, str], text_colour: str) -> int:
    print(f"text colour: {text_colour}\n")
    print(f"  {'category':9} {'hex':9} {'vs text':>10}")
    for name, colour in palette.items():
        ratio = contrast_ratio(colour, text_colour)
        mark = "AAA" if ratio >= 7 else ("AA" if ratio >= 4.5 else "FAIL")
        print(f"  {name:9} {colour:9} {ratio:7.2f}:1  {mark}")

    print("\n  pairwise CIEDE2000 — how tellable apart two badges are")
    header = f"  {'pair':18}" + "".join(f"{(k or 'normal'):>14}" for k in VIEWS)
    print(header)
    failures = []
    for x, y in itertools.combinations(palette, 2):
        row = f"  {x + '/' + y:18}"
        for kind in VIEWS:
            d = separation(palette[x], palette[y], kind)
            flag = "!" if d < SAME_COLOUR else " "
            row += f"{d:13.1f}{flag}"
            if d < SAME_COLOUR:
                failures.append((x, y, kind or "normal", d))
        print(row)

    worst, pair, kind = worst_separation(palette)
    print(f"\n  worst case: {pair} at {kind or 'normal'} vision -> dE {worst:.1f}")
    if failures:
        print(f"\n  {len(failures)} pair(s) below dE {SAME_COLOUR:.0f} — effectively the same colour:")
        for x, y, k, d in failures:
            print(f"    {x} and {y} are indistinguishable under {k} (dE {d:.1f})")
        return 1
    print(f"  all pairs clear dE {SAME_COLOUR:.0f} under every simulated vision type")
    return 0


def self_test() -> int:
    """Fail in both directions: known-identical must read 0, known-distinct high."""
    ok = True

    d = separation("#134e4a", "#134e4a")
    if d > 1e-9:
        print(f"FAIL: a colour against itself should be 0, got {d}")
        ok = False

    d = separation("#000000", "#ffffff")
    if d < 95:
        print(f"FAIL: black vs white should be ~100, got {d:.1f}")
        ok = False

    # The defect this probe exists to catch: navy and violet are distinct in
    # normal vision and collapse under deuteranopia.
    normal = separation("#1e3a8a", "#4c1d95")
    deut = separation("#1e3a8a", "#4c1d95", "deuteranopia")
    if not normal > 10:
        print(f"FAIL: navy/violet should be distinct in normal vision, got {normal:.1f}")
        ok = False
    if not deut < SAME_COLOUR:
        print(f"FAIL: navy/violet should collapse under deuteranopia, got {deut:.1f}")
        ok = False

    # Simulation must actually change a colour it should change, and leave
    # greys alone (a grey has no chroma to lose).
    if simulate("#808080", "deuteranopia") != "#808080":
        print("FAIL: grey should be unchanged by deuteranopia simulation")
        ok = False
    if simulate("#ff0000", "protanopia") == "#ff0000":
        print("FAIL: red should be changed by protanopia simulation")
        ok = False

    print("self-test: PASS" if ok else "self-test: FAIL")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, help="read the image: section of a real config.yml")
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

    palette = {name: getattr(cfg, f"{name}_badge_color") for name in CATEGORIES}
    return report(palette, cfg.badge_text_color)


if __name__ == "__main__":
    raise SystemExit(main())
