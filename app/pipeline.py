from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .arr_sync import MODE_DRY_RUN, MODE_LIVE, ArrTagSync, store_report
from .clients.jellyfin import JellyfinClient
from .clients.radarr import RadarrClient
from .clients.readonly import ReadOnlyTransport
from .clients.sonarr import SonarrClient
from .config import AppConfig
from .overlay import BadgeGroup, apply_overlay
from .scanner import AudioTrack, MediaInfo, SubTrack, probe_file
from .state import (
    MediaState,
    clear_scan_errors,
    finish_scan_run,
    get_meta,
    get_session,
    set_meta,
    start_scan_run,
    upsert_media_state,
    upsert_scan_error,
)
from .tagger import build_tags

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
    dest = cfg.tags.destinations
    raw = (
        f"{cfg.tags.managed_prefix}|{cfg.tags.dual_audio_tag}|{cfg.tags.multi_audio_tag}"
        f"|legacy:{','.join(sorted(cfg.tags.legacy_prefixes))}"
        f"|video:{','.join(sorted(dest.video))}"
        f"|audio:{','.join(sorted(dest.audio))}"
        f"|subtitles:{','.join(sorted(dest.subtitles))}"
        f"|rating:{','.join(sorted(dest.rating))}"
    )
    # Appended only when switched ON, so the shipped defaults hash exactly as
    # before and an upgrade does not force a full rescan. Turning either on
    # does: the next scan re-tags everything, which is what going live means.
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
) -> bool:
    """Tag, overlay, and persist one media item. Returns True if an image was modified."""
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
    )

    try:
        jf.set_managed_tags(item_id, item, prefix, jf_tags, fallback_rating=arr_cert, legacy_prefixes=legacy_prefixes)
    except Exception as exc:
        log.warning("Jellyfin tag error for %s: %s", name, exc)

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

    upsert_media_state(
        session,
        item_id=f"jellyfin:{item_id}",
        source="jellyfin",
        file_path=file_path,
        resolution=info.resolution,
        languages=info.languages,
        tags_applied=jf_tags,
        image_path=str(modified_path) if modified_path else None,
        file_mtime=mtime,
        video_codec=info.video_codec,
        hdr_type=info.hdr_type,
        audio_tracks=[{"lang": t.lang, "codec": t.codec} for t in info.audio_tracks],
        subtitle_tracks=[{"lang": t.lang, "format": t.format, "embedded": t.embedded} for t in info.subtitle_tracks],
        content_rating=content_rating,
    )

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
            groups.append(BadgeGroup(video_labels, img_cfg.video_badge_color, img_cfg.badge_text_color))

    # Audio group — codec-first, languages grouped under each codec
    # e.g. [DTS-HD EN JA] [AC-3 DE] instead of [EN DTS-HD] [JA DTS-HD] [DE AC-3]
    if img_cfg.show_audio_badges and "poster" in dest.audio:
        codec_langs: dict[str, list[str]] = {}
        for t in info.audio_tracks:
            langs = codec_langs.setdefault(t.codec, [])
            if t.lang and t.lang != "UND":
                langs.append(t.lang)
        audio_labels = [f"{codec} {' '.join(langs)}" if langs else codec for codec, langs in codec_langs.items()]
        if audio_labels:
            groups.append(BadgeGroup(audio_labels, img_cfg.audio_badge_color, img_cfg.badge_text_color))

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
        sub_labels = [f"{fmt} {' '.join(langs)}" if langs else fmt for fmt, langs in fmt_langs.items()]
        if sub_labels:
            groups.append(BadgeGroup(sub_labels, img_cfg.sub_badge_color, img_cfg.badge_text_color))

    # Rating badge (top-right, independent)
    rating_group: BadgeGroup | None = None
    if img_cfg.show_rating_badge and content_rating and "poster" in dest.rating:
        rating_group = BadgeGroup([f"Rated {content_rating}"], img_cfg.rating_badge_color, img_cfg.badge_text_color)

    return groups, rating_group


def _run_scan(cfg: AppConfig, incremental: bool) -> None:
    scan_type = "incremental" if incremental else "full"
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
            upsert_scan_error(session, item_id, name, file_path or "", error_type)
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
    if to_probe:
        total_probe = len(to_probe)
        log.info("Phase 2+3: probing and tagging %d items with %d workers…", total_probe, max_workers)
        progress.emit(f"[xenotag] Probing {total_probe} files with {max_workers} workers…")
        path_to_item: dict[str, tuple[dict, str, float]] = {
            fp: (item, item_root, mtime) for item, fp, item_root, mtime in to_probe
        }
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
                    upsert_scan_error(session, item_id, name, fp, "probe_failed")
                    progress.done += 0.5
                    continue

                progress.current_item = name
                progress.emit(f"  scanning: {name}")
                try:
                    image_modified = _process_one_item(jf, arr, session, cfg, item, fp, item_root, mtime, info)
                    tagged += 1
                    images_modified += image_modified
                    progress.emit(
                        f"  done: {name} | {info.resolution} | {info.video_codec or '-'} | {info.hdr_type or '-'}"
                        f" | audio: {len(info.audio_tracks)} | subs: {len(info.subtitle_tracks)}"
                    )
                except Exception as exc:
                    log.error("Unhandled error processing %s: %s", name, exc, exc_info=True)
                    upsert_scan_error(session, item_id, name, fp, f"process_error: {exc}")
                progress.done += 0.5
    set_meta(session, _TAG_CONFIG_KEY, current_hash)
    finish_scan_run(session, run, scanned=items_count, tagged=tagged, images=images_modified)
    session.close()
    _close_clients(jf, sonarrs, radarrs)
    if sonarrs or radarrs:
        store_report(arr.report())
        for line in arr.summary_lines():
            progress.emit(line)
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
    engine = create_engine(f"sqlite:///file:{db_path}?mode=ro&uri=true")
    return sessionmaker(bind=engine)()


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
    media file is read either. ``db_path`` opens a copied index read-only
    instead of the app's own.
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
            for item in items:
                jf_rating = item.get("OfficialRating") or ""
                arr_cert = sync.fallback_rating(item) if not jf_rating else ""
                row = session.get(MediaState, f"jellyfin:{item.get('Id', '')}")
                if row is None:
                    sync.note_no_probe(item)
                    continue
                audio, subs = _tracks_from_row(row)
                rating = jf_rating or arr_cert or None
                tags = {
                    kind: build_tags(
                        row.resolution or "unknown", row.video_codec, row.hdr_type, audio, subs, rating, cfg.tags, kind
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


def run_full_scan(cfg: AppConfig) -> None:
    if not progress.try_start():
        log.warning("Scan already in progress, skipping")
        return
    _run_scan(cfg, incremental=False)


def run_incremental_scan(cfg: AppConfig) -> None:
    if not progress.try_start():
        log.warning("Scan already in progress, skipping")
        return
    _run_scan(cfg, incremental=True)


def _resolve_webhook_jf_item(jf: JellyfinClient, source: str, payload: dict) -> dict | None:
    """Resolve the Jellyfin item dict from an inbound webhook payload."""
    if source == "jellyfin":
        item_id = payload.get("ItemId") or payload.get("item_id")
        if not item_id:
            return None
        return jf.get_item_by_id(item_id) or None

    if source == "sonarr":
        series = payload.get("series") or {}
        tvdb_id = series.get("tvdbId")
        if tvdb_id:
            return jf.find_item_by_provider_id("Tvdb", str(tvdb_id))
        return None

    if source == "radarr":
        movie = payload.get("movie") or {}
        tmdb_id = movie.get("tmdbId")
        if tmdb_id:
            return jf.find_item_by_provider_id("Tmdb", str(tmdb_id))
        return None

    return None


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
