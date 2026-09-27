"""I12: `_utcnow()` replaces the deprecated stdlib call with the SAME naive UTC value.

Two things must hold: the helper returns naive UTC (nothing stored or compared changes), and
the column defaults take the function, not a call — `default=_utcnow()` would freeze import
time into every row.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, Integer, create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.state import Base, ScanError, ScanRun, _utcnow


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_utcnow_is_naive_and_current_utc():
    got = _utcnow()
    ref = datetime.now(UTC).replace(tzinfo=None)
    assert got.tzinfo is None
    assert abs((got - ref).total_seconds()) < 1


def _two_rows_a_moment_apart(session, model, column: str) -> tuple[datetime, datetime]:
    first = model()
    session.add(first)
    session.commit()
    time.sleep(0.02)
    second = model()
    session.add(second)
    session.commit()
    return getattr(first, column), getattr(second, column)


def test_the_check_catches_a_frozen_default():
    # Negative control: a default evaluated once at class creation must fail the per-row check.
    class _FrozenBase(DeclarativeBase):
        pass

    class Frozen(_FrozenBase):
        __tablename__ = "frozen"
        id = Column(Integer, primary_key=True)
        stamp = Column(DateTime, default=_utcnow())

    engine = create_engine("sqlite:///:memory:")
    _FrozenBase.metadata.create_all(engine)
    a, b = _two_rows_a_moment_apart(sessionmaker(bind=engine)(), Frozen, "stamp")
    assert a == b


def test_defaults_fire_per_row():
    session = _session()
    cases = [(ScanRun, "started_at"), (ScanError, "first_seen"), (ScanError, "last_seen")]
    for model, column in cases:
        a, b = _two_rows_a_moment_apart(session, model, column)
        assert a is not None and b is not None, (model.__name__, column)
        assert a.tzinfo is None and b.tzinfo is None, (model.__name__, column)
        assert b > a, (model.__name__, column, a, b)
        assert abs((b - _utcnow()).total_seconds()) < 1, (model.__name__, column)
