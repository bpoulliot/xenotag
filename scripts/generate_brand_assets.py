#!/usr/bin/env python3
"""Generate every shipped brand raster from the two source images.

Roadmap item P8. `assets/brand/` holds the source artwork; everything under
`app/static/` that shows the mark is derived from it by this script, so the
sizes never drift apart and adding one is a one-line change.

Two things here are deliberate and are easy to undo by accident:

1. **The favicon and app icons get a Charcoal backing tile, the in-app logo
   does not.** The mark is two-tone — a light bone arc and a dark green swirl —
   so exactly one half of it loses contrast on any given background, and a
   browser picks the background for a favicon. On Charcoal the bone arc carries
   it (12.82:1) while the green fades (1.42:1); on light chrome that inverts.
   The tile puts both halves on their intended background whatever the chrome
   does. `logo.png` renders inside the app, where we control the background, so
   it stays transparent.

2. **The icon source is not centred in its canvas** (padding L122 T189 R228
   B159). Everything is squared on the *solid* bounding box first; generating
   straight from the canvas bakes that offset into every size.

    python3 scripts/generate_brand_assets.py           # write the assets
    python3 scripts/generate_brand_assets.py --check   # verify, write nothing
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "assets" / "brand"
OUT = ROOT / "app" / "static"

CHARCOAL = (23, 27, 25, 255)  # #171B19
TILE_RADIUS = 0.22  # corner radius as a fraction of tile size
TILE_INSET = 0.80  # mark size as a fraction of tile size
SS = 4  # supersampling factor for tile rendering

ICO_SIZES = (16, 32, 48)
LOGO_PX = 512
WORDMARK_PX = 1600


def solid_bbox(im: Image.Image, threshold: int = 128) -> tuple[int, int, int, int]:
    """Bounding box of pixels that are more than half opaque."""
    alpha = im.convert("RGBA").split()[-1]
    box = alpha.point(lambda v: 255 if v > threshold else 0).getbbox()
    if box is None:
        raise SystemExit("source image is fully transparent")
    return box


def squared_mark(im: Image.Image) -> Image.Image:
    """Trim to the solid bbox, then centre on a transparent square canvas."""
    mark = im.convert("RGBA").crop(solid_bbox(im))
    side = max(mark.size)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(mark, ((side - mark.width) // 2, (side - mark.height) // 2), mark)
    return canvas


def tile(mark: Image.Image, px: int, rounded: bool = True) -> Image.Image:
    """The mark on a Charcoal tile, rendered supersampled.

    rounded=False gives a full-bleed opaque square, which is what an
    apple-touch-icon must be: iOS applies its own mask, and transparent corners
    come out black rather than rounded.
    """
    big = px * SS
    if rounded:
        canvas = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        ImageDraw.Draw(canvas).rounded_rectangle([0, 0, big - 1, big - 1], radius=int(big * TILE_RADIUS), fill=CHARCOAL)
    else:
        canvas = Image.new("RGBA", (big, big), CHARCOAL)
    inner = mark.resize((int(big * TILE_INSET),) * 2, Image.LANCZOS)
    off = (big - inner.width) // 2
    canvas.paste(inner, (off, off), inner)
    out = canvas.resize((px, px), Image.LANCZOS)
    return out if rounded else out.convert("RGB")


def build() -> dict[str, bytes]:
    """Render every asset to bytes, keyed by filename under app/static."""
    mark = squared_mark(Image.open(SRC / "icon.png"))
    word = Image.open(SRC / "wordmark.png").convert("RGBA")
    word = word.crop(solid_bbox(word))

    out: dict[str, bytes] = {}

    def png(name: str, im: Image.Image) -> None:
        buf = io.BytesIO()
        im.save(buf, "PNG", optimize=True)
        out[name] = buf.getvalue()

    # In-app logo: transparent, we own the background it sits on.
    png("logo.png", mark.resize((LOGO_PX, LOGO_PX), Image.LANCZOS))

    # Browser and OS icons: Charcoal tile, because we do not own the background.
    png("favicon.png", tile(mark, 256))
    png("apple-touch-icon.png", tile(mark, 180, rounded=False))
    png("icon-192.png", tile(mark, 192))
    png("icon-512.png", tile(mark, 512))

    buf = io.BytesIO()
    tile(mark, max(ICO_SIZES)).save(buf, "ICO", sizes=[(s, s) for s in ICO_SIZES])
    out["favicon.ico"] = buf.getvalue()

    wm_h = round(word.height * WORDMARK_PX / word.width)
    png("wordmark.png", word.resize((WORDMARK_PX, wm_h), Image.LANCZOS))

    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="verify assets match the sources; write nothing")
    args = ap.parse_args()

    assets = build()

    if args.check:
        stale = []
        for name, data in assets.items():
            path = OUT / name
            if not path.exists():
                stale.append(f"{name}: missing")
            elif path.read_bytes() != data:
                stale.append(f"{name}: differs from the generated output")
        if stale:
            print("brand assets are out of date:")
            for line in stale:
                print(f"  {line}")
            print("\nre-run: python3 scripts/generate_brand_assets.py")
            return 1
        print(f"all {len(assets)} brand assets match their sources")
        return 0

    for name, data in assets.items():
        (OUT / name).write_bytes(data)
        print(f"  wrote app/static/{name:24} {len(data):>8,} bytes")
    print(f"\n{len(assets)} assets generated from {SRC.relative_to(ROOT)}/")
    print("Remember to bump VERSION — static URLs are cache-busted by ?v=<VERSION>.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
