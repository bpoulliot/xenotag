from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from . import metrics
from .arr_sync import MODE_DRY_RUN, MODE_LIVE, ArrTagSync, _norm_path, item_folders, store_report
from .clients.jellyfin import TAG_READ_BATCH, JellyfinClient
from .clients.radarr import RadarrClient
from .clients.readonly import ReadOnlyTransport
from .clients.sonarr import SonarrClient
from .config import AppConfig
from .deleted_items import run_deleted_items
from .overlay import BadgeGroup, apply_overlay, order_pills_by_language
from .scanner import RESOLUTION_RULE_VERSION, AudioTrack, MediaInfo, SubTrack, probe_file
from .state import (
    MediaState,
    ScanError,
    clear_scan_errors,
    finish_scan_run,
    get_meta,
    get_session,
    read_only_session,
    set_meta,
    start_scan_run,
    upsert_media_state,
    upsert_scan_error,
)
from .tagger import TAG_VOCABULARY, build_tags

log = logging.getLogger(__name__)


class ScanProgress:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.running = False
        self.cancelled = False
        self.total = 0
        self.done = 0
        self.current_item = ""
        self.error: str | None = None
        self.log_lines: list[str] = []
        self._callbacks: list[Callable[[str], None]] = []

    def try_start(self) -> bool:
        with self._lock:
            if self.running:
                return False
            self.running = True
            self.cancelled = False
            self.total = 0
            self.done = 0
            self.current_item = ""
            self.error = None
            return True

    def cancel(self) -> bool:
        with self._lock:
            if not self.running:
                return False
            self.cancelled = True
            return True

    def finish(self, error: str | None = None) -> None:
        with self._lock:
            self.running = False
            self.error = error

    def subscribe(self, cb: Callable[[str], None]) -> None:
        with self._lock:
            self._callbacks.append(cb)

    def unsubscribe(self, cb: Callable[[str], None]) -> None:
        with self._lock:
            try:
                self._callbacks.remove(cb)
            except ValueError:
                pass

    def emit(self, msg: str) -> None:
        with self._lock:
            self.log_lines.append(msg)
            if len(self.log_lines) > 200:
                self.log_lines = self.log_lines[-200:]
            cbs = list(self._callbacks)
        for cb in cbs:
            try:
                cb(msg)
            except Exception:  # noqa: S110
                pass


progress = ScanProgress()


_TAG_CONFIG_KEY = "tag_config_hash"


def _tag_config_hash(cfg: AppConfig) -> str:
    """What the tags depend on; when it differs from the stored hash, the next scan is a full re-tag.

    A config change moves it, and so do two code versions on purpose: a tag
    respelling (``TAG_VOCABULARY``) and a resolution-rule change
    (``RESOLUTION_RULE_VERSION``, roadmap B13). Bumping either makes the upgrade's
    first scan a full re-tag -- it re-probes every file and writes every changed
    tag, on Jellyfin and on every live *arr -- because otherwise an unchanged file
    keeps what the old code gave it. Nothing else in a release forces one.
    """
    dest = cfg.tags.destinations
    raw = (
        f"{cfg.tags.managed_prefix}|{cfg.tags.dual_audio_tag}|{cfg.tags.multi_audio_tag}"
        f"|legacy:{','.join(sorted(cfg.tags.legacy_prefixes))}"
        f"|video:{','.join(sorted(dest.video))}"
        f"|audio:{','.join(sorted(dest.audio))}"
        f"|subtitles:{','.join(sorted(dest.subtitles))}"
        f"|rating:{','.join(sorted(dest.rating))}"
        # Code, not config: an upgrade that respells existing tags (B9) must force
        # one full re-tag, or unchanged files keep the old spelling forever.
        f"|vocab:{TAG_VOCABULARY}"
        # Code too: media_state keeps the class, not the frame size, so a new rule
        # reaches an unchanged file only through a re-probe (B13).
        f"|resolution-rule:{RESOLUTION_RULE_VERSION}"
    )
    # Appended only when switched ON, so the shipped defaults hash the same with
    # them off. Turning either on forces a full re-tag: the next scan re-tags
    # everything, which is what going live means.
    if cfg.arr_sync.mode == MODE_LIVE:
        raw += "|arr:live"
    if cfg.arr_sync.certification_fallback:
        raw += "|cert-fallback"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _clients_from_config(
    cfg: AppConfig, *, read_only: bool = False, guards: list[ReadOnlyTransport] | None = None
) -> tuple[JellyfinClient, list[SonarrClient], list[RadarrClient]]:
    """Build the clients. Unless *arr writes are live, the *arr clients can only read.

    The *arr guard is the transport, not an ``if``: while ``arr_sync.mode`` is
    not ``live``, a Sonarr/Radarr client physically cannot send a POST or PUT.
    ``read_only`` extends that to Jellyfin too (the dry run). Every guard made
    is appended to ``guards``, so a caller can report what went over the wire.
    """
    arr_guarded = read_only or cfg.arr_sync.mode != MODE_LIVE

    def guard(on: bool) -> ReadOnlyTransport | None:
        if not on:
            return None
        transport = ReadOnlyTransport()
        if guards is not None:
            guards.append(transport)
        return transport

    def arr_transport() -> ReadOnlyTransport | None:
        return guard(arr_guarded)

    jf = JellyfinClient(cfg.jellyfin.url, cfg.jellyfin.api_key, transport=guard(read_only))
    sonarrs = [
        SonarrClient(inst.url, inst.api_key, inst.name, transport=arr_transport()) for inst in cfg.sonarr.instances
    ]
    radarrs = [
        RadarrClient(inst.url, inst.api_key, inst.name, transport=arr_transport()) for inst in cfg.radarr.instances
    ]
    return jf, sonarrs, radarrs


def _close_clients(jf: JellyfinClient, sonarrs: list[SonarrClient], radarrs: list[RadarrClient]) -> None:
    jf.close()
    for c in sonarrs:
        c.close()
    for c in radarrs:
        c.close()


def _tags_match_ci(a: list[str], b: list[str]) -> bool:
    """Equal sets of tags once case is folded (roadmap B12(a)) -- Jellyfin's own ``Tags=`` compare."""
    return {t.casefold() for t in a} == {t.casefold() for t in b}


def _tag_drift(current: list[str], row: MediaState | None, prefix: str) -> tuple[list[str], list[str]] | None:
    """Roadmap U9: managed tags Jellyfin carries that xenotag did not write, and the reverse.

    Compares Jellyfin's current ``prefix`` tags with the row's ``tags_applied``
    -- what xenotag last wrote -- never with the tags about to be written, so a
    vocabulary change (B9's respelling, a new tag) is not drift: an item nobody
    touched still carries exactly its row's old spelling. No row, or a row with
    nothing recorded, is a first write and returns None; so does no difference.

    The compare is case-insensitive (roadmap B12(a)): the *arrs' NFO merge
    respells existing tags in lowercase, and Jellyfin itself matches tags
    without regard to case, so that is not drift. A tag that is genuinely
    missing or extra is still reported, spelled as Jellyfin and the row
    actually carry it.
    """
    if row is None or row.tags_applied is None:
        return None
    try:
        applied = json.loads(row.tags_applied)
    except ValueError:
        return None
    had = {t for t in applied if isinstance(t, str) and t.startswith(prefix)}
    has = {t for t in current if t.startswith(prefix)}
    if _tags_match_ci(had, has):
        return None
    had_cf = {t.casefold() for t in had}
    has_cf = {t.casefold() for t in has}
    added = sorted(t for t in has if t.casefold() not in had_cf)
    missing = sorted(t for t in had if t.casefold() not in has_cf)
    return added, missing


def _warn_tag_drift(session: object, jf: JellyfinClient, item: dict, prefix: str) -> None:
    """Log one WARNING line if the item's managed tags changed since xenotag wrote them.

    Observes only; the write that follows replaces them as before. The tags are
    ``jf.get_tags(item)`` -- the copy ``set_managed_tags()`` works from -- so no
    extra request is made, and whatever feeds that write feeds this check. In a
    scan that copy is the ``Ids=`` read ``_with_current_tags()`` made (B18).
    """
    item_id = item.get("Id", "")
    try:
        row = session.get(MediaState, f"jellyfin:{item_id}")
        drift = _tag_drift(jf.get_tags(item), row, prefix)
    except Exception as exc:  # a detector must never stop the write
        log.debug("Tag drift check failed for %s: %s", item_id, exc)
        return
    if drift is None:
        return
    added, missing = drift
    log.warning(
        "Tag drift: %s (%s) — Jellyfin has %s tags xenotag did not write %s and lacks %s; this write replaces them",
        item.get("Name", item_id),
        item_id,
        prefix,
        added,
        missing,
    )
    metrics.tag_drift()


class _TagFallbacks:
    """Items whose current tags could not be read by ``Ids=`` this scan (B18); logs once."""

    def __init__(self) -> None:
        self.count = 0

    def note(self, item_ids: list[str], reason: str) -> None:
        if not item_ids:
            return
        if self.count == 0:
            log.warning(
                "Current tags unavailable for %s (%s); using the listing's copy, which can be stale "
                "(roadmap B18). Further fallbacks this scan are only counted.",
                item_ids[0],
                reason,
            )
        self.count += len(item_ids)


def _with_current_tags(jf: JellyfinClient, items: list[dict], fallbacks: _TagFallbacks) -> list[dict]:
    """Return copies of ``items`` whose ``Tags`` are Jellyfin's current ones (roadmap B18).

    The scan's items come from the recursive listing, which can serve stale
    ``Tags``; ``set_managed_tags()`` keeps the non-managed ones and U9's drift
    check compares the managed ones, so both must see the ``Ids=`` read. An id
    the read does not return -- or a read that fails -- keeps the listing's
    copy and is noted in ``fallbacks``; it never fails the item.
    """
    ids = [item.get("Id", "") for item in items]
    try:
        current = jf.get_current_tags(ids)
    except Exception as exc:
        fallbacks.note(ids, f"read failed: {exc}")
        return items
    fallbacks.note([i for i in ids if i not in current], "not returned by /Items?Ids=")
    return [{**item, "Tags": current[item.get("Id", "")]} if item.get("Id", "") in current else item for item in items]


# Roadmap B17: in B9's re-tag Jellyfin re-saved 22 of 9,340 items with their old
# tags 60-700 ms after the refresh xenotag requests. A write is read back only once
# this long has passed since the item's last write or refresh -- about three times
# the worst delay seen. Bounded on purpose; a loaded production Jellyfin may need more.
TAG_READBACK_SETTLE_S = 2.0


@dataclass
class _PendingRecord:
    """An item whose tag write returned, waiting for its read-back before it is recorded (B17)."""

    item: dict
    written: list[str]
    fallback_rating: str
    # upsert_media_state() arguments other than tags_applied; None records no row
    # (the reconciliation pass, B12(b): the row's tags_applied is what it restores).
    record: dict | None
    settled_from: float  # time.monotonic() of the item's last write or refresh


class _Readback:
    """One scan's read-back outcomes (B17); logged once at the end."""

    def __init__(self) -> None:
        self.stuck = 0  # read back as written the first time
        self.fixed = 0
        self.unresolved = 0
        self.unrecorded = 0

    def summary(self) -> str | None:
        if not (self.fixed or self.unresolved or self.unrecorded):
            return None
        return (
            f"Tag read-back: {self.fixed} undone write(s) fixed by a retry, {self.unresolved} still wrong "
            f"(recorded as read), {self.unrecorded} not recorded (the next scan retries them) (roadmap B17)"
        )


def _settle(since: float) -> None:
    wait = since + TAG_READBACK_SETTLE_S - time.monotonic()
    if wait > 0:
        time.sleep(wait)


def _read_back(jf: JellyfinClient, pending: list[_PendingRecord], readback: _Readback) -> dict[str, list[str]] | None:
    ids = [p.item.get("Id", "") for p in pending]
    try:
        return jf.get_current_tags(ids)
    except Exception as exc:
        log.warning(
            "Tag read-back failed for %d item(s) (%s); recording none of them, the next scan retries (roadmap B17)",
            len(ids),
            exc,
        )
        readback.unrecorded += len(ids)
        return None


def _record(session: object, p: _PendingRecord, tags_applied: list[str]) -> None:
    if p.record is not None:
        upsert_media_state(session, tags_applied=tags_applied, **p.record)


def _record_after_readback(
    jf: JellyfinClient, session: object, cfg: AppConfig, pending: list[_PendingRecord], readback: _Readback
) -> None:
    """Read back what ``pending`` wrote, write once more what Jellyfin undid, and record (roadmap B17).

    One ``Ids=`` read (B18) for all of ``pending``, after the settle delay. An
    item whose managed tags read back as written is recorded as written. One that
    does not is written again -- from the tags just read, so a non-managed tag
    gained meanwhile is kept -- and read again; if it is still wrong, the tags
    that were *read* are recorded, with one WARNING and a count. An item that
    cannot be read, or whose retry raises, is not recorded, so the next scan
    reaches it again.

    "Read back as written" is case-insensitive (roadmap B12(a)): the *arrs'
    NFO merge respells existing tags in lowercase, and Jellyfin matches tags
    without regard to case, so a read-back that differs from what was sent
    only by case is a match, and what was *sent* is recorded, not what was
    read.
    """
    if not pending:
        return
    prefix = cfg.tags.managed_prefix

    def managed(tags: list[str]) -> list[str]:
        return [t for t in tags if t.startswith(prefix)]

    def missing(p: _PendingRecord) -> None:
        log.info(
            "Tag read-back: %s (%s) not returned by /Items?Ids=; not recorded", p.item.get("Name"), p.item.get("Id")
        )
        readback.unrecorded += 1

    _settle(max(p.settled_from for p in pending))
    current = _read_back(jf, pending, readback)
    if current is None:
        return
    undone: list[tuple[_PendingRecord, list[str]]] = []
    for p in pending:
        tags = current.get(p.item.get("Id", ""))
        if tags is None:
            missing(p)
        elif _tags_match_ci(managed(tags), managed(p.written)):
            readback.stuck += 1
            _record(session, p, p.written)
        else:
            undone.append((p, tags))
    if not undone:
        return

    retried: list[_PendingRecord] = []
    for p, tags in undone:
        try:
            jf.set_managed_tags(
                p.item.get("Id", ""),
                {**p.item, "Tags": tags},
                prefix,
                p.written,
                fallback_rating=p.fallback_rating,
                legacy_prefixes=cfg.tags.legacy_prefixes,
            )
        except Exception as exc:
            log.warning("Jellyfin tag retry error for %s: %s; not recorded", p.item.get("Name"), exc)
            readback.unrecorded += 1
            continue
        retried.append(p)
    if not retried:
        return
    _settle(time.monotonic())
    again = _read_back(jf, retried, readback)
    if again is None:
        return
    for p in retried:
        tags = again.get(p.item.get("Id", ""))
        if tags is None:
            missing(p)
            continue
        read = managed(tags)
        wanted = managed(p.written)
        if _tags_match_ci(read, wanted):
            readback.fixed += 1
            metrics.tag_writeback_mismatch(metrics.MISMATCH_FIXED)
            _record(session, p, p.written)
            continue
        wanted_cf = {t.casefold() for t in wanted}
        read_cf = {t.casefold() for t in read}
        log.warning(
            "Tag write did not stick: %s (%s) — after one retry Jellyfin lacks %s and has %s not written; "
            "recording what it has (roadmap B17)",
            p.item.get("Name"),
            p.item.get("Id"),
            sorted(t for t in wanted if t.casefold() not in read_cf),
            sorted(t for t in read if t.casefold() not in wanted_cf),
        )
        readback.unresolved += 1
        metrics.tag_writeback_mismatch(metrics.MISMATCH_UNRESOLVED)
        _record(session, p, read)


def _process_one_item(
    jf: JellyfinClient,
    arr: ArrTagSync,
    session: object,
    cfg: AppConfig,
    item: dict,
    file_path: str,
    item_root: str,
    mtime: float,
    info: MediaInfo,
    pending: list[_PendingRecord] | None = None,
) -> bool:
    """Tag, overlay, and persist one media item. Returns True if an image was modified.

    The row is written only after the tags are read back (roadmap B17): at once
    for a single item, or -- when the scan passes ``pending`` -- by the scan, for
    its whole batch. A tag write that raises records nothing for the item.
    """
    item_id = item.get("Id", "")
    name = item.get("Name", item_id)
    prefix = cfg.tags.managed_prefix
    legacy_prefixes = cfg.tags.legacy_prefixes

    jf_rating = item.get("OfficialRating") or ""
    # Counted either way; non-empty only when arr_sync.certification_fallback is on.
    arr_cert = arr.fallback_rating(item) if not jf_rating else ""
    content_rating = jf_rating or arr_cert or None

    jf_tags = build_tags(
        info.resolution,
        info.video_codec,
        info.hdr_type,
        info.audio_tracks,
        info.subtitle_tracks,
        content_rating,
        cfg.tags,
        destination="jellyfin",
        field_order=info.field_order,
    )
    sonarr_tags = build_tags(
        info.resolution,
        info.video_codec,
        info.hdr_type,
        info.audio_tracks,
        info.subtitle_tracks,
        content_rating,
        cfg.tags,
        destination="sonarr",
        field_order=info.field_order,
    )
    radarr_tags = build_tags(
        info.resolution,
        info.video_codec,
        info.hdr_type,
        info.audio_tracks,
        info.subtitle_tracks,
        content_rating,
        cfg.tags,
        destination="radarr",
        field_order=info.field_order,
    )

    _warn_tag_drift(session, jf, item, prefix)
    try:
        jf.set_managed_tags(item_id, item, prefix, jf_tags, fallback_rating=arr_cert, legacy_prefixes=legacy_prefixes)
        wrote = True
    except Exception as exc:
        log.warning("Jellyfin tag error for %s: %s; not recorded, the next scan retries (roadmap B17)", name, exc)
        wrote = False

    # Resolved per instance by provider id + folder; a dry run only counts.
    arr.sync_item(item, {"sonarr": sonarr_tags, "radarr": radarr_tags})

    item_folder = Path(item_root) if item_root else Path(file_path).parent
    if not item_folder.is_dir():
        item_folder = Path(file_path).parent

    groups, rating_group = _make_badge_groups(info, content_rating, cfg)
    modified_path = None
    image_modified = False
    if groups or rating_group:
        try:
            modified_path = apply_overlay(item_folder, groups, rating_group, cfg.image)
            if modified_path:
                image_modified = True
                jf.refresh_item(item_id)
        except Exception as exc:
            log.warning("Overlay error for %s: %s", name, exc)

    if not wrote:
        return image_modified
    record = _PendingRecord(
        item=item,
        written=jf_tags,
        fallback_rating=arr_cert,
        record={
            "item_id": f"jellyfin:{item_id}",
            "source": "jellyfin",
            "file_path": file_path,
            "resolution": info.resolution,
            "languages": info.languages,
            "image_path": str(modified_path) if modified_path else None,
            "file_mtime": mtime,
            "video_codec": info.video_codec,
            "hdr_type": info.hdr_type,
            "audio_tracks": [{"lang": t.lang, "codec": t.codec} for t in info.audio_tracks],
            "subtitle_tracks": [
                {"lang": t.lang, "format": t.format, "embedded": t.embedded} for t in info.subtitle_tracks
            ],
            "content_rating": content_rating,
            "field_order": info.field_order,
        },
        settled_from=time.monotonic(),
    )
    if pending is None:
        _record_after_readback(jf, session, cfg, [record], _Readback())
    else:
        pending.append(record)
    return image_modified


def _get_file_mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _passes_path_filter(file_path: str, filters: list[str]) -> bool:
    if not filters:
        return True
    return any(file_path.startswith(f) for f in filters)


def _filter_items(items: list[dict], cfg: AppConfig) -> list[dict]:
    if not cfg.scan.path_filters:
        return items
    return [
        i
        for i in items
        if _passes_path_filter(
            (i.get("MediaSources") or [{}])[0].get("Path", i.get("Path", "")),
            cfg.scan.path_filters,
        )
    ]


def _make_badge_groups(
    info: MediaInfo,
    content_rating: str | None,
    cfg: AppConfig,
) -> tuple[list[BadgeGroup], BadgeGroup | None]:
    """Build BadgeGroup list and optional rating group from MediaInfo + image config."""
    img_cfg = cfg.image
    dest = cfg.tags.destinations

    groups: list[BadgeGroup] = []

    # Video group (shown if "poster" is in video destinations and show flag is on)
    if img_cfg.show_video_badges and "poster" in dest.video:
        video_labels: list[str] = []
        if info.resolution and info.resolution != "unknown":
            video_labels.append(info.resolution)
        if info.video_codec:
            video_labels.append(info.video_codec)
        if info.hdr_type:
            video_labels.append(info.hdr_type)
        if video_labels:
            groups.append(
                BadgeGroup(
                    video_labels, img_cfg.video_badge_color, img_cfg.badge_text_color, img_cfg.backup_video_badge_color
                )
            )

    # Audio group — codec-first, languages grouped under each codec
    # e.g. [DTS-HD EN JA] [AC-3 DE] instead of [EN DTS-HD] [JA DTS-HD] [DE AC-3]
    if img_cfg.show_audio_badges and "poster" in dest.audio:
        codec_langs: dict[str, list[str]] = {}
        for t in info.audio_tracks:
            langs = codec_langs.setdefault(t.codec, [])
            if t.lang and t.lang != "UND":
                langs.append(t.lang)
        audio_labels = order_pills_by_language(codec_langs, img_cfg.prefer_languages)
        if audio_labels:
            groups.append(
                BadgeGroup(
                    audio_labels, img_cfg.audio_badge_color, img_cfg.badge_text_color, img_cfg.backup_audio_badge_color
                )
            )

    # Subtitle group — format-first, languages grouped under each format
    # e.g. [PGS EN JA IT] [SRT EN] instead of [EN PGS] [JA PGS] [IT PGS] [EN SRT]
    if img_cfg.show_sub_badges and "poster" in dest.subtitles:
        fmt_langs: dict[str, list[str]] = {}
        seen_lang_fmt: set[tuple[str, str]] = set()
        for t in info.subtitle_tracks:
            key = (t.lang, t.format)
            if key in seen_lang_fmt:
                continue
            seen_lang_fmt.add(key)
            langs = fmt_langs.setdefault(t.format, [])
            if t.lang and t.lang != "UND":
                langs.append(t.lang)
        sub_labels = order_pills_by_language(fmt_langs, img_cfg.prefer_languages)
        if sub_labels:
            groups.append(
                BadgeGroup(
                    sub_labels, img_cfg.sub_badge_color, img_cfg.badge_text_color, img_cfg.backup_sub_badge_color
                )
            )

    # Rating badge -- its own corner, image.rating_position (roadmap B10)
    rating_group: BadgeGroup | None = None
    if img_cfg.show_rating_badge and content_rating and "poster" in dest.rating:
        rating_group = BadgeGroup(
            [f"Rated {content_rating}"],
            img_cfg.rating_badge_color,
            img_cfg.badge_text_color,
            img_cfg.backup_rating_badge_color,
        )

    return groups, rating_group


def _record_scan_error(session: Session, item_id: str, name: str, file_path: str, error_type: str) -> None:
    upsert_scan_error(session, item_id, name, file_path, error_type)
    metrics.scan_error(error_type)


def _run_scan(cfg: AppConfig, incremental: bool) -> None:
    scan_type = "incremental" if incremental else "full"
    started = time.time()
    progress.emit(f"[xenotag] Starting {scan_type} scan…")

    jf, sonarrs, radarrs = _clients_from_config(cfg)

    if not sonarrs:
        progress.emit("[xenotag] No Sonarr instances configured — skipping Sonarr tagging")
    if not radarrs:
        progress.emit("[xenotag] No Radarr instances configured — skipping Radarr tagging")

    session = get_session()
    run = start_scan_run(session, scan_type)

    current_hash = _tag_config_hash(cfg)
    stored_hash = get_meta(session, _TAG_CONFIG_KEY)
    tag_config_changed = stored_hash != current_hash
    if tag_config_changed and incremental:
        progress.emit("[xenotag] Tag config changed — forcing full re-tag of all items")
        incremental = False

    if not incremental:
        clear_scan_errors(session)

    try:
        items = jf.get_items(cfg.jellyfin.library_ids or None)
    except Exception as exc:
        msg = f"[xenotag] FATAL: Could not fetch Jellyfin items: {exc}"
        progress.emit(msg)
        log.error(msg)
        session.close()
        _close_clients(jf, sonarrs, radarrs)
        progress.finish(error=str(exc))
        metrics.scan_finished(scan_type, metrics.FAILED)
        return

    if cfg.scan.path_filters:
        before = len(items)
        items = _filter_items(items, cfg)
        progress.emit(f"[xenotag] Path filter: {before} → {len(items)} items")

    progress.total = len(items)
    progress.emit(f"[xenotag] {len(items)} items to process")

    tagged = 0
    images_modified = 0

    # Preload the Sonarr/Radarr catalogues (GETs) and resolve every item to its
    # series/movie on each instance up front -- the claim check needs them all.
    arr = ArrTagSync(cfg, sonarrs, radarrs, source=f"{scan_type} scan", emit=progress.emit)
    if sonarrs or radarrs:
        mode = "LIVE — tags will be written" if arr.live else "dry run — nothing is written"
        progress.emit(f"[xenotag] Preloading Sonarr/Radarr catalogues ({mode})…")
        arr.prepare()
        arr.resolve_all(items)

    # Phase 1a: pre-resolve episode paths for ALL series in parallel.
    # We always resolve regardless of whether the series folder exists locally — the folder
    # may have moved (e.g. mount restructure) while Jellyfin still knows the episode paths.
    series_ids_needing_path: list[str] = [item.get("Id", "") for item in items if item.get("Type") == "Series"]

    resolved_episode_paths: dict[str, str] = {}
    if series_ids_needing_path:
        progress.emit(f"[xenotag] Resolving episode paths for {len(series_ids_needing_path)} series…")
        workers = min(cfg.scan.max_workers, len(series_ids_needing_path))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_to_sid = {pool.submit(_get_first_episode_path, jf, sid): sid for sid in series_ids_needing_path}
            for future in as_completed(future_to_sid):
                sid = future_to_sid[future]
                try:
                    resolved_episode_paths[sid] = future.result() or ""
                except Exception as exc:
                    log.debug("Episode path resolution error for %s: %s", sid, exc)
                    resolved_episode_paths[sid] = ""

    # Phase 1b: filter already-current items (sequential — SQLite reads)
    log.info("Phase 1b: checking %d items (mtime filter, network stat calls)…", len(items))
    progress.emit(f"[xenotag] Phase 1b: checking {len(items)} items…")
    to_probe: list[tuple[dict, str, str, float]] = []  # (item, file_path, item_root, mtime)
    _checked = 0
    for item in items:
        _checked += 1
        if _checked % 1000 == 0:
            progress.emit(f"[xenotag] Checked {_checked}/{len(items)} items…")
        if progress.cancelled:
            progress.emit("[xenotag] Scan cancelled by user")
            break

        item_id = item.get("Id", "")
        name = item.get("Name", item_id)

        media_sources = item.get("MediaSources") or []
        file_path = media_sources[0].get("Path", "") if media_sources else ""
        if not file_path:
            file_path = item.get("Path", "")

        item_root = item.get("Path", "")
        if item.get("Type") == "Series":
            resolved = resolved_episode_paths.get(item_id, "")
            if resolved:
                item_root = file_path if (file_path and Path(file_path).is_dir()) else ""
                file_path = resolved

        if not file_path or not Path(file_path).exists():
            error_type = "no_path" if not file_path else "no_file"
            msg = f"skip ({error_type}): {name} | path tried: {file_path or '(empty)'}"
            progress.emit(f"  {msg}")
            log.warning(msg)
            _record_scan_error(session, item_id, name, file_path or "", error_type)
            progress.done += 1
            continue

        mtime = _get_file_mtime(file_path)

        if incremental:
            from . import state as _state

            existing = session.get(_state.MediaState, f"jellyfin:{item_id}")
            if existing and existing.file_mtime == mtime:
                progress.done += 1
                continue

        to_probe.append((item, file_path, item_root, mtime))

    # Free the full items list — to_probe holds refs only to items that need processing.
    # Skipped/already-current items can now be GC'd.
    items_count = len(items)
    del items

    # Phase 2+3 merged: probe files in parallel; process (tag, overlay, persist) each result
    # immediately as it arrives instead of accumulating all probe_results before Phase 3.
    # Each item contributes 0.5 on probe completion and 0.5 on process completion so the
    # progress total stays at len(items_count) and shows real item counts throughout.
    log.info("Phase 1b complete: %d items queued for probe", len(to_probe))
    max_workers = min(cfg.scan.max_workers, max(1, len(to_probe)))
    tag_fallbacks = _TagFallbacks()
    readback = _Readback()
    if to_probe:
        total_probe = len(to_probe)
        log.info("Phase 2+3: probing and tagging %d items with %d workers…", total_probe, max_workers)
        progress.emit(f"[xenotag] Probing {total_probe} files with {max_workers} workers…")
        path_to_item: dict[str, tuple[dict, str, float]] = {
            fp: (item, item_root, mtime) for item, fp, item_root, mtime in to_probe
        }
        # Probed items are tagged in batches, each preceded by one /Items?Ids= read
        # of their current tags (B18): the listing they came from can be stale.
        probed_ok: list[tuple[dict, str, str, float, MediaInfo]] = []

        def process_batch() -> None:
            nonlocal tagged, images_modified
            batch = probed_ok[:]
            probed_ok.clear()
            if not batch:
                return
            current = _with_current_tags(jf, [b[0] for b in batch], tag_fallbacks)
            # Rows are written after the batch's tags are read back (B17).
            pending: list[_PendingRecord] = []
            for (_, fp, item_root, mtime, info), item in zip(batch, current, strict=True):
                item_id = item.get("Id", "")
                name = item.get("Name", item_id)
                progress.current_item = name
                progress.emit(f"  scanning: {name}")
                try:
                    image_modified = _process_one_item(
                        jf, arr, session, cfg, item, fp, item_root, mtime, info, pending=pending
                    )
                    tagged += 1
                    images_modified += image_modified
                    progress.emit(
                        f"  done: {name} | {info.resolution} | {info.video_codec or '-'} | {info.hdr_type or '-'}"
                        f" | audio: {len(info.audio_tracks)} | subs: {len(info.subtitle_tracks)}"
                    )
                except Exception as exc:
                    log.error("Unhandled error processing %s: %s", name, exc, exc_info=True)
                    _record_scan_error(session, item_id, name, fp, f"process_error: {exc}")
                progress.done += 0.5
            try:
                _record_after_readback(jf, session, cfg, pending, readback)
            except Exception as exc:  # never fails the scan; unrecorded items are retried
                log.error("Tag read-back/record failed for a batch of %d: %s", len(pending), exc, exc_info=True)
                session.rollback()

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            future_to_path = {pool.submit(probe_file, fp): fp for fp in path_to_item}
            probed = 0
            for future in as_completed(future_to_path):
                if progress.cancelled:
                    progress.emit("[xenotag] Scan cancelled by user")
                    break
                fp = future_to_path[future]
                item, item_root, mtime = path_to_item[fp]
                item_id = item.get("Id", "")
                name = item.get("Name", item_id)
                try:
                    info = future.result()
                except Exception as exc:
                    log.warning("probe_file error for %s: %s", fp, exc)
                    info = None
                probed += 1
                progress.done += 0.5
                if probed % 100 == 0 or probed == total_probe:
                    progress.emit(f"[xenotag] Probed {probed}/{total_probe} files…")

                if info is None:
                    progress.emit(f"  ffprobe failed: {name} | path: {fp}")
                    _record_scan_error(session, item_id, name, fp, "probe_failed")
                    progress.done += 0.5
                    continue

                probed_ok.append((item, fp, item_root, mtime, info))
                if len(probed_ok) >= TAG_READ_BATCH:
                    process_batch()
            # A cancelled scan stops here; its unprocessed batch is not written.
            if not progress.cancelled:
                process_batch()
    if tag_fallbacks.count:
        log.warning("Current tags: %d item(s) used the listing's copy this scan (roadmap B18)", tag_fallbacks.count)
    if summary := readback.summary():
        log.warning(summary)
        progress.emit(f"[xenotag] {summary}")
    set_meta(session, _TAG_CONFIG_KEY, current_hash)
    finish_scan_run(session, run, scanned=items_count, tagged=tagged, images=images_modified)
    session.close()
    _close_clients(jf, sonarrs, radarrs)
    metrics.scan_finished(
        scan_type,
        metrics.CANCELLED if progress.cancelled else metrics.SUCCESS,
        started=started,
        scanned=items_count,
        tagged=tagged,
        images=images_modified,
    )
    if sonarrs or radarrs:
        # A cancelled scan did not sync everything, so it cannot clear a halt.
        metrics.arr_sync_finished(arr.live and not progress.cancelled, arr.halted is not None)
        store_report(arr.report())
        for line in arr.summary_lines():
            progress.emit(line)
    # Roadmap U2: items Jellyfin no longer has. Its own clients and session --
    # read-only unless deleted_items.mode is "remove" -- and its own listing.
    if progress.cancelled:
        progress.emit("[xenotag] Deleted items: skipped (scan cancelled)")
    else:
        try:
            run_deleted_items(cfg, source=f"{scan_type} scan", emit=progress.emit)
        except Exception as exc:
            log.error("Deleted-items pass failed: %s", exc, exc_info=True)
            progress.emit(f"[xenotag] Deleted items: pass failed — {exc}")
    progress.finish()
    progress.emit(f"[xenotag] Scan complete — scanned={items_count}, tagged={tagged}, images={images_modified}")


def _get_first_episode_path(jf: JellyfinClient, series_id: str) -> str | None:
    try:
        data = jf._get(
            "/Items",
            ParentId=series_id,
            Recursive="true",
            IncludeItemTypes="Episode",
            Fields="Path,MediaSources",
            SortBy="SortName",
            SortOrder="Ascending",
            Limit=1,
        )
        eps = data.get("Items", [])
        if not eps:
            return None
        ep = eps[0]
        sources = ep.get("MediaSources") or []
        path = sources[0].get("Path", "") if sources else ""
        return path or ep.get("Path", "") or None
    except Exception as exc:
        log.debug("Could not resolve episode path for series %s: %s", series_id, exc)
        return None


def _tracks_from_row(row: MediaState) -> tuple[list[AudioTrack], list[SubTrack]]:
    audio = [
        AudioTrack(lang=t.get("lang") or "", codec=t.get("codec") or "") for t in json.loads(row.audio_tracks or "[]")
    ]
    subs = [
        SubTrack(lang=t.get("lang") or "", format=t.get("format") or "", embedded=bool(t.get("embedded")))
        for t in json.loads(row.subtitle_tracks or "[]")
    ]
    return audio, subs


def _read_only_session(db_path: str | Path) -> Session:
    """A session on ``db_path`` opened ``mode=ro`` -- for a copied production index."""
    return read_only_session(db_path)


def _unreachable_errors(session: Session) -> dict[str, tuple]:
    """Each item's newest ``scan_errors`` row, as ``{jellyfin id: (last_seen, error_type)}``.

    An item is unreachable when this is newer than its ``media_state.last_scanned``
    (B11): the scan skipped it before tagging, so its row is stale. Per item, not
    per scan run -- ``scan_errors`` is cleared only by a full scan, and an
    incremental scan skips a failed item whose stale row carries the file's mtime.
    """
    newest: dict[str, tuple] = {}
    for item_id, error_type, last_seen in session.query(ScanError.item_id, ScanError.error_type, ScanError.last_seen):
        if last_seen is not None and (item_id not in newest or last_seen > newest[item_id][0]):
            newest[item_id] = (last_seen, error_type or "")
    return newest


arr_dry_run_state: dict = {"running": False, "error": None}


def run_arr_dry_run(
    cfg: AppConfig,
    *,
    db_path: str | Path | None = None,
    store: bool = True,
    emit: Callable[[str], None] | None = None,
) -> dict:
    """Match every Jellyfin item and count what a live *arr sync would do. Writes nothing.

    Every client -- Jellyfin included -- sits behind a read-only transport, and
    the tags are computed from the local index (each item's last probe), so no
    media file is read either. An item whose last scan error is newer than its
    row is one the scan cannot reach -- counted as unreachable, not planned (B11).
    ``db_path`` opens a copied index read-only instead of the app's own.
    """
    guards: list[ReadOnlyTransport] = []
    jf, sonarrs, radarrs = _clients_from_config(cfg, read_only=True, guards=guards)
    sync = ArrTagSync(cfg, sonarrs, radarrs, mode=MODE_DRY_RUN, source="index", emit=emit)
    items: list[dict] = []
    try:
        sync.prepare()
        items = _filter_items(jf.get_items(cfg.jellyfin.library_ids or None), cfg)
        sync.resolve_all(items)
        session = _read_only_session(db_path) if db_path else get_session()
        try:
            errors = _unreachable_errors(session)
            for item in items:
                jf_rating = item.get("OfficialRating") or ""
                arr_cert = sync.fallback_rating(item) if not jf_rating else ""
                row = session.get(MediaState, f"jellyfin:{item.get('Id', '')}")
                if row is None:
                    sync.note_no_probe(item)
                    continue
                error = errors.get(item.get("Id", ""))
                if error and (row.last_scanned is None or error[0] > row.last_scanned):
                    sync.note_unreachable(item, error[1])
                    continue
                audio, subs = _tracks_from_row(row)
                rating = jf_rating or arr_cert or None
                # The same inputs the scan tagged with, read back from the row -- including
                # field_order, NULL on a row not re-probed since U5 (so no `xt-interlaced`).
                tags = {
                    kind: build_tags(
                        row.resolution or "unknown",
                        row.video_codec,
                        row.hdr_type,
                        audio,
                        subs,
                        rating,
                        cfg.tags,
                        kind,
                        field_order=row.field_order,
                    )
                    for kind in ("sonarr", "radarr")
                }
                sync.sync_item(item, tags)
        finally:
            session.close()
    finally:
        _close_clients(jf, sonarrs, radarrs)
    report = sync.report()
    report["items"]["jellyfin_items"] = len(items)
    sent: dict[str, int] = {}
    for g in guards:
        for method, n in g.sent.items():
            sent[method] = sent.get(method, 0) + n
    report["guard"] = {"transports": len(guards), "sent": sent, "blocked": [b for g in guards for b in g.blocked]}
    if store:
        store_report(report)
    return report


def run_arr_dry_run_background(cfg: AppConfig) -> None:
    """Thread target for the Settings button: one dry run at a time, report stored."""
    arr_dry_run_state.update(running=True, error=None)
    try:
        run_arr_dry_run(cfg)
    except Exception as exc:
        log.error("*arr dry run failed: %s", exc, exc_info=True)
        arr_dry_run_state["error"] = str(exc)
    finally:
        arr_dry_run_state["running"] = False


def _run_scan_recorded(cfg: AppConfig, incremental: bool) -> None:
    """``_run_scan``, with an exception that escapes it recorded as a failed scan (I5).

    The exception also releases the scan lock (B22): only ``_run_scan()``'s own
    exits call ``progress.finish()``, so without this every later scan would skip
    as "already in progress" until a restart. If ``_run_scan()`` already finished
    before raising, its own ``finish()`` stands.
    """
    try:
        _run_scan(cfg, incremental)
    except Exception as exc:
        if progress.running:
            progress.finish(error=str(exc))
        metrics.scan_finished("incremental" if incremental else "full", metrics.FAILED)
        raise


def run_full_scan(cfg: AppConfig) -> None:
    if not progress.try_start():
        log.warning("Scan already in progress, skipping")
        return
    _run_scan_recorded(cfg, incremental=False)


def run_incremental_scan(cfg: AppConfig) -> None:
    if not progress.try_start():
        log.warning("Scan already in progress, skipping")
        return
    _run_scan_recorded(cfg, incremental=True)


def _resolve_webhook_jf_item(jf: JellyfinClient, source: str, payload: dict) -> dict | None:
    """Resolve the Jellyfin item dict from an inbound webhook payload.

    Sonarr/Radarr payloads resolve by FOLDER only (roadmap B8): Jellyfin ignores
    every provider-id filter, and a provider id cannot tell an HD copy from its
    4K twin, while the folder can. The match is B5's own ``item_folders()``.
    """
    if source == "jellyfin":
        item_id = payload.get("ItemId") or payload.get("item_id")
        if not item_id:
            return None
        return jf.get_item_by_id(item_id) or None

    if source == "sonarr":
        folder, item_type = (payload.get("series") or {}).get("path"), "Series"
    elif source == "radarr":
        folder, item_type = (payload.get("movie") or {}).get("folderPath"), "Movie"
    else:
        return None
    folder = _norm_path(folder)
    if not folder:
        return None
    matches = [i for i in jf.list_item_paths(item_type) if folder in item_folders(i)]
    if not matches:
        log.info("Webhook %s: %s not in Jellyfin yet; the next scan will reach it", source, folder)
        return None
    if len(matches) > 1:
        ids = ", ".join(i.get("Id", "?") for i in matches)
        log.warning("Webhook %s: %s matches %d Jellyfin items (%s); not guessing", source, folder, len(matches), ids)
        return None
    return jf.get_item_by_id(matches[0].get("Id", "")) or None


def handle_webhook(cfg: AppConfig, source: str, payload: dict) -> None:
    """Background task: resolve, probe, tag, and overlay a single item from a webhook event."""
    jf, sonarrs, radarrs = _clients_from_config(cfg)
    try:
        item = _resolve_webhook_jf_item(jf, source, payload)
        if not item:
            log.info("Webhook %s: could not resolve Jellyfin item from payload", source)
            return

        item_id = item.get("Id", "")
        name = item.get("Name", item_id)

        media_sources = item.get("MediaSources") or []
        file_path = media_sources[0].get("Path", "") if media_sources else item.get("Path", "")
        item_root = item.get("Path", "")

        if item.get("Type") == "Series" and file_path and Path(file_path).is_dir():
            item_root = file_path
            file_path = _get_first_episode_path(jf, item_id) or ""

        if not file_path or not Path(file_path).exists():
            log.warning("Webhook %s: no accessible file for %s", source, name)
            return

        mtime = _get_file_mtime(file_path)
        info = probe_file(file_path)
        if not info:
            log.warning("Webhook %s: ffprobe failed for %s", source, name)
            return

        # The full catalogue per instance is the price of resolving the item
        # per instance; skip it when nothing *arr-side would use it.
        arr = ArrTagSync(cfg, sonarrs, radarrs, source=f"webhook/{source}")
        if arr.live or arr.cert_fallback:
            arr.prepare()
            arr.resolve_all([item])
        session = get_session()
        image_modified = _process_one_item(jf, arr, session, cfg, item, file_path, item_root, mtime, info)
        session.close()
        log.info("Webhook %s: processed %s | image_modified=%s", source, name, image_modified)
        for line in arr.summary_lines() if (arr.live or arr.cert_fallback) else ():
            log.info("Webhook %s: %s", source, line.removeprefix("[xenotag] "))
    except Exception as exc:
        log.error("Webhook %s handler error: %s", source, exc)
    finally:
        _close_clients(jf, sonarrs, radarrs)
