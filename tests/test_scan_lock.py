"""Roadmap B22: an exception that escapes a scan must release the scan lock.

``run_full_scan()`` / ``run_incremental_scan()`` take ``progress.try_start()``;
before B22 only ``_run_scan()``'s own exits released it, so anything it raised
left ``progress.running`` True and every later scan logged ``Scan already in
progress, skipping`` until the container restarted. Each test raises out of a
first scan and requires a SECOND scan to actually run.
"""

from __future__ import annotations

import pytest

from app import pipeline
from app.config import AppConfig


@pytest.fixture(autouse=True)
def _released_lock():
    pipeline.progress.finish()
    yield
    pipeline.progress.finish()


@pytest.mark.parametrize("run", [pipeline.run_full_scan, pipeline.run_incremental_scan])
def test_a_scan_runs_after_one_that_raised(monkeypatch, run):
    calls: list[bool] = []

    def scan(cfg, incremental):
        calls.append(incremental)
        if len(calls) == 1:
            raise RuntimeError("database is locked")
        pipeline.progress.finish()

    monkeypatch.setattr(pipeline, "_run_scan", scan)
    with pytest.raises(RuntimeError, match="database is locked"):
        run(AppConfig())
    assert pipeline.progress.running is False
    assert pipeline.progress.error == "database is locked"

    run(AppConfig())
    assert len(calls) == 2


def test_a_scan_that_returns_normally_keeps_its_own_finish(monkeypatch):
    """The fix touches only the raising path: a clean exit is not finished twice over its error."""

    def scan(cfg, incremental):
        pipeline.progress.finish(error="its own error")

    monkeypatch.setattr(pipeline, "_run_scan", scan)
    pipeline.run_incremental_scan(AppConfig())
    assert pipeline.progress.running is False
    assert pipeline.progress.error == "its own error"
