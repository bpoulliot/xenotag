#!/usr/bin/env python3
"""How many items sit just under a resolution threshold, and what each candidate rule changes (B13).

``scanner._detect_resolution()`` names a file's class from its first video
stream's WIDTH alone (``>= 3840`` -> 4K, 1920 -> 1080p, 1280 -> 720p, 854 ->
480p, else SD), so a 3836-wide scope crop is 1080p. This probe reads every
item's video ``Width``x``Height`` from Jellyfin, buckets how far below each
threshold the width falls (0-1 %, 1-5 %, 5-10 %) and by aspect, and counts the
items each candidate rule would put in a different class from today's rule --
which it IMPORTS, never re-implements.

Candidate rules (all keep today's class names, so no new tag label appears):

* ``width-N%``    -- width against each threshold less N %.
* ``h-or-w``      -- width reaches the width threshold OR height reaches
  2160 / 1080 / 720 / 480.
* ``nominal``     -- ``max(W/16, H/9) * 9`` (the 16:9-equivalent line count),
  rounded to the nearest of 2160 / 1080 / 720 / 480 (linear midpoints); below
  480 it is SD, the same boundary as today's 854-wide 480p floor.

    python3 scripts/measure_resolution_thresholds.py --self-test
    python3 scripts/measure_resolution_thresholds.py --config /path/config.yml \\
        --url http://HOST:8096 [--state-db COPY] [--arr-plans DIR] [--episodes] [--out FILE]
    python3 scripts/measure_resolution_thresholds.py --records FILE [--state-db COPY] [--arr-plans DIR]

The live mode is manual and GET-only: ``/Items`` with the scan's own library
list (``cfg.jellyfin.library_ids``, deduplicated across libraries as
``get_all_items()`` does), plus, per series, the same first-episode query the
scan uses (``pipeline._get_first_episode_path``). A movie's class comes from
its own item's streams, a series' from that first episode -- the two things a
scan tags. ``--episodes`` adds every episode as an untagged distribution.
``--state-db`` (a COPY) cross-checks today's rule on Jellyfin's numbers
against the class the last scan recorded. ``--arr-plans`` is a directory of B5
go-live reports whose ``plan`` rows map a Jellyfin item to each *arr object
that owns it; a class change is one tag rename on Jellyfin plus one per owner.

``--out`` writes per-item rows INCLUDING TITLES: keep it outside the repo.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.scanner import RESOLUTION_THRESHOLDS, _detect_resolution  # noqa: E402

CLASSES = ["4K", "1080p", "720p", "480p", "SD", "unknown"]
HEIGHTS = {"4K": 2160, "1080p": 1080, "720p": 720, "480p": 480}
BANDS = [("0-1%", 1), ("1-5%", 5), ("5-10%", 10)]
ASPECTS = [
    ("scope >=2.2", 2.2),
    ("flat 1.80-2.2", 1.80),
    ("16:9 1.70-1.80", 1.70),
    ("open matte 1.40-1.70", 1.40),
    ("4:3 1.25-1.40", 1.25),
    ("narrower <1.25", 0.0),
]
# The two cases the filing measured with ffprobe (public in ROADMAP.md).
KNOWN = [("Return to Silent Hill (4K)", 3836, 1604), ("Dr. Strangelove (4K)", 3584, 2160)]


# --------------------------------------------------------------------------- rules


def today(w: int, h: int) -> str:
    """The shipped rule, by calling the shipped function."""
    return _detect_resolution([{"codec_type": "video", "width": w, "height": h}])


def width_tol(pct: float):
    def rule(w: int, h: int) -> str:
        for threshold, label in RESOLUTION_THRESHOLDS:
            if w * 100 >= threshold * (100 - pct):
                return label
        return "SD"

    return rule


def h_or_w_tol(pct: float, hd_only: bool = False):
    """Width OR height within ``pct`` of the class; ``hd_only`` leaves 480p/SD to width alone."""

    def rule(w: int, h: int) -> str:
        for threshold, label in RESOLUTION_THRESHOLDS:
            use_h = not (hd_only and label == "480p")
            if w * 100 >= threshold * (100 - pct) or (use_h and h * 100 >= HEIGHTS[label] * (100 - pct)):
                return label
        return "SD"

    return rule


def nominal(w: int, h: int) -> str:
    lines = max(w / 16, h / 9) * 9
    if lines < 480:
        return "SD"
    return min(HEIGHTS, key=lambda label: (abs(lines - HEIGHTS[label]), -HEIGHTS[label]))


RULES = {
    "width-1%": width_tol(1),
    "width-2%": width_tol(2),
    "width-5%": width_tol(5),
    "width-10%": width_tol(10),
    "h-or-w": h_or_w_tol(0),
    "hw-1%": h_or_w_tol(1),
    "hw-1%-hd": h_or_w_tol(1, hd_only=True),
    "hw-5%-hd": h_or_w_tol(5, hd_only=True),
    "nominal": nominal,
}


# --------------------------------------------------------------------------- buckets


def band(w: int) -> tuple[int, str] | None:
    """(threshold, band) when ``w`` is 0-10 % below a threshold; None at or above it, or further below."""
    for threshold, _label in RESOLUTION_THRESHOLDS:
        if w >= threshold:
            continue
        deficit = threshold - w  # > 0
        for name, pct in BANDS:
            if deficit * 100 <= threshold * pct:
                return threshold, name
    return None


def aspect(w: int, h: int) -> str:
    if not w or not h:
        return "no size"
    ratio = w / h
    for name, floor in ASPECTS:
        if ratio >= floor:
            return name
    return ASPECTS[-1][0]


def pick_video(streams: list[dict]) -> dict:
    """First Type=Video stream by Index, and whether ffprobe would see something else first.

    ffprobe reports an attached picture as ``codec_type: video``; Jellyfin calls
    it ``EmbeddedImage``. Jellyfin also gives PGS subtitles a Width/Height, so
    the Type filter matters.
    """
    ordered = sorted((s for s in streams if not s.get("IsExternal")), key=lambda s: s.get("Index", 0))
    videos = [s for s in ordered if s.get("Type") == "Video"]
    first = videos[0] if videos else None
    image_first = False
    if first is not None:
        image_first = any(
            s.get("Type") == "EmbeddedImage" and s.get("Index", 0) < first.get("Index", 0) for s in ordered
        )
    return {
        "w": int((first or {}).get("Width") or 0),
        "h": int((first or {}).get("Height") or 0),
        "has_video": first is not None,
        "n_video": len(videos),
        "image_first": image_first,
    }


# --------------------------------------------------------------------------- analysis


def analyse(records: list[dict], owners: dict[str, int] | None = None, state: dict[str, str] | None = None) -> dict:
    """Aggregate records ({id, kind, w, h, has_video, ...}) into the tables B13 records."""
    owners = owners or {}
    out: dict = {"n": Counter(), "flags": defaultdict(Counter)}
    bands: dict = defaultdict(Counter)  # (threshold, band) -> kind -> n
    band_aspect: dict = defaultdict(Counter)  # (threshold, band) -> aspect -> n  (tagged kinds only)
    aspect_all: Counter = Counter()
    rules: dict = {
        name: {"trans": Counter(), "pairs": Counter(), "items": 0, "arr": 0, "owners": Counter()} for name in RULES
    }
    under_1080: Counter = Counter()  # tagged items today 720p with W in [1728, 1920) or H >= 1080
    cross: Counter = Counter()
    ids = {r["id"] for r in records}
    alts: Counter = Counter()
    for r in records:
        for alt in r.get("alts") or []:
            twin = "separately tagged item" if alt["id"] in ids else "version inside this item"
            alts[twin] += 1
            if r["has_video"] and alt["has_video"] and today(alt["w"], alt["h"]) != today(r["w"], r["h"]):
                alts[f"{twin}, today's class differs from the item's"] += 1
        kind = r["kind"]
        out["n"][kind] += 1
        for flag in ("image_first", "multi_source"):
            if r.get(flag):
                out["flags"][kind][flag] += 1
        if r.get("n_video", 1) > 1:
            out["flags"][kind]["multi_video"] += 1
        if not r["has_video"]:
            out["flags"][kind]["no_video"] += 1
            continue
        w, h = r["w"], r["h"]
        b = band(w)
        if b:
            bands[b][kind] += 1
        tagged = kind in ("movie", "series")
        if not tagged:
            continue
        aspect_all[aspect(w, h)] += 1
        if b:
            band_aspect[b][aspect(w, h)] += 1
        now = today(w, h)
        if now == "720p" and (w >= 1728 or h >= 1080):
            under_1080[(w, h)] += 1
        if state is not None:
            cross[(now, state.get(r["id"], "no row"))] += 1
        for name, rule in RULES.items():
            new = rule(w, h)
            if new != now:
                rules[name]["trans"][(now, new)] += 1
                rules[name]["pairs"][(w, h)] += 1
                rules[name]["items"] += 1
                rules[name]["arr"] += owners.get(r["id"], 0)
                rules[name]["owners"][owners.get(r["id"], 0)] += 1
    out.update(
        bands=bands,
        band_aspect=band_aspect,
        aspect_all=aspect_all,
        rules=rules,
        under_1080=under_1080,
        cross=cross,
        alts=alts,
    )
    return out


def render(res: dict, owners_source: str) -> str:
    lines: list[str] = []
    add = lines.append
    kinds = [k for k in ("movie", "series", "episode") if res["n"][k]]
    add("## Records")
    for k in kinds:
        flags = ", ".join(f"{f} {n}" for f, n in sorted(res["flags"][k].items()))
        add(f"- {k}: {res['n'][k]}" + (f" ({flags})" if flags else ""))
    add("")
    add("## Width bands below each threshold (count by kind)")
    add("| threshold | band | " + " | ".join(kinds) + " |")
    add("|---|---|" + "---|" * len(kinds))
    for threshold, _label in RESOLUTION_THRESHOLDS:
        for name, _pct in BANDS:
            row = res["bands"].get((threshold, name), Counter())
            add(f"| {threshold} | {name} | " + " | ".join(str(row[k]) for k in kinds) + " |")
    add("")
    add("## Aspect of the banded TAGGED items (movie + series)")
    names = [a for a, _ in ASPECTS]
    add("| threshold | band | " + " | ".join(names) + " |")
    add("|---|---|" + "---|" * len(names))
    for threshold, _label in RESOLUTION_THRESHOLDS:
        for name, _pct in BANDS:
            row = res["band_aspect"].get((threshold, name), Counter())
            add(f"| {threshold} | {name} | " + " | ".join(str(row[a]) for a in names) + " |")
    add("")
    add("All tagged items by aspect: " + ", ".join(f"{a} {res['aspect_all'][a]}" for a in names))
    add("")
    add(f"## Candidate rules vs today (tagged items; *arr owners from {owners_source})")
    add("| rule | items changing class | Jellyfin renames | *arr renames | items by owner count | transitions |")
    add("|---|---:|---:|---:|---|---|")
    for name, r in res["rules"].items():
        trans = ", ".join(f"{a}->{b} {n}" for (a, b), n in sorted(r["trans"].items(), key=lambda kv: -kv[1]))
        own = ", ".join(f"{k}: {n}" for k, n in sorted(r["owners"].items()))
        add(f"| {name} | {r['items']} | {r['items']} | {r['arr']} | {own or '-'} | {trans or '-'} |")
    add("")
    add("## Width x height pairs each rule classifies differently (tagged items, count)")
    for name, r in res["rules"].items():
        pairs = sorted(r["pairs"].items(), key=lambda kv: (-kv[1], kv[0]))
        shown = ", ".join(f"{w}x{h} ({n})" for (w, h), n in pairs[:40])
        more = f" ... +{len(pairs) - 40} more pairs" if len(pairs) > 40 else ""
        add(f"- **{name}** ({len(pairs)} distinct pairs): {shown or '-'}{more}")
    add("")
    add("## Other versions (MediaSources beyond the item's own; only the item's own file is probed)")
    add(", ".join(f"{k}: {n}" for k, n in sorted(res["alts"].items())) or "-")
    add("")
    add("## Known cases")
    add("| case | WxH | today | " + " | ".join(RULES) + " |")
    add("|---|---|---|" + "---|" * len(RULES))
    for label, w, h in KNOWN:
        add(f"| {label} | {w}x{h} | {today(w, h)} | " + " | ".join(rule(w, h) for rule in RULES.values()) + " |")
    add("")
    add("## Today 720p, but W >= 1728 or H >= 1080 (1080p crops and 1440x1080-style frames)")
    u = res["under_1080"]
    add(f"{sum(u.values())} items: " + (", ".join(f"{w}x{h} ({n})" for (w, h), n in u.most_common(40)) or "-"))
    if res["cross"]:
        add("")
        add("## Cross-check: today's rule on Jellyfin's numbers vs the class state.db recorded")
        agree = sum(n for (a, b), n in res["cross"].items() if a == b)
        total = sum(res["cross"].values())
        add(f"agree {agree}/{total}; disagreements: ")
        for (a, b), n in sorted(res["cross"].items(), key=lambda kv: -kv[1]):
            if a != b:
                add(f"- jellyfin->{a}, state.db {b}: {n}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- live sweep


def load_owners(plan_dir: Path) -> dict[str, int]:
    """item_id -> number of *arr instances whose B5 plan claims it."""
    per_item: dict[str, set] = defaultdict(set)
    for f in sorted(plan_dir.glob("*.json.gz")):
        data = json.load(gzip.open(f))
        for row in data.get("plan") or []:
            per_item[row["item_id"]].add(f.name.split("-")[0])
    if not per_item:
        raise SystemExit(f"no B5 plan rows under {plan_dir}: refusing to report zero *arr renames")
    return {k: len(v) for k, v in per_item.items()}


def load_state(db: Path) -> dict[str, str]:
    import sqlite3

    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT item_id, resolution FROM media_state").fetchall()
    finally:
        con.close()
    return {k.removeprefix("jellyfin:"): v or "unknown" for k, v in rows}


def sweep(url: str, api_key: str, library_ids: list[str], episodes: bool) -> list[dict]:
    import httpx

    from app.clients.readonly import ReadOnlyTransport

    guard = ReadOnlyTransport()
    headers = {"Authorization": f'MediaBrowser Token="{api_key}"'}
    records: list[dict] = []
    with httpx.Client(base_url=url.rstrip("/"), headers=headers, timeout=120, transport=guard) as c:

        def paged(params: dict) -> list[dict]:
            got: list[dict] = []
            params = {**params, "Limit": 500, "StartIndex": 0}
            while True:
                r = c.get("/Items", params=params)
                r.raise_for_status()
                data = r.json()
                page = data.get("Items", [])
                got.extend(page)
                if len(got) >= data.get("TotalRecordCount", 0) or not page:
                    return got
                params["StartIndex"] = len(got)

        seen: set[str] = set()
        items: list[tuple[str, dict]] = []
        for lib in library_ids:
            for it in paged(
                {
                    "ParentId": lib,
                    "Recursive": "true",
                    "IncludeItemTypes": "Movie,Series",
                    "Fields": "MediaStreams,MediaSources,Path",
                }
            ):
                if it["Id"] not in seen:
                    seen.add(it["Id"])
                    items.append((lib, it))
        print(f"swept {len(items)} movies+series", file=sys.stderr, flush=True)
        for n, (lib, it) in enumerate(items, 1):
            if it.get("Type") == "Movie":
                pv = pick_video(it.get("MediaStreams") or [])
                pv["multi_source"] = len(it.get("MediaSources") or []) > 1
                pv["alts"] = [
                    {"id": ms.get("Id"), **pick_video(ms.get("MediaStreams") or [])}
                    for ms in it.get("MediaSources") or []
                    if ms.get("Id") != it["Id"]
                ]
                records.append({"id": it["Id"], "kind": "movie", "lib": lib, "name": it.get("Name"), **pv})
            elif it.get("Type") == "Series":
                # The scan's own first-episode query, plus the streams.
                r = c.get(
                    "/Items",
                    params={
                        "ParentId": it["Id"],
                        "Recursive": "true",
                        "IncludeItemTypes": "Episode",
                        "Fields": "Path,MediaSources,MediaStreams",
                        "SortBy": "SortName",
                        "SortOrder": "Ascending",
                        "Limit": 1,
                    },
                )
                r.raise_for_status()
                eps = r.json().get("Items", [])
                if eps:
                    sources = eps[0].get("MediaSources") or []
                    streams = (sources[0].get("MediaStreams") if sources else None) or eps[0].get("MediaStreams") or []
                    pv = pick_video(streams)
                    pv["multi_source"] = len(sources) > 1
                else:
                    pv = {"w": 0, "h": 0, "has_video": False, "n_video": 0, "image_first": False}
                records.append({"id": it["Id"], "kind": "series", "lib": lib, "name": it.get("Name"), **pv})
            if n % 1000 == 0:
                print(f"  {n}/{len(items)}", file=sys.stderr, flush=True)
        if episodes:
            eseen: set[str] = set()
            for lib in library_ids:
                for ep in paged(
                    {"ParentId": lib, "Recursive": "true", "IncludeItemTypes": "Episode", "Fields": "MediaStreams"}
                ):
                    if ep["Id"] in eseen:
                        continue
                    eseen.add(ep["Id"])
                    pv = pick_video(ep.get("MediaStreams") or [])
                    records.append({"id": ep["Id"], "kind": "episode", "lib": lib, "name": None, **pv})
            print(f"swept {len(eseen)} episodes", file=sys.stderr, flush=True)
    if guard.blocked:
        raise SystemExit(f"read-only guard blocked a request: {guard.blocked}")
    print(f"GETs sent: {dict(guard.sent)}; blocked 0", file=sys.stderr)
    return records


# --------------------------------------------------------------------------- self-test


def _band_failures(fn) -> list[str]:
    """Boundary cases any correct band() must meet."""
    cases = {
        3839: (3840, "0-1%"),
        3840: None,
        3802: (3840, "0-1%"),  # 0.99 % under
        3801: (3840, "1-5%"),  # 1.02 % under
        3648: (3840, "1-5%"),  # exactly 5 %
        3647: (3840, "5-10%"),
        3456: (3840, "5-10%"),  # exactly 10 %
        3455: None,
        1919: (1920, "0-1%"),
        1920: None,
        1916: (1920, "0-1%"),
        1279: (1280, "0-1%"),
        853: (854, "0-1%"),
        854: None,
        720: None,  # 15.7 % under 854
    }
    return [f"band({w}) = {fn(w)!r}, want {want!r}" for w, want in cases.items() if fn(w) != want]


def _mutant_band(w: int):
    """band() with an inclusive upper edge: puts the threshold itself in 0-1 %."""
    for threshold, _label in RESOLUTION_THRESHOLDS:
        if w > threshold:
            continue
        for name, pct in BANDS:
            if (threshold - w) * 100 <= threshold * pct:
                return threshold, name
    return None


def self_test() -> int:
    fails: list[str] = []
    check = fails.append

    real = _band_failures(band)
    if real:
        fails.extend(real)
    if not _band_failures(_mutant_band):
        check("the band checks did not catch a mutant that bands the threshold itself")

    # today() is the imported rule: the filing's cases must come out as filed.
    for (w, h), want in {(3836, 1604): "1080p", (3584, 2160): "1080p", (3840, 2160): "4K", (1916, 800): "720p"}.items():
        if today(w, h) != want:
            check(f"today({w}x{h}) = {today(w, h)}, want {want}")
    # A zero tolerance must agree with today everywhere; a positive one must not.
    grid = [(w, h) for w in range(600, 4200, 7) for h in (480, 576, 800, 1080, 1604, 2160)]
    if [p for p in grid if width_tol(0)(*p) != today(*p)]:
        check("width-0% disagrees with today's rule")
    if all(width_tol(1)(*p) == today(*p) for p in grid):
        check("width-1% never differs from today's rule")
    expect = {
        ("width-1%", 3836, 1604): "4K",
        ("width-1%", 3584, 2160): "1080p",
        ("width-10%", 3584, 2160): "4K",
        ("width-1%", 1916, 800): "1080p",
        ("h-or-w", 3584, 2160): "4K",
        ("h-or-w", 3836, 1604): "1080p",
        ("h-or-w", 1440, 1080): "1080p",
        ("h-or-w", 720, 480): "480p",
        ("h-or-w", 640, 360): "SD",
        ("hw-1%", 720, 480): "480p",
        ("hw-1%-hd", 720, 480): "SD",
        ("hw-1%-hd", 720, 576): "SD",
        ("hw-1%-hd", 848, 480): "480p",
        ("hw-1%-hd", 3836, 1604): "4K",
        ("hw-1%-hd", 3584, 2160): "4K",
        ("hw-1%-hd", 1904, 1072): "1080p",
        ("hw-1%-hd", 1440, 1080): "1080p",
        ("hw-1%-hd", 1792, 1060): "720p",
        ("hw-1%-hd", 960, 720): "720p",
        ("hw-1%-hd", 1920, 800): "1080p",
        ("nominal", 3836, 1604): "4K",
        ("nominal", 3584, 2160): "4K",
        ("nominal", 1920, 1080): "1080p",
        ("nominal", 1920, 800): "1080p",
        ("nominal", 2880, 1200): "4K",  # 1620 lines: the midpoint goes up
        ("nominal", 2800, 1200): "1080p",
        ("nominal", 853, 470): "SD",
        ("nominal", 854, 480): "480p",
    }
    for (name, w, h), want in expect.items():
        got = RULES[name](w, h)
        if got != want:
            check(f"{name}({w}x{h}) = {got}, want {want}")

    for (w, h), want in {
        (3836, 1604): "scope >=2.2",
        (3584, 2160): "open matte 1.40-1.70",
        (1920, 1080): "16:9 1.70-1.80",
        (1998, 1080): "flat 1.80-2.2",
        (1440, 1080): "4:3 1.25-1.40",
        (0, 0): "no size",
    }.items():
        if aspect(w, h) != want:
            check(f"aspect({w}x{h}) = {aspect(w, h)}, want {want}")

    # Stream choice: a PGS subtitle carries a size and sits first; a cover image before the video is flagged.
    pv = pick_video(
        [
            {"Index": 0, "Type": "Subtitle", "Width": 1920, "Height": 1080},
            {"Index": 1, "Type": "Video", "Width": 3836, "Height": 1604},
        ]
    )
    if (pv["w"], pv["h"], pv["image_first"]) != (3836, 1604, False):
        check(f"pick_video took the wrong stream: {pv}")
    pv = pick_video(
        [
            {"Index": 1, "Type": "Video", "Width": 1920, "Height": 1080},
            {"Index": 0, "Type": "EmbeddedImage", "Width": 600, "Height": 900},
        ]
    )
    if not pv["image_first"] or pv["w"] != 1920:
        check(f"pick_video missed an image ahead of the video: {pv}")
    if pick_video([{"Index": 0, "Type": "Audio"}])["has_video"]:
        check("pick_video found a video stream in an audio-only item")

    # Aggregation: degenerate input gives nothing; a planted change is counted with its owners.
    empty = analyse([])
    if any(r["items"] for r in empty["rules"].values()) or empty["bands"]:
        check("analyse([]) is not empty")
    base = {"has_video": True, "n_video": 1, "image_first": False}
    recs = [
        {
            "id": "a",
            "kind": "movie",
            "w": 3836,
            "h": 1604,
            **base,
            "alts": [{"id": "b", "w": 1920, "h": 1080, **base}, {"id": "zz", "w": 3840, "h": 2160, **base}],
        },
        {"id": "b", "kind": "movie", "w": 1920, "h": 1080, **base},
        {"id": "c", "kind": "episode", "w": 3836, "h": 1604, **base},
        {"id": "d", "kind": "series", "w": 0, "h": 0, **{**base, "has_video": False}},
    ]
    res = analyse(recs, owners={"a": 2, "b": 1}, state={"a": "1080p", "b": "720p"})
    r1 = res["rules"]["width-1%"]
    if (r1["items"], r1["arr"], dict(r1["trans"])) != (1, 2, {("1080p", "4K"): 1}):
        check(f"width-1% on the fixture: {r1['items']} items, {r1['arr']} arr, {dict(r1['trans'])}")
    if res["bands"][(3840, "0-1%")] != Counter({"movie": 1, "episode": 1}):
        check(f"band counts on the fixture: {dict(res['bands'])}")
    want_alts = {
        "separately tagged item": 1,
        "version inside this item": 1,
        "version inside this item, today's class differs from the item's": 1,
    }
    if dict(res["alts"]) != want_alts:
        check(f"alt versions on the fixture: {dict(res['alts'])}")
    if res["flags"]["series"]["no_video"] != 1:
        check("the no-video series was not flagged")
    if res["cross"] != Counter({("1080p", "1080p"): 1, ("1080p", "720p"): 1}):
        check(f"state cross-check on the fixture: {dict(res['cross'])}")

    for f in fails:
        print("FAIL:", f)
    print("self-test:", "FAILED" if fails else "ok")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--config", help="xenotag config.yml (library_ids, api key)")
    ap.add_argument("--url", help="Jellyfin URL, overriding the config's (which is container-internal)")
    ap.add_argument("--state-db", type=Path, help="a COPY of state.db, for the cross-check")
    ap.add_argument("--arr-plans", type=Path, help="directory of B5 go-live reports (*.json.gz with 'plan')")
    ap.add_argument("--episodes", action="store_true", help="also sweep every episode (untagged)")
    ap.add_argument("--out", type=Path, help="per-item JSON (holds TITLES: keep it out of the repo)")
    ap.add_argument("--records", type=Path, help="re-analyse an earlier --out file instead of sweeping")
    args = ap.parse_args()
    if self_test() != 0:
        return 1
    if args.self_test:
        return 0
    if args.records:
        records = json.loads(args.records.read_text())
    elif args.config:
        from app.config import load_config

        cfg = load_config(args.config)
        records = sweep(
            args.url or cfg.jellyfin.url, cfg.jellyfin.api_key, list(cfg.jellyfin.library_ids), args.episodes
        )
    else:
        ap.error("--config (a live sweep) or --records is required")
    owners = load_owners(args.arr_plans) if args.arr_plans else {}
    state = load_state(args.state_db) if args.state_db else None
    if args.out:
        args.out.write_text(json.dumps(records))
    source = f"B5 plans in {args.arr_plans.name}" if args.arr_plans else "none (arr renames not counted)"
    print(render(analyse(records, owners, state), source))
    return 0


if __name__ == "__main__":
    sys.exit(main())
