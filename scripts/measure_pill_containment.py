#!/usr/bin/env python3
"""Measure whether every poster pill stays inside the poster margins (roadmap P7).

P7's containment half asks whether the layout *guarantees* that every pill
lands inside the poster margin -- at every ``badge_size``, poster aspect and
badge/rating corner -- or merely usually achieves it. The operator's 2026-09-23
direction adds the constraint that makes it a real question: containment must
not be bought by hiding metadata.

The probe does not reason about the layout; it renders. Every case goes
through the real ``overlay.render_badge_groups()`` with badge groups built by
the real ``pipeline._make_badge_groups()`` from an invented ``MediaInfo``, on a
blank synthetic canvas -- no library file, no poster, no network. Three
independent reads of each render:

* **rectangles** -- the ``(kind, (x, y, w, h))`` the render reports through its
  ``placed=`` hook (the hook B10's ``tests/test_rating_position.py`` uses);
* **text** -- what each pill actually says, recorded by wrapping
  ``overlay._pill_tile`` for the duration of the call (read side only: the
  wrapper returns the genuine tile). This is how hidden metadata is counted,
  and how the ``+N`` pill that counts it (P7, decision (d), 2026-10-05) is
  read: every badge not drawn must be in a ``+N`` of its group's colour;
* **pixels** -- the bounding box of every pixel painted in exactly a group's
  fill colour. It must agree with the reported rectangles, or the hook is not
  describing the render and every number from it is void.

Per render it counts: pills crossing the poster margin, pills off the canvas,
tag pills overlapping the rating, tag rows overlapping each other; hidden
metadata (labels dropped, labels truncated, badges hidden -- a badge being a
label's codec/format or one of its language codes); and three ways hiding can
be wrong: a hidden badge no ``+N`` counts (or a ``+N`` that over-counts), a
``…`` anywhere (the pre-P7 marker that said nothing of how much), and badges
drawn out of their label order (which would defeat ``prefer_languages``).

    python3 scripts/measure_pill_containment.py --self-test   # offline, in CI
    python3 scripts/measure_pill_containment.py               # the full grid
    python3 scripts/measure_pill_containment.py --json /tmp/p7.json
    python3 scripts/measure_pill_containment.py --breakpoints # how short a canvas fits
    python3 scripts/measure_pill_containment.py --budget      # what not hiding would cost
    python3 scripts/measure_pill_containment.py --db /tmp/copy/state.db --badge-size desktop

The full grid is 4 tag sets x 5 aspects x 4 widths x 3 sizes x 16 corner
pairs, with ``normalize_portrait`` both off and on for the aspects it pads:
6,144 renders, ~9 min on chromaserv (2026-09-26). It exits 1 if any render
crosses the margin, overlaps, or hides a badge without counting it. Before P7
shipped it did, at 2.39:1 with padding off (192 renders), and every hiding
render was uncounted. Hiding itself is reported, not failed: a 2-row budget per
group is the decided bound. ``--db`` renders every row of a COPIED state.db
(opened read-only; ~10 min for 10,583 rows) and counts the items that hide
something; nothing from the database is printed but counts.
"""

from __future__ import annotations

import argparse
import itertools
import json
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageChops  # noqa: E402

from app import overlay  # noqa: E402
from app.config import AppConfig, ImageConfig  # noqa: E402
from app.overlay import BadgeGroup  # noqa: E402
from app.pipeline import _make_badge_groups  # noqa: E402
from app.scanner import AudioTrack, MediaInfo, SubTrack  # noqa: E402

CORNERS = ("top-left", "top-right", "bottom-left", "bottom-right")
SIZES = ("desktop", "tv", "tv_plus")
WIDTHS = (300, 600, 1000, 2000)
# (name, width units, height units). 16:9 is a backdrop; 1:1 is the short poster.
# 2.39:1 (a scope backdrop) is beyond the spec's grid: it is the first common
# aspect past where the heaviest set stops fitting (see --breakpoints).
ASPECTS = (("2:3", 2, 3), ("27:40", 27, 40), ("16:9", 16, 9), ("1:1", 1, 1), ("2.39:1", 239, 100))
ELLIPSIS = "…"
COUNT_PILL = re.compile(r"\+(\d+)")

# Invented language codes (ISO 639-1, upper case as the scanner stores them).
LANGS = (
    "EN JA DE FR ES IT PT NL SV NO DA FI PL CS HU RO BG EL TR RU UK HE AR FA HI TH VI ID MS KO ZH "
    "HR SR SL SK LT LV ET IS GA CY EU CA GL TA TE KN ML BN MR GU PA UR SW AF SQ MK BS"
).split()


def _langs(n: int, start: int = 0) -> list[str]:
    return list(LANGS[start : start + n])


# Synthetic tag sets shaped on production's distribution, read from a copy of
# prod state.db on 2026-09-26 (10,583 rows; distinct subtitle languages per
# item p50 1 / p99 12 / p99.9 34-35 / max 52; audio p50 1 / p99.9 3 / max 21;
# longest single token DVB_SUBTITLE, longest rating "Rated Not Rated"). The
# codec and format names are the scanner's own vocabulary; nothing here is a
# real item.
TAG_SETS: dict[str, dict] = {
    "p50": {
        "video": ("1080p", "H.264", None),
        "audio": [("AAC", ["EN"])],
        "subs": [("SRT", ["EN"])],
        "rating": "R",
    },
    "p99": {
        "video": ("1080p", "H.265", None),
        "audio": [("DD+", ["EN"])],
        "subs": [("SRT", _langs(12))],
        "rating": "PG-13",
    },
    "p99.9": {
        "video": ("1080p", "H.265", "HDR10"),
        "audio": [("DD+", _langs(3))],
        "subs": [("PGS", _langs(35))],
        "rating": "TV-PG",
    },
    "max": {
        "video": ("4K", "H.265", "HDR10"),
        "audio": [("TrueHD", _langs(20)), ("AAC", _langs(1, 20))],
        "subs": [("PGS", _langs(52)), ("SRT", _langs(3)), ("DVB_SUBTITLE", [""])],
        "rating": "Not Rated",
    },
}


def media_info(spec: dict) -> MediaInfo:
    resolution, codec, hdr = spec["video"]
    audio = [AudioTrack(lang=lang, codec=c) for c, langs in spec["audio"] for lang in langs]
    subs = [SubTrack(lang=lang, format=f, embedded=True) for f, langs in spec["subs"] for lang in langs]
    return MediaInfo(
        resolution=resolution,
        languages=[],
        raw_audio_langs=[],
        video_codec=codec,
        hdr_type=hdr,
        audio_tracks=audio,
        subtitle_tracks=subs,
    )


def badge_groups(spec: dict, cfg: ImageConfig) -> tuple[list[BadgeGroup], BadgeGroup | None]:
    """The groups a scan would draw for this tag set -- the pipeline's own builder."""
    return _make_badge_groups(media_info(spec), spec["rating"], AppConfig(image=cfg))


# ── one render ───────────────────────────────────────────────────────────────


@dataclass
class Pill:
    kind: str  # "rating" | "tags"
    fill: str
    text: str
    rect: tuple[int, int, int, int]


@dataclass
class Render:
    size: tuple[int, int]
    margin: int
    pills: list[Pill]
    image: Image.Image
    groups: list[BadgeGroup]
    rating: BadgeGroup | None


def render(
    canvas: tuple[int, int],
    groups: list[BadgeGroup],
    rating: BadgeGroup | None,
    cfg: ImageConfig,
) -> Render:
    """Render through the real path and capture rectangles and pill text."""
    placed: list = []
    texts: list[tuple[str, str]] = []
    real = overlay._pill_tile

    def recording_tile(text, fill_hex, *rest):
        texts.append((text, fill_hex))
        return real(text, fill_hex, *rest)

    overlay._pill_tile = recording_tile
    try:
        image = overlay.render_badge_groups(Image.new("RGBA", canvas, (0, 0, 0, 0)), groups, rating, cfg, placed=placed)
    finally:
        overlay._pill_tile = real
    if len(texts) != len(placed):
        raise RuntimeError(f"{len(texts)} tiles drawn but {len(placed)} rectangles reported: hook and render disagree")
    pills = [Pill(kind, fill, text, tuple(rect)) for (kind, rect), (text, fill) in zip(placed, texts, strict=True)]
    margin = overlay._compute_layout_params(canvas[0], cfg)["margin"]
    return Render(canvas, margin, pills, image, groups, rating)


# ── detectors ────────────────────────────────────────────────────────────────


def intersects(a, b) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def crosses_margin(rect, size, margin) -> bool:
    x, y, w, h = rect
    W, H = size
    return x < margin or y < margin or x + w > W - margin or y + h > H - margin


def exact_fill_bbox(image: Image.Image, hex_color: str):
    """Bounding box of pixels painted exactly ``hex_color`` at full alpha, or None."""
    target = (*overlay._parse_color(hex_color), 255)
    mask = None
    for band, value in zip(image.split(), target, strict=True):
        m = band.point(lambda v, value=value: 255 if v == value else 0)
        mask = m if mask is None else ImageChops.multiply(mask, m)
    return mask.getbbox()


def atoms(label: str) -> list[str]:
    """A label's badges: its head (codec/format, one or two words) and then one
    per language code (2-3 upper-case letters, as the scanner writes them).

    Written here from that definition, not imported from the overlay, so a
    renderer that splits ``TrueHD Atmos`` is caught rather than agreed with."""
    words = label.split()
    i = 1
    while i < len(words) and not (
        words[i].isascii() and words[i].isalpha() and words[i].isupper() and len(words[i]) in (2, 3)
    ):
        i += 1
    return [" ".join(words[:i]), *words[i:]] if words else []


def hidden_counts(labels: list[str], drawn: list[str]) -> dict:
    """Compare a group's labels with the pill texts drawn in its colour, in order.

    ``+N`` pills are read as counts. Every other pill must be a run of the
    group's own badges, in order -- matched against the labels' badges, never
    re-parsed from the pill: a continuation pill such as ``AR 10 UK`` (a label
    holding a legacy numeric language) does not say where its badges split.
    A pre-P7 cut label (``PGS EN JA…``) is read without its ``…``, and the
    ``…`` itself is counted."""
    ellipsis = sum(ELLIPSIS in d for d in drawn)
    counted = sum(int(m.group(1)) for d in drawn if (m := COUNT_PILL.fullmatch(d)))
    pills = [d.rstrip(ELLIPSIS).strip() for d in drawn if d != ELLIPSIS and not COUNT_PILL.fullmatch(d)]
    wanted = [(i, a) for i, label in enumerate(labels) for a in atoms(label)]
    seq = [a for _, a in wanted]
    matched: list[int] = []
    pos = 0
    out_of_order = 0
    for text in pills:
        # The earliest run seq[j:j+k] at or after pos that spells this pill.
        hit = next(
            (
                (j, k)
                for j in range(pos, len(seq))
                for k in range(1, len(seq) - j + 1)
                if " ".join(seq[j : j + k]) == text
            ),
            None,
        )
        if hit is None:
            out_of_order += 1
            continue
        j, k = hit
        matched.extend(wanted[m][0] for m in range(j, j + k))
        pos = j + k
    per_label = [matched.count(i) for i in range(len(labels))]
    total = [len(atoms(label)) for label in labels]
    hidden = len(wanted) - len(matched)
    return {
        "dropped": sum(1 for n in per_label if n == 0),
        "truncated": sum(1 for n, t in zip(per_label, total, strict=True) if 0 < n < t),
        "tokens_hidden": hidden,
        "counted": counted,
        "miscounted": abs(hidden - counted),
        "ellipsis": ellipsis,
        "order": out_of_order,
    }


def check(r: Render) -> dict:
    """Every count this probe reports, for one render."""
    rating = [p for p in r.pills if p.kind == "rating"]
    tags = [p for p in r.pills if p.kind == "tags"]
    out = {
        "pills": len(r.pills),
        "margin": sum(crosses_margin(p.rect, r.size, r.margin) for p in r.pills),
        "off_canvas": sum(crosses_margin(p.rect, r.size, 0) for p in r.pills),
        "rating_overlap": sum(intersects(t.rect, q.rect) for t in tags for q in rating),
        "tag_overlap": sum(
            intersects(a.rect, b.rect)
            for a, b in itertools.combinations(tags, 2)
            if a.rect[1] != b.rect[1]  # pills of one row sit side by side by construction
        ),
    }

    # Hidden metadata, per group (each group has its own fill colour; a group
    # with no row of its own is counted in its colour on another group's row).
    hidden = dict.fromkeys(
        ("dropped", "truncated", "tokens_hidden", "counted", "miscounted", "ellipsis", "order", "tokens"), 0
    )
    by_fill: dict[str, int] = {}
    for group in [g for g in [*r.groups, r.rating] if g and g.labels]:
        kind = "rating" if group is r.rating else "tags"
        drawn = [p.text for p in r.pills if p.kind == kind and p.fill == group.fill_color]
        counts = hidden_counts(group.labels, drawn)
        for k, v in counts.items():
            hidden[k] += v
        hidden["tokens"] += sum(len(atoms(label)) for label in group.labels)
        if counts["tokens_hidden"]:
            by_fill[group.fill_color] = counts["tokens_hidden"]
    out.update(hidden)
    out["hidden_by_fill"] = by_fill

    # Pixels: each fill's painted extent must sit on the reported rectangles
    # (a rounded_rectangle paints its far edge inclusively, hence the 1 px),
    # and is itself checked against the margin.
    out["hook_mismatch"] = 0
    out["drawn_margin"] = 0
    fills = {p.fill for p in r.pills} | {g.fill_color for g in [*r.groups, r.rating] if g}
    W, H = r.size
    for fill in fills:
        bbox = exact_fill_bbox(r.image, fill)
        rects = [p.rect for p in r.pills if p.fill == fill]
        if not rects:
            out["hook_mismatch"] += bbox is not None
            continue
        ux0 = min(x for x, _, _, _ in rects)
        uy0 = min(y for _, y, _, _ in rects)
        ux1 = max(x + w for x, _, w, _ in rects) + 1
        uy1 = max(y + h for _, y, _, h in rects) + 1
        # Clipped rows: a rect partly off the canvas can only paint the visible
        # part, and one wholly off it paints nothing.
        cx0, cy0, cx1, cy1 = max(ux0, 0), max(uy0, 0), min(ux1, W), min(uy1, H)
        if cx0 >= cx1 or cy0 >= cy1:
            out["hook_mismatch"] += bbox is not None
            continue
        if bbox is None:
            out["hook_mismatch"] += 1
            continue
        x0, y0, x1, y1 = bbox
        if x0 < cx0 or y0 < cy0 or x1 > cx1 or y1 > cy1:
            out["hook_mismatch"] += 1
        # getbbox is exclusive on the far side; the inclusive edge is allowed.
        m = r.margin
        out["drawn_margin"] += x0 < m or y0 < m or x1 > W - m + 1 or y1 > H - m + 1
    return out


# ── the grid ─────────────────────────────────────────────────────────────────


def canvas_for(aspect: tuple[str, int, int], width: int, normalize: bool) -> tuple[int, int]:
    """The canvas render_badge_groups() receives in apply_overlay() for this poster."""
    _, aw, ah = aspect
    size = (width, round(width * ah / aw))
    if normalize and size[0] / size[1] > overlay._PORTRAIT_RATIO + 0.05:
        # The same test apply_overlay() makes, then the real padder's output size.
        return overlay._pad_to_portrait(Image.new("RGBA", size)).size
    return size


def grid_cases():
    for aspect in ASPECTS:
        _, aw, ah = aspect
        pads = aw / ah > overlay._PORTRAIT_RATIO + 0.05
        for normalize in (False, True) if pads else (True,):
            for width, size, (bp, rp), tag_set in itertools.product(
                WIDTHS, SIZES, itertools.product(CORNERS, CORNERS), TAG_SETS
            ):
                yield aspect, normalize, width, size, bp, rp, tag_set


def run_grid() -> list[dict]:
    records = []
    canvases: dict = {}
    for aspect, normalize, width, size, bp, rp, tag_set in grid_cases():
        key = (aspect, width, normalize)
        if key not in canvases:
            canvases[key] = canvas_for(aspect, width, normalize)
        cfg = ImageConfig(badge_size=size, badge_position=bp, rating_position=rp, normalize_portrait=normalize)
        groups, rating = badge_groups(TAG_SETS[tag_set], cfg)
        counts = check(render(canvases[key], groups, rating, cfg))
        records.append(
            {
                "aspect": aspect[0],
                "normalize": normalize,
                "width": width,
                "canvas": list(canvases[key]),
                "size": size,
                "badge_position": bp,
                "rating_position": rp,
                "tag_set": tag_set,
                **counts,
            }
        )
    return records


VIOLATIONS = (
    "margin",
    "off_canvas",
    "rating_overlap",
    "tag_overlap",
    "drawn_margin",
    "miscounted",
    "ellipsis",
    "order",
)


def summarise(records: list[dict]) -> None:
    n = len(records)
    print(f"renders: {n}")
    bad = [r for r in records if any(r[k] for k in VIOLATIONS)]
    print(f"renders with a containment violation: {len(bad)}")
    for k in (*VIOLATIONS, "hook_mismatch"):
        print(f"  {k:15s} renders {sum(1 for r in records if r[k]):5d}   total {sum(r[k] for r in records)}")
    if bad:
        worst = max(bad, key=lambda r: sum(r[k] for k in VIOLATIONS))
        print("worst violation:", json.dumps(worst))

    print("\nhidden metadata (tokens hidden / tokens in the set), worst over the 16 corner pairs:")
    cells: dict = defaultdict(list)
    for r in records:
        cells[(r["tag_set"], r["aspect"], r["normalize"], r["width"], r["size"])].append(r)
    header = f"{'tag set':7s} {'aspect':6s} {'norm':4s} {'width':>5s} " + " ".join(f"{s:>9s}" for s in SIZES)
    print(header)
    for tag_set in TAG_SETS:
        for aspect, normalize, width in sorted(
            {(a, nz, w) for (t, a, nz, w, _s) in cells if t == tag_set},
            key=lambda k: ([a[0] for a in ASPECTS].index(k[0]), k[1], k[2]),
        ):
            row = []
            for s in SIZES:
                rs = cells[(tag_set, aspect, normalize, width, s)]
                worst = max(rs, key=lambda r: (r["tokens_hidden"], r["dropped"]))
                row.append(f"{worst['tokens_hidden']:>3d}/{worst['tokens']:<3d}d{worst['dropped']}")
            print(
                f"{tag_set:7s} {aspect:6s} {'on' if normalize else 'off':4s} {width:5d} "
                + " ".join(f"{c:>9s}" for c in row)
            )

    by_set: dict = defaultdict(lambda: [0, 0])
    for r in records:
        by_set[r["tag_set"]][0] += 1
        by_set[r["tag_set"]][1] += bool(r["tokens_hidden"] or r["dropped"])
    print("\nrenders hiding anything, per tag set:", {k: f"{v[1]}/{v[0]}" for k, v in by_set.items()})
    empty = [r for r in records if r["dropped"] and r["tokens_hidden"] == r["tokens"]]
    print(f"renders where a whole tag set vanished: {len(empty)}")


# ── breakpoints: how short a canvas still contains the heaviest set ─────────


def fits(width: int, height: int, cfg: ImageConfig, tag_set: str = "max") -> bool:
    groups, rating = badge_groups(TAG_SETS[tag_set], cfg)
    c = check(render((width, height), groups, rating, cfg))
    return not any(c[k] for k in VIOLATIONS)


def breakpoints(width: int = 1000) -> None:
    """Shortest canvas height (normalize_portrait off) with zero violations for the
    max set, per size and corner pair; the monotonicity of the answer is checked
    at the reported height, one pixel below, and a spread of heights above."""
    print(f"width {width}: shortest height with zero violations (max tag set), as width:height")
    for size in SIZES:
        worst = (0, None)
        for bp, rp in itertools.product(CORNERS, CORNERS):
            cfg = ImageConfig(badge_size=size, badge_position=bp, rating_position=rp, normalize_portrait=False)
            lo, hi = 1, width * 2
            if not fits(width, hi, cfg):
                raise RuntimeError(f"{size} {bp}/{rp} does not fit even at {width}x{hi}")
            while lo < hi:
                mid = (lo + hi) // 2
                if fits(width, mid, cfg):
                    hi = mid
                else:
                    lo = mid + 1
            above = [lo + d for d in (1, 7, 31, 97) if lo + d <= width * 2]
            if lo > 1 and fits(width, lo - 1, cfg) or not all(fits(width, h, cfg) for h in above):
                raise RuntimeError(f"{size} {bp}/{rp}: containment is not monotonic in height near {lo}")
            worst = max(worst, (lo, (bp, rp)))
        h, (bp, rp) = worst
        print(f"  {size:8s} min height {h:4d}px -> {width / h:.2f}:1 (worst pair tags {bp} / rating {rp})")


# ── what not hiding would cost: arithmetic on the real font metrics ───────────


def budget(width: int = 1000, height: int = 1500) -> None:
    """For each tag set and size: rows the untruncated labels would need if a
    group wrapped onto further rows without limit, the rows a poster has, and
    the font a single row per group would need instead. Arithmetic on
    _load_font()'s own metrics and _compute_layout_params(), NOT a render: the
    shipped layout (P7) caps a group at 2 rows, so the uncapped one is not drawn."""
    print(f"poster {width}x{height}, tags bottom-left / rating top-left (defaults)")
    print(
        f"{'tag set':7s} {'size':8s} {'rows now':>8s} {'rows if wrapped':>15s} {'rows fit':>8s} {'1-row font px':>13s}"
    )
    for tag_set, size in itertools.product(TAG_SETS, SIZES):
        cfg = ImageConfig(badge_size=size)
        p = overlay._compute_layout_params(width, cfg)
        font = overlay._load_font(p["font_size"])
        row_h = overlay._measure_group_height(p["font_size"], p["pad_v"])
        avail = width - 2 * p["margin"]
        groups, rating = badge_groups(TAG_SETS[tag_set], cfg)
        wrapped = 0
        scale = 1.0
        for g in groups:
            # Token-granular wrap: a long label's languages continue on the next row.
            text_w = sum(font.getbbox(label)[2] - font.getbbox(label)[0] for label in g.labels)
            total = text_w + len(g.labels) * 2 * p["pad_h"] + (len(g.labels) - 1) * p["col_gap"]
            wrapped += -(-total // avail)
            scale = min(scale, avail / total)
        rows_now = len(groups) + bool(rating)
        fit = (height - 2 * p["margin"] + p["row_gap"]) // (row_h + p["row_gap"])
        print(
            f"{tag_set:7s} {size:8s} {rows_now:8d} {wrapped + bool(rating):15d} {fit:8d} "
            f"{p['font_size'] * scale:13.1f}"
        )


# ── production census: a COPY of state.db, read-only ─────────────────────────


def census(db: Path, badge_size: str = "tv", width: int = 1000, height: int = 1500) -> None:
    """Every index row's real badge groups, rendered at default settings: how many
    items hide something today, and in which category. Opens the copy mode=ro."""
    from sqlalchemy.orm import load_only

    from app.pipeline import _read_only_session, _tracks_from_row
    from app.state import MediaState

    cfg = ImageConfig(badge_size=badge_size)
    names = {
        cfg.video_badge_color: "video",
        cfg.audio_badge_color: "audio",
        cfg.sub_badge_color: "subtitles",
        cfg.rating_badge_color: "rating",
    }
    session = _read_only_session(db)
    # Only the columns read below: a copy of a released index lacks any column
    # main has added since (U5's field_order broke a plain query, 2026-10-06).
    cols = ("resolution", "video_codec", "hdr_type", "audio_tracks", "subtitle_tracks", "content_rating")
    rows = session.query(MediaState).options(load_only(*(getattr(MediaState, c) for c in cols))).all()
    items = hiding = dropped = 0
    violations = 0
    per_cat: dict[str, int] = defaultdict(int)
    tokens_hidden: list[int] = []
    for row in rows:
        audio, subs = _tracks_from_row(row)
        info = MediaInfo(
            resolution=row.resolution or "",
            languages=[],
            raw_audio_langs=[],
            video_codec=row.video_codec,
            hdr_type=row.hdr_type,
            audio_tracks=audio,
            subtitle_tracks=subs,
        )
        groups, rating = _make_badge_groups(info, row.content_rating, AppConfig(image=cfg))
        if not groups and not rating:
            continue
        items += 1
        c = check(render((width, height), groups, rating, cfg))
        violations += any(c[k] for k in VIOLATIONS)
        if c["tokens_hidden"] or c["dropped"]:
            hiding += 1
            dropped += bool(c["dropped"])
            tokens_hidden.append(c["tokens_hidden"])
            for fill in c["hidden_by_fill"]:
                per_cat[names.get(fill, fill)] += 1
    session.close()
    tokens_hidden.sort()
    print(
        f"rows {len(rows)}, items with a badge {items}, canvas {width}x{height}, badge_size {badge_size}, other settings default"
    )
    print(f"items with a containment violation: {violations}")
    print(f"items hiding anything: {hiding} ({100 * hiding / max(items, 1):.2f}%), with a label dropped: {dropped}")
    print(f"items hiding, by category: {dict(per_cat)}")
    if tokens_hidden:
        mid = tokens_hidden[len(tokens_hidden) // 2]
        print(f"tokens hidden per hiding item: median {mid}, max {tokens_hidden[-1]}")


# ── self-test ────────────────────────────────────────────────────────────────


def self_test() -> int:
    failures: list[str] = []

    def expect(cond: bool, msg: str) -> None:
        print(("  ok   " if cond else "  FAIL ") + msg)
        if not cond:
            failures.append(msg)

    base_cfg = ImageConfig()
    fill = base_cfg.video_badge_color

    # Detectors against a naive reference: set-of-pixels intersection.
    def pixels(rect):
        x, y, w, h = rect
        return {(i, j) for i in range(x, x + w) for j in range(y, y + h)}

    pairs = [((0, 0, 10, 10), (10, 0, 5, 5)), ((0, 0, 10, 10), (9, 9, 5, 5)), ((5, 5, 2, 2), (0, 0, 20, 20))]
    pairs += [((0, 0, 10, 10), (0, 10, 10, 10)), ((3, 3, 4, 4), (20, 20, 1, 1))]
    expect(
        all(intersects(a, b) == bool(pixels(a) & pixels(b)) for a, b in pairs),
        "intersects() agrees with a pixel-set reference",
    )
    expect(not crosses_margin((12, 12, 50, 20), (100, 100), 12), "a rect touching the margin line passes")
    expect(crosses_margin((11, 12, 50, 20), (100, 100), 12), "a rect 1 px into the margin fails")
    expect(crosses_margin((40, 69, 48, 20), (100, 100), 12), "a rect 1 px into the bottom margin fails")

    # Degenerate: nothing to draw gives nothing.
    empty = check(render((1000, 1500), [], None, base_cfg))
    expect(
        empty["pills"] == 0 and not any(empty[k] for k in (*VIOLATIONS, "hook_mismatch")),
        "no groups -> no pills, no violations",
    )

    # Direction 1: a single short pill must pass, and the hook must agree with the pixels.
    short = render((1000, 1500), [BadgeGroup(["SD"], fill)], None, base_cfg)
    c = check(short)
    expect(c["pills"] == 1 and short.pills[0].text == "SD", "the _pill_tile wrap sees the pill's text")
    expect(not any(c[k] for k in (*VIOLATIONS, "hook_mismatch")), f"a single short pill passes {c}")
    expect(c["tokens_hidden"] == 0 and c["dropped"] == 0, "a single short pill hides nothing")
    bbox = exact_fill_bbox(short.image, fill)
    x, y, w, h = short.pills[0].rect
    expect(bbox is not None and abs(bbox[0] - x) <= 1 and abs(bbox[3] - (y + h)) <= 1, "fill pixels sit on the rect")

    # The pixel read must catch a hook that lies about where it drew.
    lie = Render(
        short.size, short.margin, [Pill("tags", fill, "SD", (x + 300, y, w, h))], short.image, short.groups, None
    )
    expect(check(lie)["hook_mismatch"] == 1, "a rect reported 300 px from the drawn pill is a hook mismatch")
    unreported = Render(short.size, short.margin, [], short.image, short.groups, None)
    expect(check(unreported)["hook_mismatch"] == 1, "a drawn pill the hook did not report is a mismatch")

    # Direction 2: a planted oversized stack must be reported. The renderer now
    # clamps its stack (P7), so the plant is painted here, pill by pill, with
    # the renderer's own _place_pill(): twelve rows climbing from the bottom of
    # a short 16:9 canvas, off its top and over a rating on the opposite edge.
    # One fill per row: check() attributes pills to groups by fill colour.
    cfg = ImageConfig(badge_size="tv_plus", badge_position="bottom-left", rating_position="top-left")
    p = overlay._compute_layout_params(1000, cfg)
    row_h = overlay._measure_group_height(p["font_size"], p["pad_v"])
    font = overlay._load_font(p["font_size"])
    gm = overlay._GLOW_MARGIN
    layer = Image.new("RGBA", (1000, 562), (0, 0, 0, 0))
    planted: list[Pill] = []
    rating = BadgeGroup(["Rated R"], base_cfg.rating_badge_color)
    tall = [BadgeGroup([f"R{i}"], f"#{16 + i:02x}2b3c") for i in range(12)]
    rows = [("rating", rating, p["margin"])]
    rows += [("tags", g, 562 - p["margin"] - row_h - i * (row_h + p["row_gap"])) for i, g in enumerate(reversed(tall))]
    for kind, g, y in rows:
        bb = font.getbbox(g.labels[0])
        w = bb[2] - bb[0] + 2 * p["pad_h"]
        tile_args = (g.fill_color, g.text_color, p["alpha"], p["font_size"], p["pad_h"], p["pad_v"])
        overlay._place_pill(layer, (p["margin"] - gm, y - gm), g.labels[0], *tile_args)
        planted.append(Pill(kind, g.fill_color, g.labels[0], (p["margin"], y, w, row_h)))
    over = check(Render((1000, 562), p["margin"], planted, layer, tall, rating))
    expect(
        over["margin"] > 0 and over["off_canvas"] > 0, f"a planted 12-row stack crosses the margin and the edge {over}"
    )
    expect(over["rating_overlap"] > 0, "a planted 12-row stack is reported covering the opposite-edge rating")
    expect(over["drawn_margin"] > 0, "the pixel read also sees pills painted into the margin")
    expect(over["hook_mismatch"] == 0, "the hook stays truthful for clipped rows")

    # The same twelve groups through the real renderer: twelve rows cannot all
    # fit 562 px, so a renderer either overflows or hides, and the probe must
    # report one or the other (pre-P7 it overflowed; since, it hides and counts).
    real = check(render((1000, 562), tall, rating, cfg))
    expect(
        real["tokens_hidden"] > 0 or any(real[k] for k in VIOLATIONS),
        f"twelve rows on a 562 px canvas are reported as overflowing or hiding {real}",
    )

    # Counting: atoms, then +N read against hidden badges -- in both directions.
    expect(atoms("TrueHD Atmos EN JA") == ["TrueHD Atmos", "EN", "JA"], "a two-word codec is one badge")
    expect(atoms("PGS EN") == ["PGS", "EN"] and atoms("JA DE") == ["JA", "DE"], "format and continuation heads")
    expect(atoms("Rated Not Rated") == ["Rated Not Rated"], "a rating is one badge")
    label = ["PGS EN JA DE", "SRT FR"]
    good = hidden_counts(label, ["PGS EN", "+4"])
    expect(good["tokens_hidden"] == 4 and good["miscounted"] == 0, f"hidden badges counted by +N pass {good}")
    expect(good["dropped"] == 1 and good["truncated"] == 1, "a label cut and a label dropped are told apart")
    expect(hidden_counts(label, ["PGS EN"])["miscounted"] == 4, "hidden badges with no +N are miscounted")
    expect(hidden_counts(label, ["PGS EN", "+5"])["miscounted"] == 1, "a +N that over-counts is miscounted")
    expect(hidden_counts(label, ["PGS EN", "JA DE", "SRT FR"])["miscounted"] == 0, "a label continued on a row passes")
    old = hidden_counts(label, ["PGS EN JA…", "…"])
    expect(old["ellipsis"] == 2 and old["miscounted"] == 3, f"the pre-P7 cut label and … pill are caught {old}")
    expect(hidden_counts(label, ["PGS DE EN", "+2"])["order"] == 1, "badges drawn out of their order are caught")
    expect(hidden_counts(["TrueHD Atmos EN"], ["TrueHD", "+2"])["order"] == 1, "a split two-word codec is caught")
    legacy = ["SRT VI AR 10 UK EN"]  # a stale row's numeric "language", split mid-label
    expect(
        hidden_counts(legacy, ["SRT VI", "AR 10 UK", "+1"])["miscounted"] == 0,
        "a continuation pill starting with a code before a numeric token is matched as badges",
    )

    # Hidden metadata must be seen: 35 languages in one label on a 300 px poster,
    # and a single token too wide for the row.
    groups, rating = badge_groups(TAG_SETS["p99.9"], ImageConfig(badge_size="tv_plus"))
    r = render((300, 450), groups, rating, ImageConfig(badge_size="tv_plus"))
    h = check(r)
    expect(h["tokens_hidden"] >= 1, f"a 35-language label on a 300 px poster is reported hiding badges {h}")
    plus = sum(int(m.group(1)) for q in r.pills if (m := COUNT_PILL.fullmatch(q.text)))
    expect(h["counted"] == plus, "every +N the renderer drew is read")
    wide = check(render((300, 450), [BadgeGroup(["X" * 40], fill)], None, base_cfg))
    expect(
        wide["tokens_hidden"] == 1 and wide["margin"] == 0,
        f"an unfittable single token is hidden, not overflowed {wide}",
    )

    # The grid's tag sets have the shapes they claim.
    groups, _ = badge_groups(TAG_SETS["max"], base_cfg)
    sub = next(g for g in groups if g.fill_color == base_cfg.sub_badge_color)
    expect(len(sub.labels) == 3 and len(sub.labels[0].split()) == 53, "max set: 3 subtitle labels, 52 languages on PGS")

    if failures:
        print(f"SELF-TEST FAILED ({len(failures)}): the measurement is void")
        return 1
    print("self-test passed")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--breakpoints", action="store_true")
    ap.add_argument("--budget", action="store_true", help="rows/font a no-hiding layout would need")
    ap.add_argument("--db", type=Path, help="census over a COPY of state.db (opened read-only)")
    ap.add_argument("--badge-size", choices=SIZES, default="tv", help="badge_size for --db")
    ap.add_argument("--json", type=Path, help="write every render's counts here")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    if self_test() != 0:
        return 1
    if args.breakpoints:
        breakpoints()
        return 0
    if args.budget:
        budget()
        return 0
    if args.db:
        census(args.db, args.badge_size)
        return 0
    start = time.perf_counter()
    records = run_grid()
    elapsed = time.perf_counter() - start
    summarise(records)
    print(f"\ngrid runtime {elapsed:.1f}s")
    if args.json:
        args.json.write_text(json.dumps(records, indent=1))
    return 1 if any(any(r[k] for k in VIOLATIONS) for r in records) else 0


if __name__ == "__main__":
    sys.exit(main())
