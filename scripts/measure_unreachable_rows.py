#!/usr/bin/env python3
"""Count the index rows the *arr dry run must not plan (roadmap B11).

A scan skips an item before tagging when it has no path, its file is gone, its
ffprobe fails, or processing raises -- and records a ``scan_errors`` row
instead. Its ``media_state`` row then keeps describing the last file the scan
*could* read. The index-driven *arr dry run (``run_arr_dry_run()``) reports
such an item as unreachable when its newest error is later than its row; this
script applies the same classification (``pipeline._unreachable_errors``) to a
copied index, with no Jellyfin or *arr call, so ownership is NOT determined
here: the counts are items with an error row, not owned items.

    python3 scripts/measure_unreachable_rows.py --self-test
    python3 scripts/measure_unreachable_rows.py --db /tmp/copy/state.db

Copy ``state.db`` with its ``-wal``/``-shm`` first; it is opened ``mode=ro``.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.pipeline import _read_only_session, _unreachable_errors  # noqa: E402
from app.state import Base, MediaState, ScanError  # noqa: E402


def classify(db_path: Path) -> dict:
    """Every item with a ``scan_errors`` row: unreachable, no row, or fixed since."""
    session = _read_only_session(db_path)
    try:
        errors = _unreachable_errors(session)
        rows = {
            item_id.removeprefix("jellyfin:"): last_scanned
            for item_id, last_scanned in session.query(MediaState.item_id, MediaState.last_scanned)
        }
        names = dict(session.query(ScanError.item_id, ScanError.item_name))
    finally:
        session.close()
    out: dict = {"unreachable": Counter(), "no_row": Counter(), "fixed_since": Counter(), "unreachable_items": []}
    for item_id, (last_seen, error_type) in errors.items():
        kind = error_type.split(":", 1)[0]
        if item_id not in rows:
            out["no_row"][kind] += 1
        elif rows[item_id] is None or last_seen > rows[item_id]:
            out["unreachable"][kind] += 1
            out["unreachable_items"].append(
                {
                    "id": item_id,
                    "item": names.get(item_id),
                    "error": kind,
                    "error_seen": str(last_seen),
                    "row": str(rows[item_id]),
                }
            )
        else:
            out["fixed_since"][kind] += 1
    out["items_with_errors"] = len(errors)
    out["index_rows"] = len(rows)
    return out


def naive(db_path: Path) -> set[str]:
    """Reference: the same rule as one SQL join, compared as ISO strings."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        q = """SELECT e.item_id FROM scan_errors e JOIN media_state m ON m.item_id = 'jellyfin:' || e.item_id
               GROUP BY e.item_id HAVING m.last_scanned IS NULL OR MAX(e.last_seen) > m.last_scanned"""
        return {r[0] for r in conn.execute(q)}
    finally:
        conn.close()


def self_test() -> None:
    old, new = datetime(2026, 6, 2), datetime(2026, 9, 26, 9)
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "state.db"
        engine = create_engine(f"sqlite:///{db}")
        Base.metadata.create_all(engine)
        s = sessionmaker(bind=engine)()
        rows = {"stale": old, "fixed": new, "clean": old}
        s.add_all(MediaState(item_id=f"jellyfin:{k}", source="jellyfin", last_scanned=v) for k, v in rows.items())
        s.add_all(
            [
                ScanError(item_id="stale", item_name="stale", error_type="process_error: boom", last_seen=new),
                ScanError(item_id="fixed", item_name="fixed", error_type="probe_failed", last_seen=old),
                ScanError(item_id="gone", item_name="gone", error_type="no_file", last_seen=new),
            ]
        )
        s.commit()
        s.close()
        engine.dispose()
        got = classify(db)
        # must find what is there, must not find what is not, and must agree with the reference
        assert got["unreachable"] == Counter(process_error=1), got
        assert got["fixed_since"] == Counter(probe_failed=1), got
        assert got["no_row"] == Counter(no_file=1), got
        assert naive(db) == {x["id"] for x in got["unreachable_items"]} == {"stale"}, naive(db)
        assert got["items_with_errors"] == 3 and got["index_rows"] == 3
    print("self-test OK")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, help="a COPY of state.db (with -wal/-shm)")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    self_test()
    if args.self_test:
        return 0
    if not args.db:
        ap.error("--db is required")
    got = classify(args.db)
    ref = naive(args.db)
    mine = {x["id"] for x in got["unreachable_items"]}
    if mine != ref:
        print(f"REFUSING TO REPORT: classifier and SQL reference disagree on {sorted(mine ^ ref)}")
        return 1
    got["unreachable_items"].sort(key=lambda x: (x["error"], x["item"] or ""))
    print(json.dumps({k: dict(v) if isinstance(v, Counter) else v for k, v in got.items()}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
