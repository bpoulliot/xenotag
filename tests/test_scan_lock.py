"""Roadmap B22: an exception that escapes a scan must release the scan lock.

``run_full_scan()`` / ``run_incremental_scan()`` take ``progress.try_start()``;
before B22 only ``_run_scan()``'s own exits called ``progress.finish()``, so an
exception that escaped it left ``progress.running`` True and every later scan
logged ``Scan already in progress, skipping`` until the container restarted.
"""

from __future__ import annotations

import logging
import sqlite3

import pytest

from app import pipeline
from app.config import AppConfig
from tests.test_arr_sync import FakeJellyfin
from tests.test_metrics import _reset_gauges, scan_env, value  # noqa: F401 (pytest fixtures, used by name)

RUNNERS = {"full": pipeline.run_full_scan, "incremental": pipeline.run_incremental_scan}


@pytest.fixture
def finishes(monkeypatch):
    """Every ``progress.finish()`` call, in order, as its ``error`` argument."""
    calls: list[str | None] = []
    real = pipeline.progress.finish

    def counting(error=None):
        calls.append(error)
        real(error=error)

    monkeypatch.setattr(pipeline.progress, "finish", counting)
    return calls


def _locked(c, incremental):
    raise RuntimeError("database is locked")


@pytest.mark.parametrize("scan_type", ["full", "incremental"])
def test_a_raising_scan_releases_the_lock_and_still_raises(monkeypatch, finishes, scan_type):
    monkeypatch.setattr(pipeline, "_run_scan", _locked)
    before = value("xenotag_scans_total", scan_type=scan_type, outcome="failed")
    with pytest.raises(RuntimeError, match="database is locked"):
        RUNNERS[scan_type](AppConfig())
    assert pipeline.progress.running is False
    assert pipeline.progress.error == "database is locked"
    assert finishes == ["database is locked"]
    assert value("xenotag_scan_running") == 0
    assert value("xenotag_scans_total", scan_type=scan_type, outcome="failed") == before + 1


@pytest.mark.parametrize("scan_type", ["full", "incremental"])
def test_the_next_scan_runs_after_a_raising_one(monkeypatch, caplog, scan_type):
    monkeypatch.setattr(pipeline, "_run_scan", _locked)
    with pytest.raises(RuntimeError):
        RUNNERS[scan_type](AppConfig())

    ran: list[bool] = []

    def ok(c, incremental):
        ran.append(incremental)
        pipeline.progress.finish()

    monkeypatch.setattr(pipeline, "_run_scan", ok)
    with caplog.at_level(logging.WARNING, logger=pipeline.log.name):
        pipeline.run_incremental_scan(AppConfig())
    assert ran == [True]
    assert "Scan already in progress" not in caplog.text
    assert pipeline.progress.running is False


@pytest.mark.usefixtures("scan_env")
def test_a_real_scan_whose_first_commit_fails_releases_the_lock(monkeypatch, finishes):
    """The narrow case the note names: ``start_scan_run()`` on a locked SQLite."""

    def locked(session, scan_type):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(pipeline, "start_scan_run", locked)
    monkeypatch.setattr(pipeline, "_clients_from_config", lambda c: (FakeJellyfin([]), [], []))
    with pytest.raises(sqlite3.OperationalError):
        pipeline.run_incremental_scan(AppConfig())
    assert pipeline.progress.running is False
    assert finishes == ["database is locked"]
    assert value("xenotag_scan_running") == 0


@pytest.mark.usefixtures("scan_env")
def test_a_clean_scan_finishes_exactly_once(monkeypatch, finishes):
    monkeypatch.setattr(pipeline, "_clients_from_config", lambda c: (FakeJellyfin([]), [], []))
    pipeline.run_incremental_scan(AppConfig())
    assert finishes == [None]
    assert pipeline.progress.running is False


def test_a_scan_that_finished_itself_is_not_finished_again(monkeypatch, finishes):
    """``_run_scan()``'s own failure exit already called ``finish()``; nothing overwrites it."""

    def finished_then_raised(c, incremental):
        pipeline.progress.finish(error="own exit")
        raise RuntimeError("after finish")

    monkeypatch.setattr(pipeline, "_run_scan", finished_then_raised)
    with pytest.raises(RuntimeError, match="after finish"):
        pipeline.run_full_scan(AppConfig())
    assert finishes == ["own exit"]
    assert pipeline.progress.error == "own exit"
    assert pipeline.progress.running is False
