"""Sonarr/Radarr tag sync: matching, dry-run accounting, guarded writes (roadmap B5).

Until B5 nothing had ever been written to an *arr: the match read
``ProviderIds["Sonarr"]``/``["Radarr"]``, keys Jellyfin sets on no item at all.
This module matches on what Jellyfin does supply (Tvdb / Tmdb / Imdb) and is
built around four facts measured against production on 2026-09-26:

1. **An *arr id is per instance.** Series 42 on one Sonarr is a different show
   from series 42 on another, so an item is resolved to an object *inside each
   instance*; no id ever crosses instances.
2. **The same title lives in more than one instance.** 133 films match by id in
   both radarr/general and radarr/4k, and 12 series in both sonarr/general and
   sonarr/4k -- the HD and the 4K copy. Matching by id alone would write the 4K
   file's tags onto the HD copy and back again on every scan. So a match must
   also agree on the FOLDER: the object's ``path`` must be the item's folder.
   Every container mounts media at the same ``/media/...`` paths, so the two are
   directly comparable; with that check no item has more than one owner.
3. **Ambiguity writes nothing.** Ids that point at two objects in one instance
   (1 series in production), and an object claimed by two Jellyfin items in one
   scan, are refused and reported -- never guessed.
4. **A write touches only managed tags.** See ``clients/arr.py``: the bulk
   editor adds/removes tag ids and nothing else, and every write is read back.

``ArrSyncConfig.mode`` defaults to ``dry_run``: everything above runs and is
counted, and nothing but GETs reaches an *arr.
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

from .clients.arr import ArrClient
from .config import AppConfig

log = logging.getLogger(__name__)

MODE_DRY_RUN = "dry_run"
MODE_LIVE = "live"

OWNED = "owned"  # one object matched by id, and it lives in the item's folder
ELSEWHERE = "elsewhere"  # one object matched by id, in a different folder
AMBIGUOUS = "ambiguous"  # the item's ids point at more than one object
NONE = "none"

# Fields an *arr rewrites on its own schedule (metadata refresh, disk scan,
# search). The read-back ignores them, at any depth: a tag edit changed none of
# them on the dev instances, so a difference there is the *arr's own doing, and
# halting a scan for it would be a false alarm. Anything else that differs
# between the before- and after-copies halts further writes.
VOLATILE_FIELDS = frozenset({"statistics", "lastInfoSync", "lastSearchTime", "ratings", "images", "popularity"})

_EXAMPLES = 25  # per category, in the report


def _norm_id(value: object) -> str:
    text = str(value).strip().lower() if value is not None else ""
    return "" if text in ("", "0", "none") else text


def _norm_path(path: str | None) -> str:
    if not path:
        return ""
    return posixpath.normpath(path)


def item_folders(item: dict) -> set[str]:
    """The folders an *arr object must point at to own this Jellyfin item.

    A series item's ``Path`` is the series folder; a film's is the file, so its
    folder is the parent (a disc-folder film's ``Path`` is the folder itself).
    Both the path and its parent are candidates -- the parent of a series folder
    is a root folder, which no series or film points at.
    """
    paths = [item.get("Path") or ""]
    sources = item.get("MediaSources") or []
    if sources:
        paths.append(sources[0].get("Path") or "")
    folders: set[str] = set()
    for path in paths:
        norm = _norm_path(path)
        if norm:
            folders.add(norm)
            folders.add(posixpath.dirname(norm))
    return folders


def kind_for(item: dict) -> str | None:
    return {"Series": "sonarr", "Movie": "radarr"}.get(item.get("Type") or "")


@dataclass(frozen=True)
class Match:
    status: str
    by: str = ""  # the most authoritative key that hit
    object_id: int | None = None
    candidates: tuple[int, ...] = ()


class InstanceIndex:
    """Provider id -> object ids, for one instance, from its preload."""

    def __init__(self, client: ArrClient) -> None:
        self.client = client
        self.by_key: dict[str, dict[str, list[int]]] = {key: {} for key, _ in client.MATCH_KEYS}
        for object_id, obj in client.objects.items():
            for key, arr_field in client.MATCH_KEYS:
                value = _norm_id(obj.get(arr_field))
                if value:
                    self.by_key[key].setdefault(value, []).append(object_id)

    def resolve(self, item: dict) -> Match:
        provider_ids = item.get("ProviderIds") or {}
        hits: dict[int, list[str]] = {}
        for key, _ in self.client.MATCH_KEYS:
            value = _norm_id(provider_ids.get(key))
            if not value:
                continue
            for object_id in self.by_key[key].get(value, ()):
                hits.setdefault(object_id, []).append(key)
        if not hits:
            return Match(NONE)
        if len(hits) > 1:
            return Match(AMBIGUOUS, candidates=tuple(sorted(hits)))
        object_id, keys = next(iter(hits.items()))
        obj = self.client.objects[object_id]
        status = OWNED if _norm_path(obj.get("path")) in item_folders(item) else ELSEWHERE
        return Match(status, by=keys[0], object_id=object_id)


@dataclass
class TagPlan:
    current: list[int]
    desired_ids: list[int]  # managed tags that exist already and should be present
    create: list[str]  # managed labels that would have to be created
    rejected: list[str]  # managed labels the instance refuses
    add: list[int]
    remove: list[int]

    @property
    def changes(self) -> bool:
        return bool(self.add or self.remove or self.create)


def plan_tags(
    client: ArrClient, current: Iterable[int], desired_labels: Iterable[str], prefixes: tuple[str, ...]
) -> TagPlan:
    """What to add and remove so the object's managed tags are exactly ``desired_labels``.

    Only tags whose label starts with a managed (or legacy) prefix are ever
    removed, so a tag the operator applied can never be touched. Pure: reads the
    client's tag map, sends nothing.
    """
    current = list(current or [])
    by_label = client.tags
    by_id = {tag_id: label for label, tag_id in by_label.items()}
    desired_ids: list[int] = []
    create: list[str] = []
    rejected: list[str] = []
    for label in dict.fromkeys(lbl.lower() for lbl in desired_labels):
        if not label.startswith(prefixes):
            raise ValueError(f"refusing to manage a tag outside the managed prefixes: {label!r}")
        if not client.label_accepted(label):
            rejected.append(label)
        elif label in by_label:
            desired_ids.append(by_label[label])
        else:
            create.append(label)
    managed_now = [t for t in current if by_id.get(t, "").startswith(prefixes)]
    return TagPlan(
        current=current,
        desired_ids=desired_ids,
        create=create,
        rejected=rejected,
        add=[t for t in desired_ids if t not in current],
        remove=[t for t in managed_now if t not in desired_ids],
    )


def _changed_fields(before: object, after: object, path: str = "") -> list[str]:
    if isinstance(before, dict) and isinstance(after, dict):
        out: list[str] = []
        for key in sorted(set(before) | set(after)):
            if key in VOLATILE_FIELDS:
                continue
            out += _changed_fields(before.get(key, "<absent>"), after.get(key, "<absent>"), f"{path}.{key}")
        return out
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        out = []
        for i, (b, a) in enumerate(zip(before, after, strict=True)):
            out += _changed_fields(b, a, f"{path}[{i}]")
        return out
    return [] if before == after else [path or "."]


def readback_problems(
    before: dict,
    after: dict,
    intended_managed: set[int],
    is_managed: Callable[[int], bool],
) -> list[str]:
    """Compare an object before and after a tag write. Empty list = as intended.

    Three checks: the managed tags are exactly what was intended; the
    non-managed tags are unchanged; and no other field changed (ignoring
    ``VOLATILE_FIELDS``, which the *arr rewrites on its own).
    """
    problems: list[str] = []
    before_tags = list(before.get("tags") or [])
    after_tags = list(after.get("tags") or [])
    managed_after = {t for t in after_tags if is_managed(t)}
    if managed_after != intended_managed:
        problems.append(f"managed tags are {sorted(managed_after)}, intended {sorted(intended_managed)}")
    user_before = sorted(t for t in before_tags if not is_managed(t))
    user_after = sorted(t for t in after_tags if not is_managed(t))
    if user_before != user_after:
        problems.append(f"non-managed tags changed: {user_before} -> {user_after}")
    for changed in _changed_fields(
        {k: v for k, v in before.items() if k != "tags"}, {k: v for k, v in after.items() if k != "tags"}
    ):
        problems.append(f"field changed: {changed}")
    return problems


@dataclass
class _Owner:
    client: ArrClient
    object_id: int
    by: str


@dataclass
class _InstanceStats:
    kind: str
    name: str
    available: bool = False
    error: str | None = None
    objects: int = 0
    labels: int = 0
    managed_labels: int = 0
    matched_by: Counter = field(default_factory=Counter)
    owned: int = 0
    elsewhere: int = 0
    ambiguous: int = 0
    claimed_twice: int = 0
    synced: int = 0
    would_change: int = 0
    already_current: int = 0
    tags_to_add: int = 0
    tags_to_remove: int = 0
    labels_to_create: Counter = field(default_factory=Counter)
    labels_rejected: Counter = field(default_factory=Counter)
    written: int = 0
    labels_created: int = 0
    readback_failures: int = 0
    write_errors: int = 0
    changed_since_preload: int = 0
    skipped_halted: int = 0
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
            "labels": self.labels,
            "managed_labels": self.managed_labels,
            "matched_by": dict(self.matched_by),
            "owned": self.owned,
            "elsewhere": self.elsewhere,
            "ambiguous": self.ambiguous,
            "claimed_twice": self.claimed_twice,
            "synced": self.synced,
            "would_change": self.would_change,
            "already_current": self.already_current,
            "tags_to_add": self.tags_to_add,
            "tags_to_remove": self.tags_to_remove,
            "labels_to_create": dict(sorted(self.labels_to_create.items())),
            "labels_rejected": dict(sorted(self.labels_rejected.items())),
            "written": self.written,
            "labels_created": self.labels_created,
            "readback_failures": self.readback_failures,
            "write_errors": self.write_errors,
            "changed_since_preload": self.changed_since_preload,
            "skipped_halted": self.skipped_halted,
            "examples": self.examples,
        }


class ArrTagSync:
    """One scan's worth of *arr matching and tag writes (or, in dry run, counting)."""

    def __init__(
        self,
        cfg: AppConfig,
        sonarrs: Iterable[ArrClient],
        radarrs: Iterable[ArrClient],
        *,
        mode: str | None = None,
        source: str = "scan",
        emit: Callable[[str], None] | None = None,
    ) -> None:
        self.mode = mode or cfg.arr_sync.mode
        self.source = source
        self.cert_fallback = cfg.arr_sync.certification_fallback
        self.prefixes = tuple(p.lower() for p in (cfg.tags.managed_prefix, *cfg.tags.legacy_prefixes) if p)
        self.clients: dict[str, list[ArrClient]] = {"sonarr": list(sonarrs), "radarr": list(radarrs)}
        self.stats = {c.label: _InstanceStats(c.KIND, c.name) for cs in self.clients.values() for c in cs}
        self._index: dict[str, InstanceIndex] = {}
        self._owners: dict[str, list[_Owner]] = {}
        self._refused: set[tuple[str, int]] = set()
        self._emit = emit or (lambda _msg: None)
        self.halted: str | None = None
        self.started_at = datetime.now(UTC)
        self.items: Counter = Counter()
        self.unowned_examples: dict[str, list] = {}
        self.cert = {"blank_rating": 0, "would_fill": 0, "conflicts": 0, "by_value": Counter(), "examples": []}

    @property
    def live(self) -> bool:
        return self.mode == MODE_LIVE

    # --- preparation ---

    def prepare(self) -> None:
        """Preload every instance (GETs only). A failed instance is reported and skipped."""
        clients = [c for cs in self.clients.values() for c in cs]
        if not clients:
            return
        with ThreadPoolExecutor(max_workers=len(clients)) as pool:
            results = {c.label: pool.submit(c.preload) for c in clients}
        for client in clients:
            st = self.stats[client.label]
            try:
                results[client.label].result()
                self._index[client.label] = InstanceIndex(client)
            except Exception as exc:
                st.error = f"{type(exc).__name__}: {exc}"
                log.warning("[%s] preload failed — no match or write for this instance: %s", client.label, exc)
                self._emit(f"[xenotag] {client.label}: preload failed ({st.error}) — skipped")
                continue
            st.available = True
            st.objects = len(client.objects)
            st.labels = len(client.tags)
            st.managed_labels = sum(1 for label in client.tags if label.startswith(self.prefixes))

    def resolve_all(self, items: Iterable[dict]) -> None:
        """Resolve every item's owners, and refuse any object two items claim."""
        claims: dict[tuple[str, int], list[dict]] = {}
        for item in items:
            owners = self._resolve(item)
            for owner in owners:
                claims.setdefault((owner.client.label, owner.object_id), []).append(item)
        for (label, object_id), claimants in claims.items():
            if len(claimants) > 1:
                self._refused.add((label, object_id))
                st = self.stats[label]
                st.claimed_twice += 1
                st.example("claimed_twice", {"object_id": object_id, "items": [c.get("Name") for c in claimants]})

    def _resolve(self, item: dict) -> list[_Owner]:
        item_id = item.get("Id", "")
        if item_id in self._owners:
            return self._owners[item_id]
        kind = kind_for(item)
        owners: list[_Owner] = []
        statuses: list[str] = []
        self.items[f"{kind or 'other'}_items"] += 1
        for client in self.clients.get(kind or "", []):
            index = self._index.get(client.label)
            if index is None:
                continue
            match = index.resolve(item)
            statuses.append(match.status)
            st = self.stats[client.label]
            name = item.get("Name", item_id)
            if match.status in (OWNED, ELSEWHERE):
                st.matched_by[match.by] += 1
            if match.status == OWNED:
                st.owned += 1
                owners.append(_Owner(client, match.object_id, match.by))
            elif match.status == ELSEWHERE:
                st.elsewhere += 1
                st.example(
                    "elsewhere",
                    {
                        "item": name,
                        "item_path": item.get("Path"),
                        "arr_path": client.objects[match.object_id].get("path"),
                    },
                )
            elif match.status == AMBIGUOUS:
                st.ambiguous += 1
                st.example(
                    "ambiguous",
                    {"item": name, "candidates": [(c, client.objects[c].get("title")) for c in match.candidates]},
                )
        self._owners[item_id] = owners
        if kind:
            self._count_ownership(item, kind, owners, statuses)
        return owners

    def _count_ownership(self, item: dict, kind: str, owners: list[_Owner], statuses: list[str]) -> None:
        if len(owners) > 1:
            self.items[f"{kind}_multi_owner"] += 1
        if owners:
            self.items[f"{kind}_owned"] += 1
            return
        keys = [k for c in self.clients[kind] for k, _ in c.MATCH_KEYS] or [k for k, _ in _default_keys(kind)]
        provider_ids = item.get("ProviderIds") or {}
        if not any(_norm_id(provider_ids.get(k)) for k in keys):
            reason = "anidb_only" if _norm_id(provider_ids.get("AniDB")) else "no_provider_ids"
        elif AMBIGUOUS in statuses:
            reason = "ambiguous"
        elif ELSEWHERE in statuses:
            reason = "elsewhere"
        else:
            reason = "not_in_any_instance"
        self.items[f"{kind}_unowned_{reason}"] += 1
        bucket = self.unowned_examples.setdefault(f"{kind}_{reason}", [])
        if len(bucket) < _EXAMPLES:
            bucket.append({"item": item.get("Name"), "path": item.get("Path"), "provider_ids": provider_ids})

    # --- ratings ---

    def certification(self, item: dict) -> str:
        """The certification the item's owning object(s) carry; "" if none or if they disagree."""
        owners = self._resolve(item)
        certs = {(o.client.objects[o.object_id].get("certification") or "").strip() for o in owners}
        certs.discard("")
        if len(certs) > 1:
            self.cert["conflicts"] += 1
            return ""
        return certs.pop() if certs else ""

    def fallback_rating(self, item: dict) -> str:
        """Always counted; returned only when ``certification_fallback`` is on."""
        if item.get("OfficialRating"):
            return ""
        self.cert["blank_rating"] += 1
        cert = self.certification(item)
        if cert:
            self.cert["would_fill"] += 1
            self.cert["by_value"][cert] += 1
            if len(self.cert["examples"]) < _EXAMPLES:
                self.cert["examples"].append({"item": item.get("Name"), "certification": cert})
        return cert if self.cert_fallback else ""

    # --- tags ---

    def sync_item(self, item: dict, tags_by_kind: dict[str, list[str]]) -> None:
        """Plan (dry run) or write (live) the managed tags for every owner of ``item``."""
        for owner in self._resolve(item):
            st = self.stats[owner.client.label]
            if (owner.client.label, owner.object_id) in self._refused:
                continue
            desired = tags_by_kind.get(owner.client.KIND, [])
            try:
                if self.live:
                    self._write(owner, item, desired, st)
                else:
                    cached = owner.client.objects[owner.object_id]
                    self._account(plan_tags(owner.client, cached.get("tags"), desired, self.prefixes), st, item)
            except Exception as exc:
                st.write_errors += 1
                log.error("[%s] %s: tag sync error: %s", owner.client.label, item.get("Name"), exc)
                if self.live:
                    self._halt(f"{owner.client.label}: {type(exc).__name__}: {exc}")

    def _account(self, plan: TagPlan, st: _InstanceStats, item: dict) -> None:
        st.synced += 1
        st.labels_rejected.update(plan.rejected)
        if plan.rejected:
            st.example("labels_rejected", {"item": item.get("Name"), "labels": plan.rejected})
        if not plan.changes:
            st.already_current += 1
            return
        st.would_change += 1
        st.tags_to_add += len(plan.add) + len(plan.create)
        st.tags_to_remove += len(plan.remove)
        st.labels_to_create.update(plan.create)

    def _halt(self, reason: str) -> None:
        if self.halted is None:
            self.halted = reason
            log.error("*arr tag writes HALTED for the rest of this scan: %s", reason)
            self._emit(f"[xenotag] *arr tag writes HALTED for the rest of this scan: {reason}")

    def _write(self, owner: _Owner, item: dict, desired: list[str], st: _InstanceStats) -> None:
        client = owner.client
        cached_plan = plan_tags(client, client.objects[owner.object_id].get("tags"), desired, self.prefixes)
        if not cached_plan.changes:
            self._account(cached_plan, st, item)
            return
        if self.halted:
            st.skipped_halted += 1
            self._account(cached_plan, st, item)
            return
        # The cache is only a hint. Re-fetch, and re-check that this is still the
        # object we matched, in the item's folder, before touching it.
        try:
            before: dict | None = client.get_object(owner.object_id)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
            before = None  # deleted since the preload
        if before is None or not self._still_owned(before, item, owner):
            st.changed_since_preload += 1
            st.example("changed_since_preload", {"item": item.get("Name"), "object_id": owner.object_id})
            return
        plan = plan_tags(client, before.get("tags"), desired, self.prefixes)
        self._account(plan, st, item)
        if not plan.changes:
            return
        add = list(plan.add)
        intended = set(plan.desired_ids)
        for label in plan.create:
            try:
                tag_id = client.create_tag(label)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 400:
                    raise
                st.labels_rejected[label] += 1
                log.warning("[%s] refused tag label %r: %s", client.label, label, exc.response.text[:200])
                continue
            st.labels_created += 1
            add.append(tag_id)
            intended.add(tag_id)
        if add:
            client.edit_tags(owner.object_id, add, "add")
        if plan.remove:
            client.edit_tags(owner.object_id, plan.remove, "remove")
        after = client.get_object(owner.object_id)
        by_id = {tag_id: label for label, tag_id in client.tags.items()}
        problems = readback_problems(before, after, intended, lambda t: by_id.get(t, "").startswith(self.prefixes))
        if problems:
            st.readback_failures += 1
            st.example(
                "readback_failures", {"item": item.get("Name"), "object_id": owner.object_id, "problems": problems}
            )
            log.error(
                "[%s] READ-BACK MISMATCH on %s (id %s): %s", client.label, item.get("Name"), owner.object_id, problems
            )
            self._halt(f"{client.label}: read-back mismatch on {item.get('Name')!r}: {problems[0]}")
            return
        client.objects[owner.object_id] = after
        st.written += 1

    def _still_owned(self, obj: dict, item: dict, owner: _Owner) -> bool:
        field_name = dict(owner.client.MATCH_KEYS)[owner.by]
        provider_ids = item.get("ProviderIds") or {}
        return _norm_id(obj.get(field_name)) == _norm_id(provider_ids.get(owner.by)) and _norm_path(
            obj.get("path")
        ) in item_folders(item)

    # --- report ---

    def note_no_probe(self, item: dict) -> None:
        self.items["no_probe_record"] += 1

    def report(self) -> dict:
        cert = dict(self.cert)
        cert["by_value"] = dict(cert["by_value"].most_common())
        cert["enabled"] = self.cert_fallback
        return {
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "source": self.source,
            "mode": self.mode,
            "halted": self.halted,
            "instances": {label: st.as_dict() for label, st in self.stats.items()},
            "items": dict(sorted(self.items.items())),
            "unowned_examples": self.unowned_examples,
            "certification_fallback": cert,
        }

    def summary_lines(self) -> list[str]:
        verb = "written" if self.live else "WOULD change (dry run)"
        lines = []
        for label, st in self.stats.items():
            if not st.available:
                lines.append(f"[xenotag] {label}: unavailable — {st.error or 'not preloaded'}")
                continue
            lines.append(
                f"[xenotag] {label}: owned {st.owned}, id-matched elsewhere {st.elsewhere}, ambiguous {st.ambiguous},"
                f" {verb} {st.written if self.live else st.would_change}, current {st.already_current},"
                f" labels to create {len(st.labels_to_create)}, labels refused {len(st.labels_rejected)}"
            )
        if self.halted:
            lines.append(f"[xenotag] *arr writes HALTED: {self.halted}")
        return lines


def _default_keys(kind: str) -> tuple[tuple[str, str], ...]:
    from .clients.radarr import RadarrClient
    from .clients.sonarr import SonarrClient

    return {"sonarr": SonarrClient.MATCH_KEYS, "radarr": RadarrClient.MATCH_KEYS}[kind]


# ---------------------------------------------------------------------------
# The last report: kept in memory and in a JSON file beside state.db. Not in
# the database -- a new table would be a schema change, and those are gated on
# roadmap I3.
# ---------------------------------------------------------------------------

_last_report: dict | None = None


def report_path() -> Path:
    explicit = os.environ.get("ARR_SYNC_REPORT")
    if explicit:
        return Path(explicit)
    return Path(os.environ.get("STATE_DB", "/config/state.db")).with_name("arr-sync-report.json")


def store_report(report: dict) -> None:
    global _last_report
    _last_report = report
    path = report_path()
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(report, indent=2, default=str))
        tmp.replace(path)
    except OSError as exc:
        log.warning("Could not write the *arr sync report to %s: %s", path, exc)


def load_report() -> dict | None:
    if _last_report is not None:
        return _last_report
    try:
        return json.loads(report_path().read_text())
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# CLI: python -m app.arr_sync --dry-run
# ---------------------------------------------------------------------------


def _print_summary(report: dict) -> None:
    print(f"*arr tag sync — {report['mode']} from {report['source']}, generated {report['generated_at']}")
    for label, st in report["instances"].items():
        if not st["available"]:
            print(f"  {label}: UNAVAILABLE — {st['error']}")
            continue
        print(
            f"  {label}: {st['objects']} objects, {st['labels']} labels ({st['managed_labels']} managed) |"
            f" matched by {st['matched_by']} | owned {st['owned']}, elsewhere {st['elsewhere']},"
            f" ambiguous {st['ambiguous']}, claimed twice {st['claimed_twice']}"
        )
        print(
            f"      would change {st['would_change']} of {st['synced']} (current {st['already_current']});"
            f" +{st['tags_to_add']} / -{st['tags_to_remove']} tags;"
            f" labels to create {len(st['labels_to_create'])}; labels refused {st['labels_rejected'] or '{}'}"
        )
    print(f"  items: {report['items']}")
    cert = report["certification_fallback"]
    print(
        f"  certification fallback (enabled={cert['enabled']}): blank rating {cert['blank_rating']},"
        f" would fill {cert['would_fill']} {cert['by_value']}, conflicts {cert['conflicts']}"
    )
    if report.get("guard"):
        print(f"  transport guard: {report['guard']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m app.arr_sync",
        description="Sonarr/Radarr tag sync report (roadmap B5). Read-only: every client is behind a "
        "transport that refuses anything but GET.",
    )
    ap.add_argument("--dry-run", action="store_true", help="match and count against the live instances (GETs only)")
    ap.add_argument("--self-test", action="store_true", help="prove the read-only guard in both directions and exit")
    ap.add_argument("--config", type=Path, help="config.yml (default: $CONFIG_PATH)")
    ap.add_argument("--db", type=Path, help="state.db to read probe results from, opened read-only (copy it first)")
    ap.add_argument("--out", type=Path, help="write the full JSON report here")
    args = ap.parse_args(argv)

    from .clients.readonly import self_test

    failures = self_test()
    print(
        "read-only guard self-test:",
        "PASS (GET passes; POST/PUT/DELETE/PATCH raise before sending)" if not failures else "FAIL",
    )
    for failure in failures:
        print("  -", failure)
    if failures:
        return 2
    if args.self_test:
        return 0
    if not args.dry_run:
        ap.error("nothing to do: pass --dry-run or --self-test")

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s — %(message)s")
    from .config import load_config
    from .pipeline import run_arr_dry_run

    cfg = load_config(args.config)
    report = run_arr_dry_run(cfg, db_path=args.db, store=False)
    _print_summary(report)
    if args.out:
        args.out.write_text(json.dumps(report, indent=2, default=str))
        print(f"  full report: {args.out}")
    return 1 if report.get("guard", {}).get("blocked") else 0


if __name__ == "__main__":
    sys.exit(main())
