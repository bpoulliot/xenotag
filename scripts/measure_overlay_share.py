#!/usr/bin/env python3
"""Measure the overlay's share of per-item scan time (roadmap I7, pillow-simd).

For a seeded sample of index rows that carry an ``image_path``, this times the
two halves of what a scan pays per item:

* **probe** -- ``scanner.probe_file()`` itself (imported, never retyped) on the
  item's media file, which only reads it;
* **overlay** -- the real ``overlay.apply_overlay()`` on a COPY of the item's
  clean poster (its ``.orig`` backup where one exists) placed under
  ``--work-dir``, with badge groups built by ``pipeline._make_badge_groups()``
  from the row, so the open/convert/render/JPEG-save I/O the scan pays is timed.

The scan probes on a ``ThreadPoolExecutor`` of ``scan.max_workers`` and runs
``_process_one_item()`` (and so the overlay) serially on the consuming thread,
so a second pass times the probes through a pool of that size to give the
probe side's real throughput.

Nothing is written outside ``--work-dir``, which is refused if it sits under a
media root. Run it in a throwaway container of the prod image with the media
mounted ``:ro`` (same ffprobe, same Pillow, same CPU limit as a real scan)::

    python3 scripts/measure_overlay_share.py --self-test
    python3 scripts/measure_overlay_share.py --db /tmp/copy/state.db \\
        --config /tmp/copy/config.yml --n 300 --work-dir /tmp/i7 --out /tmp/i7.json

Copy ``state.db`` with its ``-wal``/``-shm`` first; it is opened ``mode=ro``.
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import statistics
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

from app.config import AppConfig, load_config  # noqa: E402
from app.overlay import _backup_path, _find_image, apply_overlay  # noqa: E402
from app.pipeline import _make_badge_groups, _read_only_session, _tracks_from_row  # noqa: E402
from app.scanner import MediaInfo, probe_file  # noqa: E402
from app.state import MediaState  # noqa: E402

_FORBIDDEN_ROOTS = ("/mnt/media", "/media")


def _guard_work_dir(work_dir: Path) -> None:
    resolved = str(work_dir.resolve())
    for root in _FORBIDDEN_ROOTS:
        if resolved == root or resolved.startswith(root + "/"):
            raise SystemExit(f"refusing --work-dir under a media root: {resolved}")


def _pct(values: list[float], q: float) -> float:
    """Nearest-rank percentile (q in 0..100)."""
    ordered = sorted(values)
    rank = max(1, -(-len(ordered) * q // 100))
    return ordered[int(rank) - 1]


def summarise(values: list[float]) -> dict:
    return {
        "n": len(values),
        "median_ms": round(statistics.median(values) * 1000, 1),
        "p95_ms": round(_pct(values, 95) * 1000, 1),
        "mean_ms": round(statistics.fmean(values) * 1000, 1),
        "total_s": round(sum(values), 2),
    }


def _info_from_row(row: MediaState) -> MediaInfo:
    audio, subs = _tracks_from_row(row)
    return MediaInfo(
        resolution=row.resolution or "",
        languages=json.loads(row.languages or "[]"),
        raw_audio_langs=[],
        video_codec=row.video_codec,
        hdr_type=row.hdr_type,
        audio_tracks=audio,
        subtitle_tracks=subs,
    )


def stage_poster(image_path: Path, dest_dir: Path, suffix: str) -> Path | None:
    """Copy an item's clean poster (backup if present) into ``dest_dir`` as both
    the target and its backup, so ``apply_overlay`` runs its steady-state path.
    Only reads ``image_path``; returns the staged folder, or None."""
    backup = _backup_path(image_path, suffix)
    source = backup if backup.exists() else image_path
    if not source.exists():
        return None
    dest_dir.mkdir(parents=True)
    staged = dest_dir / image_path.name
    shutil.copyfile(source, staged)
    shutil.copyfile(source, _backup_path(staged, suffix))
    return dest_dir


def time_overlay(folder: Path, info: MediaInfo, rating: str | None, cfg: AppConfig) -> float | None:
    groups, rating_group = _make_badge_groups(info, rating, cfg)
    if not (groups or rating_group):
        return None
    start = time.perf_counter()
    out = apply_overlay(folder, groups, rating_group, cfg.image)
    elapsed = time.perf_counter() - start
    return elapsed if out else None


def time_probe(path: str) -> tuple[float, bool]:
    start = time.perf_counter()
    info = probe_file(path)
    return time.perf_counter() - start, info is not None


def measure(args: argparse.Namespace) -> dict:
    work_dir = Path(args.work_dir)
    _guard_work_dir(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    cfg = load_config(args.config)
    session = _read_only_session(Path(args.db))
    try:
        rows = session.query(MediaState).filter(MediaState.image_path.isnot(None)).all()
        session.expunge_all()
    finally:
        session.close()
    rows.sort(key=lambda r: r.item_id)
    rng = random.Random(args.seed)  # noqa: S311  # reproducible sampling, not crypto
    sample = rng.sample(rows, min(args.n, len(rows)))

    records = []
    for i, row in enumerate(sample):
        image_path = Path(row.image_path)
        rec = {"item_id": row.item_id, "image": image_path.name, "file_path": row.file_path}
        folder = stage_poster(image_path, work_dir / f"{i:04d}", cfg.image.backup_suffix)
        if folder is None:
            rec["skip"] = "no poster on disk"
            records.append(rec)
            continue
        with Image.open(_find_image(folder, cfg.image.targets)) as img:
            rec["poster_px"] = list(img.size)
        rec["overlay_s"] = time_overlay(folder, _info_from_row(row), row.content_rating, cfg)
        rec["probe_s"], rec["probe_ok"] = time_probe(row.file_path)
        records.append(rec)
        shutil.rmtree(folder)
        if (i + 1) % 25 == 0:
            print(f"  {i + 1}/{len(sample)}", file=sys.stderr, flush=True)

    ok = [r for r in records if r.get("overlay_s") is not None and r.get("probe_ok")]
    overlay = [r["overlay_s"] for r in ok]
    probe = [r["probe_s"] for r in ok]
    shares = [r["overlay_s"] / (r["overlay_s"] + r["probe_s"]) for r in ok]

    # Pass 2: the probe side as the scan runs it -- a pool of scan.max_workers.
    # Cached from pass 1, so this is a lower bound on the probe side's wall time.
    workers = cfg.scan.max_workers
    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(time_probe, [r["file_path"] for r in ok]))
    pool_wall = time.perf_counter() - start

    return {
        "sampled": len(sample),
        "rows_with_image": len(rows),
        "measured": len(ok),
        "skipped": len(records) - len(ok),
        "overlay": summarise(overlay) if overlay else None,
        "probe_serial": summarise(probe) if probe else None,
        "share_per_item": {
            "median": round(statistics.median(shares), 4) if shares else None,
            "p95": round(_pct(shares, 95), 4) if shares else None,
            "of_totals": round(sum(overlay) / (sum(overlay) + sum(probe)), 4) if ok else None,
        },
        "probe_pool": {
            "workers": workers,
            "wall_s": round(pool_wall, 2),
            "per_item_ms": round(pool_wall / len(ok) * 1000, 1) if ok else None,
            "note": "second read of the same files (page cache warm): lower bound",
        },
        "records": records,
    }


def self_test() -> int:
    """Fails if the timer, the overlay, the share arithmetic or the guard cannot
    tell things that must differ apart."""
    failures = []
    # Timer, both directions: a 50 ms sleep reads >= 50 ms, a no-op reads < 5 ms.
    start = time.perf_counter()
    time.sleep(0.05)
    slept = time.perf_counter() - start
    start = time.perf_counter()
    noop = time.perf_counter() - start
    if not (slept >= 0.05 and noop < 0.005):
        failures.append(f"timer: slept={slept} noop={noop}")

    # Percentile against a naive reference.
    vals = [float(v) for v in range(1, 101)]
    if _pct(vals, 95) != 95.0 or _pct(vals, 50) != 50.0 or _pct([7.0], 95) != 7.0:
        failures.append("percentile")

    cfg = AppConfig()
    with tempfile.TemporaryDirectory() as tmp:
        src_dir = Path(tmp) / "library"
        src_dir.mkdir()
        poster = src_dir / "poster.jpg"
        Image.new("RGB", (600, 900), (40, 90, 160)).save(poster, format="JPEG", quality=92)
        original = poster.read_bytes()
        folder = stage_poster(poster, Path(tmp) / "work" / "0000", cfg.image.backup_suffix)
        staged = folder / "poster.jpg"
        info = MediaInfo("1080p", ["EN"], ["eng"], "H.265", "HDR10", [], [])
        from app.scanner import AudioTrack, SubTrack

        info.audio_tracks = [AudioTrack("EN", "EAC3")]
        info.subtitle_tracks = [SubTrack("EN", "SRT", False)]
        elapsed = time_overlay(folder, info, "PG-13", cfg)
        # Overlay must change the staged copy, and must NOT touch the source.
        if elapsed is None or elapsed <= 0:
            failures.append("overlay was not timed")
        if staged.read_bytes() == original:
            failures.append("overlay left the staged poster unchanged")
        if poster.read_bytes() != original:
            failures.append("overlay touched the source poster")
        if _backup_path(staged, cfg.image.backup_suffix).read_bytes() != original:
            failures.append("staged backup is not the clean original")
        # Degenerate: no badges at all -> nothing timed, nothing written.
        bare = MediaInfo("", [], [], None, None, [], [])
        bare_cfg = cfg.model_copy(deep=True)
        bare_cfg.image.show_rating_badge = False
        bare_cfg.image.show_video_badges = False
        bare_cfg.image.show_audio_badges = False
        bare_cfg.image.show_sub_badges = False
        folder2 = stage_poster(poster, Path(tmp) / "work" / "0001", cfg.image.backup_suffix)
        if time_overlay(folder2, bare, None, bare_cfg) is not None:
            failures.append("a badge-less item was timed")
        if (folder2 / "poster.jpg").read_bytes() != original:
            failures.append("a badge-less item was written")
        # A missing poster is skipped, not staged.
        if stage_poster(src_dir / "nope.jpg", Path(tmp) / "work" / "0002", ".orig") is not None:
            failures.append("a missing poster was staged")

    # Guard, both directions.
    for bad in ("/mnt/media/x", "/media/core"):
        try:
            _guard_work_dir(Path(bad))
            failures.append(f"guard let {bad} through")
        except SystemExit:
            pass
    try:
        _guard_work_dir(Path(tempfile.gettempdir()) / "i7-ok")
    except SystemExit:
        failures.append("guard refused /tmp")

    if failures:
        print("SELF-TEST FAILED:", *failures, sep="\n  ")
        return 1
    print("self-test ok")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--db", help="COPY of state.db (opened read-only)")
    ap.add_argument("--config", help="config.yml (read-only) whose image/tag settings shape the badges")
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--work-dir", help="scratch dir for poster copies (never under a media root)")
    ap.add_argument("--out", help="write the full result (with per-item records) here")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    if self_test():
        return 1
    if not (args.db and args.config and args.work_dir):
        ap.error("--db, --config and --work-dir are required")
    result = measure(args)
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != "records"}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
