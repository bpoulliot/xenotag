#!/usr/bin/env python3
"""Upgrade a COPY of a real ``state.db`` to the migration head and prove no row moved (roadmap I3).

I3's acceptance: a copy of production ``state.db`` (with its ``-wal``/``-shm``)
upgrades to head with every table's rows byte-identical in every column it
already had -- each value as SQLite's ``quote()``, which is what ``.dump``
writes -- excluding only ``alembic_version``; every column a revision added
(0002: ``media_state.field_order``, U5) is NULL on every existing row; and the
same copy, unversioned and with one column dropped, is refused, left unstamped
and unchanged.

The source may be unversioned (a pre-I3 file: stamped, then upgraded) or at an
older revision (production since v1.8.0: upgraded).

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

from alembic import command  # noqa: E402

from app import migrate  # noqa: E402

VERSION = migrate.VERSION_TABLE
DRIFT_COLUMN = ("media_state", "content_rating")


def _copy(src: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir()
    for suffix in ("", "-wal", "-shm"):
        f = Path(f"{src}{suffix}")
        if f.exists():
            shutil.copy2(f, dest_dir / f"state.db{suffix}")
    return dest_dir / "state.db"


def _columns(db: Path) -> dict[str, list[str]]:
    """Every user table but the version table, with its columns in declared order."""
    con = sqlite3.connect(db)
    try:
        tables = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        return {
            t: [r[1] for r in con.execute(f'PRAGMA table_info("{t}")')]
            for (t,) in sorted(tables.fetchall())
            if t != VERSION
        }
    finally:
        con.close()


def _data(db: Path, columns: dict[str, list[str]]) -> list[str]:
    """One line per row, ``table: quote(col1), quote(col2), ...`` over ``columns`` only.

    ``quote()`` is what ``.dump`` writes, so this is the ``.dump`` comparison, by
    column name: a column a revision appends cannot shift the ones compared.
    """
    con = sqlite3.connect(db)
    try:
        out: list[str] = []
        for table, cols in columns.items():
            # Table and column names come from the database's own schema.
            select = ", ".join(f'quote("{c}")' for c in cols)
            query = f'SELECT {select} FROM "{table}" ORDER BY rowid'  # noqa: S608
            out += [f"{table}: " + ", ".join(row) for row in con.execute(query)]
        return out
    finally:
        con.close()


def _non_null(db: Path, columns: dict[str, list[str]]) -> dict[str, int]:
    """``table.column`` -> how many rows hold a value in it, for each of ``columns``."""
    con = sqlite3.connect(db)
    try:
        return {
            f"{t}.{c}": con.execute(f'SELECT count(*) FROM "{t}" WHERE "{c}" IS NOT NULL').fetchone()[0]  # noqa: S608
            for t, cols in columns.items()
            for c in cols
        }
    finally:
        con.close()


def _rows_per_table(data: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for ln in data:
        table = ln.split(":", 1)[0]
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
    before_db = _copy(src, work / "before")
    before_columns = _columns(before_db)
    before_data = _data(before_db, before_columns)
    if not before_data:
        return ["the source database holds no rows -- a byte-identical diff of nothing proves nothing"]
    counts = _rows_per_table(before_data)
    print(f"  rows: {sum(counts.values())} ({', '.join(f'{t} {n}' for t, n in sorted(counts.items()))})")
    before_version = _version(before_db)

    after_db = _copy(src, work / "after")
    outcome = migrate.upgrade_to_head(after_db)
    head = migrate.ScriptDirectory.from_config(migrate._config()).get_current_head()
    print(f"  upgrade: {outcome}; version {before_version} -> {_version(after_db)}; head {head}")
    expected = "stamped" if not before_version else ("current" if before_version == [(head,)] else "upgraded")
    if outcome != expected:
        failures.append(f"expected a database at {before_version or 'no version'} to be {expected}, got {outcome!r}")
    if _version(after_db) != [(head,)]:
        failures.append(f"alembic_version is {_version(after_db)}, expected [({head!r},)]")
    after_columns = _columns(after_db)
    for table, cols in before_columns.items():
        lost = [c for c in cols if c not in after_columns.get(table, [])]
        if table not in after_columns or lost:
            failures.append(f"{table}: lost {lost or 'the whole table'}")
    if failures:
        return failures
    after_data = _data(after_db, before_columns)
    diffs = data_differences(before_data, after_data)
    print(f"  data lines before {len(before_data)}, after {len(after_data)}, differing {len(diffs)}")
    failures += [f"row changed: {d}" for d in diffs[:20]]
    added = {t: [c for c in cols if c not in before_columns.get(t, [])] for t, cols in after_columns.items()}
    added = {t: cols for t, cols in added.items() if cols}
    held = _non_null(after_db, added)
    print(f"  columns added: {', '.join(held) or 'none'}; rows holding a value in them: {sum(held.values())}")
    failures += [f"added column {col} holds a value on {n} existing row(s)" for col, n in held.items() if n]
    again = migrate.upgrade_to_head(after_db)
    print(f"  second start: {again}")
    if again != "current":
        failures.append(f"second start did {again!r}, expected 'current'")

    # Drift is judged only on an unversioned file, so the drifted copy loses its version too.
    drift_db = _copy(src, work / "drifted")
    con = sqlite3.connect(drift_db)
    con.execute(f"ALTER TABLE {DRIFT_COLUMN[0]} DROP COLUMN {DRIFT_COLUMN[1]}")
    con.execute(f"DROP TABLE IF EXISTS {VERSION}")
    con.commit()
    con.close()
    drift_before = _data(drift_db, _columns(drift_db))
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
    if _data(drift_db, _columns(drift_db)) != drift_before:
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

        # A pre-I3 database: the baseline revision's schema -- what every pre-I3 release
        # built -- with no version table. Never today's models, which have moved on.
        src = tmp / "src.db"
        engine = migrate._engine(src)
        try:
            with engine.connect() as conn, conn.begin():
                command.upgrade(migrate._config(conn), migrate.BASELINE_REVISION)
                conn.exec_driver_sql(f"DROP TABLE {VERSION}")
        finally:
            engine.dispose()
        con = sqlite3.connect(src)
        con.execute("INSERT INTO media_state (item_id, source, content_rating) VALUES ('jellyfin:x', 'jellyfin', 'PG')")
        con.execute("INSERT INTO app_meta VALUES ('config_hash', 'abc')")
        con.commit()
        con.close()
        (tmp / "w1").mkdir()
        assert verify(src, tmp / "w1") == [], "an untouched pre-I3 copy must pass"

        # The same file at the baseline revision -- production since v1.8.0 -- upgrades.
        versioned = tmp / "versioned.db"
        shutil.copy2(src, versioned)
        engine = migrate._engine(versioned)
        try:
            with engine.connect() as conn, conn.begin():
                command.stamp(migrate._config(conn), migrate.BASELINE_REVISION)
        finally:
            engine.dispose()
        (tmp / "w1v").mkdir()
        assert verify(versioned, tmp / "w1v") == [], "an untouched copy at the baseline revision must pass"

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

        # And a value planted in a column the upgrade added.
        def filling(db):
            out = real(db)
            c = sqlite3.connect(db)
            c.execute("UPDATE media_state SET field_order = 'tt'")
            c.commit()
            c.close()
            return out

        migrate.upgrade_to_head = filling
        try:
            (tmp / "w3").mkdir()
            found = verify(versioned, tmp / "w3")
        finally:
            migrate.upgrade_to_head = real
        assert any("media_state.field_order holds a value" in f for f in found), found
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
