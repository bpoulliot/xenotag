"""Prometheus metrics (roadmap I5): what a scrape needs to alert on xenotag.

Scraped at ``GET /metrics`` over the Docker network; the host's Prometheus
turns these into three alerts (no successful scan in 26 h, the last scan
failed, \\*arr writes halted). A halt is the reason this is a scrape and not a
push: it is a *state* that persists until a later scan clears it, and a scrape
keeps seeing it however the one notification it would have produced went.

**One process, one plain registry.** xenotag runs a single uvicorn worker, so
this is a ``CollectorRegistry`` in memory. It must never run in
``prometheus_client``'s multiprocess mode: that mode writes one mmap'd file per
pid per metric type and merges every file on every scrape, and on this host an
unpruned multiprocess directory grew to 69,120 files and took the machine to a
host-wide OOM (2026-09-18). The library picks its value class from
``PROMETHEUS_MULTIPROC_DIR`` when it is imported, so the variable is removed
before the import below -- an inherited environment cannot switch it on.

**Restarts.** In-memory values reset when the container restarts, so the two
states an alert depends on are seeded at startup from what is on disk
(:func:`seed`): the last completed scan from ``state.db``'s ``scan_runs`` and
the halt from the stored \\*arr sync report. Without that, every deploy would
read as "never scanned". A failure is not persisted anywhere, so a restart
forgets one; the 26-hour rule still catches a scan that keeps failing.

Metrics (all ``xenotag_*``):

==============================================  =======  ==========================
name                                            type     labels
==============================================  =======  ==========================
build_info                                      gauge    version
scan_running                                    gauge
scans_total                                     counter  scan_type, outcome
scan_last_success_timestamp_seconds             gauge
scan_last_failure_timestamp_seconds             gauge
scan_last_duration_seconds                      gauge
scan_last_items_scanned                         gauge
scan_last_items_tagged                          gauge
scan_last_images_modified                       gauge
scan_errors_total                               counter  error_type
arr_tag_writes_total                            counter  arr_instance, result
arr_halts_total                                 counter
arr_sync_halted                                 gauge
arr_last_halt_timestamp_seconds                 gauge
tag_drift_total                                 counter
tag_writeback_mismatch_total                    counter  result
reconcile_halted                                gauge
reconcile_candidates                            gauge
reconcile_writes_total                          counter  result
==============================================  =======  ==========================

``outcome`` is ``success`` / ``failed`` / ``cancelled``. ``error_type`` is one of
the four shapes ``scan_errors.error_type`` stores -- ``no_path``, ``no_file``,
``probe_failed``, ``process_error`` (the exception text after the colon is
dropped: a label is never free text) -- or ``other``. ``result`` is
``written`` / ``error`` / ``readback_mismatch``, counted only for live writes.
The label is ``arr_instance``, not ``instance``, which Prometheus owns.
``tag_drift_total`` is cumulative for the process (roadmap U9): items whose
Jellyfin ``xt-`` tags differed from what xenotag last wrote, counted when a scan
or webhook reached them, before the write that replaced them.
``tag_writeback_mismatch_total`` counts Jellyfin tag writes whose read-back did
not show the managed tags written (roadmap B17), by ``result``: ``fixed`` (the
one retry stuck) or ``unresolved`` (still wrong; the row records what was read).

The ``reconcile_*`` metrics are the tag reconciliation pass's (roadmap B12(b)).
``reconcile_halted`` is 1 after a *scheduled* pass found more items to re-write
than ``scan.reconcile_write_threshold`` and wrote none of them, and 0 after a
pass that was not halted (a manual one included); a cancelled pass leaves it.
It is the alert: the host's ``XenotagReconcileHalted`` rule fires on it, and the
count and a sample are in the WARNING line and ``reconcile-report.json``.
``reconcile_candidates`` is the last pass's count of items missing a managed
tag. ``reconcile_writes_total`` counts its writes by ``result``: ``written``
(read back as written), ``readback_fixed`` (stuck on the one retry),
``unresolved`` (still wrong after it) or ``error`` (the write, its re-read or
its read-back failed). Both gauges are seeded at startup from the stored report.
A pass is not a scan: it never moves ``scans_total`` or the ``scan_last_*``
gauges, and its ``scan_runs`` rows (``scan_type="reconcile"``) are not what
:func:`seed_from_disk` reads as the last scan.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime

for _var in ("PROMETHEUS_MULTIPROC_DIR", "prometheus_multiproc_dir"):
    if os.environ.pop(_var, None) is not None:
        logging.getLogger(__name__).warning(
            "%s is set; ignoring it -- xenotag's metrics are single-process by design", _var
        )

# Imported late on purpose (E402): the environment above must be cleaned first.
from prometheus_client import (  # noqa: E402
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    disable_created_metrics,
    gc_collector,
    generate_latest,
    platform_collector,
    process_collector,
)

log = logging.getLogger(__name__)

SUCCESS = "success"
FAILED = "failed"
CANCELLED = "cancelled"

ERROR_TYPES = ("no_path", "no_file", "probe_failed", "process_error")

WRITTEN = "written"
WRITE_ERROR = "error"
READBACK_MISMATCH = "readback_mismatch"

MISMATCH_FIXED = "fixed"
MISMATCH_UNRESOLVED = "unresolved"

RECONCILE_WRITTEN = "written"
RECONCILE_FIXED = "readback_fixed"
RECONCILE_UNRESOLVED = "unresolved"
RECONCILE_ERROR = "error"
RECONCILE_RESULTS = (RECONCILE_WRITTEN, RECONCILE_FIXED, RECONCILE_UNRESOLVED, RECONCILE_ERROR)

# The `*_created` twin of every counter is noise for a scrape-and-alert setup.
disable_created_metrics()

REGISTRY = CollectorRegistry()
process_collector.ProcessCollector(registry=REGISTRY)
platform_collector.PlatformCollector(registry=REGISTRY)
gc_collector.GCCollector(registry=REGISTRY)

build_info = Gauge("xenotag_build_info", "Always 1; the running version as a label.", ["version"], registry=REGISTRY)
scan_running = Gauge("xenotag_scan_running", "1 while a scan holds the scan lock.", registry=REGISTRY)
scans_total = Counter(
    "xenotag_scans", "Scans finished, by type and outcome.", ["scan_type", "outcome"], registry=REGISTRY
)
last_success = Gauge(
    "xenotag_scan_last_success_timestamp_seconds",
    "Unix time the last scan completed successfully (seeded from state.db at startup).",
    registry=REGISTRY,
)
last_failure = Gauge(
    "xenotag_scan_last_failure_timestamp_seconds",
    "Unix time the last scan failed, in this process; 0 if none has.",
    registry=REGISTRY,
)
last_duration = Gauge("xenotag_scan_last_duration_seconds", "Wall time of the last successful scan.", registry=REGISTRY)
last_scanned = Gauge(
    "xenotag_scan_last_items_scanned", "Items the last successful scan listed from Jellyfin.", registry=REGISTRY
)
last_tagged = Gauge("xenotag_scan_last_items_tagged", "Items the last successful scan tagged.", registry=REGISTRY)
last_images = Gauge(
    "xenotag_scan_last_images_modified", "Posters the last successful scan re-rendered.", registry=REGISTRY
)
scan_errors = Counter(
    "xenotag_scan_errors", "Per-item scan errors recorded, by error type.", ["error_type"], registry=REGISTRY
)
arr_writes = Counter(
    "xenotag_arr_tag_writes",
    "Live Sonarr/Radarr tag writes, by instance and result.",
    ["arr_instance", "result"],
    registry=REGISTRY,
)
arr_halts = Counter("xenotag_arr_halts", "Times live *arr tag writes halted.", registry=REGISTRY)
arr_halted = Gauge(
    "xenotag_arr_sync_halted",
    "1 from a live *arr halt until a later live scan finishes its *arr sync without one.",
    registry=REGISTRY,
)
arr_last_halt = Gauge(
    "xenotag_arr_last_halt_timestamp_seconds", "Unix time of the last *arr halt; 0 if none.", registry=REGISTRY
)
tag_drift_total = Counter(
    "xenotag_tag_drift",
    "Items whose Jellyfin managed tags differed from what xenotag last wrote, seen before a write.",
    registry=REGISTRY,
)
tag_writeback_mismatch_total = Counter(
    "xenotag_tag_writeback_mismatch",
    "Jellyfin tag writes whose read-back differed, by whether the one retry fixed it.",
    ["result"],
    registry=REGISTRY,
)
reconcile_halted = Gauge(
    "xenotag_reconcile_halted",
    "1 after a scheduled tag reconciliation halted at its write threshold, writing nothing; 0 after one that did not.",
    registry=REGISTRY,
)
reconcile_candidates = Gauge(
    "xenotag_reconcile_candidates",
    "Items the last tag reconciliation pass found missing a managed Jellyfin tag.",
    registry=REGISTRY,
)
reconcile_writes = Counter(
    "xenotag_reconcile_writes",
    "Jellyfin tag writes by the reconciliation pass, by read-back result.",
    ["result"],
    registry=REGISTRY,
)


# Every known label set exists from the first scrape, at 0: a series that only
# appears on its first increment makes ``increase()`` miss that increment.
for _type in ("full", "incremental"):
    for _outcome in (SUCCESS, FAILED, CANCELLED):
        scans_total.labels(scan_type=_type, outcome=_outcome)
for _error in (*ERROR_TYPES, "other"):
    scan_errors.labels(error_type=_error)
for _result in (MISMATCH_FIXED, MISMATCH_UNRESOLVED):
    tag_writeback_mismatch_total.labels(result=_result)
for _result in RECONCILE_RESULTS:
    reconcile_writes.labels(result=_result)


def _scan_running() -> float:
    from .pipeline import progress  # the pipeline imports this module

    return 1.0 if progress.running else 0.0


scan_running.set_function(_scan_running)


def set_version(version: str) -> None:
    build_info.labels(version=version).set(1)


def error_type_label(error_type: str) -> str:
    """``process_error: <text>`` -> ``process_error``; anything unexpected -> ``other``."""
    shape = (error_type or "").split(":", 1)[0].strip()
    return shape if shape in ERROR_TYPES else "other"


def scan_error(error_type: str) -> None:
    scan_errors.labels(error_type=error_type_label(error_type)).inc()


def scan_finished(
    scan_type: str,
    outcome: str,
    *,
    started: float | None = None,
    scanned: int = 0,
    tagged: int = 0,
    images: int = 0,
    now: float | None = None,
) -> None:
    """Record one scan's end. Only a success moves the ``scan_last_*`` gauges."""
    now = time.time() if now is None else now
    scans_total.labels(scan_type=scan_type, outcome=outcome).inc()
    if outcome == FAILED:
        last_failure.set(now)
    elif outcome == SUCCESS:
        last_success.set(now)
        last_scanned.set(scanned)
        last_tagged.set(tagged)
        last_images.set(images)
        if started is not None:
            last_duration.set(max(0.0, now - started))


def arr_instances(labels: list[str]) -> None:
    """A live sync's instances, so their write counters exist at 0 before the first write."""
    for label in labels:
        for result in (WRITTEN, WRITE_ERROR, READBACK_MISMATCH):
            arr_writes.labels(arr_instance=label, result=result)


def arr_write(arr_instance: str, result: str) -> None:
    arr_writes.labels(arr_instance=arr_instance, result=result).inc()


def tag_drift() -> None:
    tag_drift_total.inc()


def tag_writeback_mismatch(result: str) -> None:
    tag_writeback_mismatch_total.labels(result=result).inc()


def reconcile_finished(candidates: int, *, halted: bool, writes: dict[str, int], cancelled: bool = False) -> None:
    """End of a reconciliation pass (roadmap B12(b)). A cancelled pass moves only the write counters."""
    for result, n in writes.items():
        if n:
            reconcile_writes.labels(result=result).inc(n)
    if cancelled:
        return
    reconcile_candidates.set(candidates)
    reconcile_halted.set(1 if halted else 0)


def seed_reconcile(report: dict | None) -> None:
    """Restore the reconciliation gauges from its stored report after a restart."""
    if not report or report.get("outcome") == "cancelled":
        return
    reconcile_halted.set(1 if report.get("halted") else 0)
    reconcile_candidates.set(report.get("candidates") or 0)


def arr_halt(now: float | None = None) -> None:
    arr_halts.inc()
    arr_halted.set(1)
    arr_last_halt.set(time.time() if now is None else now)


def arr_sync_finished(live: bool, halted: bool) -> None:
    """End of a scan's *arr sync: a live one that did not halt clears the halt."""
    if live and not halted:
        arr_halted.set(0)


def _epoch(value: object) -> float | None:
    """A stored timestamp as Unix time. Naive datetimes are UTC (state.py stores them so)."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.timestamp()


def seed(last_run: object | None, report: dict | None) -> None:
    """Restore what an alert depends on after a restart.

    ``last_run`` is the newest ``ScanRun`` with a ``completed_at`` (the table
    cannot tell a cancelled scan from a successful one, so it counts as one).
    ``report`` is the stored *arr sync report; only a live one says anything
    about a halt -- a dry run neither writes nor halts.
    """
    if last_run is not None:
        completed = _epoch(getattr(last_run, "completed_at", None))
        if completed is not None:
            last_success.set(completed)
            last_scanned.set(getattr(last_run, "items_scanned", 0) or 0)
            last_tagged.set(getattr(last_run, "items_tagged", 0) or 0)
            last_images.set(getattr(last_run, "items_image_modified", 0) or 0)
            started = _epoch(getattr(last_run, "started_at", None))
            if started is not None:
                last_duration.set(max(0.0, completed - started))
    if report and report.get("mode") == "live" and report.get("halted"):
        arr_halted.set(1)
        when = _epoch(report.get("generated_at"))
        if when is not None:
            arr_last_halt.set(when)


def seed_from_disk() -> None:
    """Startup: :func:`seed` from ``state.db`` and the stored report. Never raises."""
    from .arr_sync import load_report
    from .state import ScanRun, get_session, scans_only

    try:
        session = get_session()
        try:
            last_run = (
                scans_only(session.query(ScanRun))
                .filter(ScanRun.completed_at.isnot(None))
                .order_by(ScanRun.completed_at.desc())
                .first()
            )
        finally:
            session.close()
        seed(last_run, load_report())
    except Exception as exc:  # metrics must never stop the app booting
        log.warning("Could not seed metrics from disk: %s", exc)
    try:
        from .reconcile import load_report as load_reconcile_report

        seed_reconcile(load_reconcile_report())
    except Exception as exc:
        log.warning("Could not seed the reconciliation metrics from disk: %s", exc)


def render() -> tuple[bytes, str]:
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


class _MetricsAccessFilter(logging.Filter):
    """Drop uvicorn's access line for a good scrape: one a minute would drown the log.

    uvicorn logs ``(client, method, path, http_version, status)``; a scrape that
    did not answer 200 is still logged.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        return not (isinstance(args, tuple) and len(args) == 5 and args[2] == "/metrics" and args[4] == 200)


def quiet_access_log() -> None:
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, _MetricsAccessFilter) for f in access.filters):
        access.addFilter(_MetricsAccessFilter())
