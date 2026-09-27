"""I3: ``state.db`` schema is versioned by Alembic, and ``init_db()`` owns the upgrade.

The fixtures rebuild a pre-I3 database from literal DDL -- the ``CREATE`` text
production's file holds, and the ``ALTER TABLE ... ADD COLUMN`` path the old
``_migrate_schema()`` took -- never from today's models, which will move on.
"""

from __future__ import annotations

import logging
import multiprocessing
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.util import CommandError
from sqlalchemy import Column, MetaData, String

from app import migrate, state
from app.state import Base

# What production's state.db holds (read from a copy, 2026-09-26).
PRE_I3_DDL = [
    """CREATE TABLE media_state (
	item_id VARCHAR NOT NULL, source VARCHAR NOT NULL, file_path TEXT, resolution VARCHAR,
	languages TEXT, tags_applied TEXT, image_path TEXT, last_scanned DATETIME, file_mtime FLOAT,
	video_codec VARCHAR, hdr_type VARCHAR, audio_tracks TEXT, subtitle_tracks TEXT,
	content_rating VARCHAR, PRIMARY KEY (item_id))""",
    """CREATE TABLE app_meta ("key" VARCHAR NOT NULL, value TEXT, PRIMARY KEY ("key"))""",
    """CREATE TABLE scan_runs (id INTEGER NOT NULL, started_at DATETIME, completed_at DATETIME,
	items_scanned INTEGER, items_tagged INTEGER, items_image_modified INTEGER, scan_type VARCHAR,
	PRIMARY KEY (id))""",
    """CREATE TABLE scan_errors (id INTEGER NOT NULL, item_id VARCHAR, item_name TEXT, file_path TEXT,
	error_type VARCHAR, first_seen DATETIME, last_seen DATETIME, scan_count INTEGER, PRIMARY KEY (id))""",
    "CREATE INDEX ix_scan_errors_item_id ON scan_errors (item_id)",
]

# The oldest shape: media_state before the five columns, then _migrate_schema()'s ALTERs.
ALTER_BUILT_DDL = [
    """CREATE TABLE media_state (
	item_id VARCHAR NOT NULL, source VARCHAR NOT NULL, file_path TEXT, resolution VARCHAR,
	languages TEXT, tags_applied TEXT, image_path TEXT, last_scanned DATETIME, file_mtime FLOAT,
	PRIMARY KEY (item_id))""",
    *PRE_I3_DDL[1:],
    "ALTER TABLE media_state ADD COLUMN video_codec VARCHAR",
    "ALTER TABLE media_state ADD COLUMN hdr_type VARCHAR",
    "ALTER TABLE media_state ADD COLUMN audio_tracks TEXT",
    "ALTER TABLE media_state ADD COLUMN subtitle_tracks TEXT",
    "ALTER TABLE media_state ADD COLUMN content_rating VARCHAR",
]

ROWS = [
    "INSERT INTO media_state VALUES ('jellyfin:a1', 'jellyfin', '/m/Film (1999)/f.mkv', '1080p', '[\"EN\"]', "
    "'[\"xt-1080p\"]', '/m/Film (1999)/poster.jpg', '2026-09-01 03:00:00.000000', 1725160000.5, 'hevc', 'hdr10', "
    '\'[{"lang": "EN", "codec": "eac3"}]\', \'[]\', \'PG-13\')',
    "INSERT INTO media_state (item_id, source) VALUES ('jellyfin:b2', 'jellyfin')",
    "INSERT INTO app_meta VALUES ('config_hash', 'abc123')",
    "INSERT INTO scan_runs VALUES (1, '2026-09-01 03:00:00', '2026-09-01 04:00:00', 10, 2, 1, 'full')",
    "INSERT INTO scan_errors VALUES (7, 'jellyfin:c3', 'Show S01E01', '/m/show.mkv', 'probe_failed', "
    "'2026-09-01 03:00:00', '2026-09-02 03:00:00', 2)",
]


def _build(db: Path, ddl: list[str], rows: list[str] = ROWS) -> Path:
    con = sqlite3.connect(db)
    for stmt in ddl + rows:
        con.execute(stmt)
    con.commit()
    con.close()
    return db


def _data(db: Path) -> list[str]:
    """``.dump``'s INSERT lines, minus the version table -- the rows, byte for byte."""
    con = sqlite3.connect(db)
    try:
        return [
            line
            for line in con.iterdump()
            if line.startswith("INSERT INTO") and not line.startswith(f'INSERT INTO "{migrate.VERSION_TABLE}"')
        ]
    finally:
        con.close()


# Columns later revisions added to a table the baseline already had. Upgrading a
# database adds each one to every existing row as NULL and leaves the rest alone.
ADDED_SINCE_BASELINE = {"media_state": ("field_order",)}  # 0002 (U5)


def _rows(db: Path) -> dict[str, list[dict[str, str]]]:
    """Every row of every table but the version table, per column as SQLite's ``quote()``.

    ``quote()`` is what ``.dump`` writes, so a value is compared byte for byte, type
    included, but by column name -- an added column cannot shift the others.
    """
    con = sqlite3.connect(db)
    try:
        out: dict[str, list[dict[str, str]]] = {}
        tables = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        for (table,) in sorted(tables):
            if table == migrate.VERSION_TABLE:
                continue
            cols = [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]
            # Every name here was read from the file's own schema, not from input.
            select = ", ".join(f'quote("{c}")' for c in cols)
            query = f'SELECT {select} FROM "{table}" ORDER BY rowid'  # noqa: S608
            out[table] = [dict(zip(cols, r, strict=True)) for r in con.execute(query)]
        return out
    finally:
        con.close()


def _at_head(rows: dict[str, list[dict[str, str]]]) -> dict[str, list[dict[str, str]]]:
    """``rows`` (read before an upgrade) as the upgrade to head must leave them."""
    return {
        table: [{**row, **{c: "NULL" for c in ADDED_SINCE_BASELINE.get(table, ())}} for row in table_rows]
        for table, table_rows in rows.items()
    }


def _version(db: Path) -> list[tuple]:
    con = sqlite3.connect(db)
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if migrate.VERSION_TABLE not in tables:
            return []
        return con.execute("SELECT version_num FROM alembic_version").fetchall()
    finally:
        con.close()


def _head() -> str:
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(migrate._config()).get_current_head()


def test_fresh_database_is_built_at_head(tmp_path):
    db = tmp_path / "state.db"
    assert migrate.upgrade_to_head(db) == "created"
    assert _version(db) == [(_head(),)]
    con = sqlite3.connect(db)
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    con.close()


def test_pre_i3_database_is_stamped_and_its_rows_are_untouched(tmp_path):
    db = _build(tmp_path / "state.db", PRE_I3_DDL)
    assert len(_data(db)) == len(ROWS)
    before = _rows(db)
    assert migrate.upgrade_to_head(db) == "stamped"
    assert _version(db) == [(_head(),)]
    assert _rows(db) == _at_head(before)


def test_alter_built_database_is_stamped_although_its_create_text_differs(tmp_path):
    a = _build(tmp_path / "a.db", PRE_I3_DDL)
    b = _build(tmp_path / "b.db", ALTER_BUILT_DDL)

    def create_text(db):
        con = sqlite3.connect(db)
        try:
            return con.execute("SELECT sql FROM sqlite_master WHERE name='media_state'").fetchone()[0]
        finally:
            con.close()

    # The premise: same schema, different stored text. A text comparison would refuse b.
    assert create_text(a) != create_text(b)
    before = _rows(b)
    assert migrate.upgrade_to_head(b) == "stamped"
    assert _rows(b) == _at_head(before)


def test_0002_adds_field_order_as_null_and_keeps_every_row(tmp_path):
    """U5: a database at 0001 -- production since v1.8.0 -- upgrades in place."""
    db = _build(tmp_path / "state.db", PRE_I3_DDL)
    engine = migrate._engine(db)
    try:
        with engine.connect() as conn, conn.begin():
            command.stamp(migrate._config(conn), "0001")
    finally:
        engine.dispose()
    before = _rows(db)
    assert migrate.upgrade_to_head(db) == "upgraded"
    assert _version(db) == [(_head(),)]
    after = _rows(db)
    assert after == _at_head(before)
    # The comparison can fail both ways: the column really was added, and a changed value shows.
    assert after != before
    planted = _at_head(before)
    planted["media_state"][0]["content_rating"] = "'R'"
    assert after != planted
    con = sqlite3.connect(db)
    column = [r for r in con.execute("PRAGMA table_info(media_state)") if r[1] == "field_order"]
    con.close()
    assert column == [(14, "field_order", "VARCHAR", 0, None, 0)]  # nullable, no default, appended


def test_second_start_changes_nothing(tmp_path):
    db = _build(tmp_path / "state.db", PRE_I3_DDL)
    migrate.upgrade_to_head(db)
    before = _data(db)
    assert migrate.upgrade_to_head(db) == "current"
    assert _data(db) == before
    assert _version(db) == [(_head(),)]


DRIFTS = {
    "missing column": (
        ["ALTER TABLE media_state DROP COLUMN content_rating"],
        "media_state: column content_rating missing",
    ),
    "extra column": (["ALTER TABLE scan_runs ADD COLUMN notes TEXT"], "scan_runs: column notes not in the baseline"),
    "changed type": (
        [
            "ALTER TABLE app_meta RENAME TO old",
            'CREATE TABLE app_meta ("key" VARCHAR NOT NULL, value BLOB, PRIMARY KEY ("key"))',
            "INSERT INTO app_meta SELECT * FROM old",
            "DROP TABLE old",
        ],
        "app_meta: column value expected",
    ),
    "missing index": (["DROP INDEX ix_scan_errors_item_id"], "scan_errors: index ix_scan_errors_item_id missing"),
    "extra index": (
        ["CREATE INDEX ix_extra ON media_state (source)"],
        "media_state: index ix_extra not in the baseline",
    ),
    "extra table": (["CREATE TABLE notes (id INTEGER)"], "table notes: not in the baseline"),
    "extra view": (["CREATE VIEW v AS SELECT item_id FROM media_state"], "view v: not in the baseline"),
    "missing table": (["DROP TABLE scan_runs"], "table scan_runs: missing"),
}


@pytest.mark.parametrize("drift", sorted(DRIFTS))
def test_drifted_database_is_refused_and_left_unstamped(tmp_path, drift):
    ddl, expected_line = DRIFTS[drift]
    db = _build(tmp_path / "state.db", PRE_I3_DDL)
    con = sqlite3.connect(db)
    for stmt in ddl:
        con.execute(stmt)
    con.commit()
    con.close()
    before = _data(db)
    with pytest.raises(migrate.SchemaMismatchError) as exc:
        migrate.upgrade_to_head(db)
    assert expected_line in str(exc.value)
    assert _version(db) == []
    assert _data(db) == before


def test_init_db_refuses_a_drifted_database(tmp_path):
    db = _build(tmp_path / "state.db", PRE_I3_DDL)
    con = sqlite3.connect(db)
    con.execute("ALTER TABLE media_state DROP COLUMN hdr_type")
    con.commit()
    con.close()
    with pytest.raises(migrate.SchemaMismatchError, match="hdr_type missing"):
        state.init_db(db)


def test_init_db_opens_a_pre_i3_database(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "_engine", None)
    monkeypatch.setattr(state, "_SessionLocal", None)
    db = _build(tmp_path / "state.db", ALTER_BUILT_DDL)
    state.init_db(db)
    session = state.get_session()
    try:
        assert session.get(state.MediaState, "jellyfin:a1").content_rating == "PG-13"
    finally:
        session.close()
        state._engine.dispose()


def test_a_revision_this_image_does_not_know_is_left_alone(tmp_path, caplog):
    db = tmp_path / "state.db"
    migrate.upgrade_to_head(db)
    con = sqlite3.connect(db)
    con.execute("UPDATE alembic_version SET version_num = '9999'")
    con.commit()
    con.close()
    with caplog.at_level(logging.WARNING, logger="app.migrate"):
        assert migrate.upgrade_to_head(db) == "newer"
    assert "9999" in caplog.text
    assert _version(db) == [("9999",)]


def test_a_failed_migration_leaves_nothing_half_applied(tmp_path):
    # scan_errors already exists, so the baseline's LAST create fails after three succeed.
    db = _build(tmp_path / "state.db", [PRE_I3_DDL[3]], rows=[])
    engine = migrate._engine(db)
    try:
        with pytest.raises(Exception, match="already exists"):
            with engine.connect() as conn, conn.begin():
                command.upgrade(migrate._config(conn), "head")
    finally:
        engine.dispose()
    con = sqlite3.connect(db)
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master")}
    con.close()
    assert names == {"scan_errors"}


def _start_together(barrier, results, db):
    barrier.wait()
    try:
        results.put(migrate.upgrade_to_head(db))
    except Exception as exc:  # reported, not raised: the parent asserts on it
        results.put(f"{type(exc).__name__}: {exc}")


@pytest.mark.parametrize("kind", ["fresh", "pre-I3", "pre-I3 WAL"])
def test_simultaneous_starts_migrate_once(tmp_path, kind):
    """Eight processes released by one barrier. Measured, 10 runs of each kind: with a deferred
    BEGIN instead of BEGIN IMMEDIATE this test fails 30 of 30; without the WAL-switch retry,
    12 of 30 (fresh 7, pre-I3 5, already-WAL 0 -- a file already in WAL never needs it)."""
    db = tmp_path / "state.db"
    if kind != "fresh":
        _build(db, PRE_I3_DDL)
    if kind == "pre-I3 WAL":
        con = sqlite3.connect(db)
        con.execute("PRAGMA journal_mode=WAL")
        con.close()
    before = _rows(db)
    ctx = multiprocessing.get_context("fork")
    barrier, results = ctx.Barrier(8), ctx.Queue()
    procs = [ctx.Process(target=_start_together, args=(barrier, results, db)) for _ in range(8)]
    for p in procs:
        p.start()
    outcomes = [results.get(timeout=120) for _ in procs]
    for p in procs:
        p.join(timeout=30)
    winner = "created" if kind == "fresh" else "stamped"
    assert sorted(outcomes) == sorted([winner] + ["current"] * 7)
    assert _version(db) == [(_head(),)]
    if kind == "fresh":
        assert _data(db) == []
    else:
        assert _rows(db) == _at_head(before)


def test_check_passes_when_models_match_the_revisions():
    migrate.check()


def test_check_fails_on_a_model_change_with_no_revision():
    drifted = MetaData()
    for table in Base.metadata.sorted_tables:
        table.to_metadata(drifted)
    drifted.tables["media_state"].append_column(Column("planted", String))
    with pytest.raises(CommandError, match="planted"):
        migrate.check(drifted)


def test_the_baseline_matches_what_production_holds(tmp_path):
    db = _build(tmp_path / "state.db", PRE_I3_DDL, rows=[])
    engine = migrate._engine(db)
    try:
        with engine.connect() as conn:
            actual = migrate.schema_signature(conn)
    finally:
        engine.dispose()
    assert migrate.schema_differences(migrate.baseline_signature(), actual) == []


def test_downgrade_below_the_baseline_is_refused(tmp_path):
    db = tmp_path / "state.db"
    migrate.upgrade_to_head(db)
    engine = migrate._engine(db)
    try:
        with pytest.raises(NotImplementedError):
            with engine.connect() as conn, conn.begin():
                command.downgrade(migrate._config(conn), "base")
    finally:
        engine.dispose()
    assert _version(db) == [(_head(),)]
