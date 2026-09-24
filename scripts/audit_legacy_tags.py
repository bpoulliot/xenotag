#!/usr/bin/env python3
"""Count legacy (`mf-*`) and managed (`xt-*`) tags, locally and outward.

Roadmap item U1. The question U1 exists to answer is not "how do I remove the
legacy tags" but "where are they, actually" — and the answer turns out to be
two different places with two different truths:

  * **the local index** (`state.db`, `media_state.tags_applied`) is xenotag's
    own record of what it last wrote, and it is only refreshed for items a scan
    reaches. An item whose file has gone, or whose ffprobe fails, is skipped
    before `_process_one_item()` and so keeps its pre-migration record forever.
  * **the outward destinations** (Jellyfin, Sonarr, Radarr) are rewritten by
    `set_managed_tags()`, which strips `tags.legacy_prefixes` on every write.

So a legacy count in `state.db` does *not* imply a legacy tag on the media
server. This script reports both, separately, and never writes anything.

    python3 scripts/audit_legacy_tags.py --self-test          # probe self-check
    python3 scripts/audit_legacy_tags.py --db state.db        # the local index
    python3 scripts/audit_legacy_tags.py --db state.db --config config.yml --live
                                                              # + Jellyfin/*arr

`--live` issues read-only GETs only: `/Items` on Jellyfin and `/api/v3/tag` on
each *arr instance. It never PUTs, POSTs or DELETEs anything. Copy `state.db`
before pointing `--db` at a live deployment — it is opened `mode=ro`, but the
WAL means a copy needs its `-wal`/`-shm` siblings to read consistently.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.state import Base, MediaState, partition_legacy_tags, purge_legacy_tags  # noqa: E402

DEFAULT_LEGACY = ["mf-"]
DEFAULT_MANAGED = "xt-"


# ── the local index ──────────────────────────────────────────────────────────
def count_db(
    db_path: Path,
    legacy_prefixes: list[str],
    managed_prefix: str,
) -> dict:
    """Count tag applications in `media_state.tags_applied`, read-only."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT tags_applied FROM media_state").fetchall()
    finally:
        conn.close()
    return count_tag_lists([json.loads(r[0] or "[]") for r in rows], legacy_prefixes, managed_prefix)


def count_tag_lists(tag_lists: list[list[str]], legacy_prefixes: list[str], managed_prefix: str) -> dict:
    """Shared counter, so the DB path and the self-test agree by construction."""
    legacy: Counter[str] = Counter()
    managed: Counter[str] = Counter()
    other: Counter[str] = Counter()
    legacy_items = 0
    managed_items = 0
    for tags in tag_lists:
        tags = [t for t in tags if isinstance(t, str)]
        _, removed = partition_legacy_tags(tags, legacy_prefixes, managed_prefix)
        kept_managed = [t for t in tags if managed_prefix and t.startswith(managed_prefix)]
        if removed:
            legacy_items += 1
        if kept_managed:
            managed_items += 1
        legacy.update(removed)
        managed.update(kept_managed)
        other.update(t for t in tags if t not in removed and t not in kept_managed)
    return {
        "rows": len(tag_lists),
        "legacy_distinct": len(legacy),
        "legacy_applications": sum(legacy.values()),
        "legacy_items": legacy_items,
        "legacy_by_tag": dict(legacy.most_common()),
        "managed_distinct": len(managed),
        "managed_applications": sum(managed.values()),
        "managed_items": managed_items,
        "other_distinct": len(other),
    }


# ── the outward destinations ─────────────────────────────────────────────────
def count_live(config_path: Path, legacy_prefixes: list[str], managed_prefix: str) -> dict:
    """Read-only sweep of Jellyfin and every configured *arr instance."""
    import httpx
    import yaml

    cfg = yaml.safe_load(config_path.read_text()) or {}
    out: dict = {}

    jf = cfg.get("jellyfin") or {}
    base = (jf.get("url") or "").rstrip("/")
    libs = jf.get("library_ids") or [None]
    client = httpx.Client(headers={"Authorization": f'MediaBrowser Token="{jf.get("api_key", "")}"'}, timeout=120)
    try:
        seen: set[str] = set()
        tag_lists: list[list[str]] = []
        for lib in libs:
            start = 0
            while True:
                params = {
                    "Recursive": "true",
                    "IncludeItemTypes": "Movie,Series",
                    "Fields": "Tags",
                    "Limit": 500,
                    "StartIndex": start,
                }
                if lib:
                    params["ParentId"] = lib
                resp = client.get(f"{base}/Items", params=params)
                resp.raise_for_status()
                data = resp.json()
                page = data.get("Items", [])
                for item in page:
                    if item["Id"] in seen:
                        continue
                    seen.add(item["Id"])
                    tag_lists.append(item.get("Tags") or [])
                start += len(page)
                if start >= data.get("TotalRecordCount", 0) or not page:
                    break
        out["jellyfin"] = count_tag_lists(tag_lists, legacy_prefixes, managed_prefix)
    finally:
        client.close()

    arrs: dict[str, dict] = {}
    for kind in ("sonarr", "radarr"):
        for inst in (cfg.get(kind) or {}).get("instances") or []:
            name = f"{kind}/{inst.get('name', '?')}"
            try:
                url = (inst.get("url") or "").rstrip("/")
                r = httpx.get(f"{url}/api/v3/tag", headers={"X-Api-Key": inst.get("api_key", "")}, timeout=30)
                r.raise_for_status()
                labels = [t.get("label", "") for t in r.json()]
                _, removed = partition_legacy_tags(labels, legacy_prefixes, managed_prefix)
                arrs[name] = {
                    "labels": len(labels),
                    "legacy": sorted(set(removed)),
                    "managed": sorted(t for t in labels if managed_prefix and t.startswith(managed_prefix)),
                }
            except Exception as exc:  # an unreachable instance is a result, not a crash
                arrs[name] = {"error": f"{type(exc).__name__}: {exc}"}
    out["arr"] = arrs
    return out


# ── reporting ────────────────────────────────────────────────────────────────
def _print_index(label: str, c: dict, legacy_prefixes: list[str], managed_prefix: str) -> None:
    lp = ", ".join(legacy_prefixes)
    print(f"{label}: {c['rows']} items")
    print(
        f"  legacy ({lp}):  {c['legacy_distinct']:>5} distinct  {c['legacy_applications']:>6} applications"
        f"  on {c['legacy_items']} items"
    )
    print(
        f"  managed ({managed_prefix}): {c['managed_distinct']:>5} distinct  {c['managed_applications']:>6}"
        f" applications  on {c['managed_items']} items"
    )
    print(f"  neither:       {c['other_distinct']:>5} distinct")
    if c["legacy_by_tag"]:
        head = list(c["legacy_by_tag"].items())[:10]
        print("  top legacy: " + ", ".join(f"{t} ({n})" for t, n in head))


def report(db: Path | None, config: Path | None, live: bool, legacy_prefixes: list[str], managed_prefix: str) -> int:
    if db:
        _print_index(
            f"local index {db}", count_db(db, legacy_prefixes, managed_prefix), legacy_prefixes, managed_prefix
        )
    if live:
        if not config:
            print("--live needs --config", file=sys.stderr)
            return 2
        result = count_live(config, legacy_prefixes, managed_prefix)
        print()
        _print_index("live Jellyfin", result["jellyfin"], legacy_prefixes, managed_prefix)
        print()
        print("*arr tag labels (read-only):")
        for name, info in sorted(result["arr"].items()):
            if "error" in info:
                print(f"  {name:<18} {info['error']}")
            else:
                print(
                    f"  {name:<18} {info['labels']:>4} labels  legacy={len(info['legacy'])}"
                    f"  managed={len(info['managed'])}" + (f"  {info['legacy']}" if info["legacy"] else "")
                )
    return 0


# ── self-test ────────────────────────────────────────────────────────────────
def _seed(session, rows: list[tuple[str, list[str]]]) -> None:
    for item_id, tags in rows:
        session.add(MediaState(item_id=item_id, source="jellyfin", tags_applied=json.dumps(tags)))
    session.commit()


def self_test() -> int:
    """Check the probe in BOTH directions: it must see legacy tags that are
    there AND report zero once they are gone. A counter stuck at zero passes
    half of this; a counter that always fires passes the other half.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    failures: list[str] = []

    def check(name: str, got, want) -> None:
        ok = got == want
        print(f"  {'PASS' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
        if not ok:
            failures.append(name)

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    # 10 legacy applications, 7 distinct, on 4 of 6 rows -- mf-1080p appears on
    # three rows and twice on one of them, so applications != distinct and
    # applications != items. Two rows carry no legacy tag at all.
    _seed(
        session,
        [
            ("a", ["mf-1080p", "mf-EN", "mf-AV1"]),
            ("b", ["mf-1080p", "xt-1080p", "xt-EN", "anime"]),
            ("c", ["mf-1080p", "mf-1080p", "mf-sub-EN", "mf-Opus", "mf-NR"]),
            ("d", ["mf-H.264", "mfx-keepme", "xt-H.264"]),
            ("e", ["xt-720p", "xt-JA"]),
            ("f", ["luxe", "av1"]),
        ],
    )
    all_tags = [json.loads(r.tags_applied) for r in session.query(MediaState).all()]
    before = count_tag_lists(all_tags, DEFAULT_LEGACY, DEFAULT_MANAGED)

    print("direction 1 — the probe sees legacy tags that are there:")
    check("legacy distinct", before["legacy_distinct"], 7)
    check("legacy applications", before["legacy_applications"], 10)
    check("legacy items", before["legacy_items"], 4)
    check("managed applications", before["managed_applications"], 5)
    # "mfx-keepme" starts with "mf" but not with "mf-". A prefix is not a namespace.
    check("mfx-keepme not counted as legacy", "mfx-keepme" in before["legacy_by_tag"], False)
    check("mfx-keepme counted as neither", before["other_distinct"], 4)

    print()
    print("direction 2 — the probe reports zero once they are gone:")
    purged = purge_legacy_tags(session, DEFAULT_LEGACY, DEFAULT_MANAGED)
    after = count_tag_lists(
        [json.loads(r.tags_applied) for r in session.query(MediaState).all()], DEFAULT_LEGACY, DEFAULT_MANAGED
    )
    check("purge removed applications", purged["tags_removed"], 10)
    check("purge changed rows", purged["rows_changed"], 4)
    check("legacy distinct after", after["legacy_distinct"], 0)
    check("legacy applications after", after["legacy_applications"], 0)
    check("legacy items after", after["legacy_items"], 0)

    print()
    print("safety — the sweep must not touch managed tags:")
    check("managed applications unchanged", after["managed_applications"], before["managed_applications"])
    check("managed distinct unchanged", after["managed_distinct"], before["managed_distinct"])
    check("non-managed survivors unchanged", after["other_distinct"], before["other_distinct"])

    # The dangerous config: a legacy prefix that also prefix-matches the managed
    # one. "x" matches "xt-720p". The managed prefix has to win — but the sweep
    # must still remove a genuine "x"-prefixed legacy tag, or this check would
    # pass for a sweep that had simply stopped working.
    session2 = sessionmaker(bind=engine)()
    _seed(session2, [("g", ["xt-2160p", "x-ancient", "keepme"])])
    danger = purge_legacy_tags(session2, ["x"], DEFAULT_MANAGED)
    survived = json.loads(session2.get(MediaState, "g").tags_applied)
    check("overlapping prefix keeps xt-2160p", "xt-2160p" in survived, True)
    check("overlapping prefix keeps unmanaged tag", "keepme" in survived, True)
    check("overlapping prefix still removes x-ancient", danger["tags_removed"], 1)

    print()
    if failures:
        print(f"SELF-TEST FAILED: {len(failures)} check(s): {', '.join(failures)}")
        return 1
    print("SELF-TEST PASSED — the probe can both find legacy tags and confirm their absence.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, help="path to a state.db (opened read-only; copy it first)")
    ap.add_argument("--config", type=Path, help="path to a config.yml, for --live")
    ap.add_argument("--live", action="store_true", help="also sweep Jellyfin and the *arrs (read-only GETs)")
    ap.add_argument("--legacy-prefix", action="append", default=None, help=f"default {DEFAULT_LEGACY}")
    ap.add_argument("--managed-prefix", default=DEFAULT_MANAGED)
    ap.add_argument("--self-test", action="store_true", help="run the probe's own self-test and exit")
    args = ap.parse_args()

    if args.self_test:
        return self_test()
    if not args.db and not args.live:
        ap.error("nothing to do — pass --db, --live or --self-test")
    return report(args.db, args.config, args.live, args.legacy_prefix or DEFAULT_LEGACY, args.managed_prefix)


if __name__ == "__main__":
    raise SystemExit(main())
