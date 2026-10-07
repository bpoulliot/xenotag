#!/usr/bin/env python3
"""Which Jellyfin items no longer carry the xt- tags xenotag wrote, and do the *arr NFOs explain it? (roadmap B12)

For every item ``state.db`` has a ``tags_applied`` row for, compares Jellyfin's
``xt-`` tags (from a COPY of ``jellyfin.db``) with what xenotag wrote, classifies the
difference, and tests one hypothesis against each drifted item: that Jellyfin merged
the NFO beside the file into the item, NFO tags first, de-duplicated without regard
to case -- so the \\*arr's lowercase spelling of a label replaces xenotag's, and every
other tag is kept. It also times each drifted item's last Jellyfin save against the
NFO's mtime.

    python3 scripts/measure_nfo_tag_drift.py --self-test
    python3 scripts/measure_nfo_tag_drift.py --state-db COPY/state.db \\
        --jellyfin-db COPY/jellyfin.db --media-map /media=/mnt/media [--out /tmp/drift.json]
    python3 scripts/measure_nfo_tag_drift.py --jellyfin-db OLD_COPY/jellyfin.db --by-saved-date

``--by-saved-date`` needs no ``state.db``: it counts Movie/Series items with and
without any ``xt-`` tag by the day Jellyfin last saved them, which is how a
library-wide wipe shows up in an old backup.

Copy both databases with their ``-wal``/``-shm`` first; both are opened ``mode=ro``,
NFO files are only read, and nothing is written anywhere but ``--out``.

Self-check (live): the NFO-merge prediction must hold for at least 95% of the drifted
items whose NFO is readable, or the instrument is reading the wrong thing and the
script refuses to report the classification. ``--self-test`` builds synthetic
databases and NFOs and requires: a flipped item to be seen as drift AND predicted; a
matching item to be seen as a match; an item with an extra tag the NFO does not
explain to be seen as drift and NOT predicted; and an item with no xt- tag at all.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import sys
import tempfile
import uuid
from collections import Counter
from pathlib import Path

PREFIX = "xt-"
NFO_TAG = re.compile(r"<tag>(.*?)</tag>", re.S)


def _ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _split(tags: str | None) -> list[str]:
    return [t for t in (tags or "").split("|") if t]


def _ts(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value[:26]).replace(tzinfo=dt.UTC)


def nfo_path(item_path: str, item_type: str, media_map: tuple[str, str]) -> Path | None:
    """The NFO an \\*arr's Kodi metadata consumer writes for this item, if it exists."""
    src, dst = media_map
    host = item_path.replace(src, dst, 1) if item_path.startswith(src) else item_path
    if item_type.endswith("Series"):
        candidates = [Path(host) / "tvshow.nfo"]
    else:
        p = Path(host)
        candidates = [p.with_suffix(".nfo"), p.parent / "movie.nfo"]
    for c in candidates:
        if c.is_file():
            return c
    return None


def nfo_merge(nfo_tags: list[str], applied: list[str]) -> list[str]:
    """The hypothesis: NFO tags first, then xenotag's, first spelling of each case-folded tag wins."""
    seen: set[str] = set()
    out = []
    for t in nfo_tags + applied:
        if t.lower().startswith(PREFIX) and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return sorted(out)


def classify(applied: list[str], jf_tags: list[str]) -> str:
    jx = sorted(t for t in jf_tags if t.lower().startswith(PREFIX))
    if sorted(applied) == jx:
        return "match"
    if not jx:
        return "no_xt"
    if sorted({t.lower() for t in applied}) == sorted({t.lower() for t in jx}):
        return "case_only"
    return "other"


def measure(state_db: Path, jellyfin_db: Path, media_map: tuple[str, str]) -> dict:
    st = _ro(state_db)
    jf = _ro(jellyfin_db)
    rows = []
    for item_id, applied_json, last_scanned in st.execute(
        "select item_id, tags_applied, last_scanned from media_state where source = 'jellyfin'"
    ):
        applied = json.loads(applied_json or "[]")
        if not applied:
            continue
        hexid = item_id.split(":", 1)[1]
        r = jf.execute(
            "select Name, Type, Path, Tags, DateLastSaved from BaseItems where Id = ?",
            (str(uuid.UUID(hexid)).upper(),),
        ).fetchone()
        if r is None:
            continue
        name, typ, path, tags, saved = r
        jf_tags = _split(tags)
        nfo = nfo_path(path or "", typ or "", media_map)
        nfo_tags = NFO_TAG.findall(nfo.read_text(errors="replace")) if nfo else None
        nfo_mtime = nfo.stat().st_mtime if nfo else None
        status = classify(applied, jf_tags)
        rows.append(
            {
                "id": hexid,
                "name": name,
                "type": (typ or "").rsplit(".", 1)[-1],
                "status": status,
                "applied": applied,
                "jf_xt": sorted(t for t in jf_tags if t.lower().startswith(PREFIX)),
                "nfo_xt": [t for t in (nfo_tags or []) if t.lower().startswith(PREFIX)],
                "nfo_found": nfo is not None,
                "predicted": (
                    nfo_merge(nfo_tags, applied) == sorted(t for t in jf_tags if t.lower().startswith(PREFIX))
                    if nfo_tags is not None
                    else None
                ),
                "saved": saved,
                "last_written": last_scanned,
                "saved_after_write": bool(saved and last_scanned and _ts(saved) > _ts(last_scanned)),
                "save_minus_nfo_s": (_ts(saved).timestamp() - nfo_mtime) if (saved and nfo_mtime) else None,
            }
        )
    return summarise(rows)


def _lag_bin(x: float | None) -> str:
    if x is None:
        return "no NFO"
    if x < 0:
        return "saved before the NFO's mtime"
    for lim, label in ((120, "0-2 min after"), (600, "2-10 min after"), (3600, "10-60 min after")):
        if x <= lim:
            return label
    return "> 1 h after"


def summarise(rows: list[dict]) -> dict:
    drift = [r for r in rows if r["status"] != "match"]
    testable = [r for r in drift if r["predicted"] is not None and r["status"] != "no_xt"]
    predicted = sum(1 for r in testable if r["predicted"])
    return {
        "items": len(rows),
        "status": dict(Counter(r["status"] for r in rows)),
        "status_by_type": {f"{k[0]}/{k[1]}": v for k, v in Counter((r["status"], r["type"]) for r in rows).items()},
        "drift_saved_after_xenotag_write": dict(Counter(r["saved_after_write"] for r in drift)),
        "match_saved_after_xenotag_write": dict(
            Counter(r["saved_after_write"] for r in rows if r["status"] == "match")
        ),
        "drift_save_vs_nfo_mtime": dict(Counter(_lag_bin(r["save_minus_nfo_s"]) for r in drift)),
        "match_nfo_carries_xt": dict(Counter(bool(r["nfo_xt"]) for r in rows if r["status"] == "match")),
        "nfo_merge_predicted": {"testable": len(testable), "predicted": predicted},
        "drift_saved_by_day": dict(sorted(Counter((r["saved"] or "")[:10] for r in drift).items())),
        "not_predicted": [r for r in testable if not r["predicted"]][:20],
        "no_xt": [r for r in drift if r["status"] == "no_xt"][:20],
        "rows": rows,
    }


def by_saved_date(jellyfin_db: Path) -> dict:
    jf = _ro(jellyfin_db)
    c: Counter = Counter()
    for tags, saved in jf.execute(
        "select Tags, DateLastSaved from BaseItems where Type like '%.Movie' or Type like '%.Series'"
    ):
        has = any(t.lower().startswith(PREFIX) for t in _split(tags))
        c[((saved or "")[:10], "with xt-" if has else "without xt-")] += 1
    days = sorted({d for d, _ in c})
    return {d: {"with xt-": c[(d, "with xt-")], "without xt-": c[(d, "without xt-")]} for d in days}


# --- self-test -------------------------------------------------------------------------


def _mk_dbs(root: Path, items: list[tuple]) -> tuple[Path, Path]:
    """items: (name, applied, jf_tags, nfo_tags or None, saved, last_written)."""
    st_path, jf_path = root / "state.db", root / "jellyfin.db"
    st, jf = sqlite3.connect(st_path), sqlite3.connect(jf_path)
    st.execute("create table media_state (item_id text, source text, tags_applied text, last_scanned text)")
    jf.execute("create table BaseItems (Id text, Name text, Type text, Path text, Tags text, DateLastSaved text)")
    for name, applied, jf_tags, nfo_tags, saved, written in items:
        iid = uuid.uuid4()
        folder = root / "media" / name
        folder.mkdir(parents=True)
        video = folder / f"{name}.mkv"
        video.write_bytes(b"")
        if nfo_tags is not None:
            nfo = video.with_suffix(".nfo")
            nfo.write_text("<movie>" + "".join(f"<tag>{t}</tag>" for t in nfo_tags) + "</movie>")
            t0 = _ts(saved).timestamp() - 30
            os.utime(nfo, (t0, t0))
        st.execute(
            "insert into media_state values (?, 'jellyfin', ?, ?)",
            (f"jellyfin:{iid.hex}", json.dumps(applied), written),
        )
        jf.execute(
            "insert into BaseItems values (?, ?, 'MediaBrowser.Controller.Entities.Movies.Movie', ?, ?, ?)",
            (str(iid).upper(), name, f"/media/{name}/{name}.mkv", "|".join(jf_tags), saved),
        )
    st.commit()
    jf.commit()
    return st_path, jf_path


def self_test() -> int:
    applied = ["xt-1080p", "xt-H264", "xt-AAC", "xt-sub-EN", "xt-R"]
    w, s = "2026-01-01 10:00:00.000000", "2026-01-02 19:10:00.0000000"
    items = [
        # the *arr's NFO flipped the spelling: drift, and the NFO merge must predict it
        (
            "flipped",
            applied,
            ["luxe", "xt-1080p", "xt-h264", "xt-aac", "kw", "xt-sub-EN", "xt-R"],
            ["luxe", "xt-1080p", "xt-h264", "xt-aac"],
            s,
            w,
        ),
        # untouched since xenotag wrote it: a match
        ("matching", applied, ["kw"] + applied, ["luxe", "xt-1080p", "xt-h264", "xt-aac"], w, w),
        # an extra tag the NFO does not carry: drift, and NOT predicted
        (
            "unexplained",
            applied,
            ["xt-1080p", "xt-h264", "xt-aac", "xt-H.264", "xt-sub-EN", "xt-R"],
            ["xt-1080p", "xt-h264", "xt-aac"],
            s,
            w,
        ),
        # wiped: no xt- tag at all
        ("wiped", applied, ["luxe", "kw"], ["luxe"], s, w),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        st_path, jf_path = _mk_dbs(root, items)
        res = measure(st_path, jf_path, ("/media", str(root / "media")))
        by = {r["name"]: r for r in res["rows"]}
        checks = {
            "flipped is drift (case_only)": by["flipped"]["status"] == "case_only",
            "flipped is predicted by the NFO merge": by["flipped"]["predicted"] is True,
            "matching is a match": by["matching"]["status"] == "match",
            "unexplained is drift": by["unexplained"]["status"] == "other",
            "unexplained is NOT predicted": by["unexplained"]["predicted"] is False,
            "wiped has no xt-": by["wiped"]["status"] == "no_xt",
            "flipped saved 0-2 min after its NFO": _lag_bin(by["flipped"]["save_minus_nfo_s"]) == "0-2 min after",
            "nfo_merge keeps the NFO spelling": nfo_merge(["xt-aac"], ["xt-AAC", "xt-R"]) == ["xt-R", "xt-aac"],
        }
        hist = by_saved_date(jf_path)
        checks["by_saved_date counts the wiped item"] = hist["2026-01-02"]["without xt-"] == 1
        checks["by_saved_date counts the matching item"] = hist["2026-01-01"]["with xt-"] == 1
    bad = [k for k, ok in checks.items() if not ok]
    for k, ok in checks.items():
        print(("ok   " if ok else "FAIL ") + k)
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--state-db", type=Path)
    ap.add_argument("--jellyfin-db", type=Path)
    ap.add_argument("--media-map", default="/media=/mnt/media", help="container=host path prefix")
    ap.add_argument("--by-saved-date", action="store_true")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if not a.jellyfin_db:
        ap.error("--jellyfin-db is required")
    if a.by_saved_date:
        print(json.dumps(by_saved_date(a.jellyfin_db), indent=1))
        return 0
    if not a.state_db:
        ap.error("--state-db is required")
    src, dst = a.media_map.split("=", 1)
    res = measure(a.state_db, a.jellyfin_db, (src, dst))
    p = res["nfo_merge_predicted"]
    if p["testable"] and p["predicted"] / p["testable"] < 0.95:
        print(f"REFUSING: the NFO merge predicts only {p['predicted']}/{p['testable']} drifted items", file=sys.stderr)
        return 2
    rows = res.pop("rows")
    print(json.dumps(res, indent=1, default=str))
    if a.out:
        a.out.write_text(json.dumps({**res, "rows": rows}, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
