from __future__ import annotations

import logging
import os

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

log = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None
_JOB_ID = "xenotag_incremental_scan"
# Roadmap B12(b): the tag reconciliation pass, on its own cron (scan.reconcile_schedule).
_RECONCILE_JOB_ID = "xenotag_tag_reconcile"


def _add(job_id: str, schedule: str, fn, what: str, verb: str) -> None:
    try:
        _scheduler.remove_job(job_id)
    except Exception:  # noqa: S110
        pass
    if schedule:
        _scheduler.add_job(
            fn,
            CronTrigger.from_crontab(schedule),
            id=job_id,
            replace_existing=True,
        )
        log.info("%s %s: %s", verb, what, schedule)


def start(schedule: str, scan_fn, reconcile_schedule: str = "", reconcile_fn=None) -> None:
    global _scheduler
    tz = os.environ.get("TZ", "UTC") or "UTC"
    _scheduler = BackgroundScheduler(timezone=tz)
    _add(_JOB_ID, schedule, scan_fn, "incremental scan", "Scheduled")
    if reconcile_fn is not None:
        _add(_RECONCILE_JOB_ID, reconcile_schedule, reconcile_fn, "tag reconciliation", "Scheduled")
    _scheduler.start()


def reschedule(schedule: str, scan_fn) -> None:
    if _scheduler is None:
        return
    _add(_JOB_ID, schedule, scan_fn, "incremental scan", "Rescheduled")


def reschedule_reconcile(schedule: str, reconcile_fn) -> None:
    """Replace the reconciliation job; an empty schedule leaves none (roadmap B12(b))."""
    if _scheduler is None:
        return
    _add(_RECONCILE_JOB_ID, schedule, reconcile_fn, "tag reconciliation", "Rescheduled")


def _next(job_id: str) -> str | None:
    if _scheduler is None:
        return None
    job = _scheduler.get_job(job_id)
    if job and job.next_run_time:
        return job.next_run_time.isoformat()
    return None


def next_run_time() -> str | None:
    return _next(_JOB_ID)


def next_reconcile_time() -> str | None:
    return _next(_RECONCILE_JOB_ID)


def stop() -> None:
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
