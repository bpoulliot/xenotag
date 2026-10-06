#!/usr/bin/env python3
"""Measure how a pill is composited onto a poster, against what the chips claim.

Roadmap item B21. Until it was fixed, `_render_group()` placed every pill tile
with one masked paste -- `overlay.paste(tile, xy, tile)` -- which blends every
band, alpha included. A translucent fill therefore landed at a**3 over
(1 - a**2) of the poster, while `app.contrast` (B1's instrument, and B2's
Settings chips built on it) composites the tile at a. Below 100% the chip and
the poster disagreed; at 100% they agreed, which is why nobody saw it.

The operator chose (b): composite the pill correctly and keep the glow's
squared paste, on the premise that at 100% -- production -- nothing moves. This
probe is the evidence for that premise and for the fix. It renders through the
real `render_badge_groups()` twice: once with the shipped `_place_pill()`, and
once with LEGACY, a copy of the single paste it replaced, swapped in for the
duration of the call (read side only: nothing is written anywhere).

    python3 scripts/measure_pill_composite.py              # the full report
    python3 scripts/measure_pill_composite.py --self-test  # probe self-check

Sections: (1) LEGACY vs shipped at 100% over the README fixtures and synthetic
posters -- max per-channel difference and the count of differing pixels; (2) the
same below 100%, where they must differ; (3) chip vs poster, interior pixel and
ratio, per badge, opacity and backdrop; (4) the glow band left of a pill on a
mid-grey poster; (5) B21's algebra case; (6) the opacity floors for WCAG AA and
AAA over black, white and grey posters, read off the poster path.

Exits non-zero if any 100% render moved, or the chip and the poster disagree.
"""

from __future__ import annotations

import argparse
import io
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageChops  # noqa: E402

from app import overlay  # noqa: E402
from app.config import AppConfig, ImageConfig  # noqa: E402
from app.contrast import (  # noqa: E402
    AA,
    AAA,
    BACKDROPS,
    BADGES,
    contrast_ratio,
    render_over,
    sample_interior,
)
from app.overlay import BadgeGroup, badge_alpha, render_badge_groups  # noqa: E402

Placer = Callable[..., None]

OPACITIES = (0.65, 0.80, 0.90, 1.00)
POSTER = (1000, 1500)
# The sampler's inset into the pill rectangle `placed=` reports; the same one
# `app.contrast.sample_interior()` uses inside a tile.
_INSET = 5


# ── The two compositors ─────────────────────────────────────────────────────
def legacy_place(layer, xy, text, fill_hex, text_hex, alpha, font_size, pad_h, pad_v) -> None:
    """The placement `_render_group()` shipped until B21: glow and pill flattened
    into one tile (`Image.alpha_composite(glow, pill)`, as `_render_pill_tile()`
    returned it), pasted with itself as the mask."""
    pill = overlay._render_pill_tile(text, fill_hex, text_hex, alpha, font_size, pad_h, pad_v)
    tile = Image.alpha_composite(overlay._render_glow(pill.size), pill)
    layer.paste(tile, xy, tile)


SHIPPED: Placer = overlay._place_pill


@contextmanager
def placer(fn: Placer) -> Iterator[None]:
    saved = overlay._place_pill
    overlay._place_pill = fn
    try:
        yield
    finally:
        overlay._place_pill = saved


def render(fn: Placer, base: Image.Image, cfg: ImageConfig, groups, rating, placed=None) -> Image.Image:
    with placer(fn):
        return render_badge_groups(base.copy(), groups, rating, cfg, placed=placed).convert("RGB")


# ── Posters and fixtures ────────────────────────────────────────────────────
def flat(rgb: tuple[int, int, int], size: tuple[int, int] = POSTER) -> Image.Image:
    return Image.new("RGBA", size, (*rgb, 255))


def gradient(size: tuple[int, int] = POSTER) -> Image.Image:
    """Red ramps left to right, green bottom to top, blue top to bottom."""
    w, h = size
    ramp = Image.linear_gradient("L")
    horiz = ramp.rotate(90, expand=True).transpose(Image.Transpose.FLIP_LEFT_RIGHT).resize(size)
    vert = ramp.resize(size)
    return Image.merge("RGBA", (horiz, ImageChops.invert(vert), vert, Image.new("L", size, 255)))


SYNTHETIC: dict[str, Callable[[], Image.Image]] = {
    "white": lambda: flat((255, 255, 255)),
    "black": lambda: flat((0, 0, 0)),
    "grey128": lambda: flat((128, 128, 128)),
    "gradient": gradient,
}


def readme_scenes() -> list[tuple[str, Image.Image, ImageConfig, list, BadgeGroup | None]]:
    """`scripts/generate_readme_images.py`'s overlays, as (name, base, cfg, groups, rating),
    on their own sample backgrounds and on every synthetic poster."""
    from app.pipeline import _make_badge_groups
    from app.preview_samples import _GENERATORS
    from scripts.generate_readme_images import _OVERLAYS, _RATING

    media = _readme_media()
    scenes = []
    for name, sample, overrides in _OVERLAYS:
        cfg = AppConfig()
        cfg.image = cfg.image.model_copy(update=overrides)
        groups, rating = _make_badge_groups(media, _RATING, cfg)
        buf = io.BytesIO()
        _GENERATORS[sample]().save(buf, format="PNG")
        bases = {sample: Image.open(io.BytesIO(buf.getvalue())).convert("RGBA").resize((280, 420), Image.LANCZOS)}
        bases.update({k: mk() for k, mk in SYNTHETIC.items()})
        for bname, base in bases.items():
            scenes.append((f"{name} on {bname}", base, cfg.image, groups, rating))
    return scenes


def _readme_media():
    from app.scanner import AudioTrack, MediaInfo, SubTrack

    # The same invented title generate_readme_images.py renders.
    return MediaInfo(
        resolution="4K",
        languages=["EN", "JA"],
        raw_audio_langs=["eng", "jpn"],
        video_codec="H.265",
        hdr_type="HDR10",
        audio_tracks=[AudioTrack("EN", "TrueHD Atmos"), AudioTrack("JA", "DTS-HD")],
        subtitle_tracks=[SubTrack("EN", "PGS", True), SubTrack("JA", "PGS", True), SubTrack("EN", "SRT", False)],
    )


def with_opacity(cfg: ImageConfig, opacity: float) -> ImageConfig:
    return cfg.model_copy(update={"badge_opacity": opacity})


# ── Comparisons ─────────────────────────────────────────────────────────────
def diff(a: Image.Image, b: Image.Image) -> dict:
    """Max per-channel difference, the number of pixels that differ, and their bbox."""
    if a.size != b.size:
        raise ValueError(f"sizes differ: {a.size} vs {b.size}")
    r, g, bl = ImageChops.difference(a.convert("RGB"), b.convert("RGB")).split()
    d = ImageChops.lighter(ImageChops.lighter(r, g), bl)
    hist = d.histogram()
    return {
        "max": max((i for i, n in enumerate(hist) if n and i), default=0),
        "pixels": sum(hist[1:]),
        "bbox": d.getbbox(),
    }


def modal_interior(img: Image.Image, rect: tuple[int, int, int, int]) -> tuple[int, int, int]:
    x, y, w, h = rect
    inset = min(_INSET, max(1, w // 4), max(1, h // 4))
    region = img.crop((x + inset, y + inset, x + w - inset, y + h - inset))
    return max(region.getcolors(maxcolors=region.width * region.height), key=lambda c: c[0])[1]


def poster_interior(fn: Placer, backdrop, fill_hex: str, text_hex: str, opacity: float) -> tuple[int, int, int]:
    """One `1080p` pill through `render_badge_groups()` on a flat poster; its interior."""
    cfg = with_opacity(ImageConfig(badge_text_color=text_hex), opacity)
    placed: list = []
    img = render(fn, flat(backdrop), cfg, [BadgeGroup(["1080p"], fill_hex, text_hex)], None, placed)
    _, rect = placed[0]
    return modal_interior(img, rect)


def chip_interior(backdrop, fill_hex: str, text_hex: str, opacity: float) -> tuple[int, int, int]:
    img, wh = render_over(backdrop, fill_hex, text_hex, alpha=badge_alpha(opacity))
    return sample_interior(img, wh)


def chip_vs_poster(fn: Placer, opacities=OPACITIES) -> list[dict]:
    cfg = ImageConfig()
    rows = []
    for name, colour_field, _ in BADGES:
        fill = getattr(cfg, colour_field)
        for opacity in opacities:
            for bd, rgb in BACKDROPS.items():
                chip = chip_interior(rgb, fill, cfg.badge_text_color, opacity)
                poster = poster_interior(fn, rgb, fill, cfg.badge_text_color, opacity)
                text = tuple(int(cfg.badge_text_color[i : i + 2], 16) for i in (1, 3, 5))
                rows.append(
                    {
                        "badge": name,
                        "opacity": opacity,
                        "backdrop": bd,
                        "chip": chip,
                        "poster": poster,
                        "chip_ratio": contrast_ratio(text, chip),
                        "poster_ratio": contrast_ratio(text, poster),
                        "delta": max(abs(p - c) for p, c in zip(poster, chip, strict=True)),
                    }
                )
    return rows


def glow_band(fn: Placer, opacity: float, grey: int = 128) -> list[tuple[int, int, int]]:
    """The pixels 1-8 px left of a lone pill's left edge, at its vertical centre."""
    cfg = with_opacity(ImageConfig(), opacity)
    placed: list = []
    img = render(fn, flat((grey,) * 3), cfg, [BadgeGroup(["1080p"], cfg.video_badge_color)], None, placed)
    _, (x, y, _w, h) = placed[0]
    return [img.getpixel((x - d, y + h // 2)) for d in range(1, 9)]


def floors(fn: Placer, *, step: int = 1) -> dict[str, float | None]:
    """The lowest opacity, in hundredths, at which the WORST shipped badge over
    black/white/grey posters clears AA and AAA, through the poster path."""
    cfg = ImageConfig()
    text = tuple(int(cfg.badge_text_color[i : i + 2], 16) for i in (1, 3, 5))
    found: dict[str, float | None] = {"AA": None, "AAA": None}
    for pct in range(100, 0, -step):
        opacity = pct / 100
        worst = min(
            contrast_ratio(text, poster_interior(fn, rgb, getattr(cfg, field), cfg.badge_text_color, opacity))
            for _, field, _ in BADGES
            for rgb in BACKDROPS.values()
        )
        for label, threshold in (("AA", AA), ("AAA", AAA)):
            if worst >= threshold:
                found[label] = opacity
        if worst < AA:
            break
    return found


ALGEBRA = ("#73485b", (0, 0, 0), 0.65)


def algebra_expected(power: int) -> tuple[int, int, int]:
    """Fill times a**power over black: power 1 is correct, 3 is what LEGACY rendered."""
    fill_hex, _, opacity = ALGEBRA
    a = badge_alpha(opacity) / 255
    return tuple(round(int(fill_hex[i : i + 2], 16) * a**power) for i in (1, 3, 5))


def algebra_rendered(fn: Placer) -> tuple[int, int, int]:
    fill_hex, backdrop, opacity = ALGEBRA
    return poster_interior(fn, backdrop, fill_hex, "#ffffff", opacity)


def tile_alpha_levels(fn_tile=None) -> set[int]:
    """Every alpha a 100% pill tile takes, over the README label set at every badge size.

    The 100% identity rests on this being {0, 255}: then each pixel is either
    wholly pill or not pill at all, and a paste and a composite agree on it."""
    fn_tile = fn_tile or overlay._render_pill_tile
    labels = ["4K", "1080p", "720p", "H.265", "HDR10", "EN JA", "dual-audio", "TrueHD Atmos", "sub-EN", "PG-13", "…"]
    levels: set[int] = set()
    for size in overlay._BADGE_SIZE_PX.values():
        for scale in (0.28, 1.0, 2.0):  # a preview, the reference poster, a 2000 px poster
            p = overlay._compute_layout_params(round(1000 * scale), ImageConfig())
            fs = max(8, round(size * scale))
            for text in labels:
                tile = fn_tile(text, "#134e4a", "#ffffff", 255, fs, p["pad_h"], p["pad_v"])
                levels.update(i for i, n in enumerate(tile.getchannel("A").histogram()) if n)
    return levels


# ── Report ──────────────────────────────────────────────────────────────────
def identity_rows(opacity: float, fn_new: Placer = SHIPPED, fn_old: Placer = legacy_place) -> list[tuple[str, dict]]:
    rows = []
    for name, base, cfg, groups, rating in readme_scenes():
        cfg = with_opacity(cfg, opacity)
        rows.append((name, diff(render(fn_old, base, cfg, groups, rating), render(fn_new, base, cfg, groups, rating))))
    return rows


def report() -> int:
    bad = 0
    print(f"Pillow {Image.__version__}; font: {overlay._load_font(20).getname()}")

    print("\n(1) LEGACY vs shipped at 100% -- must be byte-identical")
    rows = identity_rows(1.0)
    for name, d in rows:
        print(f"  {name:<40} max {d['max']:3d}  pixels {d['pixels']:7d}")
    moved = [n for n, d in rows if d["pixels"]]
    print(f"  {len(rows) - len(moved)}/{len(rows)} identical")
    bad += bool(moved)

    print("\n(2) LEGACY vs shipped at 65% -- the fix is visible here")
    for name, d in identity_rows(0.65):
        print(f"  {name:<40} max {d['max']:3d}  pixels {d['pixels']:7d}")

    print("\n(3) chip vs poster interior (white text)")
    print("  badge   opac  backdrop  chip            poster          chip:1  poster:1  LEGACY poster:1")
    shipped = chip_vs_poster(SHIPPED)
    legacy = chip_vs_poster(legacy_place)
    for s, old in zip(shipped, legacy, strict=True):
        print(
            f"  {s['badge']:<7} {s['opacity']:.2f}  {s['backdrop']:<8}  {str(s['chip']):<15} {str(s['poster']):<15}"
            f" {s['chip_ratio']:6.2f}  {s['poster_ratio']:8.2f}  {old['poster_ratio']:15.2f}"
        )
    worst = max(r["delta"] for r in shipped)
    print(f"  max per-channel chip/poster difference: {worst}")
    bad += worst > 0

    print("\n(4) glow band, 1-8 px left of a pill on grey 128 (red channel)")
    for opacity in (1.0, 0.65):
        old, new = glow_band(legacy_place, opacity), glow_band(SHIPPED, opacity)
        print(f"  {opacity:.2f}  LEGACY {[p[0] for p in old]}  shipped {[p[0] for p in new]}  same={old == new}")
        bad += old != new

    print("\n(5) algebra: rating #73485b over black at 65%")
    print(f"  fill*a   expected {algebra_expected(1)}  shipped renders {algebra_rendered(SHIPPED)}")
    print(f"  fill*a^3 expected {algebra_expected(3)}  LEGACY renders  {algebra_rendered(legacy_place)}")
    bad += algebra_rendered(SHIPPED) != algebra_expected(1)

    print("\n(6) opacity floors, worst shipped badge over black/white/grey, poster path")
    for label, fn in (("shipped", SHIPPED), ("LEGACY", legacy_place)):
        f = floors(fn)
        print(f"  {label:<8} AA {f['AA']}  AAA {f['AAA']}")

    print(f"\ntile alpha levels at 100%: {sorted(tile_alpha_levels())}")
    return 1 if bad else 0


# ── Self-test: must be able to fail in BOTH directions ──────────────────────
def self_test() -> int:
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"  {'ok  ' if ok else 'FAIL'}  {name}{': ' + detail if detail else ''}")
        if not ok:
            failures.append(name)

    base = gradient((400, 600))
    cfg = ImageConfig()
    groups = [
        BadgeGroup(["1080p", "H.265", "HDR10"], cfg.video_badge_color),
        BadgeGroup(["EN", "JA"], cfg.audio_badge_color),
    ]
    rating = BadgeGroup(["PG-13"], cfg.rating_badge_color)

    # The differ: silent on a render compared with itself, loud on one level.
    img = render(SHIPPED, base, cfg, groups, rating)
    check("diff of a render with itself is empty", diff(img, img)["pixels"] == 0)
    nudged = img.copy()
    nudged.putpixel((5, 5), tuple(c ^ 1 for c in nudged.getpixel((5, 5))))
    d = diff(img, nudged)
    check("diff sees one pixel moved one level", d["pixels"] == 1 and d["max"] == 1, str(d))

    # The swap must reach the render: LEGACY and shipped differ below 100%...
    half = with_opacity(cfg, 0.65)
    d = diff(render(legacy_place, base, half, groups, rating), render(SHIPPED, base, half, groups, rating))
    check("placer swap reaches the render (LEGACY != shipped at 65%)", d["pixels"] > 0, str(d))

    # ...and a compositor off by one alpha level at 100% must be reported.
    def off_by_one(layer, xy, text, fill_hex, text_hex, alpha, *rest):
        pill = overlay._render_pill_tile(text, fill_hex, text_hex, min(alpha, 254), *rest)
        overlay._composite_pill(layer, xy, overlay._render_glow(pill.size), pill)

    d = diff(render(legacy_place, base, cfg, groups, rating), render(off_by_one, base, cfg, groups, rating))
    check("a planted alpha-254 compositor is caught at 100%", d["pixels"] > 0 and d["max"] >= 1, str(d))

    # Chip vs poster must disagree under LEGACY (B21 as filed) and agree when shipped.
    fill = cfg.rating_badge_color
    old = poster_interior(legacy_place, (255, 255, 255), fill, "#ffffff", 0.65)
    chip = chip_interior((255, 255, 255), fill, "#ffffff", 0.65)
    check("LEGACY poster disagrees with the chip at 65% (B21 reproduced)", old != chip, f"{old} vs {chip}")
    new = poster_interior(SHIPPED, (255, 255, 255), fill, "#ffffff", 0.65)
    check("shipped poster equals the chip at 65%", new == chip, f"{new} vs {chip}")

    # The algebra both ways: LEGACY reproduces a**3, shipped a.
    check("LEGACY renders fill*a^3", algebra_rendered(legacy_place) == algebra_expected(3))
    check("shipped renders fill*a", algebra_rendered(SHIPPED) == algebra_expected(1))
    check("a and a^3 are distinguishable here", algebra_expected(1) != algebra_expected(3))

    # The level census: binary today, and it would see an antialiased tile.
    check("100% pill tiles are binary in alpha", tile_alpha_levels() == {0, 255}, str(sorted(tile_alpha_levels())))

    def smoothed(*args):
        t = overlay._render_pill_tile(*args)
        return t.resize((t.width * 2, t.height * 2)).resize(t.size, Image.LANCZOS)

    check("an antialiased tile is caught by the census", tile_alpha_levels(smoothed) != {0, 255})

    print("SELF-TEST " + ("PASSED" if not failures else f"FAILED: {', '.join(failures)}"))
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true", help="run the probe's own self-test and exit")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    if self_test():
        print("refusing to report: the self-test failed")
        return 2
    print()
    return report()


if __name__ == "__main__":
    sys.exit(main())
