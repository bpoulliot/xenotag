#!/usr/bin/env python3
"""How often ffprobe answers `unknown` for field_order, and how many files are interlaced (U5).

``xt-interlaced`` comes from ``scanner.probe_file()``'s ``field_order``: ``tt``,
``bb``, ``tb`` or ``bt`` tag the item; ``progressive`` does not; ``unknown`` -- or
no ``field_order`` at all -- is never tagged, either way, and nothing guesses. This
probe counts each answer over real files, so the size of the "no answer" group is a
number, not an assumption.

    python3 scripts/measure_field_order.py --self-test
    python3 scripts/measure_field_order.py --paths DIR [DIR ...]
    python3 scripts/measure_field_order.py --state-db COPY --sample N [--legacy M] [--seed S] [--out FILE]

It IMPORTS the scanner's rule (``probe_file``, ``is_interlaced``) and checks every
file against a naive reference -- a separate ``ffprobe -show_entries
stream=codec_type,field_order`` call parsed here -- so a probe that disagrees with
ffprobe itself refuses to report. Read-only: ffprobe reads each file, the index
(a COPY) is opened ``mode=ro``, nothing is written but ``--out``.

``--state-db`` samples ``media_state.file_path`` rows: ``--sample`` at random from
the whole index, plus ``--legacy`` at random from the rows most likely to be
interlaced (MPEG-2 / VC-1 / MPEG-4 video, or SD / 480p), reported as separate
strata. Paths are the index's own, so run it where they resolve (the xenotag
image, media mounted read-only). ``--out`` writes per-file rows INCLUDING PATHS:
keep it outside the repo.

``--self-test`` synthesises progressive, top-field-first and bottom-field-first
files with ffmpeg and needs both directions right; without ffmpeg it refuses.
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.scanner import is_interlaced, probe_file  # noqa: E402

LEGACY_CODECS = ("MPEG-2", "VC-1", "MPEG-4")
LEGACY_RESOLUTIONS = ("SD", "480p")
VIDEO_EXTS = {".mkv", ".mp4", ".m4v", ".avi", ".ts", ".m2ts", ".mpg", ".mpeg", ".vob", ".wmv", ".mov", ".webm"}


def reference_field_order(path: str) -> str | None:
    """The naive reference: ffprobe's own answer for the first video stream, parsed here."""
    cmd = ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,field_order", "-of", "json", path]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        return "<ffprobe failed>"
    for s in json.loads(out.stdout or "{}").get("streams", []):
        if s.get("codec_type") == "video":
            return (s.get("field_order") or "unknown").lower()
    return None


def measure(path: str) -> dict:
    info = probe_file(path)
    row = {"path": path, "field_order": info.field_order if info else "<probe failed>"}
    row["interlaced"] = bool(info and is_interlaced(info.field_order))
    row["codec"] = info.video_codec if info else None
    row["resolution"] = info.resolution if info else None
    row["reference"] = reference_field_order(path)
    return row


def run(paths: list[str], workers: int = 4) -> list[dict]:
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(measure, paths))


def disagreements(rows: list[dict]) -> list[dict]:
    """Rows where the scanner's answer is not ffprobe's own; a probe failure is not one."""
    return [r for r in rows if not r["field_order"].startswith("<") and r["field_order"] != r["reference"]]


def summarise(label: str, rows: list[dict]) -> dict:
    orders = Counter(r["field_order"] for r in rows)
    by_codec: dict[str, Counter] = {}
    for r in rows:
        by_codec.setdefault(str(r["codec"]), Counter())[r["field_order"]] += 1
    n = len(rows)
    unknown = orders.get("unknown", 0)
    interlaced = sum(r["interlaced"] for r in rows)
    print(f"{label}: n={n}")
    print(f"  field_order: {dict(orders.most_common())}")
    if n:
        print(
            f"  interlaced {interlaced} ({100 * interlaced / n:.1f} %), unknown {unknown} ({100 * unknown / n:.1f} %)"
        )
    for codec, c in sorted(by_codec.items(), key=lambda kv: -sum(kv[1].values())):
        print(f"    {codec:>8}: {dict(c.most_common())}")
    return {"n": n, "field_order": dict(orders), "interlaced": interlaced, "unknown": unknown}


def sample_index(db: Path, sample: int, legacy: int, seed: int) -> dict[str, list[str]]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT file_path, video_codec, resolution FROM media_state WHERE file_path IS NOT NULL AND file_path != ''"
        ).fetchall()
    finally:
        con.close()
    rng = random.Random(seed)  # noqa: S311
    everything = sorted({r[0] for r in rows})
    old = sorted({r[0] for r in rows if r[1] in LEGACY_CODECS or r[2] in LEGACY_RESOLUTIONS})
    print(f"index: {len(everything)} distinct paths, {len(old)} in the legacy stratum; seed {seed}")
    return {
        "random": rng.sample(everything, min(sample, len(everything))),
        "legacy": rng.sample(old, min(legacy, len(old))),
    }


def _synth(path: Path, top: int | None) -> None:
    flags = [] if top is None else ["-flags", "+ildct+ilme", "-top", str(top)]
    cmd = [
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=320x240:r=25:d=1",
        "-c:v", "libx264", "-preset", "ultrafast", *flags, str(path),
    ]  # fmt: skip
    subprocess.run(cmd, check=True, timeout=60)


def self_test() -> None:
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        raise SystemExit("self-test REFUSED: ffmpeg/ffprobe not found, so nothing can be measured")
    with tempfile.TemporaryDirectory() as tmp:
        files = {"progressive": Path(tmp) / "p.mkv", "tt": Path(tmp) / "tt.mkv", "bb": Path(tmp) / "bb.mkv"}
        _synth(files["progressive"], None)
        _synth(files["tt"], 1)
        _synth(files["bb"], 0)
        rows = {name: measure(str(p)) for name, p in files.items()}
    # Matroska stores x264's top-field-first as `tb`, so the exact code is ffprobe's
    # business; the class is ours. A field-order code must come back, never unknown.
    assert rows["progressive"]["field_order"] == "progressive", rows
    for name in ("tt", "bb"):
        assert rows[name]["field_order"] in {"tt", "bb", "tb", "bt"}, rows[name]
    assert rows["tt"]["field_order"] != rows["bb"]["field_order"], rows
    assert not rows["progressive"]["interlaced"] and rows["tt"]["interlaced"] and rows["bb"]["interlaced"]
    assert all(r["field_order"] == r["reference"] for r in rows.values()), rows
    # The reference check itself must be able to fail.
    assert disagreements([{**rows["tt"], "reference": "progressive"}]) != []
    assert disagreements(list(rows.values())) == []
    print("self-test OK")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--paths", nargs="+", type=Path, help="measure every video file under these directories")
    ap.add_argument("--state-db", type=Path, help="a COPY of state.db to sample file paths from (opened read-only)")
    ap.add_argument("--sample", type=int, default=200)
    ap.add_argument("--legacy", type=int, default=0)
    ap.add_argument("--seed", type=int, default=20260927)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", type=Path, help="per-file rows as JSON (holds paths: keep outside the repo)")
    args = ap.parse_args()
    self_test()
    if args.self_test:
        return 0
    strata: dict[str, list[str]] = {}
    if args.paths:
        strata["paths"] = sorted(
            str(f) for d in args.paths for f in d.rglob("*") if f.is_file() and f.suffix.lower() in VIDEO_EXTS
        )
    if args.state_db:
        strata.update(sample_index(args.state_db, args.sample, args.legacy, args.seed))
    if not strata:
        ap.error("give --paths or --state-db")
    results: dict = {"strata": {}, "rows": {}}
    bad: list[dict] = []
    for label, paths in strata.items():
        rows = run(paths, args.workers)
        results["rows"][label] = rows
        results["strata"][label] = summarise(label, rows)
        failed = [r for r in rows if r["field_order"] == "<probe failed>"]
        if failed:
            print(f"  probe failed: {len(failed)} (not counted as unknown)")
        bad += disagreements(rows)
    if args.out:
        args.out.write_text(json.dumps(results, indent=1))
    if bad:
        for r in bad[:20]:
            print(f"DISAGREES with ffprobe: {r}")
        print(f"{len(bad)} disagreement(s): the scanner is not reporting ffprobe's answer -- numbers withheld")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
