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

## `vectors-wip/` — reference only, not sources

The PNGs above are the source of truth; the operator closed **P11** on 2026-09-25 with *"just use
the pngs."* These SVGs are the operator's early rough vectors, kept for reference. Nothing reads
them, and they do not match the concept art (the icon is mirror-symmetric where the concept is a
C2 pinwheel).

A traced vector was also tried and rejected: the concept art is a rendered image whose shapes sit
inside a soft glow, so tracing it reproduces the glow's irregular edge rather than the design's
crisp bevel line. `ROADMAP.md` P11 records the measurements. An exact vector needs a designer's
hand trace, or vectors exported from whatever produced the concept.
