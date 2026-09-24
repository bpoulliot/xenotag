#!/usr/bin/env python3
"""Measure whether `_PILL_CACHE`'s key actually covers everything `_pill_tile()` renders.

Roadmap item B3. A memo cache is only correct while its key is a *superset* of
the inputs its value depends on. `_pill_tile()` takes seven arguments; for the
life of the B3 defect the key carried five of them, so two posters whose widths
happened to agree on `font_size` and disagree on `pad_h`/`pad_v` shared one
cache entry — and whichever was rendered first supplied the tile for both.

The probe does not re-state the key tuple and check it by eye; restating the
key is how the bug got written in the first place. It **renders**. For every
poster width it asks the live cache for a tile, renders the same arguments
again with a scratch cache to get ground truth, and compares the bytes. A
width whose served tile differs from its own correct tile is a *collision*:
that poster is wearing another poster's badge.

    python3 scripts/measure_pill_cache_key.py              # sweep 200-4000px
    python3 scripts/measure_pill_cache_key.py --self-test  # probe self-check
    python3 scripts/measure_pill_cache_key.py --badge-size desktop --max-width 800
    python3 scripts/measure_pill_cache_key.py --compare    # the B3 before/after table

Exits non-zero when any width is served a tile it did not ask for.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

from app import overlay  # noqa: E402
from app.config import ImageConfig  # noqa: E402

# Captured at import so a monkeypatched `overlay._pill_tile` (the self-test
# installs two) can still be compared against the genuine renderer.
_REAL_PILL_TILE = overlay._pill_tile

PillArgs = tuple[str, str, str, int, int, int, int]
TileFn = Callable[..., Image.Image]

DEFAULT_TEXT = "1080p"
DEFAULT_FILL = "#134e4a"
DEFAULT_TEXT_COLOR = "#ffffff"

TERM_NAMES = ("text", "fill_hex", "text_hex", "alpha", "font_size", "pad_h", "pad_v")
# The key the app shipped until 2026-09-24: appearance only, no padding.
_NARROW_KEY = (0, 1, 2, 3, 4)
_BADGE_SIZES = ("desktop", "tv", "tv_plus")


def pill_args(width: int, cfg: ImageConfig, text: str, fill: str, text_color: str) -> PillArgs:
    """The seven arguments `_render_group()` would pass for a poster this wide."""
    p = overlay._compute_layout_params(width, cfg)
    return (text, fill, text_color, p["alpha"], p["font_size"], p["pad_h"], p["pad_v"])


def render_uncached(args: PillArgs) -> Image.Image:
    """Ground truth: the real renderer with a cache that cannot hit."""
    saved = overlay._PILL_CACHE
    overlay._PILL_CACHE = {}
    try:
        return _REAL_PILL_TILE(*args)
    finally:
        overlay._PILL_CACHE = saved


def _fingerprint(tile: Image.Image) -> tuple[tuple[int, int], bytes]:
    return (tile.size, tile.tobytes())


def check_sequence(arg_seq: list[PillArgs], tile_fn: TileFn | None = None) -> dict:
    """Serve every tuple in order through one shared cache; report who got the wrong tile.

    The order matters and is the point: whichever tuple a process meets first
    wins the cache entry, so a collision is always reported against the *later*
    poster, which is the one that renders wrong.
    """
    if tile_fn is None:
        tile_fn = overlay._pill_tile

    overlay.clear_pill_cache()
    truth: dict[PillArgs, tuple[tuple[int, int], bytes]] = {}
    collisions: list[dict] = []

    for index, args in enumerate(arg_seq):
        if args not in truth:
            truth[args] = _fingerprint(render_uncached(args))
        served = _fingerprint(tile_fn(*args))
        if served != truth[args]:
            collisions.append(
                {
                    "index": index,
                    "font_size": args[4],
                    "pad_h": args[5],
                    "pad_v": args[6],
                    "wanted_size": truth[args][0],
                    "served_size": served[0],
                }
            )

    # A stub keeps its own store; the module cache would read 0 for one.
    entries = len(getattr(tile_fn, "store", overlay._PILL_CACHE))
    overlay.clear_pill_cache()
    calls = len(arg_seq)
    return {
        "calls": calls,
        "collisions": collisions,
        "cache_entries": entries,
        "ideal_entries": len(truth),
        "hit_rate": (calls - entries) / calls if calls else 0.0,
    }


def sweep(
    cfg: ImageConfig,
    min_width: int = 200,
    max_width: int = 4000,
    text: str = DEFAULT_TEXT,
    fill: str = DEFAULT_FILL,
    text_color: str = DEFAULT_TEXT_COLOR,
    tile_fn: TileFn | None = None,
) -> dict:
    """Render one badge at every width in [min_width, max_width] through one shared cache."""
    widths = list(range(min_width, max_width + 1))
    arg_seq = [pill_args(w, cfg, text, fill, text_color) for w in widths]
    result = check_sequence(arg_seq, tile_fn)
    for c in result["collisions"]:
        c["width"] = widths[c["index"]]
    result["widths"] = widths
    result["arg_seq"] = arg_seq
    return result


def load_bearing(arg_seq: list[PillArgs], term: int) -> bool:
    """Does `term` ever vary among tuples that agree on every other term?

    A term that never varies is *redundant over this sweep* -- the key would be
    just as correct without it, today. That is an arithmetic accident of the
    layout constants, not a property of the cache, so it is a reason to say so
    in the report, never a reason to drop the term.
    """
    seen: dict[tuple, object] = {}
    for args in arg_seq:
        rest = tuple(v for i, v in enumerate(args) if i != term)
        if rest in seen and seen[rest] != args[term]:
            return True
        seen[rest] = args[term]
    return False


# ── the probe's own controls ─────────────────────────────────────────────────


def _cache_stub(key_indices: tuple[int, ...]) -> TileFn:
    """A `_pill_tile()` that renders correctly but keys its cache on `key_indices` only.

    The store is exposed as `stub.store` and is per-instance: a stub reused
    across two sweeps carries the first sweep's entries into the second and
    reports nonsense. Build a fresh one per sweep.
    """
    store: dict[tuple, Image.Image] = {}

    def stub(*args: object) -> Image.Image:
        key = tuple(args[i] for i in key_indices)
        if key not in store:
            store[key] = render_uncached(args)  # type: ignore[arg-type]
        return store[key]

    stub.store = store  # type: ignore[attr-defined]
    return stub


def self_test() -> int:
    """Fail in BOTH directions: see a planted collision, and stay silent without one.

    Every control renders through the real `_pill_tile()` body, so the only
    thing that differs between them is the cache key. A probe that could only
    confirm "zero collisions" would confirm it whether or not that were true.
    """
    ok = True
    cfg = ImageConfig()
    # 480-520 straddles the 494/501 boundary: same font_size 36, pad_v 2 vs 3.
    lo, hi = 480, 520

    broken = _cache_stub(_NARROW_KEY)  # the key shipped for the life of B3
    if not sweep(cfg, lo, hi, tile_fn=broken)["collisions"]:
        print(f"FAIL: a key omitting pad_h/pad_v must collide over {lo}-{hi}px, probe saw none")
        ok = False

    whole = _cache_stub(tuple(range(7)))  # every argument in the key
    n = len(sweep(cfg, 200, 4000, tile_fn=whole)["collisions"])
    if n:
        print(f"FAIL: a key covering every argument must not collide, probe saw {n}")
        ok = False

    # The two paddings reach different parts of the tile -- pad_h its width,
    # pad_v its height -- so catching one is no evidence of catching the other.
    # pad_h has to be planted by hand: no *width* can produce a pad_h collision
    # (see load_bearing() and the report), so a width sweep cannot test for it.
    base = (DEFAULT_TEXT, DEFAULT_FILL, DEFAULT_TEXT_COLOR, 255, 36)
    planted = {
        "pad_h": ((0, 1, 2, 3, 4, 6), [(*base, 4, 2), (*base, 5, 2)]),
        "pad_v": ((0, 1, 2, 3, 4, 5), [(*base, 4, 2), (*base, 4, 3)]),
    }
    for dropped, (indices, seq) in planted.items():
        if not check_sequence(seq, _cache_stub(indices))["collisions"]:
            print(f"FAIL: a key omitting {dropped} must collide on tuples that differ in {dropped}")
            ok = False
        if check_sequence(seq, _cache_stub(tuple(range(7))))["collisions"]:
            print(f"FAIL: a whole key must not collide on tuples that differ in {dropped}")
            ok = False

    # Ground truth must be genuinely uncached, or every comparison is a tautology.
    if _fingerprint(render_uncached((*base, 4, 2))) == _fingerprint(render_uncached((*base, 4, 3))):
        print("FAIL: pad_v 2 and 3 must render different tiles, or there is nothing to detect")
        ok = False
    if _fingerprint(render_uncached((*base, 4, 2))) == _fingerprint(render_uncached((*base, 5, 2))):
        print("FAIL: pad_h 4 and 5 must render different tiles, or there is nothing to detect")
        ok = False
    if overlay._PILL_CACHE:
        print("FAIL: render_uncached() leaked into the live cache")
        ok = False

    # load_bearing() must answer both ways on inputs whose answer is known.
    if load_bearing([(*base, 4, 2), (*base, 4, 3)], 5):
        print("FAIL: load_bearing() called pad_h load-bearing on tuples where only pad_v varies")
        ok = False
    if not load_bearing([(*base, 4, 2), (*base, 4, 3)], 6):
        print("FAIL: load_bearing() missed pad_v varying")
        ok = False

    print("self-test: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


# ── report ───────────────────────────────────────────────────────────────────


def compare(min_width: int, max_width: int, text: str = DEFAULT_TEXT) -> int:
    """Print the B3 before/after table by re-running the sweep against the old key.

    "Before" is the key the app shipped until 2026-09-24 —
    `(text, fill_hex, text_hex, alpha, font_size)` — reproduced as a stub so the
    numbers stay regenerable without checking out the defect.
    """
    print(f"widths {min_width}-{max_width}px, label={text!r}")
    print(f"{'badge_size':<12} {'entries':>17} {'collisions':>14} {'hit rate':>17}")
    print(f"{'':12} {'before -> after':>17} {'before -> after':>14} {'before -> after':>17}")
    worst = 0
    for size in _BADGE_SIZES:
        cfg = ImageConfig(badge_size=size)
        # A fresh stub per sweep: its store is per-instance and would otherwise
        # carry the previous badge size's entries into this one.
        before = sweep(cfg, min_width, max_width, text=text, tile_fn=_cache_stub(_NARROW_KEY))
        after = sweep(cfg, min_width, max_width, text=text)
        worst = max(worst, len(after["collisions"]))
        print(
            f"{size:<12} {before['cache_entries']:>7} -> {after['cache_entries']:<7}"
            f" {len(before['collisions']):>6} -> {len(after['collisions']):<6}"
            f" {before['hit_rate'] * 100:>7.2f}% -> {after['hit_rate'] * 100:.2f}%"
        )
    return 1 if worst else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-width", type=int, default=200)
    ap.add_argument("--max-width", type=int, default=4000)
    ap.add_argument("--badge-size", default=None, help="desktop | tv | tv_plus (default: config default)")
    ap.add_argument("--text", default=DEFAULT_TEXT, help="badge label to render")
    ap.add_argument("--self-test", action="store_true", help="run the probe's own self-test and exit")
    ap.add_argument("--list", type=int, default=5, help="how many colliding widths to print")
    ap.add_argument(
        "--compare",
        action="store_true",
        help="regenerate the B3 before/after table: the shipped key vs the pre-fix 5-term key",
    )
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    if args.compare:
        return compare(args.min_width, args.max_width, args.text)

    cfg = ImageConfig(**({"badge_size": args.badge_size} if args.badge_size else {}))
    r = sweep(cfg, args.min_width, args.max_width, text=args.text)
    n = len(r["collisions"])

    print(f"widths {args.min_width}-{args.max_width}px, badge_size={cfg.badge_size}, label={args.text!r}")
    print(f"  renders requested      {r['calls']}")
    print(f"  distinct tiles needed  {r['ideal_entries']}")
    print(f"  cache entries created  {r['cache_entries']}")
    print(f"  cache hit rate         {r['hit_rate'] * 100:.2f}%")
    print(f"  COLLISIONS             {n}")

    if n:
        print("\n  width  font_size  pad_h  pad_v   wanted -> served")
        for c in r["collisions"][: args.list]:
            print(
                f"  {c['width']:5d}  {c['font_size']:9d}  {c['pad_h']:5d}  {c['pad_v']:5d}   "
                f"{c['wanted_size']} -> {c['served_size']}"
            )
        if n > args.list:
            print(f"  ... and {n - args.list} more")

    # Which key terms this sweep can actually exercise. `pad_h` reads "never
    # varies" because every _BADGE_SIZE_PX value is an exact multiple of 8, so
    # every round(8*scale) step lands on a round(base*scale) step too; 5 (pad_v)
    # divides none of them. Arithmetic luck, not a guarantee -- a badge size
    # that is not a multiple of 8 would make pad_h collide like pad_v does.
    print("\n  key term varies within the swept range, holding the others equal:")
    for i, name in enumerate(TERM_NAMES):
        print(f"    {name:<10} {'yes' if load_bearing(r['arg_seq'], i) else 'no'}")
    return 1 if n else 0


if __name__ == "__main__":
    raise SystemExit(main())
