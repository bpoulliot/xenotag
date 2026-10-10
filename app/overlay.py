from __future__ import annotations

import io
import logging
import re
import shutil
import string
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .config import ImageConfig
from .wcag import LIGHT_DARK_MIDPOINT, LINEAR_8BIT, relative_luminance

log = logging.getLogger(__name__)

_FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
]

_font_cache: dict[int, ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if size in _font_cache:
        return _font_cache[size]
    for path in _FONT_PATHS:
        if Path(path).exists():
            try:
                font = ImageFont.truetype(path, size)
                _font_cache[size] = font
                return font
            except Exception:  # noqa: S110
                pass
    font = ImageFont.load_default()
    _font_cache[size] = font
    return font


# ── Pill tile cache ──────────────────────────────────────────────────────────
_PILL_CACHE: dict[tuple, Image.Image] = {}
_GLOW_CACHE: dict[tuple[int, int], Image.Image] = {}
_GLOW_MARGIN = 14
_GLOW_EXPAND = 4
_GLOW_BLUR = 6

_REFERENCE_WIDTH = 1000
_BADGE_SIZE_PX: dict[str, int] = {"desktop": 56, "tv": 72, "tv_plus": 88}


# Roadmap P6: with image.adapt_badge_colors on, a badge row whose poster region
# is on the LABEL's side of this relative luminance (lighter than it, for a light
# label) is drawn in the backup palette instead. Measured, not picked: it is the
# lightest flat region over which the shipped main palette still renders AAA
# (7:1) at 65% opacity -- and that boundary barely moves with opacity (grey 94
# at 50%, 98 at 65%, 109 at 80%), which is what lets one threshold serve the
# whole slider. scripts/measure_adaptive_palette.py re-derives it.
# Measured through B1's instrument at 65% opacity, which the poster path matches
# since roadmap B21 (#124); the probe's self-test compares the two. Signed off by
# the operator on 2026-10-09 -- do not tune it without a new decision.
ADAPT_LUMINANCE_THRESHOLD = 0.12


@dataclass
class BadgeGroup:
    labels: list[str]
    fill_color: str
    text_color: str = "#ffffff"
    # Roadmap P6: the fill this row uses instead when the poster under it would
    # wash the main one out. Read only with image.adapt_badge_colors on.
    backup_fill_color: str | None = None


def prefer_order(langs: list[str], prefer: list[str]) -> list[str]:
    """``langs`` with the codes in ``prefer`` first, in ``prefer``'s order.

    Everything else follows in its original order. Order only: the result holds
    exactly the items of ``langs`` (roadmap P7, ``image.prefer_languages``).
    """
    if not prefer:
        return list(langs)
    rank = {code: i for i, code in enumerate(prefer)}
    # sorted() is stable, so unlisted codes keep today's relative order.
    return sorted(langs, key=lambda code: rank.get(code, len(prefer)))


def order_pills_by_language(pills: dict[str, list[str]], prefer: list[str]) -> list[str]:
    """Pill labels for ``{codec_or_format: [langs]}``, preferred languages first.

    Within each pill its languages are put in ``prefer`` order; the pills are
    then ranked by the best-preferred language each carries, pills carrying none
    keeping today's order after them. With ``prefer`` empty this is exactly the
    label list the row builders produced before P7.
    """
    labels: list[tuple[int, str]] = []
    for key, langs in pills.items():
        ordered = prefer_order(langs, prefer)
        rank = min((prefer.index(c) for c in ordered if c in prefer), default=len(prefer))
        labels.append((rank, f"{key} {' '.join(ordered)}" if ordered else key))
    return [label for _, label in sorted(labels, key=lambda t: t[0])]


def clear_pill_cache() -> None:
    _PILL_CACHE.clear()
    _GLOW_CACHE.clear()


def _pill_tile(
    text: str,
    fill_hex: str,
    text_hex: str,
    alpha: int,
    font_size: int,
    pad_h: int,
    pad_v: int,
) -> Image.Image:
    # Every argument, not just the ones that looked like "appearance". The
    # padding changes the tile's size, and _compute_layout_params() rounds
    # font_size and padding out of the poster width independently -- 494px and
    # 501px both give font_size 36 with pad_v 2 and 3 (roadmap B3). A key that
    # is not a superset of the renderer's inputs serves the first poster's tile
    # to every later one that hashes to it.
    key = (text, fill_hex, text_hex, alpha, font_size, pad_h, pad_v)
    if key in _PILL_CACHE:
        return _PILL_CACHE[key]
    tile = _render_pill_tile(text, fill_hex, text_hex, alpha, font_size, pad_h, pad_v)
    _PILL_CACHE[key] = tile
    return tile


def _render_pill_tile(
    text: str,
    fill_hex: str,
    text_hex: str,
    alpha: int,
    font_size: int,
    pad_h: int,
    pad_v: int,
) -> Image.Image:
    """Render one pill tile -- the fill and its label, no glow -- bypassing the cache.

    `_pill_tile()` is the only caller on the poster path. The contrast check in
    `app.contrast` calls this directly, so measuring a colour the operator is
    only trying out neither reads a cached tile nor leaves one behind.

    The tile is `_GLOW_MARGIN` larger than the pill on every side, so it lines
    up with the glow `_render_glow()` draws for the same size; `_composite_pill()`
    puts the two together.
    """
    font = _load_font(font_size)
    fill_rgb = _parse_color(fill_hex)
    text_rgb = _parse_color(text_hex)

    ref_h = font.getbbox("AgfpQ")[3] - font.getbbox("AgfpQ")[1]
    pill_h = ref_h + pad_v * 2
    bbox = font.getbbox(text)
    pill_w = bbox[2] - bbox[0] + pad_h * 2

    gm = _GLOW_MARGIN
    pill = Image.new("RGBA", (pill_w + 2 * gm, pill_h + 2 * gm), (0, 0, 0, 0))
    pd = ImageDraw.Draw(pill)
    pd.rounded_rectangle([(gm, gm), (gm + pill_w, gm + pill_h)], radius=8, fill=(*fill_rgb, alpha))
    text_h = bbox[3] - bbox[1]
    ty = gm + pad_v + (ref_h - text_h) // 2 - bbox[1]
    pd.text((gm + pad_h - bbox[0], ty), text, font=font, fill=(*text_rgb, 255))
    return pill


def _glow_tile(size: tuple[int, int]) -> Image.Image:
    """The glow for a pill tile of `size`, cached. It depends on nothing else."""
    if size not in _GLOW_CACHE:
        _GLOW_CACHE[size] = _render_glow(size)
    return _GLOW_CACHE[size]


def _render_glow(size: tuple[int, int]) -> Image.Image:
    """Render the white halo for a pill tile of `size` (the tile's, glow margin included)."""
    gm = _GLOW_MARGIN
    pill_w, pill_h = size[0] - 2 * gm, size[1] - 2 * gm
    glow = Image.new("RGBA", size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.rounded_rectangle(
        [(gm - _GLOW_EXPAND, gm - _GLOW_EXPAND), (gm + pill_w + _GLOW_EXPAND, gm + pill_h + _GLOW_EXPAND)],
        radius=8 + _GLOW_EXPAND,
        fill=(255, 255, 255, 210),
    )
    glow = glow.filter(ImageFilter.GaussianBlur(radius=_GLOW_BLUR))

    # Clear the glow out from under the pill footprint. Behind a translucent
    # fill the glow is what the viewer sees THROUGH the badge, which pinned
    # every badge's rendered contrast near white whatever the poster was
    # (roadmap B1: 3.7-5.3:1 against text the config claimed was 9.4-11.0:1).
    # Punched out, the glow is what it was meant to be -- a halo AROUND the
    # pill -- and the rendered fill is the configured colour. Deflated by 1px
    # so the pill's outermost pixel ring still lands on glow, not a hard cut
    # (the rectangle itself is not antialiased: at 100% that ring is opaque).
    # NOTE: filter() returns a new image, so the Draw handle must be rebound.
    gd = ImageDraw.Draw(glow)
    gd.rounded_rectangle(
        [(gm + 1, gm + 1), (gm + pill_w - 1, gm + pill_h - 1)],
        radius=7,
        fill=(0, 0, 0, 0),
    )
    return glow


def _composite_pill(layer: Image.Image, xy: tuple[int, int], glow: Image.Image, pill: Image.Image) -> None:
    """Draw one pill and its glow onto `layer`, a transparent row layer, at `xy`.

    The two are composited differently, on purpose (roadmap B21, decision (b)):

    - The glow is pasted with itself as the mask, as it always was. A masked
      paste blends every band, alpha included, so the glow lands squared --
      and that is the look every poster has had, so it is kept.
    - The pill is alpha-composited, so its fill lands at exactly the alpha
      `badge_opacity` names. Pasted the same way as the glow, the fill landed
      at a**3 over (1 - a**2) of the poster -- at a saved 0.65 the poster showed
      through as if the opacity were 0.42 -- and `app.contrast`, which models
      a, measured a badge no poster had.

    At 100% the two are byte-identical to the old single paste: the pill's
    rounded rectangle is not antialiased, so every pixel of it is either fully
    opaque or not the pill at all (measured, `scripts/measure_pill_composite.py`).
    """
    layer.paste(glow, xy, glow)
    layer.alpha_composite(pill, dest=xy)


def badge_alpha(opacity: float) -> int:
    """The fill alpha a `badge_opacity` renders at. One mapping, shared with
    `app.contrast`, so the measured ratio is the ratio of what renders."""
    return int(opacity * 255)


def _parse_color(hex_str: str) -> tuple[int, int, int]:
    """``#rrggbb`` to an RGB tuple. Anything else raises ValueError.

    It used to return black for every other spelling, silently (roadmap B6).
    ImageConfig now normalises every colour to ``#rrggbb`` at validation, so a
    value reaching here in another form is a bug upstream, and a loud one is
    better than a poster full of black badges.
    """
    if not (len(hex_str) == 7 and hex_str[0] == "#" and all(c in string.hexdigits for c in hex_str[1:])):
        raise ValueError(f"not a #rrggbb colour: {hex_str!r}")
    return (int(hex_str[1:3], 16), int(hex_str[3:5], 16), int(hex_str[5:7], 16))


def _find_image(folder: Path, targets: list[str]) -> Path | None:
    for name in targets:
        p = folder / name
        if p.exists():
            return p
    return None


def _backup_path(image_path: Path, suffix: str) -> Path:
    return image_path.with_name(image_path.name + suffix)


def _compute_layout_params(img_w: int, cfg: ImageConfig) -> dict:
    scale = img_w / _REFERENCE_WIDTH
    base_px = _BADGE_SIZE_PX.get(cfg.badge_size, _BADGE_SIZE_PX["tv"])
    return {
        "font_size": max(8, round(base_px * scale)),
        "pad_h": max(3, round(8 * scale)),
        "pad_v": max(2, round(5 * scale)),
        "col_gap": max(4, round(10 * scale)),
        "row_gap": max(4, round(12 * scale)),
        "margin": max(4, round(12 * scale)),
        "alpha": badge_alpha(cfg.badge_opacity),
    }


def region_luminance(img: Image.Image, box: tuple[int, int, int, int]) -> float | None:
    """Mean WCAG relative luminance of ``img`` inside ``box`` (clipped to the image).

    Averaged in linear light, per channel, from the region's histogram -- exact,
    and independent of the region's size. None if the box misses the image.
    """
    x0, y0 = max(0, box[0]), max(0, box[1])
    x1, y1 = min(img.width, box[2]), min(img.height, box[3])
    if x1 <= x0 or y1 <= y0:
        return None
    hist = img.crop((x0, y0, x1, y1)).convert("RGB").histogram()
    n = (x1 - x0) * (y1 - y0)
    r, g, b = (
        sum(count * LINEAR_8BIT[v] for v, count in enumerate(hist[i * 256 : (i + 1) * 256])) / n for i in range(3)
    )
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def adapted_fill(
    poster: Image.Image,
    box: tuple[int, int, int, int],
    fill_color: str,
    backup_fill_color: str,
    text_color: str,
) -> str:
    """The fill a badge row gets over ``box`` of ``poster`` (roadmap P6).

    The backup palette is for the regions that wash the label out: under a
    translucent fill the poster pulls the rendered colour toward itself, which
    hurts exactly when the poster is on the label's side of mid-grey -- light
    posters for a light label, dark ones for a dark label. One threshold,
    ADAPT_LUMINANCE_THRESHOLD, decides which side the region is on.
    """
    lum = region_luminance(poster, box)
    if lum is None:
        return fill_color
    region_is_light = lum >= ADAPT_LUMINANCE_THRESHOLD
    label_is_light = relative_luminance(_parse_color(text_color)) >= LIGHT_DARK_MIDPOINT
    return backup_fill_color if region_is_light == label_is_light else fill_color


def _measure_group_height(
    font_size: int,
    pad_v: int,
) -> int:
    """Return the pixel height of one badge row."""
    font = _load_font(font_size)
    ref_h = font.getbbox("AgfpQ")[3] - font.getbbox("AgfpQ")[1]
    return ref_h + pad_v * 2


# Roadmap P7, decision (d): a tag group wraps onto at most this many rows, and
# whatever still does not fit is counted in a "+N" pill closing its last row.
_ROW_BUDGET = 2

# One pill as laid out: (text, width px, fill colour, text colour, its badges).
# The badges are carried, never re-read from the text: a continuation pill's
# text alone does not say where its badges split (``AR 10 UK`` from a label
# holding a legacy numeric language would parse as the one head ``AR 10``).
_Pill = tuple[str, int, str, str, tuple[str, ...]]


# A language code as the scanner writes it (`_lang3_to_lang2()`): ISO 639-1, or
# the 3-letter code where there is none, upper case.
_LANG_CODE = re.compile(r"[A-Z]{2,3}")


def _atoms(label: str) -> list[str]:
    """A pill label's units: its head, then one per language code.

    The head is the codec or format and is never split, though it may be two
    words (``TrueHD Atmos``, ``DD+ Atmos``). Everything after it in an audio or
    subtitle label is a language code. Labels only: a pill's text is never
    re-parsed (see `_Pill`).
    """
    tokens = label.split()
    head = tokens[:1]
    rest = tokens[1:]
    while rest and not _LANG_CODE.fullmatch(rest[0]):
        head.append(rest.pop(0))
    return [" ".join(head), *rest] if head else []


def _pill_width(font, text: str, pad_h: int) -> int:
    bb = font.getbbox(text)
    return bb[2] - bb[0] + pad_h * 2


def _flow_rows(
    labels: list[str],
    widths: list[int],
    font,
    pad_h: int,
    col_gap: int,
) -> tuple[list[list[tuple[tuple[str, ...], int]]], int]:
    """Flow ``labels`` over ``len(widths)`` rows, row i at most ``widths[i]`` px wide.

    The unit is the badge -- a label's codec or format, or one of its language
    codes (`_atoms()`; roadmap P7). A label is one pill while it fits. One that
    does not fit the space left on a row starts the next row if it would fit
    there whole, and is otherwise split between badges, its remaining languages
    continuing as a pill on the next row; it is never split after its head
    alone once a row has other pills on it. Order is kept throughout, so
    ``prefer_languages`` decides what survives.

    Returns the rows as ``(badges, width)`` pills and the number of badges that
    did not fit, which the caller counts in a ``+N`` pill. A badge wider than
    every row cannot be drawn at all; it is counted and skipped.
    """
    widest = max(widths)
    queue: list[list[str]] = []
    hidden = 0
    for label in labels:
        tokens = []
        for token in _atoms(label):
            if _pill_width(font, token, pad_h) <= widest:
                tokens.append(token)
            else:
                hidden += 1
        if tokens:
            queue.append(tokens)

    rows: list[list[tuple[tuple[str, ...], int]]] = [[]]
    used = [0]
    for i, tokens in enumerate(queue):
        while tokens:
            r = len(rows) - 1
            gap = col_gap if rows[r] else 0
            room = widths[r] - used[r] - gap
            k = len(tokens)
            while k and _pill_width(font, " ".join(tokens[:k]), pad_h) > room:
                k -= 1
            has_next = r + 1 < len(widths)
            if k < len(tokens) and has_next:
                fits_next = _pill_width(font, " ".join(tokens), pad_h) <= widths[r + 1]
                if k == 0 or (rows[r] and (k < 2 or fits_next)):
                    rows.append([])
                    used.append(0)
                    continue
            if k == 0:
                break
            w = _pill_width(font, " ".join(tokens[:k]), pad_h)
            rows[r].append((tuple(tokens[:k]), w))
            used[r] += gap + w
            tokens = tokens[k:]
            if tokens and has_next:
                rows.append([])
                used.append(0)
            elif tokens:
                break
        if tokens:
            hidden += len(tokens) + sum(len(t) for t in queue[i + 1 :])
            break
    return rows, hidden


def _close_row(
    row: list[_Pill],
    width: int,
    counts: list[tuple[int, str, str]],
    font,
    pad_h: int,
    col_gap: int,
) -> tuple[list[_Pill], list[tuple[int, str, str]]]:
    """End ``row`` with a ``+N`` pill for each ``(n, fill, text)`` in ``counts``.

    ``counts[0]`` is the row's own group; badges trimmed off the row's tail to
    make room are added to it, so N stays the exact number of badges not drawn.
    Further entries count groups the canvas had no row for, in their own colour.
    Returns the row and the entries that did not fit even on an emptied row,
    for the caller to close onto another row.
    """
    row = list(row)
    counts = [list(c) for c in counts]

    def tail() -> list[_Pill]:
        out = []
        for n, fill, text in counts:
            if n:
                label = f"+{n}"
                out.append((label, _pill_width(font, label, pad_h), fill, text, ()))
        return out

    def row_width(pills: list[_Pill]) -> int:
        return sum(p[1] for p in pills) + col_gap * max(0, len(pills) - 1)

    while row and row_width(row + tail()) > width:
        _, _, fill, text_color, badges = row.pop()
        counts[0][0] += 1
        if len(badges) > 1:
            text = " ".join(badges[:-1])
            row.append((text, _pill_width(font, text, pad_h), fill, text_color, badges[:-1]))
    closing = tail()
    left: list[tuple[int, str, str]] = []
    while closing and row_width(row + closing) > width:
        closing.pop()
        left.insert(0, tuple(counts.pop()))
    return row + closing, left


def _draw_rows(
    base: Image.Image,
    rows: list[list[_Pill]],
    position: str,
    alpha: int,
    font_size: int,
    pad_h: int,
    pad_v: int,
    col_gap: int,
    row_gap: int,
    margin: int,
    y_offset: int = 0,
    backups: dict[str, str] | None = None,
    poster: Image.Image | None = None,
) -> tuple[Image.Image, list[tuple[int, int, int, int]]]:
    """Draw one group's rows as a block ``y_offset`` px in from its corner's edge.

    The rows read top to bottom at every corner; each is aligned to its corner's
    side. With ``backups`` (main fill -> backup fill) and ``poster`` given, each
    row's fills are chosen by the poster region that row covers (roadmap P6).
    Returns the image and the ``(x, y, w, h)`` of every pill placed.
    """
    if not any(rows):
        return base, []

    img_w, img_h = base.size
    pill_h = _measure_group_height(font_size, pad_v)
    is_bottom = "bottom" in position
    is_right = "right" in position
    n = len(rows)

    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    gm = _GLOW_MARGIN
    rects = []
    for i, row in enumerate(rows):
        if not row:
            continue
        if is_bottom:
            y = img_h - margin - pill_h - y_offset - (n - 1 - i) * (pill_h + row_gap)
        else:
            y = margin + y_offset + i * (pill_h + row_gap)
        row_w = sum(pill[1] for pill in row) + col_gap * (len(row) - 1)
        x = (img_w - margin - row_w) if is_right else margin
        # Roadmap P6: sample exactly the strip this row's pills will cover, from
        # the bare poster -- not `base`, where a neighbouring row's glow may
        # already sit. Each wrap row decides for itself, and a "+N" pill counting
        # another group follows that group's backup. The choice reaches
        # _pill_tile() as its fill_hex argument, so it is part of the cache key
        # like every other input and a tile rendered over a light region is never
        # served over a dark one.
        row_box = (x, y, x + row_w, y + pill_h)
        for text, w, fill, text_color, _ in row:
            if backups and poster is not None and fill in backups:
                fill_hex = adapted_fill(poster, row_box, fill, backups[fill], text_color)
            else:
                fill_hex = fill
            _place_pill(overlay, (x - gm, y - gm), text, fill_hex, text_color, alpha, font_size, pad_h, pad_v)
            rects.append((x, y, w, pill_h))
            x += w + col_gap

    return Image.alpha_composite(base, overlay), rects


def _layout_group(
    group: BadgeGroup,
    n_rows: int,
    row_w: int,
    narrow: int | None,
    reserve_w: int,
    font,
    pad_h: int,
    col_gap: int,
    counted: list[tuple[int, str, str]] = (),
) -> tuple[list[list[_Pill]], list[tuple[int, str, str]]]:
    """Lay ``group`` out over ``n_rows`` rows, closing the last with its ``+N``.

    Row ``narrow`` (if any) is ``reserve_w`` px shorter: it shares its band with
    the rating across the poster (roadmap B10). Each ``(n, fill, text)`` in
    ``counted`` is a group with no row of its own, closed onto this group's last
    row as ``+n`` in its colour. Returns the rows and the ``counted`` entries
    that did not fit there.
    """
    widths = [row_w - (reserve_w if i == narrow else 0) for i in range(n_rows)]
    flowed, hidden = _flow_rows(group.labels, widths, font, pad_h, col_gap)
    rows: list[list[_Pill]] = [
        [(" ".join(badges), w, group.fill_color, group.text_color, badges) for badges, w in r] for r in flowed
    ]
    rows += [[] for _ in range(n_rows - len(rows))]
    counts = [(hidden, group.fill_color, group.text_color), *counted]
    left: list[tuple[int, str, str]] = []
    if any(n for n, _, _ in counts):
        rows[-1], left = _close_row(rows[-1], widths[-1], counts, font, pad_h, col_gap)
    return rows, left


def _rows_wanted(group: BadgeGroup, row_w: int, narrow_last: bool | None, reserve_w: int, font, pad_h, col_gap) -> int:
    """The fewest rows, up to ``_ROW_BUDGET``, that show every token of ``group``.

    ``narrow_last`` says which row shares the rating's band: the last (True),
    the first (False) or none (None).
    """
    for n in range(1, _ROW_BUDGET + 1):
        narrow = None if narrow_last is None else (n - 1 if narrow_last else 0)
        widths = [row_w - (reserve_w if i == narrow else 0) for i in range(n)]
        if _flow_rows(group.labels, widths, font, pad_h, col_gap)[1] == 0:
            return n
    return _ROW_BUDGET


def _place_pill(
    layer: Image.Image,
    xy: tuple[int, int],
    text: str,
    fill_hex: str,
    text_hex: str,
    alpha: int,
    font_size: int,
    pad_h: int,
    pad_v: int,
) -> None:
    tile = _pill_tile(text, fill_hex, text_hex, alpha, font_size, pad_h, pad_v)
    _composite_pill(layer, xy, _glow_tile(tile.size), tile)


def render_badge_groups(
    base: Image.Image,
    groups: list[BadgeGroup],
    rating_group: BadgeGroup | None,
    cfg: ImageConfig,
    placed: list | None = None,
) -> Image.Image:
    """Composite all badge groups onto base and return the result.

    If ``placed`` is given, it is extended with ``(kind, (x, y, w, h))`` for every
    pill drawn, ``kind`` being "rating" or "tags" -- so tests can check layout
    against the real render path rather than a re-implementation of it.
    """
    img_w, img_h = base.size
    p = _compute_layout_params(img_w, cfg)

    result = base.convert("RGBA") if base.mode != "RGBA" else base
    font = _load_font(p["font_size"])
    row_h = _measure_group_height(p["font_size"], p["pad_v"])
    pitch = row_h + p["row_gap"]
    row_w = img_w - 2 * p["margin"]
    draw = (p["alpha"], p["font_size"], p["pad_h"], p["pad_v"], p["col_gap"], p["row_gap"], p["margin"])

    # Roadmap P6. Off (the default) passes no backup, so no row samples the
    # poster and every render is byte-identical to one made before P6 existed.
    # Nor at 100% opacity: an opaque badge hides the poster, so the region
    # under it cannot change what renders -- which is what the checkbox says.
    # A pill finds its backup by its main fill, so a "+N" pill counting a group
    # that had no row of its own (roadmap P7) follows that group's backup. Two
    # groups sharing a main fill share the first one's backup, and so keep
    # rendering alike.
    backups: dict[str, str] = {}
    if cfg.adapt_badge_colors and p["alpha"] < 255:
        for g in [*groups, *([rating_group] if rating_group else [])]:
            if g.backup_fill_color is not None:
                backups.setdefault(g.fill_color, g.backup_fill_color)
    adapt = {"backups": backups, "poster": result} if backups else {}

    # Roadmap B10. The rating and the tag rows each have their own corner, and
    # every combination must render without overlap:
    #   same corner          -> stack: the rating sits nearest the corner and
    #                           the tag rows continue past it;
    #   same edge, opposite  -> the one tag row sharing the rating's band is
    #   sides                   narrowed so it stops short of the rating (a tag
    #                           row may otherwise span the poster's full width);
    #   different edges      -> independent.
    # The rating is placed first because it owns its corner. It is one row.
    rating_rects: list[tuple[int, int, int, int]] = []
    if rating_group and rating_group.labels:
        rows, _ = _layout_group(rating_group, 1, row_w, None, 0, font, p["pad_h"], p["col_gap"])
        result, rating_rects = _draw_rows(result, rows, cfg.rating_position, *draw, **adapt)
        if placed is not None:
            placed.extend(("rating", r) for r in rating_rects)

    same_corner = bool(rating_rects) and cfg.rating_position == cfg.badge_position
    same_edge = bool(rating_rects) and cfg.rating_position.split("-")[0] == cfg.badge_position.split("-")[0]
    shares_band = same_edge and not same_corner
    rating_w = max(x + w for x, _, w, _ in rating_rects) - min(x for x, _, _, _ in rating_rects) if rating_rects else 0
    reserve_w = rating_w + p["col_gap"] if shares_band else 0

    # Roadmap P7, decision (d). The tag stack is clamped to the canvas: it gets
    # the rows between the margins, less the rating's row unless the rating
    # shares a band with a narrowed tag row -- so the rating is never covered.
    # Each group gets one row, then up to _ROW_BUDGET as rows allow; what is
    # given up when they do not is the lowest-priority group's first, priority
    # being group order (video, audio, subtitles): its wrap row, then -- on a
    # canvas with fewer rows than groups -- its whole row, which is then
    # counted, in its own colour, at the end of the last group that has one
    # (and of the group before it, should that row fill up).
    tag_groups = [g for g in groups if g.labels]
    avail = img_h - 2 * p["margin"] - (0 if shares_band or not rating_rects else pitch)
    slots = max(0, (avail + p["row_gap"]) // pitch)
    kept, dropped = tag_groups[:slots], tag_groups[slots:]

    # The group drawn at the edge (the last, stacked first) owns the band row:
    # its last row when the tags are at the bottom, its first at the top.
    is_bottom = "bottom" in cfg.badge_position
    spare = slots - len(kept)
    n_rows = []
    for i, group in enumerate(kept):
        narrow_last = is_bottom if shares_band and i == len(kept) - 1 else None
        want = _rows_wanted(group, row_w, narrow_last, reserve_w, font, p["pad_h"], p["col_gap"])
        extra = min(want - 1, spare)
        spare -= extra
        n_rows.append(1 + extra)

    # Tag groups at cfg.badge_position, stacked away from the edge.
    pending = [(sum(len(_atoms(label)) for label in g.labels), g.fill_color, g.text_color) for g in dropped]
    cumulative_offset = pitch if same_corner else 0
    for i in reversed(range(len(kept))):
        edge = i == len(kept) - 1
        narrow = (n_rows[i] - 1 if is_bottom else 0) if shares_band and edge else None
        rows, pending = _layout_group(
            kept[i],
            n_rows[i],
            row_w,
            narrow,
            reserve_w,
            font,
            p["pad_h"],
            p["col_gap"],
            counted=pending,
        )
        result, rects = _draw_rows(result, rows, cfg.badge_position, *draw, y_offset=cumulative_offset, **adapt)
        if placed is not None:
            placed.extend(("tags", r) for r in rects)
        cumulative_offset += n_rows[i] * pitch

    return result


def _make_placeholder(width: int, height: int, font_size: int) -> Image.Image:
    base = Image.new("RGBA", (width, height))
    draw = ImageDraw.Draw(base)
    for y in range(height):
        t = y / height
        r = int(28 + (45 - 28) * t)
        g = int(35 + (52 - 35) * t)
        b = int(51 + (80 - 51) * t)
        draw.line([(0, y), (width, y)], fill=(r, g, b, 255))
    label_font = _load_font(max(font_size - 2, 10))
    draw.text((width // 2, height // 2), "PREVIEW", fill=(80, 95, 120, 160), anchor="mm", font=label_font)
    return base


_PORTRAIT_RATIO = 2 / 3  # target width:height for portrait posters


def _pad_to_portrait(img: Image.Image) -> Image.Image:
    """Extend a square or landscape image downward to a 2:3 portrait ratio.

    The added area is filled with a blurred sample of the bottom edge so the
    extension blends naturally rather than showing a hard black bar.
    """
    w, h = img.size
    target_h = round(w / _PORTRAIT_RATIO)
    if target_h <= h:
        return img  # already portrait

    pad_h = target_h - h
    # Sample a thin strip from the bottom of the image and blur it to fill the pad
    strip_src_h = min(h, max(4, round(h * 0.05)))  # up to 5% of image height
    strip = img.crop((0, h - strip_src_h, w, h)).resize((w, pad_h), Image.LANCZOS)
    strip = strip.filter(ImageFilter.GaussianBlur(radius=max(8, pad_h // 6)))

    canvas = Image.new("RGBA", (w, target_h), (0, 0, 0, 255))
    canvas.paste(img, (0, 0))
    canvas.paste(strip, (0, h))
    return canvas


def apply_overlay(
    item_folder: Path,
    groups: list[BadgeGroup],
    rating_group: BadgeGroup | None,
    cfg: ImageConfig,
) -> Path | None:
    """
    Find poster image in item_folder, back up original on first run,
    render badge overlay, and save back in place.
    Returns the modified image path, or None if no image found.
    """
    image_path = _find_image(item_folder, cfg.targets)
    if image_path is None:
        log.debug("No poster image found in %s", item_folder)
        return None

    backup = _backup_path(image_path, cfg.backup_suffix)
    if not backup.exists():
        shutil.copy2(image_path, backup)
        log.debug("Backed up original: %s", backup)

    try:
        base = Image.open(backup).convert("RGBA")
    except Exception as exc:
        log.warning("Cannot open image %s: %s", backup, exc)
        return None

    w, h = base.size
    if w > 10000 or h > 10000:
        log.warning("Image too large to overlay (%dx%d): %s", w, h, image_path)
        return None

    if cfg.normalize_portrait and w / h > _PORTRAIT_RATIO + 0.05:
        log.debug("Padding %dx%d image to portrait: %s", w, h, image_path)
        base = _pad_to_portrait(base)

    composited = render_badge_groups(base, groups, rating_group, cfg).convert("RGB")

    try:
        composited.save(str(image_path), format="JPEG", quality=92)
    except Exception:
        composited.save(str(image_path))

    log.info("Overlay applied: %s", image_path)
    return image_path


def generate_preview_bytes(
    groups: list[BadgeGroup],
    rating_group: BadgeGroup | None,
    cfg: ImageConfig,
    width: int = 280,
    height: int = 420,
    base_image_bytes: bytes | None = None,
) -> bytes:
    """Generate a poster JPEG with badge overlay for UI preview."""
    font_size = _BADGE_SIZE_PX.get(cfg.badge_size, _BADGE_SIZE_PX["tv"])

    if base_image_bytes:
        try:
            base = Image.open(io.BytesIO(base_image_bytes)).convert("RGBA")
            bw, bh = base.size
            if bw > 10000 or bh > 10000:
                log.warning("Preview image too large (%dx%d), using placeholder", bw, bh)
                base = _make_placeholder(width, height, font_size)
            else:
                base = base.resize((width, height), Image.LANCZOS)
        except Exception:
            base = _make_placeholder(width, height, font_size)
    else:
        base = _make_placeholder(width, height, font_size)

    result = render_badge_groups(base, groups, rating_group, cfg).convert("RGB")
    buf = io.BytesIO()
    result.save(buf, format="JPEG", quality=88)
    return buf.getvalue()
