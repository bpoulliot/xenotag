# Brand source artwork

Everything under `app/static/` that shows the mark is **generated** from this
directory by `scripts/generate_brand_assets.py`. Do not hand-edit the files in
`app/static/` — CI runs `--check` and a drifted file fails the build.

## Live sources

| file | what it is |
|---|---|
| `mark.png` | the orbital glyph alone, 936×1026 RGBA |
| `wordmark.png` | the full XENOTAG lockup, 4130×812 RGBA |

Both are **fully contained** (nothing clipped at an edge) and near enough to
speckle-free to trace. `mark.png` is *not* centred in its canvas, which is why
the generator squares everything on the solid bounding box first.

Regenerate after changing either:

    python3 scripts/generate_brand_assets.py
    # then bump VERSION -- static URLs are cache-busted by ?v=<VERSION>

## `vectors-wip/` — not sources yet

Vector versions of the mark and wordmark live here **until they match the
concept art exactly**. They are not wired into anything, and nothing generates
from them.

The ones supplied so far do not match: the icon draws two fat lens shapes in
*mirror* symmetry, which reads as an eye, where the concept art is a **C2
pinwheel** — 180° rotational symmetry, slender tapered blades. Those are
different symmetry groups, so they need redrawing rather than nudging. The same
two paths are reused as the wordmark's `O`, so the error appears in every file.
The letterforms themselves are fine and should not be redrawn.

**Roadmap item [P11]** owns this, and records the measurements plus the
recommended method (trace the alpha at threshold 128, re-apply the brand
gradients, so the art is reproduced by construction rather than by eye).

Preferred names, though anything recognisable is fine — they get normalised on
the way in:

    vectors-wip/mark.svg
    vectors-wip/wordmark-primary.svg
    vectors-wip/wordmark-medium.svg
    vectors-wip/wordmark-small.svg

Note the three weights arrived **inverted**: `primary` was `stroke-width="10"`,
`medium` `11.5` and `small` `13`, so the file named *small* was the boldest,
while the brand sheet captions it "reduced weight for tight spaces". One of the
two is wrong and it is six values per file either way.

### When P11 lands

The vectors graduate out of `vectors-wip/` to `mark.svg` and `wordmark.svg`
beside the PNGs and become the sources the rasters are generated from. That
also unlocks an **SVG favicon** (`<link rel="icon" type="image/svg+xml">`),
which is one file that stays sharp at every size instead of the fixed 16/32/48
set — the approach van1sh uses.
