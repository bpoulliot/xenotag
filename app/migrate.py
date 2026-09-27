"""Versioned schema management for ``state.db`` (roadmap I3), driven by Alembic.

``init_db()`` calls ``upgrade_to_head()`` on every start, from the app, the
scanner and ``python -m app.arr_sync`` alike. What it does depends on the file:

* **empty** (no tables): ``upgrade head`` builds the schema from the revisions;
* **versioned** (``alembic_version`` holds a revision this image knows): ``upgrade head``;
* **unversioned with tables** (built by a pre-I3 release's ``create_all()``):
  compared with the baseline revision's schema and **stamped** only on an exact
  match, then upgraded. Any difference refuses to start, naming it;
* **versioned by a newer image** (a revision this image does not know): logged
  and left alone. An additive migration keeps an older image working.

"Exact" compares, per table, every column's name, declared type, NOT NULL,
default and primary-key position; every index's columns, uniqueness and
partialness (explicit indexes by name, SQLite's automatic ones by their
columns); and the set of tables, views and triggers. It never compares SQLite's
stored ``CREATE`` text, which differs for the same schema depending on whether a
column came from ``CREATE TABLE`` or a later ``ALTER TABLE ... ADD COLUMN``.
The reference is built by running the baseline revision on a scratch database
and reading it back the same way, so both sides go through one reader.

Concurrency: every step runs in one transaction opened ``BEGIN IMMEDIATE``, which
takes SQLite's write lock before the version is read. A second process starting
at the same moment waits for the lock, then re-reads the version under it and
finds nothing left to do. SQLite DDL is transactional, so a failure leaves the
file exactly as it was.

    python -m app.migrate check                     # alembic check: models == revisions
    python -m app.migrate revision -m "add x" --rev-id 0002
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util import CommandError
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Connection, Engine

from .state import _configure_sqlite

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).with_name("migrations")
BASELINE_REVISION = "0001"
VERSION_TABLE = "alembic_version"

# IMMEDIATE takes the write lock at BEGIN, so the version read that follows
# cannot go stale before we act on it. A plain (deferred) BEGIN would let two
# processes both read "unversioned" and then race to create the same tables.
_BEGIN = "BEGIN IMMEDIATE"
# How long a second process waits for the first one's migration to commit.
_LOCK_TIMEOUT_S = 60.0


class SchemaMismatchError(RuntimeError):
    """An unversioned ``state.db`` whose schema is not the baseline; never stamped."""


def _config(connection: Connection | None = None, **attributes) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR).replace("%", "%%"))
    cfg.set_main_option("path_separator", "os")
    cfg.attributes["connection"] = connection
    cfg.attributes.update(attributes)
    return cfg


def _engine(db_path: str | Path) -> Engine:
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"timeout": _LOCK_TIMEOUT_S, "check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _connect(dbapi_conn, record) -> None:
        # The driver would otherwise run DDL outside any transaction; we open them.
        dbapi_conn.isolation_level = None
        # Switching a file INTO WAL needs an exclusive lock, and SQLite answers
        # "database is locked" at once rather than through the busy timeout --
        # measured: 8 processes starting on a new file lost that race 13 times in 20.
        # It cannot move inside BEGIN IMMEDIATE (no journal-mode change in a transaction).
        deadline = time.monotonic() + _LOCK_TIMEOUT_S
        while True:
            try:
                _configure_sqlite(dbapi_conn, record)
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc) or time.monotonic() > deadline:
                    raise
                time.sleep(0.05)

    @event.listens_for(engine, "begin")
    def _begin(conn) -> None:
        conn.exec_driver_sql(_BEGIN)

    return engine


def schema_signature(conn: Connection) -> dict:
    """The comparable shape of every user table, view and trigger (``alembic_version`` excluded)."""
    sig: dict = {}
    rows = conn.exec_driver_sql(
        "SELECT type, name, tbl_name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' AND type != 'index'"
    ).all()
    for kind, name, _tbl in rows:
        if name == VERSION_TABLE:
            continue
        if kind != "table":
            sig[name] = {"kind": kind}
            continue
        columns = {
            r[1]: {"type": (r[2] or "").upper(), "not_null": bool(r[3]), "default": r[4], "pk": r[5]}
            for r in conn.exec_driver_sql(f'PRAGMA table_info("{name}")')
        }
        indexes = {}
        for r in conn.exec_driver_sql(f'PRAGMA index_list("{name}")'):
            idx_name, unique, origin, partial = r[1], r[2], r[3], r[4]
            cols = tuple(c[2] for c in conn.exec_driver_sql(f'PRAGMA index_info("{idx_name}")'))
            key = idx_name if origin == "c" else f"<automatic {origin} index on {', '.join(cols)}>"
            indexes[key] = {"columns": cols, "unique": bool(unique), "partial": bool(partial)}
        sig[name] = {"kind": "table", "columns": columns, "indexes": indexes}
    return sig


def baseline_signature() -> dict:
    """``schema_signature()`` of a scratch database built by the baseline revision alone."""
    with tempfile.TemporaryDirectory() as tmp:
        engine = _engine(Path(tmp) / "baseline.db")
        try:
            with engine.connect() as conn, conn.begin():
                command.upgrade(_config(conn), BASELINE_REVISION)
                return schema_signature(conn)
        finally:
            engine.dispose()


def schema_differences(expected: dict, actual: dict) -> list[str]:
    """Every way ``actual`` differs from ``expected``, one readable line each; empty = identical."""
    out: list[str] = []
    for name in sorted(expected.keys() - actual.keys()):
        out.append(f"{expected[name]['kind']} {name}: missing")
    for name in sorted(actual.keys() - expected.keys()):
        out.append(f"{actual[name]['kind']} {name}: not in the baseline")
    for name in sorted(expected.keys() & actual.keys()):
        exp, act = expected[name], actual[name]
        if exp["kind"] != act["kind"]:
            out.append(f"{name}: expected a {exp['kind']}, found a {act['kind']}")
            continue
        if exp["kind"] != "table":
            continue
        for part, label in (("columns", "column"), ("indexes", "index")):
            e, a = exp[part], act[part]
            for key in sorted(e.keys() - a.keys()):
                out.append(f"{name}: {label} {key} missing")
            for key in sorted(a.keys() - e.keys()):
                out.append(f"{name}: {label} {key} not in the baseline ({a[key]})")
            for key in sorted(e.keys() & a.keys()):
                if e[key] != a[key]:
                    out.append(f"{name}: {label} {key} expected {e[key]}, found {a[key]}")
    return out


def _known_revisions() -> set[str]:
    return {s.revision for s in ScriptDirectory.from_config(_config()).walk_revisions()}


def upgrade_to_head(db_path: str | Path) -> str:
    """Bring ``db_path`` to the head revision; return what happened.

    One of ``created``, ``stamped``, ``upgraded``, ``current`` or ``newer``.
    Raises ``SchemaMismatchError`` for an unversioned database that is not the
    baseline -- it is left untouched and unstamped.
    """
    head = ScriptDirectory.from_config(_config()).get_current_head()
    engine = _engine(db_path)
    try:
        with engine.connect() as conn, conn.begin():
            # Under the write lock from here on: re-read, then act.
            current = MigrationContext.configure(conn).get_current_revision()
            if current is None:
                actual = schema_signature(conn)
                if not actual:
                    command.upgrade(_config(conn), "head")
                    outcome = "created"
                else:
                    diffs = schema_differences(baseline_signature(), actual)
                    if diffs:
                        raise SchemaMismatchError(
                            f"{db_path} has no schema version and does not match the xenotag baseline, "
                            "so it will not be stamped. Differences:\n  - "
                            + "\n  - ".join(diffs)
                            + "\nRestore a backup of state.db, or move it aside to let xenotag build a fresh "
                            "index (the next scan re-tags everything)."
                        )
                    command.stamp(_config(conn), BASELINE_REVISION)
                    if head != BASELINE_REVISION:
                        command.upgrade(_config(conn), "head")
                    outcome = "stamped"
            elif current == head:
                outcome = "current"
            elif current in _known_revisions():
                command.upgrade(_config(conn), "head")
                outcome = "upgraded"
            else:
                log.warning(
                    "%s is at schema revision %s, which this xenotag image does not know: a newer "
                    "release migrated it. Starting without migrating. An additive migration keeps "
                    "an older image working; after a non-additive one, restore the pre-upgrade backup.",
                    db_path,
                    current,
                )
                return "newer"
    finally:
        engine.dispose()
    log.info("state.db schema: %s (revision %s)", outcome, head)
    return outcome


def check(target_metadata=None) -> None:
    """``alembic check`` against a fresh database built by the startup path.

    Raises ``alembic.util.CommandError`` (``AutogenerateDiffsDetected``) if the
    models and the revisions disagree -- a model change with no revision, or
    the reverse.
    """
    attributes = {} if target_metadata is None else {"target_metadata": target_metadata}
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "state.db"
        upgrade_to_head(db)
        engine = _engine(db)
        try:
            with engine.connect() as conn, conn.begin():
                command.check(_config(conn, **attributes))
        finally:
            engine.dispose()


def revision(message: str, rev_id: str | None) -> None:
    """Autogenerate a revision from the models' difference to head, into ``app/migrations/versions``."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "state.db"
        upgrade_to_head(db)
        engine = _engine(db)
        try:
            with engine.connect() as conn, conn.begin():
                command.revision(_config(conn), message=message, autogenerate=True, rev_id=rev_id)
        finally:
            engine.dispose()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.migrate", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="fail if the models and the migration revisions disagree (alembic check)")
    rev = sub.add_parser("revision", help="autogenerate a new revision from the models")
    rev.add_argument("-m", "--message", required=True)
    rev.add_argument("--rev-id", help="revision id; use the next number, e.g. 0002")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s — %(message)s")
    try:
        if args.cmd == "check":
            check()
            print("alembic check: models match the migration head")
        else:
            revision(args.message, args.rev_id)
    except CommandError as exc:
        print(f"alembic {args.cmd}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
