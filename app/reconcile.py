"""Roadmap B12(b): put back the managed Jellyfin tags Jellyfin lost.

The scan is mtime-driven, so it never returns to an unchanged file -- and on
2026-09-04 a replace-all metadata refresh left ~8,000 items with only their NFO
and provider tags, while an item Jellyfin re-creates loses its ``xt-`` tags the
same way (``docs/measurements/b12-tag-loss.md``). This pass reads every tracked
item by ``Ids=`` (B18: the recursive listing serves stale tags) and re-writes
the items missing one of the managed tags their row says xenotag wrote.

Decided by the operator (2026-10-09, three rounds), not engineering choices:

- **Jellyfin only.** No *arr client is ever built here.
- **Managed tags only**, through ``set_managed_tags()`` with B17's read-back. A
  tag is missing only if no current tag matches it regardless of case (B12(a)):
  the *arrs' NFO merge respells ``xt-AAC`` as ``xt-aac``, which is not a loss.
  Managed tags Jellyfin carries beyond the row's are left alone (U9 logs them).
- **A per-run write threshold** (``scan.reconcile_write_threshold``). When MORE
  items than that need a write, a *scheduled* run writes none of them, logs a
  WARNING with the count and a sample, records it, and sets
  ``xenotag_reconcile_halted`` -- a mass loss is an alarm, not a quiet repair.
  The *manual* rescan, started after the operator has looked, bypasses it.
- **Posters are out of scope.**

The write never uses the tags-only read: ``set_managed_tags()`` POSTs a whole
UpdateRequest (name, year, genres, studios, locks, provider ids...), and a body
built from a partial item would blank those fields. Each batch of candidates is
re-read with the scan's own ``Fields`` just before it is written.

The pass holds the scan lock (``progress``), so it never overlaps a scan, and
shows its progress through the same ``/scan/status`` stream.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import metrics
from .clients.jellyfin import TAG_READ_BATCH, JellyfinClient
from .config import AppConfig
from .pipeline import _passes_path_filter, _PendingRecord, _Readback, _record_after_readback, progress
from .state import RECONCILE_SCAN_TYPE, MediaState, current_db_path, finish_scan_run, get_session, start_scan_run

log = logging.getLogger(__name__)

SCAN_TYPE = RECONCILE_SCAN_TYPE  # scan_runs.scan_type
SCHEDULED = "scheduled"
MANUAL = "manual"
SAMPLE_SIZE = 20

_JF_KEY = "jellyfin:"


@dataclass(frozen=True)
class Candidate:
    """A tracked item missing at least one managed tag its row says xenotag wrote."""

    item_id: str  # Jellyfin's id, without the row's ``jellyfin:`` prefix
    name: str
    expected: tuple[str, ...]
    missing: tuple[str, ...]


@dataclass
class Plan:
    candidates: list[Candidate] = field(default_factory=list)
    checked: int = 0  # tracked items read back by Ids=
    not_returned: list[str] = field(default_factory=list)  # ids Ids= did not return (U2's business)


def expected_tags(tags_applied: str | None, prefix: str) -> list[str]:
    """The managed tags a row says xenotag last wrote; [] for no row data or bad JSON."""
    try:
        applied = json.loads(tags_applied) if tags_applied else []
    except ValueError:
        return []
    if not isinstance(applied, list):
        return []
    return list(dict.fromkeys(t for t in applied if isinstance(t, str) and t.startswith(prefix)))


def missing_tags(expected: list[str] | tuple[str, ...], current: list[str]) -> list[str]:
    """``expected`` tags no ``current`` tag matches once case is folded (roadmap B12(a))."""
    have = {t.casefold() for t in current if isinstance(t, str)}
    return [t for t in expected if t.casefold() not in have]


def plan(tracked: list[tuple[str, list[str]]], current: dict[str, dict]) -> Plan:
    """Which tracked items need a write. Pure: no I/O.

    ``tracked`` is ``(Jellyfin id, expected managed tags)`` per row; ``current``
    is ``{id: item}`` from the ``Ids=`` read. A row whose id was not returned is
    skipped and counted -- an item Jellyfin no longer has is U2's to remove.
    """
    result = Plan()
    for item_id, expected in tracked:
        item = current.get(item_id)
        if item is None:
            result.not_returned.append(item_id)
            continue
        result.checked += 1
        missing = missing_tags(expected, item.get("Tags") or [])
        if missing:
            result.candidates.append(Candidate(item_id, item.get("Name") or item_id, tuple(expected), tuple(missing)))
    return result


def sample(candidates: list[Candidate], size: int = SAMPLE_SIZE) -> list[dict]:
    return [{"item_id": c.item_id, "name": c.name, "missing": list(c.missing)} for c in candidates[:size]]


def _tracked(session: object, cfg: AppConfig) -> list[tuple[str, list[str]]]:
    """Every Jellyfin row with at least one managed tag recorded, inside ``scan.path_filters``."""
    prefix = cfg.tags.managed_prefix
    rows = session.query(MediaState.item_id, MediaState.file_path, MediaState.tags_applied).filter(
        MediaState.source == "jellyfin"
    )
    tracked: list[tuple[str, list[str]]] = []
    for item_id, file_path, tags_applied in rows:
        if not item_id or not item_id.startswith(_JF_KEY):
            continue
        if cfg.scan.path_filters and not _passes_path_filter(file_path or "", cfg.scan.path_filters):
            continue
        expected = expected_tags(tags_applied, prefix)
        if expected:
            tracked.append((item_id[len(_JF_KEY) :], expected))
    return tracked


# ---------------------------------------------------------------------------
# The report: one JSON file beside state.db, like the *arr sync report.
# ---------------------------------------------------------------------------


def report_path() -> Path:
    explicit = os.environ.get("RECONCILE_REPORT")
    if explicit:
        return Path(explicit)
    return Path(current_db_path()).with_name("reconcile-report.json")


def store_report(report: dict) -> None:
    path = report_path()
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(report, indent=2, default=str))
        tmp.replace(path)
    except OSError as exc:
        log.warning("Could not write the reconciliation report to %s: %s", path, exc)


def load_report() -> dict | None:
    try:
        return json.loads(report_path().read_text())
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# The pass
# ---------------------------------------------------------------------------


@dataclass
class _Writes:
    readback: _Readback = field(default_factory=_Readback)
    errors: int = 0  # write raised, or the full re-read of its batch failed
    gone: int = 0  # not returned by the full re-read
    already: int = 0  # restored by someone else between the two reads

    def counts(self) -> dict[str, int]:
        rb = self.readback
        return {
            metrics.RECONCILE_WRITTEN: rb.stuck,
            metrics.RECONCILE_FIXED: rb.fixed,
            metrics.RECONCILE_UNRESOLVED: rb.unresolved,
            # unrecorded: the read-back itself failed or did not return the item
            metrics.RECONCILE_ERROR: self.errors + rb.unrecorded,
        }


def _batches(seq: list, size: int = TAG_READ_BATCH):
    for start in range(0, len(seq), size):
        yield seq[start : start + size]


def _write(jf: JellyfinClient, session: object, cfg: AppConfig, candidates: list[Candidate]) -> _Writes:
    """Re-write each candidate's managed tags: what Jellyfin has, plus what is missing."""
    prefix = cfg.tags.managed_prefix
    out = _Writes()
    for chunk in _batches(candidates):
        if progress.cancelled:
            break
        try:
            # The scan's own Fields (ITEM_FIELDS): set_managed_tags() posts the whole item.
            fresh = jf.get_items_by_ids([c.item_id for c in chunk])
        except Exception as exc:
            log.warning("Tag reconciliation: re-read of %d item(s) failed (%s); none of them written", len(chunk), exc)
            out.errors += len(chunk)
            continue
        pending: list[_PendingRecord] = []
        for c in chunk:
            item = fresh.get(c.item_id)
            if item is None:
                out.gone += 1
                continue
            tags = item.get("Tags") or []
            missing = missing_tags(c.expected, tags)
            if not missing:
                out.already += 1
                continue
            # Jellyfin's managed tags as they are (respelling and extras untouched), plus the lost ones.
            new = [t for t in tags if t.startswith(prefix)] + missing
            progress.current_item = c.name
            try:
                jf.set_managed_tags(c.item_id, item, prefix, new, legacy_prefixes=cfg.tags.legacy_prefixes)
            except Exception as exc:
                log.warning("Tag reconciliation: write failed for %s (%s): %s", c.name, c.item_id, exc)
                out.errors += 1
                continue
            progress.emit(f"  restored: {c.name} — {', '.join(missing)}")
            pending.append(
                _PendingRecord(item=item, written=new, fallback_rating="", record=None, settled_from=time.monotonic())
            )
        _record_after_readback(jf, session, cfg, pending, out.readback)
        progress.done += len(chunk)
    return out


def _run_reconcile(cfg: AppConfig, trigger: str) -> dict:
    """One pass; the caller holds the scan lock. Returns the report it stored."""
    started = time.time()
    threshold = cfg.scan.reconcile_write_threshold
    progress.emit(f"[xenotag] Starting tag reconciliation ({trigger}) — Jellyfin only…")
    jf = JellyfinClient(cfg.jellyfin.url, cfg.jellyfin.api_key)
    session = get_session()
    try:
        run = start_scan_run(session, SCAN_TYPE)
        tracked = _tracked(session, cfg)
        progress.emit(f"[xenotag] Reading the current tags of {len(tracked)} tracked item(s) by Ids=…")

        # Read phase: half of the progress bar.
        progress.total = 2 * len(tracked)
        current: dict[str, dict] = {}
        for chunk in _batches(tracked):
            if progress.cancelled:
                break
            current.update(jf.get_items_by_ids([i for i, _ in chunk], Fields="Tags", EnableImages="false"))
            progress.done += len(chunk)
        p = plan(tracked, current) if not progress.cancelled else Plan()
        n = len(p.candidates)
        progress.emit(
            f"[xenotag] {n} item(s) missing a managed tag; {len(p.not_returned)} tracked id(s) not returned by Ids="
        )

        halted = trigger == SCHEDULED and n > threshold
        writes = _Writes()
        if halted:
            log.warning(
                "Tag reconciliation HALTED: %d item(s) need a write, more than the threshold of %d "
                "(scan.reconcile_write_threshold); nothing was written. Review, then start the manual "
                "rescan, which bypasses the threshold (roadmap B12(b)). Sample: %s",
                n,
                threshold,
                "; ".join(f"{c.name} ({c.item_id}) lacks {list(c.missing)}" for c in p.candidates[:SAMPLE_SIZE]),
            )
            progress.emit(f"[xenotag] HALTED: {n} item(s) need a write, more than {threshold}; nothing written")
        elif n and not progress.cancelled:
            # Write phase: the rest of the bar, one step per candidate.
            progress.total = len(tracked) + n
            progress.done = len(tracked)
            writes = _write(jf, session, cfg, p.candidates)
        else:
            progress.total = progress.done = len(tracked)
        if p.not_returned:
            log.info("Tag reconciliation: %d tracked id(s) not returned by Ids= (skipped)", len(p.not_returned))

        cancelled = progress.cancelled
        counts = writes.counts()
        outcome = "cancelled" if cancelled else "halted" if halted else "written" if n else "clean"
        report = {
            "generated_at": datetime.now(UTC).isoformat(),
            "trigger": trigger,
            "outcome": outcome,
            "halted": halted,
            "threshold": threshold,
            "tracked": len(tracked),
            "checked": p.checked,
            "not_returned": len(p.not_returned),
            "candidates": n,
            "writes": counts,
            "gone_before_write": writes.gone,
            "already_restored": writes.already,
            "duration_seconds": round(time.time() - started, 1),
            "sample": sample(p.candidates),
        }
        store_report(report)
        written = counts[metrics.RECONCILE_WRITTEN] + counts[metrics.RECONCILE_FIXED]
        finish_scan_run(session, run, scanned=p.checked, tagged=written, images=0)
        metrics.reconcile_finished(n, halted=halted, writes=counts, cancelled=cancelled)
        summary = (
            f"[xenotag] Tag reconciliation {outcome} — checked={p.checked}, candidates={n}, "
            f"written={counts[metrics.RECONCILE_WRITTEN]}, fixed by retry={counts[metrics.RECONCILE_FIXED]}, "
            f"unresolved={counts[metrics.RECONCILE_UNRESOLVED]}, errors={counts[metrics.RECONCILE_ERROR]}"
        )
        log.info(summary.removeprefix("[xenotag] "))
        progress.finish()
        progress.emit(summary)
        return report
    finally:
        session.close()
        jf.close()


def _run_reconcile_recorded(cfg: AppConfig, trigger: str) -> None:
    """``_run_reconcile`` with any exception releasing the scan lock (roadmap B22's rule)."""
    try:
        _run_reconcile(cfg, trigger)
    except Exception as exc:
        if progress.running:
            progress.finish(error=str(exc))
        progress.emit(f"[xenotag] Tag reconciliation failed — {exc}")
        log.error("Tag reconciliation failed: %s", exc, exc_info=True)
        raise


def run_scheduled_reconcile(cfg: AppConfig) -> None:
    """The ``scan.reconcile_schedule`` job: skips if a scan holds the lock; obeys the threshold."""
    if not progress.try_start():
        log.warning("Tag reconciliation: a scan is in progress; skipping this scheduled run")
        return
    _run_reconcile_recorded(cfg, SCHEDULED)


def start_manual_reconcile(cfg: AppConfig) -> bool:
    """The manual rescan: False if the lock is held; else runs in a thread, bypassing the threshold."""
    if not progress.try_start():
        return False
    threading.Thread(target=_run_reconcile_recorded, args=(cfg, MANUAL), daemon=True).start()
    return True
