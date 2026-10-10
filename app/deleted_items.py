"""Deleted Jellyfin items: drop their index rows, strip the *arr tags they owned (roadmap U2).

A ``media_state`` row outlives the Jellyfin item it describes -- nothing
reconciled the index with the library -- and since B5 so does every managed tag
written to that item's Sonarr/Radarr object. Operator decision 2026-09-26: when
an item is gone, a scan deletes its row and strips the managed tags from the
*arr object it owned, behind one report-only release first.

"Gone" is decided three ways, and each one can only make the pass do LESS:

1. **A complete listing.** Every Movie/Series on the server -- not only the
   configured libraries: an item outside them still exists. The pages must add
   up to the ``TotalRecordCount`` every page reports, with no id twice, and a
   second count taken after the last page must agree. Anything else aborts the
   pass and says why: a short read would mark live items deleted.
2. **A lookup by id.** Each candidate is asked for with ``/Items?Ids=``, beside
   control ids the listing just returned. A control that does not answer aborts
   the pass (the lookup cannot be trusted); a candidate that answers is not
   deleted.
3. **A bound.** When more than ``deleted_items.max_fraction`` of the index would
   go, removal refuses; the report still lists everything.

**Deleted is not unreachable.** An item Jellyfin still lists but the scan cannot
read (B11: file gone, probe failed) is in the listing, so it is never a
candidate here, whatever its scan errors say.

**Ownership is B5's rule: the folder.** The deleted item's folder is the one the
scan recorded -- the folder of the poster it overlaid, else the file's folder or
its parent -- and an object is the item's only if its ``path`` is that folder
(the deepest such match, across every instance). An object whose folder still
holds a live Jellyfin item is never stripped: that item owns it now, and a
re-encode that replaced the file is exactly that case.

``deleted_items.mode`` defaults to ``report``. Then the index is opened
read-only and every *arr client sits behind ``ReadOnlyTransport``, so the pass
cannot delete a row or send a write -- whatever a caller asks of it. In
``remove``, tags are stripped only while ``arr_sync.mode`` is ``live`` as well,
every strip is read back (a mismatch halts the pass), and a row is deleted only
once each object it owned has been stripped, so a failure leaves the row to
drive a retry.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import posixpath
import sys
from collections import Counter
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from .arr_sync import MODE_LIVE, _norm_path, item_folders, readback_problems
from .clients.arr import ArrClient
from .clients.jellyfin import COLLAPSE_BOX_SET_ITEMS, JellyfinClient
from .clients.radarr import RadarrClient
from .clients.readonly import ReadOnlyTransport
from .clients.sonarr import SonarrClient
from .config import AppConfig
from .state import MediaState, current_db_path, get_session, read_only_session

log = logging.getLogger(__name__)

MODE_REPORT = "report"
MODE_REMOVE = "remove"

PAGE_SIZE = 500
ID_BATCH = 100  # candidates per /Items?Ids= lookup (~3.6 kB of query string)
CONTROLS_PER_BATCH = 5
_EXAMPLES = 25

# Per-row outcomes, in the report.
NO_OBJECT = "no_arr_object"  # no object in any instance lives in the item's folder
KEPT_LIVE = "kept_live_item"  # an object does, but a live Jellyfin item sits in that folder too
STRIP = "strip"  # an object does, it carries managed tags, and no live item owns it
NOTHING_TO_STRIP = "nothing_to_strip"  # ...but it carries no managed tag
AMBIGUOUS = "ambiguous"  # two objects in one instance claim the folder -- never guessed
NO_FOLDER = "no_folder"  # the row records no path at all


class PassAborted(RuntimeError):
    """The pass could not establish which items are gone; nothing was changed."""


# ---------------------------------------------------------------------------
# What exists
# ---------------------------------------------------------------------------


@dataclass
class Listing:
    items: list[dict]
    total: int
    pages: int
    recount: int


def complete_listing(jf: JellyfinClient, *, page_size: int = PAGE_SIZE) -> Listing:
    """Every Movie and Series on the server, or ``PassAborted``.

    The scan's own listing (``JellyfinClient.get_items``) stops quietly on an
    empty page; that is harmless for tagging and fatal here, where an item
    missing from the list reads as deleted. So every way the pages can fail to
    add up is an abort: a page without a count, a count that changes between
    pages, an id seen twice, an empty page before the total, more items than the
    total, and a recount after the last page that disagrees.
    """
    params: dict = {
        "Recursive": "true",
        "IncludeItemTypes": "Movie,Series",
        "CollapseBoxSetItems": COLLAPSE_BOX_SET_ITEMS,
        "Fields": "Path",
        "EnableImages": "false",
        "Limit": page_size,
        "StartIndex": 0,
    }
    items: list[dict] = []
    seen: set[str] = set()
    total: int | None = None
    pages = 0
    while True:
        data = jf._get("/Items", **params)
        pages += 1
        count = data.get("TotalRecordCount") if isinstance(data, dict) else None
        if not isinstance(count, int) or count < 0:
            raise PassAborted(f"listing page {pages} carried no TotalRecordCount")
        if total is None:
            total = count
        elif count != total:
            raise PassAborted(f"TotalRecordCount changed between pages ({total} -> {count}): the library is changing")
        page = data.get("Items") or []
        for item in page:
            item_id = item.get("Id")
            if not item_id:
                raise PassAborted(f"listing page {pages} holds an item with no Id")
            if item_id in seen:
                raise PassAborted(f"listing returned {item_id} twice: the pages shifted under the read")
            seen.add(item_id)
            items.append(item)
        if len(items) > total:
            raise PassAborted(f"listing returned {len(items)} items, more than its TotalRecordCount {total}")
        if len(items) == total:
            break
        if not page:
            raise PassAborted(f"listing page {pages} came back empty at {len(items)} of {total} items")
        if pages > total // page_size + 2:
            raise PassAborted(f"listing needed {pages} pages for {total} items")
        params["StartIndex"] = len(items)
    recount = jf._get(
        "/Items",
        Recursive="true",
        IncludeItemTypes="Movie,Series",
        CollapseBoxSetItems=COLLAPSE_BOX_SET_ITEMS,
        Limit=0,
    ).get("TotalRecordCount")
    if recount != total:
        raise PassAborted(f"a recount after the listing says {recount}, the listing {total}: the library is changing")
    return Listing(items=items, total=total, pages=pages, recount=recount)


@dataclass
class Existence:
    deleted: list[str]
    answered: list[str]  # candidates the listing lacked but a lookup by id found
    calls: int = 0
    controls_sent: int = 0
    controls_answered: int = 0


def confirm_deleted(
    jf: JellyfinClient, candidates: list[str], live_ids: list[str], *, batch: int = ID_BATCH
) -> Existence:
    """Look each candidate up by id, beside live controls that must answer.

    A lookup that silently answers nothing would confirm every candidate; the
    controls are what make "not found" mean something. One missing control
    aborts the pass.
    """
    result = Existence(deleted=[], answered=[])
    if not candidates:
        return result
    if not live_ids:
        raise PassAborted("no live item to use as a control for the lookup by id")
    step = max(1, len(live_ids) // (CONTROLS_PER_BATCH * max(1, len(candidates) // batch + 1)))
    cursor = 0
    for start in range(0, len(candidates), batch):
        chunk = candidates[start : start + batch]
        controls = []
        for _ in range(min(CONTROLS_PER_BATCH, len(live_ids))):
            controls.append(live_ids[cursor % len(live_ids)])
            cursor += step
        data = jf._get("/Items", Ids=",".join(chunk + controls), Fields="Path", EnableImages="false")
        result.calls += 1
        answered = {i.get("Id") for i in data.get("Items") or []}
        missing = [c for c in controls if c not in answered]
        result.controls_sent += len(controls)
        result.controls_answered += len(controls) - len(missing)
        if missing:
            raise PassAborted(
                f"{len(missing)} of {len(controls)} live control items did not answer a lookup by id:"
                " the lookup cannot be trusted to say an item is gone"
            )
        for item_id in chunk:
            (result.answered if item_id in answered else result.deleted).append(item_id)
    return result


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------


def row_folders(row: MediaState) -> list[str]:
    """The deleted item's folder as the scan recorded it, deepest first.

    The poster the scan overlaid sits in the item's own folder (every such row
    in production, 2026-09-27); without one, the file's folder and its parent
    (a film's folder; a series' season folder, then the series).
    """
    if row.image_path:
        folder = _norm_path(posixpath.dirname(row.image_path))
        return [folder] if folder not in ("", "/", ".") else []
    file_path = _norm_path(row.file_path)
    if not file_path:
        return []
    parent = posixpath.dirname(file_path)
    return [f for f in (parent, posixpath.dirname(parent)) if f not in ("", "/", ".")]


@dataclass
class ObjectPlan:
    client: ArrClient
    object_id: int
    folder: str
    title: str
    remove_ids: list[int]
    remove_labels: list[str]
    rows: list[str] = field(default_factory=list)
    done: bool = False  # stripped and read back, or found with nothing left to strip


@dataclass
class _InstanceStats:
    kind: str
    name: str
    available: bool = False
    error: str | None = None
    objects: int = 0
    matched: set[int] = field(default_factory=set)  # distinct objects in a deleted item's folder
    kept_live_item: set[int] = field(default_factory=set)
    ambiguous: set[int] = field(default_factory=set)
    nothing_to_strip: int = 0
    strip_objects: int = 0
    strip_tags: int = 0
    labels: Counter = field(default_factory=Counter)
    stripped_objects: int = 0
    stripped_tags: int = 0
    gone_since_preload: int = 0
    changed_since_preload: int = 0
    readback_failures: int = 0
    write_errors: int = 0
    to_strip: list[dict] = field(default_factory=list)
    examples: dict[str, list] = field(default_factory=dict)

    def example(self, category: str, value: object) -> None:
        bucket = self.examples.setdefault(category, [])
        if len(bucket) < _EXAMPLES:
            bucket.append(value)

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "name": self.name,
            "available": self.available,
            "error": self.error,
            "objects": self.objects,
            "matched": len(self.matched),
            "kept_live_item": len(self.kept_live_item),
            "ambiguous": len(self.ambiguous),
            "nothing_to_strip": self.nothing_to_strip,
            "strip_objects": self.strip_objects,
            "strip_tags": self.strip_tags,
            "labels": dict(self.labels.most_common()),
            "stripped_objects": self.stripped_objects,
            "stripped_tags": self.stripped_tags,
            "gone_since_preload": self.gone_since_preload,
            "changed_since_preload": self.changed_since_preload,
            "readback_failures": self.readback_failures,
            "write_errors": self.write_errors,
            "to_strip": self.to_strip,
            "examples": self.examples,
        }


# ---------------------------------------------------------------------------
# The pass
# ---------------------------------------------------------------------------


class DeletedItemsPass:
    """One pass: find the rows whose item is gone, plan, and (in ``remove``) act.

    The clients and the session are handed in already built, and in ``report``
    mode they cannot write: the *arr transports refuse, the session is
    read-only. ``run()`` in ``report`` never calls the writing half either.
    """

    def __init__(
        self,
        cfg: AppConfig,
        jf: JellyfinClient,
        sonarrs: Iterable[ArrClient],
        radarrs: Iterable[ArrClient],
        session: Session,
        *,
        mode: str | None = None,
        source: str = "scan",
        emit: Callable[[str], None] | None = None,
        removal_log: Path | None = None,
    ) -> None:
        self.mode = mode or cfg.deleted_items.mode
        self.arr_writes = self.mode == MODE_REMOVE and cfg.arr_sync.mode == MODE_LIVE
        self.max_fraction = cfg.deleted_items.max_fraction
        self.source = source
        self.prefixes = tuple(p.lower() for p in (cfg.tags.managed_prefix, *cfg.tags.legacy_prefixes) if p)
        self.jf = jf
        self.clients = [*sonarrs, *radarrs]
        self.session = session
        self.stats = {c.label: _InstanceStats(c.KIND, c.name) for c in self.clients}
        self._emit = emit or (lambda _msg: None)
        self._removal_log = removal_log
        self.started_at = datetime.now(UTC)
        self.status = "ok"
        self.reason: str | None = None
        self.halted: str | None = None
        self.listing: dict = {}
        self.existence: dict = {}
        self.index: dict = {}
        self.rows: Counter = Counter()
        self.row_examples: dict[str, list] = {}
        self.objects: dict[tuple[str, int], ObjectPlan] = {}
        self.row_plans: dict[str, list[tuple[str, int]]] = {}
        self.row_outcome: dict[str, str] = {}
        self.rows_deleted = 0
        self.rows_kept: Counter = Counter()

    # --- the whole pass ---

    def run(self) -> dict:
        try:
            deleted = self.find_deleted()
            if deleted is not None:
                self.plan(deleted)
                if self.mode == MODE_REMOVE:
                    self.remove()
        except PassAborted as exc:
            self._abort(str(exc))
        except Exception as exc:  # any other failure: report it, change nothing more
            log.error("Deleted-items pass failed: %s", exc, exc_info=True)
            self._abort(f"{type(exc).__name__}: {exc}")
        return self.report()

    def _abort(self, reason: str) -> None:
        self.status = "aborted"
        self.reason = reason
        log.warning("Deleted-items pass ABORTED — %s", reason)

    # --- deciding what is gone ---

    def find_deleted(self) -> list[MediaState] | None:
        listing = complete_listing(self.jf)
        self.listing = {
            "jellyfin_items": len(listing.items),
            "total_record_count": listing.total,
            "pages": listing.pages,
            "recount": listing.recount,
        }
        self._live_items = listing.items
        live_ids = [i["Id"] for i in listing.items]
        live = set(live_ids)
        rows = self.session.query(MediaState).all()
        indexed = {r.item_id.split(":", 1)[1]: r for r in rows if (r.item_id or "").startswith("jellyfin:")}
        candidates = sorted(set(indexed) - live)
        existence = confirm_deleted(self.jf, candidates, live_ids)
        self.existence = {
            "candidates": len(candidates),
            "confirmed_deleted": len(existence.deleted),
            "answered_by_id": len(existence.answered),
            "lookups": existence.calls,
            "controls_sent": existence.controls_sent,
            "controls_answered": existence.controls_answered,
        }
        fraction = len(existence.deleted) / len(rows) if rows else 0.0
        self.index = {
            "rows": len(rows),
            "rows_not_jellyfin": len(rows) - len(indexed),
            "deleted_rows": len(existence.deleted),
            "fraction": round(fraction, 4),
            "max_fraction": self.max_fraction,
            "over_bound": fraction > self.max_fraction,
        }
        return [indexed[i] for i in existence.deleted]

    # --- planning ---

    def prepare(self) -> None:
        """Preload every instance (GETs). Any instance that fails aborts the pass.

        A row whose object lives in an instance we could not read cannot be
        judged, and deleting it would lose the only pointer to that object.
        """
        if not self.clients:
            return
        with ThreadPoolExecutor(max_workers=len(self.clients)) as pool:
            results = {c.label: pool.submit(c.preload) for c in self.clients}
        failed = []
        for client in self.clients:
            st = self.stats[client.label]
            try:
                results[client.label].result()
            except Exception as exc:
                log.error("[%s] preload failed: %s", client.label, exc, exc_info=True)
                st.error = type(exc).__name__
                failed.append(client.label)
                continue
            st.available = True
            st.objects = len(client.objects)
        if failed:
            raise PassAborted(f"could not read {', '.join(failed)}: rows owned there cannot be judged")

    def plan(self, deleted: list[MediaState]) -> None:
        self.prepare()
        live_folders: set[str] = set()
        for item in self._live_items:
            live_folders |= item_folders(item)
        by_path: dict[str, list[tuple[ArrClient, int]]] = {}
        for client in self.clients:
            for object_id, obj in client.objects.items():
                path = _norm_path(obj.get("path"))
                if path:
                    by_path.setdefault(path, []).append((client, object_id))
        for row in deleted:
            item_id = row.item_id.split(":", 1)[1]
            folders = row_folders(row)
            if not folders:
                self._row(item_id, row, NO_FOLDER)
                continue
            hit = next((f for f in folders if f in by_path), None)
            if hit is None:
                self._row(item_id, row, NO_OBJECT)
                continue
            owners = by_path[hit]
            per_instance = Counter(c.label for c, _ in owners)
            if any(n > 1 for n in per_instance.values()):
                for client, object_id in owners:
                    self.stats[client.label].ambiguous.add(object_id)
                self._row(item_id, row, AMBIGUOUS)
                continue
            if hit in live_folders:
                for client, object_id in owners:
                    st = self.stats[client.label]
                    st.matched.add(object_id)
                    if object_id not in st.kept_live_item:
                        st.kept_live_item.add(object_id)
                        st.example(KEPT_LIVE, {"object_id": object_id, "title": client.objects[object_id].get("title")})
                self._row(item_id, row, KEPT_LIVE)
                continue
            keys = []
            for client, object_id in owners:
                st = self.stats[client.label]
                st.matched.add(object_id)
                key = (client.label, object_id)
                plan = self.objects.get(key)
                if plan is None:
                    plan = self._plan_object(client, object_id, hit)
                    self.objects[key] = plan
                    if plan.remove_ids:
                        st.strip_objects += 1
                        st.strip_tags += len(plan.remove_ids)
                        st.labels.update(plan.remove_labels)
                        st.to_strip.append(
                            {"object_id": object_id, "title": plan.title, "path": hit, "labels": plan.remove_labels}
                        )
                    else:
                        st.nothing_to_strip += 1
                plan.rows.append(item_id)
                keys.append(key)
            self.row_plans[item_id] = keys
            self._row(item_id, row, STRIP if any(self.objects[k].remove_ids for k in keys) else NOTHING_TO_STRIP)

    def _plan_object(self, client: ArrClient, object_id: int, folder: str) -> ObjectPlan:
        obj = client.objects[object_id]
        remove_ids, remove_labels = self._managed(client, obj.get("tags"))
        return ObjectPlan(client, object_id, folder, obj.get("title") or "", remove_ids, remove_labels)

    def _managed(self, client: ArrClient, tags: Iterable[int] | None) -> tuple[list[int], list[str]]:
        by_id = {tag_id: label for label, tag_id in client.tags.items()}
        ids = [t for t in tags or [] if by_id.get(t, "").startswith(self.prefixes)]
        return ids, [by_id[t] for t in ids]

    def _row(self, item_id: str, row: MediaState, outcome: str) -> None:
        self.rows[outcome] += 1
        self.row_outcome[item_id] = outcome
        bucket = self.row_examples.setdefault(outcome, [])
        if len(bucket) < _EXAMPLES:
            bucket.append({"item_id": item_id, "file_path": row.file_path})

    # --- acting (remove mode only) ---

    def remove(self) -> None:
        if self.mode != MODE_REMOVE:
            raise RuntimeError("remove() called outside remove mode")
        if self.index.get("over_bound"):
            self.status = "refused"
            self.reason = (
                f"{self.index['deleted_rows']} of {self.index['rows']} index rows ({self.index['fraction']:.1%})"
                f" would go, more than deleted_items.max_fraction {self.max_fraction:.0%}: nothing removed"
            )
            log.warning("Deleted-items removal REFUSED — %s", self.reason)
            return
        for plan in self.objects.values():
            if self.halted:
                break
            if not plan.remove_ids:
                plan.done = True
                continue
            if not self.arr_writes:
                continue  # *arr writes are off: the row stays until they are on
            try:
                self.strip(plan)
            except Exception as exc:
                self.stats[plan.client.label].write_errors += 1
                log.error("[%s] strip of object %s failed: %s", plan.client.label, plan.object_id, exc)
                self._halt(f"{plan.client.label}: {type(exc).__name__}: {exc}")
        if self.halted:
            return  # nothing more changes; every row stays to drive the retry
        doomed = []
        for item_id, outcome in self.row_outcome.items():
            if outcome == AMBIGUOUS:
                self.rows_kept["ambiguous"] += 1
            elif outcome in (STRIP, NOTHING_TO_STRIP) and not all(
                self.objects[k].done for k in self.row_plans[item_id]
            ):
                self.rows_kept["arr_writes_off" if not self.arr_writes else "not_stripped"] += 1
            else:
                doomed.append(item_id)
        self.delete_rows(doomed)

    def strip(self, plan: ObjectPlan) -> None:
        """Remove the managed tags from one object and read it back. A WRITE.

        Re-fetched first: an object deleted or moved since the preload is not
        the deleted item's any more and is left alone.
        """
        client = plan.client
        st = self.stats[client.label]
        try:
            before = client.get_object(plan.object_id)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
            st.gone_since_preload += 1
            plan.done = True
            return
        if _norm_path(before.get("path")) != plan.folder:
            st.changed_since_preload += 1
            st.example("changed_since_preload", {"object_id": plan.object_id, "title": plan.title})
            return
        remove_ids, remove_labels = self._managed(client, before.get("tags"))
        if not remove_ids:
            plan.done = True
            return
        client.edit_tags(plan.object_id, remove_ids, "remove")
        after = client.get_object(plan.object_id)
        by_id = {tag_id: label for label, tag_id in client.tags.items()}
        problems = readback_problems(before, after, set(), lambda t: by_id.get(t, "").startswith(self.prefixes))
        if problems:
            st.readback_failures += 1
            st.example("readback_failures", {"object_id": plan.object_id, "title": plan.title, "problems": problems})
            log.error("[%s] READ-BACK MISMATCH after strip of %s: %s", client.label, plan.object_id, problems)
            self._halt(f"{client.label}: read-back mismatch on object {plan.object_id}: {problems[0]}")
            return
        st.stripped_objects += 1
        st.stripped_tags += len(remove_ids)
        plan.done = True
        self._log_removal(plan, remove_ids, remove_labels)

    def _log_removal(self, plan: ObjectPlan, tag_ids: list[int], labels: list[str]) -> None:
        """Append what was stripped, so it can be put back by hand."""
        if self._removal_log is None:
            return
        record = {
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "instance": plan.client.label,
            "object_id": plan.object_id,
            "title": plan.title,
            "path": plan.folder,
            "tag_ids": tag_ids,
            "labels": labels,
            "rows": plan.rows,
        }
        try:
            with self._removal_log.open("a") as fh:
                fh.write(json.dumps(record) + "\n")
        except OSError as exc:
            log.warning("Could not append to %s: %s", self._removal_log, exc)

    def delete_rows(self, item_ids: list[str]) -> None:
        """Delete index rows. A WRITE -- through a read-only session, SQLite refuses it."""
        for start in range(0, len(item_ids), 500):
            batch = [f"jellyfin:{i}" for i in item_ids[start : start + 500]]
            self.session.query(MediaState).filter(MediaState.item_id.in_(batch)).delete(synchronize_session=False)
            self.session.commit()
            self.rows_deleted += len(batch)

    def _halt(self, reason: str) -> None:
        if self.halted is None:
            self.halted = reason
            log.error("Deleted-items removal HALTED: %s", reason)
            self._emit(f"[xenotag] Deleted items: removal HALTED — {reason}")

    # --- report ---

    def report(self) -> dict:
        return {
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "source": self.source,
            "mode": self.mode,
            "arr_writes": self.arr_writes,
            "status": self.status,
            "reason": self.reason,
            "halted": self.halted,
            "listing": self.listing,
            "existence": self.existence,
            "index": {**self.index, "rows_deleted": self.rows_deleted, "rows_kept": dict(self.rows_kept)},
            "rows": dict(self.rows),
            "row_examples": self.row_examples,
            "instances": {label: st.as_dict() for label, st in self.stats.items()},
        }

    def summary_lines(self) -> list[str]:
        return summary_lines(self.report())


def summary_lines(report: dict) -> list[str]:
    head = "[xenotag] Deleted items"
    if report["status"] == "aborted":
        return [f"{head}: pass ABORTED — {report['reason']}. Nothing changed."]
    ix = report["index"]
    acting = report["mode"] == MODE_REMOVE
    verb = "deleted" if acting else "would delete"
    lines = [
        f"{head} ({'REMOVE' if acting else 'report only'}): {ix['deleted_rows']} of {ix['rows']} index rows"
        f" ({ix['fraction']:.1%}) describe items Jellyfin no longer has; {verb}"
        f" {ix['rows_deleted'] if acting else ix['deleted_rows']} rows. Rows: {report['rows'] or '{}'}"
    ]
    if ix.get("over_bound"):
        lines.append(
            f"{head}: over the {ix['max_fraction']:.0%} bound (deleted_items.max_fraction) — removal"
            f" {'REFUSED' if acting else 'would refuse'}"
        )
    for label, st in report["instances"].items():
        if not st["available"]:
            continue
        done = f", stripped {st['stripped_tags']} from {st['stripped_objects']}" if acting else ""
        lines.append(
            f"{head}: {label}: {'strip' if acting else 'would strip'} {st['strip_tags']} tags from"
            f" {st['strip_objects']} objects{done}; kept (live item in the folder) {st['kept_live_item']}"
        )
    if report.get("halted"):
        lines.append(f"{head}: removal HALTED — {report['halted']}")
    if report["status"] == "refused":
        lines.append(f"{head}: REFUSED — {report['reason']}")
    return lines


# ---------------------------------------------------------------------------
# Wiring: clients, session, the stored report
# ---------------------------------------------------------------------------


def _build_clients(
    cfg: AppConfig, *, arr_writable: bool, guards: list[ReadOnlyTransport]
) -> tuple[JellyfinClient, list[ArrClient], list[ArrClient]]:
    """Jellyfin is always read-only here; the *arrs unless this pass may strip."""

    def guard() -> ReadOnlyTransport:
        transport = ReadOnlyTransport()
        guards.append(transport)
        return transport

    jf = JellyfinClient(cfg.jellyfin.url, cfg.jellyfin.api_key, transport=guard())

    def arr_transport() -> ReadOnlyTransport | None:
        return None if arr_writable else guard()

    sonarrs = [
        SonarrClient(inst.url, inst.api_key, inst.name, transport=arr_transport()) for inst in cfg.sonarr.instances
    ]
    radarrs = [
        RadarrClient(inst.url, inst.api_key, inst.name, transport=arr_transport()) for inst in cfg.radarr.instances
    ]
    return jf, sonarrs, radarrs


def report_path() -> Path:
    return Path(current_db_path()).with_name("deleted-items-report.json")


def removal_log_path() -> Path:
    return Path(current_db_path()).with_name("deleted-items-removed.jsonl")


_last_report: dict | None = None
state: dict = {"running": False, "error": None}


def store_report(report: dict) -> None:
    global _last_report
    _last_report = report
    path = report_path()
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(report, indent=2, default=str))
        tmp.replace(path)
    except OSError as exc:
        log.warning("Could not write the deleted-items report to %s: %s", path, exc)


def load_report() -> dict | None:
    if _last_report is not None:
        return _last_report
    try:
        return json.loads(report_path().read_text())
    except (OSError, ValueError):
        return None


def run_deleted_items(
    cfg: AppConfig,
    *,
    mode: str | None = None,
    db_path: str | Path | None = None,
    store: bool = True,
    source: str = "scan",
    emit: Callable[[str], None] | None = None,
) -> dict:
    """One deleted-items pass. ``report`` (the default) cannot change anything.

    In ``report`` the index is opened read-only -- the app's own ``state.db``,
    or the copy at ``db_path`` -- and the *arr clients are guarded. ``remove``
    uses the app's session (or ``db_path`` read-write) and writable *arr
    clients only while ``arr_sync.mode`` is ``live``.
    """
    mode = mode or cfg.deleted_items.mode
    removing = mode == MODE_REMOVE
    guards: list[ReadOnlyTransport] = []
    jf, sonarrs, radarrs = _build_clients(cfg, arr_writable=removing and cfg.arr_sync.mode == MODE_LIVE, guards=guards)
    if removing:
        session = _writable_session(db_path) if db_path else get_session()
    else:
        session = read_only_session(db_path or current_db_path())
    try:
        dpass = DeletedItemsPass(
            cfg,
            jf,
            sonarrs,
            radarrs,
            session,
            mode=mode,
            source=source,
            emit=emit,
            removal_log=removal_log_path() if removing and not db_path else None,
        )
        report = dpass.run()
    finally:
        session.close()
        jf.close()
        for client in (*sonarrs, *radarrs):
            client.close()
    sent: dict[str, int] = {}
    for g in guards:
        for method, n in g.sent.items():
            sent[method] = sent.get(method, 0) + n
    report["guard"] = {"transports": len(guards), "sent": sent, "blocked": [b for g in guards for b in g.blocked]}
    if store:
        store_report(report)
    for line in summary_lines(report):
        log.info("%s", line.removeprefix("[xenotag] "))
        if emit:
            emit(line)
    return report


def _writable_session(db_path: str | Path) -> Session:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    return sessionmaker(bind=create_engine(f"sqlite:///{db_path}"))()


def run_report_background(cfg: AppConfig) -> None:
    """Thread target for the Settings button: always report-only, whatever the config says."""
    state.update(running=True, error=None)
    try:
        run_deleted_items(cfg, mode=MODE_REPORT, source="manual report")
    except Exception as exc:
        log.error("Deleted-items report failed: %s", exc, exc_info=True)
        state["error"] = f"{type(exc).__name__}: deleted-items pass failed; see the server log"
    finally:
        state["running"] = False


# ---------------------------------------------------------------------------
# CLI: python -m app.deleted_items --report
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m app.deleted_items",
        description="Deleted-items report (roadmap U2). Always report-only: the index is opened read-only and "
        "every client is behind a transport that refuses anything but GET.",
    )
    ap.add_argument("--report", action="store_true", help="find the rows whose item is gone and report the plan")
    ap.add_argument("--config", type=Path, help="config.yml (default: $CONFIG_PATH)")
    ap.add_argument("--db", type=Path, help="state.db to read (a copy; opened read-only). Default: the app's own")
    ap.add_argument("--out", type=Path, help="write the full JSON report here")
    args = ap.parse_args(argv)

    from .clients.readonly import self_test

    failures = self_test()
    print("read-only guard self-test:", "PASS" if not failures else "FAIL")
    for failure in failures:
        print("  -", failure)
    if failures:
        return 2
    if not args.report:
        ap.error("nothing to do: pass --report")

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s — %(message)s")
    from .config import load_config

    cfg = load_config(args.config)
    report = run_deleted_items(cfg, mode=MODE_REPORT, db_path=args.db, store=False, source="cli report")
    for line in summary_lines(report):
        print(line.removeprefix("[xenotag] "))
    print(f"  listing: {report['listing']}")
    print(f"  lookup by id: {report['existence']}")
    print(f"  transport guard: {report['guard']}")
    if args.out:
        args.out.write_text(json.dumps(report, indent=2, default=str))
        print(f"  full report: {args.out}")
    return 1 if report["guard"]["blocked"] or report["status"] == "aborted" else 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    sys.exit(main())
