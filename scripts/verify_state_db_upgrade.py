#!/usr/bin/env python3
"""Upgrade a COPY of a real ``state.db`` to the migration head and prove no row moved (roadmap I3).

I3's acceptance: a copy of production ``state.db`` (with its ``-wal``/``-shm``)
upgrades to head with every table's rows byte-identical -- the ``.dump``
``INSERT`` lines, before vs after, excluding only ``alembic_version`` -- and the
same copy with one column dropped is refused, left unstamped and unchanged.

    python3 scripts/verify_state_db_upgrade.py --self-test
    python3 scripts/verify_state_db_upgrade.py --db /path/to/state.db

``--db`` is never opened: the file and its ``-wal``/``-shm`` siblings are copied
into a temporary directory three times (before / after / drifted) and only the
copies are touched. Copy from a quiescent database (no scan running), or the
three files may not agree with each other.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import migrate  # noqa: E402
from app.state import Base  # noqa: E402

VERSION = migrate.VERSION_TABLE
DRIFT_COLUMN = ("media_state", "content_rating")


def _copy(src: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir()
    for suffix in ("", "-wal", "-shm"):
        f = Path(f"{src}{suffix}")
        if f.exists():
            shutil.copy2(f, dest_dir / f"state.db{suffix}")
    return dest_dir / "state.db"


def _dump(db: Path) -> tuple[list[str], list[str]]:
    """``.dump`` split into (data lines, schema lines), both without the version table."""
    con = sqlite3.connect(db)
    try:
        lines = list(con.iterdump())
    finally:
        con.close()
    data = [ln for ln in lines if ln.startswith("INSERT INTO ")]
    schema = [ln for ln in lines if not ln.startswith("INSERT INTO ") and ln not in ("BEGIN TRANSACTION;", "COMMIT;")]
    data = [ln for ln in data if not ln.startswith(f'INSERT INTO "{VERSION}"')]
    schema = [ln for ln in schema if f'"{VERSION}"' not in ln and f"TABLE {VERSION} " not in ln]
    return data, schema


def _rows_per_table(data: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for ln in data:
        table = ln.split(" ", 3)[2].strip('"')
        out[table] = out.get(table, 0) + 1
    return out


def data_differences(before: list[str], after: list[str]) -> list[str]:
    """Every row line present on one side only (or reordered); empty = byte-identical."""
    if before == after:
        return []
    b, a = set(before), set(after)
    out = [f"lost:  {ln[:160]}" for ln in before if ln not in a]
    out += [f"added: {ln[:160]}" for ln in after if ln not in b]
    return out or ["same rows, different order"]


def _version(db: Path) -> list[tuple]:
    con = sqlite3.connect(db)
    try:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE name=?", (VERSION,)).fetchall():
            return []
        return con.execute("SELECT version_num FROM alembic_version").fetchall()
    finally:
        con.close()


def verify(src: Path, work: Path) -> list[str]:
    """Run the three checks on copies of ``src``; return the failures (empty = pass)."""
    failures: list[str] = []
    before_data, before_schema = _dump(_copy(src, work / "before"))
    if not before_data:
        return ["the source database holds no rows -- a byte-identical diff of nothing proves nothing"]
    counts = _rows_per_table(before_data)
    print(f"  rows: {sum(counts.values())} ({', '.join(f'{t} {n}' for t, n in sorted(counts.items()))})")

    after_db = _copy(src, work / "after")
    outcome = migrate.upgrade_to_head(after_db)
    after_data, after_schema = _dump(after_db)
    head = migrate.ScriptDirectory.from_config(migrate._config()).get_current_head()
    print(f"  upgrade: {outcome}; version {_version(after_db)}; head {head}")
    if outcome != "stamped":
        failures.append(f"expected a pre-I3 database to be stamped, got {outcome!r}")
    if _version(after_db) != [(head,)]:
        failures.append(f"alembic_version is {_version(after_db)}, expected [({head!r},)]")
    diffs = data_differences(before_data, after_data)
    print(f"  data lines before {len(before_data)}, after {len(after_data)}, differing {len(diffs)}")
    failures += [f"row changed: {d}" for d in diffs[:20]]
    if before_schema != after_schema:
        failures.append("schema outside alembic_version changed:\n    " + "\n    ".join(after_schema))
    again = migrate.upgrade_to_head(after_db)
    print(f"  second start: {again}")
    if again != "current":
        failures.append(f"second start did {again!r}, expected 'current'")

    drift_db = _copy(src, work / "drifted")
    con = sqlite3.connect(drift_db)
    con.execute(f"ALTER TABLE {DRIFT_COLUMN[0]} DROP COLUMN {DRIFT_COLUMN[1]}")
    con.commit()
    con.close()
    drift_before = _dump(drift_db)
    try:
        migrate.upgrade_to_head(drift_db)
        failures.append(f"a copy with {'.'.join(DRIFT_COLUMN)} dropped was NOT refused")
    except migrate.SchemaMismatchError as exc:
        first = str(exc).splitlines()[1:2]
        print(f"  drifted copy refused: {first}")
        if DRIFT_COLUMN[1] not in str(exc):
            failures.append(f"refusal does not name {DRIFT_COLUMN[1]}: {exc}")
    if _version(drift_db):
        failures.append("the refused copy was stamped")
    if _dump(drift_db) != drift_before:
        failures.append("the refused copy was modified")
    return failures


def self_test() -> None:
    """Both directions: the comparator sees a planted change, and passes an untouched copy."""
    assert data_differences(["a", "b"], ["a", "b"]) == []
    assert data_differences(["a", "b"], ["a", "c"]) == ["lost:  b", "added: c"]
    assert data_differences(["a", "b"], ["b", "a"]) == ["same rows, different order"]
    assert data_differences(["a", "a"], ["a"]) != []  # a lost duplicate is still a difference
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        empty = tmp / "empty.db"
        sqlite3.connect(empty).close()
        (tmp / "w0").mkdir()
        assert verify(empty, tmp / "w0") != [], "an empty source must not pass"

        # A pre-I3 database, built the way pre-I3 releases did: create_all() on the models.
        src = tmp / "src.db"
        from sqlalchemy import create_engine

        engine = create_engine(f"sqlite:///{src}")
        Base.metadata.create_all(engine)
        engine.dispose()
        con = sqlite3.connect(src)
        con.execute("INSERT INTO media_state (item_id, source, content_rating) VALUES ('jellyfin:x', 'jellyfin', 'PG')")
        con.execute("INSERT INTO app_meta VALUES ('config_hash', 'abc')")
        con.commit()
        con.close()
        (tmp / "w1").mkdir()
        assert verify(src, tmp / "w1") == [], "an untouched pre-I3 copy must pass"

        # Plant a row change inside the upgrade and check the comparator catches it.
        real = migrate.upgrade_to_head

        def tampering(db):
            out = real(db)
            c = sqlite3.connect(db)
            c.execute("UPDATE app_meta SET value = 'tampered'")
            c.commit()
            c.close()
            return out

        migrate.upgrade_to_head = tampering
        try:
            (tmp / "w2").mkdir()
            found = verify(src, tmp / "w2")
        finally:
            migrate.upgrade_to_head = real
        assert any("tampered" in f for f in found), found
    print("self-test OK")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, help="state.db to verify (copied, never opened)")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    self_test()
    if args.self_test:
        return 0
    if not args.db or not args.db.exists():
        ap.error("--db must name an existing state.db")
    with tempfile.TemporaryDirectory() as tmp:
        print(f"{args.db}:")
        failures = verify(args.db, Path(tmp))
    for f in failures:
        print(f"FAIL {f}")
    print("PASS" if not failures else f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
