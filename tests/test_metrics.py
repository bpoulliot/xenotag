"""Roadmap I5: the Prometheus endpoint, and the three states the host's alerts read.

The alerts (in the host's monitoring config, not this repo) are: no successful
scan in 26 h, the last scan failed, *arr writes halted. So the tests that
matter drive a real ``_run_scan`` / ``ArrTagSync`` and read the registry back:
a success moves only the success gauge, a failure only the failure gauge, a
halt sets the halted gauge and only a clean LIVE scan clears it. Each has a
partner that must NOT move the same metric, so an implementation that set
everything (or nothing) fails here.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import arr_sync, metrics, pipeline, state
from app.arr_sync import ArrTagSync
from app.config import AppConfig
from app.state import Base, ScanError, ScanRun
from tests.test_arr_sync import FakeArr, FakeJellyfin, _info, cfg, client_for, jf_series, make_sync, series

REPO = Path(__file__).resolve().parent.parent

# The documented list (app/metrics.py's docstring). A new metric fails this
# until it is named here -- and in the PR -- on purpose.
XENOTAG_FAMILIES = {
    "xenotag_build_info",
    "xenotag_scan_running",
    "xenotag_scans",
    "xenotag_scan_last_success_timestamp_seconds",
    "xenotag_scan_last_failure_timestamp_seconds",
    "xenotag_scan_last_duration_seconds",
    "xenotag_scan_last_items_scanned",
    "xenotag_scan_last_items_tagged",
    "xenotag_scan_last_images_modified",
    "xenotag_scan_errors",
    "xenotag_arr_tag_writes",
    "xenotag_arr_halts",
    "xenotag_arr_sync_halted",
    "xenotag_arr_last_halt_timestamp_seconds",
}


def value(name: str, **labels: str) -> float:
    return metrics.REGISTRY.get_sample_value(name, labels) or 0.0


@pytest.fixture(autouse=True)
def _reset_gauges():
    """Gauges are process-wide; every test starts from the unscanned, unhalted state."""
    for g in (
        metrics.last_success,
        metrics.last_failure,
        metrics.last_duration,
        metrics.last_scanned,
        metrics.last_tagged,
        metrics.last_images,
        metrics.arr_halted,
        metrics.arr_last_halt,
    ):
        g.set(0)
    yield
    pipeline.progress.running = False
    pipeline.progress.cancelled = False


# ── the endpoint ────────────────────────────────────────────────────────────
@pytest.fixture
def client(monkeypatch):
    from app.main import app
    from app.web import routes

    monkeypatch.setattr(routes, "get_config", lambda: AppConfig())
    return TestClient(app)


def test_metrics_is_served_without_a_session(client):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    # Everything but the *arr write counters, whose labels come from the live config.
    for family in XENOTAG_FAMILIES - {"xenotag_arr_tag_writes"}:
        assert f"# TYPE {family} gauge" in resp.text or f"# TYPE {family}_total counter" in resp.text, family
    assert "_created" not in resp.text
    assert 'xenotag_scans_total{outcome="failed",scan_type="incremental"} ' in resp.text
    assert 'xenotag_scan_errors_total{error_type="process_error"} ' in resp.text
    assert 'xenotag_build_info{version="' in resp.text
    # The partner: this client really has no session -- a protected route refuses it.
    assert client.get("/api/settings").status_code == 401


def test_metrics_carries_the_security_headers(client):
    assert client.get("/metrics").headers["X-Content-Type-Options"] == "nosniff"


def test_every_app_metric_is_xenotag_prefixed_and_the_set_is_the_documented_one():
    names = {m.name for m in metrics.REGISTRY.collect()}
    ours = {n for n in names if not n.startswith(("process_", "python_"))}
    assert ours == XENOTAG_FAMILIES
    assert not any("metafin" in n for n in names)


def test_no_label_is_named_instance():
    """Prometheus owns `instance`; a metric label of that name would be renamed on scrape."""
    for family in metrics.REGISTRY.collect():
        for sample in family.samples:
            assert "instance" not in sample.labels, family.name


@pytest.mark.parametrize(
    "stored, label",
    [
        ("no_path", "no_path"),
        ("no_file", "no_file"),
        ("probe_failed", "probe_failed"),
        ("process_error: OSError: [Errno 5] Input/output error", "process_error"),
        ("process_error", "process_error"),
        ("something new", "other"),
        ("", "other"),
    ],
)
def test_error_type_label_is_one_of_the_four_shapes(stored, label):
    assert metrics.error_type_label(stored) == label


# ── a scan, end to end ──────────────────────────────────────────────────────
@pytest.fixture
def scan_env(tmp_path, monkeypatch):
    """An in-memory index, no deleted-items pass, the *arr report in tmp_path."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine)
    monkeypatch.setattr(pipeline, "get_session", lambda: maker())
    monkeypatch.setattr(pipeline, "run_deleted_items", lambda c, **kw: None)
    monkeypatch.setenv("ARR_SYNC_REPORT", str(tmp_path / "arr-sync-report.json"))
    monkeypatch.setattr(arr_sync, "_last_report", None)
    return maker


def _scan(monkeypatch, jf, sonarrs=(), config=None, incremental=True):
    monkeypatch.setattr(pipeline, "_clients_from_config", lambda c: (jf, list(sonarrs), []))
    assert pipeline.progress.try_start()
    pipeline._run_scan(config or AppConfig(), incremental=incremental)


def _movie(item_id: str, path: str) -> dict:
    return {"Id": item_id, "Name": item_id, "Type": "Movie", "Path": path, "MediaSources": [{"Path": path}]}


def test_a_scan_records_success_counts_and_every_error_shape(tmp_path, monkeypatch, scan_env):
    for name in ("good", "unprobeable", "boom"):
        (tmp_path / f"{name}.mkv").write_bytes(b"")
    items = [
        {"Id": "nopath", "Name": "nopath", "Type": "Movie"},
        _movie("gone", str(tmp_path / "gone.mkv")),
        _movie("unprobeable", str(tmp_path / "unprobeable.mkv")),
        _movie("boom", str(tmp_path / "boom.mkv")),
        _movie("good", str(tmp_path / "good.mkv")),
    ]
    monkeypatch.setattr(pipeline, "probe_file", lambda p: None if "unprobeable" in str(p) else _info())

    def process(jf, arr, session, c, item, *rest):
        if item["Id"] == "boom":
            raise RuntimeError("disk on fire")
        return True

    monkeypatch.setattr(pipeline, "_process_one_item", process)
    before = {t: value("xenotag_scan_errors_total", error_type=t) for t in metrics.ERROR_TYPES}
    ok_before = value("xenotag_scans_total", scan_type="incremental", outcome="success")
    t0 = time.time()

    _scan(monkeypatch, FakeJellyfin(items))

    assert value("xenotag_scan_last_success_timestamp_seconds") >= t0
    assert value("xenotag_scan_last_failure_timestamp_seconds") == 0
    assert value("xenotag_scans_total", scan_type="incremental", outcome="success") == ok_before + 1
    assert value("xenotag_scan_last_items_scanned") == 5
    assert value("xenotag_scan_last_items_tagged") == 1
    assert value("xenotag_scan_last_images_modified") == 1
    assert value("xenotag_scan_last_duration_seconds") >= 0
    for t in metrics.ERROR_TYPES:
        assert value("xenotag_scan_errors_total", error_type=t) == before[t] + 1, t
    # The index still records the full text; only the label is collapsed.
    s = scan_env()
    assert {e.error_type for e in s.query(ScanError)} == {
        "no_path",
        "no_file",
        "probe_failed",
        "process_error: disk on fire",
    }
    s.close()


class _BrokenJellyfin(FakeJellyfin):
    def get_items(self, library_ids=None):
        raise ConnectionError("jellyfin is down")


def test_a_scan_that_cannot_list_jellyfin_is_a_failure_not_a_success(monkeypatch, scan_env):
    failed_before = value("xenotag_scans_total", scan_type="incremental", outcome="failed")
    t0 = time.time()
    _scan(monkeypatch, _BrokenJellyfin())
    assert value("xenotag_scan_last_failure_timestamp_seconds") >= t0
    assert value("xenotag_scan_last_success_timestamp_seconds") == 0
    assert value("xenotag_scans_total", scan_type="incremental", outcome="failed") == failed_before + 1
    assert pipeline.progress.running is False


def test_an_exception_escaping_the_scan_is_recorded_and_still_raised(monkeypatch):
    def explode(c, incremental):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(pipeline, "_run_scan", explode)
    before = value("xenotag_scans_total", scan_type="full", outcome="failed")
    t0 = time.time()
    with pytest.raises(RuntimeError, match="unexpected"):
        pipeline.run_full_scan(AppConfig())
    assert value("xenotag_scan_last_failure_timestamp_seconds") >= t0
    assert value("xenotag_scans_total", scan_type="full", outcome="failed") == before + 1
    assert value("xenotag_scan_last_success_timestamp_seconds") == 0


def test_a_cancelled_scan_is_neither_a_success_nor_a_failure(monkeypatch, scan_env):
    before = value("xenotag_scans_total", scan_type="incremental", outcome="cancelled")
    monkeypatch.setattr(pipeline, "_clients_from_config", lambda c: (FakeJellyfin([]), [], []))
    assert pipeline.progress.try_start()
    pipeline.progress.cancelled = True
    pipeline._run_scan(AppConfig(), incremental=True)
    assert value("xenotag_scans_total", scan_type="incremental", outcome="cancelled") == before + 1
    assert value("xenotag_scan_last_success_timestamp_seconds") == 0
    assert value("xenotag_scan_last_failure_timestamp_seconds") == 0


def test_scan_running_follows_the_scan_lock():
    assert value("xenotag_scan_running") == 0
    assert pipeline.progress.try_start()
    assert value("xenotag_scan_running") == 1
    pipeline.progress.finish()
    assert value("xenotag_scan_running") == 0


# ── *arr writes and halts ───────────────────────────────────────────────────
def _live_item_sync(fake, tmp_path):
    sync = make_sync([fake], mode="live")
    item = jf_series("a", "A", str(tmp_path), Tvdb="1")
    sync.resolve_all([item])
    return sync, item


def test_a_live_sync_starts_its_instances_write_counters_at_zero(tmp_path):
    make_sync([FakeArr("sonarr", [])], [FakeArr("radarr", [])], mode="live")
    for label in ("sonarr/s0", "radarr/r0"):
        for result in ("written", "error", "readback_mismatch"):
            sample = metrics.REGISTRY.get_sample_value(
                "xenotag_arr_tag_writes_total", {"arr_instance": label, "result": result}
            )
            assert sample is not None, (label, result)
    # ... and a dry run creates none.
    ArrTagSync(cfg("dry_run"), [client_for(FakeArr("sonarr", []), "dryonly", read_only=True)], [])
    names = {s.labels.get("arr_instance") for f in metrics.REGISTRY.collect() for s in f.samples}
    assert "sonarr/dryonly" not in names


def test_a_clean_live_write_is_counted_and_does_not_halt(tmp_path):
    fake = FakeArr("sonarr", [series(1, "A", 1, str(tmp_path))])
    sync, item = _live_item_sync(fake, tmp_path)
    written = value("xenotag_arr_tag_writes_total", arr_instance="sonarr/s0", result="written")
    halts = value("xenotag_arr_halts_total")
    sync.sync_item(item, {"sonarr": ["xt-1080p"]})
    assert value("xenotag_arr_tag_writes_total", arr_instance="sonarr/s0", result="written") == written + 1
    assert value("xenotag_arr_halts_total") == halts
    assert value("xenotag_arr_sync_halted") == 0


def test_a_readback_mismatch_sets_the_halt(tmp_path):
    fake = FakeArr("sonarr", [series(1, "A", 1, str(tmp_path))])
    fake.on_edit = lambda obj: obj.update(qualityProfileId=99)
    sync, item = _live_item_sync(fake, tmp_path)
    mismatches = value("xenotag_arr_tag_writes_total", arr_instance="sonarr/s0", result="readback_mismatch")
    halts = value("xenotag_arr_halts_total")
    t0 = time.time()
    sync.sync_item(item, {"sonarr": ["xt-1080p"]})
    assert sync.halted
    assert value("xenotag_arr_sync_halted") == 1
    assert value("xenotag_arr_halts_total") == halts + 1
    assert value("xenotag_arr_last_halt_timestamp_seconds") >= t0
    assert value("xenotag_arr_tag_writes_total", arr_instance="sonarr/s0", result="readback_mismatch") == mismatches + 1


def test_write_errors_count_each_time_but_halt_once(tmp_path):
    fake = FakeArr("sonarr", [series(1, "A", 1, str(tmp_path / "A")), series(2, "B", 2, str(tmp_path / "B"))])
    sync = make_sync([fake], mode="live")
    a, b = jf_series("a", "A", str(tmp_path / "A"), Tvdb="1"), jf_series("b", "B", str(tmp_path / "B"), Tvdb="2")
    sync.resolve_all([a, b])
    fake.fail_status["POST"] = 503
    errors = value("xenotag_arr_tag_writes_total", arr_instance="sonarr/s0", result="error")
    halts = value("xenotag_arr_halts_total")
    sync.sync_item(a, {"sonarr": ["xt-1080p"]})
    sync.sync_item(b, {"sonarr": ["xt-720p"]})  # halted already: skipped, not an error
    assert value("xenotag_arr_tag_writes_total", arr_instance="sonarr/s0", result="error") == errors + 1
    assert value("xenotag_arr_halts_total") == halts + 1


def test_a_dry_run_error_is_not_a_live_write_error(tmp_path):
    fake = FakeArr("sonarr", [series(1, "A", 1, str(tmp_path))])
    sync = make_sync([fake])
    item = jf_series("a", "A", str(tmp_path), Tvdb="1")
    sync.resolve_all([item])
    errors = value("xenotag_arr_tag_writes_total", arr_instance="sonarr/s0", result="error")
    sync.clients["sonarr"][0]._objects = {}  # the dry run plans from the cache; break it
    sync.sync_item(item, {"sonarr": ["xt-1080p"]})
    assert sync.stats["sonarr/s0"].write_errors == 1
    assert value("xenotag_arr_tag_writes_total", arr_instance="sonarr/s0", result="error") == errors
    assert value("xenotag_arr_sync_halted") == 0


def _scan_one_live_item(tmp_path, monkeypatch, fake, mode):
    (tmp_path / "A").mkdir(exist_ok=True)
    (tmp_path / "A" / "e.mkv").write_bytes(b"")
    item = jf_series("a", "A", str(tmp_path / "A"), Tvdb="1")
    # FakeJellyfin has no episode lookup, so the scan falls back to this path.
    item["MediaSources"] = [{"Path": str(tmp_path / "A" / "e.mkv")}]
    monkeypatch.setattr(pipeline, "probe_file", lambda p: _info())
    sonarr = client_for(fake, "s0", read_only=(mode != "live"))
    _scan(monkeypatch, FakeJellyfin([item]), [sonarr], config=cfg(mode), incremental=False)


def test_only_a_clean_live_scan_clears_the_halt(tmp_path, monkeypatch, scan_env):
    # A live scan that halts: the gauge goes up and the scan still completes.
    fake = FakeArr("sonarr", [series(1, "A", 1, str(tmp_path / "A"))])
    fake.on_edit = lambda obj: obj.update(qualityProfileId=99)
    _scan_one_live_item(tmp_path, monkeypatch, fake, "live")
    assert value("xenotag_arr_sync_halted") == 1
    assert value("xenotag_scan_last_success_timestamp_seconds") > 0
    assert json.loads(Path(os.environ["ARR_SYNC_REPORT"]).read_text())["halted"]

    # A dry-run scan writes nothing, so it proves nothing: still halted.
    _scan_one_live_item(tmp_path, monkeypatch, FakeArr("sonarr", [series(1, "A", 1, str(tmp_path / "A"))]), "dry_run")
    assert value("xenotag_arr_sync_halted") == 1

    # A live scan that writes cleanly clears it.
    _scan_one_live_item(tmp_path, monkeypatch, FakeArr("sonarr", [series(1, "A", 1, str(tmp_path / "A"))]), "live")
    assert value("xenotag_arr_sync_halted") == 0


def test_a_cancelled_live_scan_does_not_clear_the_halt(monkeypatch, scan_env):
    metrics.arr_halted.set(1)
    sonarr = client_for(FakeArr("sonarr", []), "s0")
    monkeypatch.setattr(pipeline, "_clients_from_config", lambda c: (FakeJellyfin([]), [sonarr], []))
    assert pipeline.progress.try_start()
    pipeline.progress.cancelled = True
    pipeline._run_scan(cfg("live"), incremental=True)
    assert value("xenotag_arr_sync_halted") == 1


# ── surviving a restart ─────────────────────────────────────────────────────
def _state_db(tmp_path, runs):
    db = tmp_path / "state.db"
    engine = create_engine(f"sqlite:///{db}")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    for started, completed, scanned, tagged in runs:
        s.add(ScanRun(started_at=started, completed_at=completed, items_scanned=scanned, items_tagged=tagged))
    s.commit()
    s.close()
    return engine


def test_seed_restores_the_last_completed_scan_as_utc(tmp_path, monkeypatch):
    engine = _state_db(
        tmp_path,
        [
            (datetime(2026, 9, 25, 9, 0), datetime(2026, 9, 25, 9, 5), 10, 1),
            (datetime(2026, 9, 27, 9, 0), datetime(2026, 9, 27, 9, 30), 9399, 42),
            (datetime(2026, 9, 27, 12, 0), None, 0, 0),  # started, never finished: not a success
        ],
    )
    monkeypatch.setattr(state, "get_session", lambda: sessionmaker(bind=engine)())
    monkeypatch.setattr(arr_sync, "_last_report", {"mode": "live", "halted": None})
    metrics.seed_from_disk()
    # 2026-09-27 09:30 naive = UTC (state.py stores naive UTC), whatever the host's TZ.
    assert value("xenotag_scan_last_success_timestamp_seconds") == 1_790_501_400
    assert value("xenotag_scan_last_items_scanned") == 9399
    assert value("xenotag_scan_last_items_tagged") == 42
    assert value("xenotag_scan_last_duration_seconds") == 1800
    assert value("xenotag_arr_sync_halted") == 0


@pytest.mark.parametrize(
    "report, halted",
    [
        ({"mode": "live", "halted": "sonarr/s0: read-back mismatch", "generated_at": "2026-09-27T09:31:00+00:00"}, 1),
        ({"mode": "live", "halted": None, "generated_at": "2026-09-27T09:31:00+00:00"}, 0),
        ({"mode": "dry_run", "halted": "impossible", "generated_at": "2026-09-27T09:31:00+00:00"}, 0),
        (None, 0),
    ],
)
def test_seed_restores_a_live_halt_only(report, halted):
    metrics.seed(None, report)
    assert value("xenotag_arr_sync_halted") == halted
    assert value("xenotag_arr_last_halt_timestamp_seconds") == (1_790_501_460 if halted else 0)


def test_seed_from_disk_never_raises(monkeypatch, caplog):
    def broken():
        raise OSError("no database")

    monkeypatch.setattr(state, "get_session", broken)
    with caplog.at_level(logging.WARNING, logger="app.metrics"):
        metrics.seed_from_disk()
    assert "Could not seed metrics" in caplog.text
    assert value("xenotag_scan_last_success_timestamp_seconds") == 0


# ── never multiprocess mode ─────────────────────────────────────────────────
_CHILD = """
import os, sys
sys.path.insert(0, {repo!r})
if {use_app}:
    from app import metrics
    metrics.scan_error("no_file")
    metrics.render()
    print("env", os.environ.get("PROMETHEUS_MULTIPROC_DIR"))
else:
    from prometheus_client import Counter
    Counter("control", "control").inc()
"""


@pytest.mark.parametrize("variable", ["PROMETHEUS_MULTIPROC_DIR", "prometheus_multiproc_dir"])
def test_an_inherited_multiproc_dir_is_ignored(tmp_path, variable):
    """Both directions: the library DOES write files there on its own; through app.metrics it does not."""
    env = {**os.environ, variable: str(tmp_path)}
    env.pop("PROMETHEUS_MULTIPROC_DIR" if variable != "PROMETHEUS_MULTIPROC_DIR" else "prometheus_multiproc_dir", None)

    control = tmp_path / "control"
    control.mkdir()
    subprocess.run(
        [sys.executable, "-c", _CHILD.format(repo=str(REPO), use_app=False)],
        env={**env, variable: str(control)},
        check=True,
        capture_output=True,
    )
    assert list(control.iterdir()), "control: prometheus_client should have written multiprocess files"

    guarded = tmp_path / "guarded"
    guarded.mkdir()
    out = subprocess.run(
        [sys.executable, "-c", _CHILD.format(repo=str(REPO), use_app=True)],
        env={**env, variable: str(guarded)},
        check=True,
        capture_output=True,
        text=True,
    )
    assert list(guarded.iterdir()) == []
    assert "env None" in out.stdout


# ── the access log ──────────────────────────────────────────────────────────
def _access(path: str, status: int) -> logging.LogRecord:
    return logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        0,
        '%s - "%s %s HTTP/%s" %d',
        ("10.0.0.1:1", "GET", path, "1.1", status),
        None,
    )


def test_a_good_scrape_is_not_access_logged_but_everything_else_is():
    f = metrics._MetricsAccessFilter()
    assert f.filter(_access("/metrics", 200)) is False
    assert f.filter(_access("/metrics", 500)) is True
    assert f.filter(_access("/health", 200)) is True
    assert f.filter(_access("/metrics?x=1", 200)) is True


def test_quiet_access_log_installs_one_filter():
    access = logging.getLogger("uvicorn.access")
    metrics.quiet_access_log()
    metrics.quiet_access_log()
    assert sum(isinstance(f, metrics._MetricsAccessFilter) for f in access.filters) == 1
