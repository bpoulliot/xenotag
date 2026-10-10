#!/usr/bin/env python3
"""Does any library file carry NEW content under its OLD mtime? (roadmap U2, re-encode half)

The incremental scan skips an item when ``os.path.getmtime(file) == media_state.file_mtime``
(``pipeline._run_scan``, phase 1b), so a tool that rewrites a file and restores its mtime is
never re-probed. This probe compares index rows with the files they describe:

* ``sample`` -- a seeded random draw of index rows (``--db``, a COPY of state.db), each
  ``os.stat``-ed and re-probed by xenotag's own ``app.scanner.probe_file`` inside a throwaway
  container of the deployed image (media ``:ro``, ``--network none``), then classified:

    A  mtime equal, content fields equal         consistent
    B  mtime differs                             the next incremental scan re-probes it
    C  mtime equal, content fields differ        the case U2 asks about
    D  file missing
    P  probe failed (the scan records a scan_errors row instead)

  Content fields: ``video_codec``, ``resolution``, ``hdr_type`` and the audio track list.
  Subtitle tracks and ``field_order`` are compared too and reported beside the class, not in
  it (an external subtitle sidecar changes without touching the video's mtime).
  mtime tolerance: none -- equality is exact, because the scan's own skip test is ``==`` on the
  float, so any difference at all puts the file in B.

* ``census`` -- every row of an OLDER index copy (``--old-db``, rows written by one full scan,
  ``--scan``) whose path and mtime are unchanged in ``--db`` but whose content fields differ.
  Those rows are re-probed with the image that wrote the old row (``--old-image``), so a
  scanner rule change between versions reads A (old code, old answer) and only a content
  change reads C. With ``--old-image`` omitted the old and new rows must come from the same
  scanner code, and the differing rows are reported as C candidates without a probe.

    python3 scripts/measure_reencode_mtime.py --self-test --image ghcr.io/bpoulliot/xenotag:1.11.1
    python3 scripts/measure_reencode_mtime.py sample --db /tmp/copy/state.db \\
        --image ghcr.io/bpoulliot/xenotag:1.11.1 --n 200 --seed 61 --out RUNDIR
    python3 scripts/measure_reencode_mtime.py census --db /tmp/copy/state.db \\
        --old-db /tmp/copy/state.db.bak --scan 148 --old-image ghcr.io/bpoulliot/xenotag:1.9.0 --out RUNDIR

``--self-test`` (needs docker and the image; ffmpeg runs inside it, so no CI step) re-encodes
scratch copies of a generated clip and asserts each lands in its class; every other mode
refuses to run when it fails. Per-file records (paths, so titles) go to ``--out`` only; stdout
carries counts.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from collections import Counter
from pathlib import Path

MEDIA_ROOTS = ["4K", "core", "flux", "luxe", "prometheus", "scopuli", "xtor"]
CONTENT_FIELDS = ("video_codec", "resolution", "hdr_type", "audio_tracks")
SIDE_FIELDS = ("subtitle_tracks", "field_order")
ROW_COLUMNS = "item_id, file_path, file_mtime, last_scanned, " + ", ".join(CONTENT_FIELDS + SIDE_FIELDS)
EPISODE_RE = re.compile(r"[Ss]\d{1,2}[Ee]\d{1,3}|/Season \d+/", re.ASCII)


# --------------------------------------------------------------------------- comparison


def _norm(field: str, value):
    """A stored column (JSON text for the track lists) and a fresh probe compare as equal values."""
    if field in ("audio_tracks", "subtitle_tracks"):
        if isinstance(value, str):
            value = json.loads(value or "[]")
        return value or []
    return value


def classify(stored: dict, observed: dict) -> tuple[str, list[str], list[str]]:
    """Class letter, the content fields that differ, the side fields that differ."""
    if not observed.get("exists"):
        return "D", [], []
    if observed["st_mtime"] != stored["file_mtime"]:
        return "B", [], []
    probe = observed.get("probe")
    if probe is None:
        return "P", [], []
    content = [f for f in CONTENT_FIELDS if _norm(f, stored.get(f)) != _norm(f, probe.get(f))]
    side = [f for f in SIDE_FIELDS if f in probe and _norm(f, stored.get(f)) != _norm(f, probe.get(f))]
    return ("C" if content else "A"), content, side


# --------------------------------------------------------------------------- in-container


def observe(path: str) -> dict:
    """``os.stat`` and ``app.scanner.probe_file`` -- the image's own code -- for one file."""
    from app.scanner import probe_file

    out: dict = {"path": path, "exists": os.path.exists(path)}
    if not out["exists"]:
        return out
    st = os.stat(path)
    out.update(st_mtime=os.path.getmtime(path), st_mtime_ns=st.st_mtime_ns, st_size=st.st_size)
    info = probe_file(path)
    if info is None:
        out["probe"] = None
        return out
    probe = {
        "video_codec": info.video_codec,
        "resolution": info.resolution,
        "hdr_type": info.hdr_type,
        "audio_tracks": [{"lang": t.lang, "codec": t.codec} for t in info.audio_tracks],
        "subtitle_tracks": [{"lang": t.lang, "format": t.format, "embedded": t.embedded} for t in info.subtitle_tracks],
    }
    if hasattr(info, "field_order"):  # U5; images before v1.10 have no field_order
        probe["field_order"] = info.field_order
    out["probe"] = probe
    return out


def worker(jobs: Path, out: Path) -> None:
    """Serial: one probe at a time, appended and flushed per file (the host's load matters more)."""
    done = set()
    if out.exists():
        done = {json.loads(line)["key"] for line in out.read_text().splitlines() if line.strip()}
    with out.open("a") as fh:
        for line in jobs.read_text().splitlines():
            job = json.loads(line)
            if job["key"] in done:
                continue
            rec = observe(job["path"])
            rec["key"] = job["key"]
            fh.write(json.dumps(rec) + "\n")
            fh.flush()


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def self_test_inner(workdir: Path) -> dict:
    """Plant one file per class and check each lands there. Runs inside the image."""
    orig = workdir / "orig.mkv"
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=24",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
        "-t", "2", "-c:v", "libx264", "-c:a", "aac", "-metadata:s:a:0", "language=eng", str(orig),
    )  # fmt: skip
    base = observe(str(orig))
    assert base["probe"] and base["probe"]["video_codec"], f"probe of the planted clip failed: {base}"
    ns = base["st_mtime_ns"]
    stored = {"file_mtime": base["st_mtime"], **base["probe"]}

    def keep_mtime(p: Path) -> None:
        os.utime(p, ns=(ns, ns))

    planted: dict[str, tuple[Path, str]] = {}
    a = workdir / "a_untouched.mkv"
    subprocess.run(["cp", "-p", str(orig), str(a)], check=True)
    planted["untouched copy"] = (a, "A")
    b = workdir / "b_touched.mkv"
    subprocess.run(["cp", "-p", str(orig), str(b)], check=True)
    os.utime(b, ns=(ns, ns + 3_600_000_000_000))
    planted["touched only"] = (b, "B")
    c1 = workdir / "c_video_reencoded.mkv"
    _ffmpeg("-i", str(orig), "-c:v", "mpeg4", "-c:a", "copy", str(c1))
    keep_mtime(c1)
    planted["video re-encoded, mtime restored"] = (c1, "C")
    c2 = workdir / "c_audio_reencoded.mkv"
    _ffmpeg("-i", str(orig), "-c:v", "copy", "-c:a", "ac3", "-metadata:s:a:0", "language=fre", str(c2))
    keep_mtime(c2)
    planted["audio re-encoded, mtime restored"] = (c2, "C")
    c3 = workdir / "c_rescaled.mkv"
    _ffmpeg("-i", str(orig), "-vf", "scale=640:360", "-c:v", "libx264", "-c:a", "copy", str(c3))
    keep_mtime(c3)
    planted["downscaled, mtime restored"] = (c3, "C")
    b2 = workdir / "b_reencoded_new_mtime.mkv"
    _ffmpeg("-i", str(orig), "-c:v", "mpeg4", "-c:a", "copy", str(b2))
    planted["re-encoded, new mtime"] = (b2, "B")
    planted["missing"] = (workdir / "d_missing.mkv", "D")

    results, failures = {}, []
    for label, (path, want) in planted.items():
        got, content, _ = classify(stored, observe(str(path)))
        results[label] = {"want": want, "got": got, "differs": content}
        if got != want:
            failures.append(label)
    # Both directions: a comparator that never fires, or always fires, must fail here.
    seen = {r["got"] for r in results.values()}
    if not {"A", "C"} <= seen:
        failures.append("A and C not both produced")
    if classify(stored, {"exists": True, "st_mtime": stored["file_mtime"], "probe": dict(base["probe"])})[0] != "A":
        failures.append("a row compared with its own probe is not A")
    return {"results": results, "failures": failures, "ok": not failures}


# --------------------------------------------------------------------------- host side


def _docker_run(image: str, args: list[str], mounts: list[str], name: str) -> int:
    """Detached, labelled, ``--rm``; blocks on ``docker wait`` and returns the exit code."""
    cmd = [
        "docker", "run", "-d", "--rm", "--name", name,
        "--label", f"overnight.item={os.environ.get('OVERNIGHT_ITEM', 'measure_reencode_mtime')}",
        "--network", "none", "--cpus", "2", "--user", f"{os.getuid()}:{os.getgid()}",
        "-e", "PYTHONPATH=/app", "--entrypoint", "python3",
        "--mount", f"type=bind,src={Path(__file__).resolve().parent},dst=/scripts,readonly",
        *mounts, image, "/scripts/" + Path(__file__).name, *args,
    ]  # fmt: skip
    subprocess.run(cmd, check=True, capture_output=True)
    try:
        code = int(subprocess.run(["docker", "wait", name], capture_output=True, text=True, check=True).stdout)
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    return code


def run_self_test(image: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="xt-reencode-st-") as tmp:
        code = _docker_run(
            image,
            ["--self-test-inner", "/work"],
            [f"--mount=type=bind,src={tmp},dst=/work"],
            f"xt-reencode-st-{uuid.uuid4().hex[:8]}",
        )
        report_path = Path(tmp) / "self-test.json"
        report = json.loads(report_path.read_text()) if report_path.exists() else {"ok": False}
        report["exit"] = code
        report["image"] = image
        return report


def probe_in_image(image: str, jobs: list[dict], outdir: Path, tag: str) -> dict[str, dict]:
    jobs_file, obs_file = outdir / f"{tag}-jobs.jsonl", outdir / f"{tag}-observed.jsonl"
    jobs_file.write_text("".join(json.dumps(j) + "\n" for j in jobs))
    mounts = [f"--mount=type=bind,src=/mnt/media/{r},dst=/media/{r},readonly" for r in MEDIA_ROOTS]
    mounts.append(f"--mount=type=bind,src={outdir},dst=/out")
    code = _docker_run(
        image,
        ["--worker", f"/out/{jobs_file.name}", f"/out/{obs_file.name}"],
        mounts,
        f"xt-reencode-{tag}-{uuid.uuid4().hex[:8]}",
    )
    if code != 0:
        raise SystemExit(f"worker exited {code}; partial output in {obs_file}")
    return {r["key"]: r for r in map(json.loads, obs_file.read_text().splitlines())}


def _rows(db: Path, where: str = "", params: tuple = ()) -> list[dict]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        have = {r[1] for r in con.execute("PRAGMA table_info(media_state)")}
        cols = ", ".join(c for c in ROW_COLUMNS.split(", ") if c in have)  # field_order: revision 0002
        sql = f"SELECT {cols} FROM media_state WHERE file_path IS NOT NULL AND file_path != '' {where}"  # noqa: S608 -- columns from a constant, values bound
        return [dict(r) for r in con.execute(sql, params)]
    finally:
        con.close()


def _reported_deleted(report: Path | None) -> set[str]:
    """Every item id anywhere in a deleted-items report (it lists examples, not every row)."""
    if not report:
        return set()
    ids: set[str] = set()

    def walk(v):
        if isinstance(v, dict):
            for k, x in v.items():
                if k in ("item_id", "id", "jellyfin_id") and isinstance(x, str):
                    ids.add(x if x.startswith("jellyfin:") else f"jellyfin:{x}")
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(json.loads(report.read_text()))
    return ids


def kind(path: str) -> str:
    return "episode" if EPISODE_RE.search(path) else "movie"


def _summarise(rows: list[dict], observed: dict[str, dict], outdir: Path, tag: str) -> dict:
    classes: Counter = Counter()
    by_kind: Counter = Counter()
    content_diff: Counter = Counter()
    side_diff: Counter = Counter()
    b_delta: Counter = Counter()
    records = []
    for row in rows:
        obs = observed[row["item_id"]]
        cls, content, side = classify(row, obs)
        classes[cls] += 1
        by_kind[(kind(row["file_path"]), cls)] += 1
        for f in content:
            content_diff[f] += 1
        for f in side:
            side_diff[f"{cls}:{f}"] += 1
        if cls == "B":
            d = abs(obs["st_mtime"] - row["file_mtime"])
            b_delta["<1s" if d < 1 else "<1d" if d < 86400 else ">=1d"] += 1
        records.append({"class": cls, "content_differs": content, "side_differs": side, "row": row, "observed": obs})
    (outdir / f"{tag}-classified.json").write_text(json.dumps(records, indent=1))
    return {
        "classes": dict(sorted(classes.items())),
        "by_kind": {f"{k}:{c}": n for (k, c), n in sorted(by_kind.items())},
        "content_fields_differing": dict(content_diff),
        "side_fields_differing": dict(side_diff),
        "B_mtime_delta": dict(b_delta),
    }


def cmd_sample(a) -> dict:
    excluded = _reported_deleted(a.report)
    population = [r for r in _rows(a.db) if r["item_id"] not in excluded]
    kinds = Counter(kind(r["file_path"]) for r in population)
    rng = random.Random(a.seed)  # noqa: S311 -- seeded so a re-run reproduces the draw
    sample = rng.sample(population, a.n)
    sample_kinds = Counter(kind(r["file_path"]) for r in sample)
    if min(sample_kinds.get(k, 0) for k in ("movie", "episode")) < 30:
        raise SystemExit(f"a class got < 30 ({dict(sample_kinds)}): stratify before reporting")
    jobs = [{"key": r["item_id"], "path": r["file_path"]} for r in sample]
    observed = probe_in_image(a.image, jobs, a.out, "sample")
    summary = _summarise(sample, observed, a.out, "sample")
    scanned = Counter((r["last_scanned"] or "")[:10] for r in sample)
    return {
        "mode": "sample",
        "image": a.image,
        "seed": a.seed,
        "population": len(population),
        "population_kinds": dict(kinds),
        "excluded_reported_deleted": len(excluded),
        "n": a.n,
        "sample_kinds": dict(sample_kinds),
        "sample_last_scanned_days": dict(scanned.most_common()),
        **summary,
    }


def cmd_census(a) -> dict:
    con = sqlite3.connect(f"file:{a.old_db}?mode=ro", uri=True)
    started, completed = con.execute(
        "SELECT started_at, completed_at FROM scan_runs WHERE id = ?", (a.scan,)
    ).fetchone()
    con.close()
    old = _rows(a.old_db, "AND last_scanned BETWEEN ? AND ?", (started, completed))
    new = {r["item_id"]: r for r in _rows(a.db)}
    stats: Counter = Counter()
    differing = []
    for row in old:
        cur = new.get(row["item_id"])
        if cur is None:
            stats["gone"] += 1
        elif cur["file_path"] != row["file_path"]:
            stats["path_changed"] += 1
        elif cur["file_mtime"] != row["file_mtime"]:
            stats["mtime_changed"] += 1
        else:
            stats["mtime_unchanged"] += 1
            fields = [f for f in CONTENT_FIELDS if _norm(f, cur[f]) != _norm(f, row[f])]
            if fields:
                stats["mtime_unchanged_fields_differ"] += 1
                stats["differ:" + "+".join(fields)] += 1
                differing.append(row)
    result = {
        "mode": "census",
        "old_scan": a.scan,
        "old_scan_window": [started, completed],
        "rows_in_old_scan": len(old),
        **dict(stats),
    }
    if a.old_image and differing:
        jobs = [{"key": r["item_id"], "path": r["file_path"]} for r in differing]
        observed = probe_in_image(a.old_image, jobs, a.out, f"census{a.scan}")
        result["old_image"] = a.old_image
        result["reprobed"] = _summarise(differing, observed, a.out, f"census{a.scan}")
    else:
        (a.out / f"census{a.scan}-candidates.json").write_text(json.dumps(differing, indent=1))
        result["unprobed_candidates_C"] = len(differing)
    return result


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--self-test", action="store_true", help="plant one file per class (needs docker + --image)")
    p.add_argument("--self-test-inner", type=Path, help=argparse.SUPPRESS)
    p.add_argument("--worker", nargs=2, type=Path, metavar=("JOBS", "OUT"), help=argparse.SUPPRESS)
    p.add_argument("mode", nargs="?", choices=["sample", "census"])
    p.add_argument("--image", default="ghcr.io/bpoulliot/xenotag:latest", help="the deployed image")
    p.add_argument("--db", type=Path, help="COPY of state.db (with -wal/-shm), opened mode=ro")
    p.add_argument("--report", type=Path, help="newest deleted-items-report.json (sample mode)")
    p.add_argument("--n", type=int, default=200)
    p.add_argument("--seed", type=int)
    p.add_argument("--old-db", type=Path, help="older index COPY (census mode)")
    p.add_argument("--scan", type=int, help="the full scan whose rows --old-db holds (census mode)")
    p.add_argument("--old-image", help="the image that ran --scan (census mode)")
    p.add_argument("--out", type=Path, help="run directory for per-file records")
    a = p.parse_args()

    if a.self_test_inner:
        report = self_test_inner(a.self_test_inner)
        (a.self_test_inner / "self-test.json").write_text(json.dumps(report, indent=1))
        return 0 if report["ok"] else 1
    if a.worker:
        worker(*a.worker)
        return 0

    st = run_self_test(a.image)
    print(json.dumps({"self_test": st}, indent=1))
    if not st.get("ok"):
        print("SELF-TEST FAILED -- refusing to report", file=sys.stderr)
        return 2
    if a.self_test:
        return 0
    if not (a.mode and a.db and a.out):
        p.error("a mode needs --db and --out")
    a.out.mkdir(parents=True, exist_ok=True)
    if a.mode == "sample":
        if a.seed is None:
            p.error("sample needs --seed (record it)")
        result = cmd_sample(a)
    else:
        if not (a.old_db and a.scan):
            p.error("census needs --old-db and --scan")
        result = cmd_census(a)
    tag = a.mode if a.mode == "sample" else f"census{a.scan}"
    (a.out / f"{tag}-summary.json").write_text(json.dumps(result, indent=1))
    print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
