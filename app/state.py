from __future__ import annotations

import json
import os
from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path

from sqlalchemy import Column, DateTime, Float, Integer, String, Text, create_engine, event, or_, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

_engine = None
_SessionLocal = None


class Base(DeclarativeBase):
    pass


class MediaState(Base):
    __tablename__ = "media_state"

    item_id = Column(String, primary_key=True)  # e.g. "jellyfin:abc123"
    source = Column(String, nullable=False)  # jellyfin | sonarr | radarr
    file_path = Column(Text)
    resolution = Column(String)
    languages = Column(Text)  # JSON list e.g. '["EN","JA"]'
    tags_applied = Column(Text)  # JSON list of tag names
    image_path = Column(Text)
    last_scanned = Column(DateTime)
    file_mtime = Column(Float)
    video_codec = Column(String)
    hdr_type = Column(String)
    audio_tracks = Column(Text)  # JSON list of {lang, codec} dicts
    subtitle_tracks = Column(Text)  # JSON list of {lang, format, embedded} dicts
    content_rating = Column(String)


class AppMeta(Base):
    __tablename__ = "app_meta"

    key = Column(String, primary_key=True)
    value = Column(Text)


class ScanRun(Base):
    __tablename__ = "scan_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime)
    items_scanned = Column(Integer, default=0)
    items_tagged = Column(Integer, default=0)
    items_image_modified = Column(Integer, default=0)
    scan_type = Column(String)  # "full" | "incremental"


class ScanError(Base):
    __tablename__ = "scan_errors"

    id = Column(Integer, primary_key=True, autoincrement=True)
    item_id = Column(String, index=True)
    item_name = Column(Text)
    file_path = Column(Text)
    error_type = Column(String)  # "probe_failed" | "no_file"
    first_seen = Column(DateTime, default=datetime.utcnow)
    last_seen = Column(DateTime, default=datetime.utcnow)
    scan_count = Column(Integer, default=1)


def _configure_sqlite(dbapi_conn, _connection_record) -> None:
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


def _migrate_schema(engine) -> None:
    new_cols = {
        "video_codec": "VARCHAR",
        "hdr_type": "VARCHAR",
        "audio_tracks": "TEXT",
        "subtitle_tracks": "TEXT",
        "content_rating": "VARCHAR",
    }
    with engine.connect() as conn:
        existing = {row[1] for row in conn.execute(text("PRAGMA table_info(media_state)"))}
        for col, typ in new_cols.items():
            if col not in existing:
                conn.execute(text(f"ALTER TABLE media_state ADD COLUMN {col} {typ}"))
        conn.commit()


def init_db(db_path: str | Path | None = None) -> None:
    global _engine, _SessionLocal
    path = db_path or os.environ.get("STATE_DB", "/config/state.db")
    _engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    event.listen(_engine, "connect", _configure_sqlite)
    Base.metadata.create_all(_engine)
    _migrate_schema(_engine)
    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)


def get_session() -> Session:
    if _SessionLocal is None:
        init_db()
    return _SessionLocal()


def upsert_media_state(
    session: Session,
    item_id: str,
    source: str,
    file_path: str,
    resolution: str,
    languages: list[str],
    tags_applied: list[str],
    image_path: str | None,
    file_mtime: float,
    video_codec: str | None = None,
    hdr_type: str | None = None,
    audio_tracks: list | None = None,
    subtitle_tracks: list | None = None,
    content_rating: str | None = None,
) -> MediaState:
    row = session.get(MediaState, item_id)
    if row is None:
        row = MediaState(item_id=item_id)
        session.add(row)
    row.source = source
    row.file_path = file_path
    row.resolution = resolution
    row.languages = json.dumps(languages)
    row.tags_applied = json.dumps(tags_applied)
    row.image_path = image_path
    row.last_scanned = datetime.utcnow()
    row.file_mtime = file_mtime
    row.video_codec = video_codec
    row.hdr_type = hdr_type
    row.audio_tracks = json.dumps(audio_tracks or [])
    row.subtitle_tracks = json.dumps(subtitle_tracks or [])
    row.content_rating = content_rating
    session.commit()
    return row


def partition_legacy_tags(
    tags: Iterable[str],
    legacy_prefixes: Sequence[str],
    managed_prefix: str,
) -> tuple[list[str], list[str]]:
    """Split ``tags`` into (kept, removed) for a legacy-prefix sweep.

    A tag carrying ``managed_prefix`` is kept unconditionally, even when a legacy
    prefix also matches it. ``legacy_prefixes`` is operator-supplied config, so
    nothing stops it holding ``"x"`` — and without this rule that single character
    would take every ``xt-*`` tag in the index with it. The managed prefix wins.

    Order is preserved and duplicates are handled independently, so ``removed``
    counts applications rather than distinct tags.
    """
    prefixes = tuple(p for p in legacy_prefixes if p)
    if not prefixes:
        return list(tags), []
    kept: list[str] = []
    removed: list[str] = []
    for tag in tags:
        if managed_prefix and tag.startswith(managed_prefix):
            kept.append(tag)
        elif tag.startswith(prefixes):
            removed.append(tag)
        else:
            kept.append(tag)
    return kept, removed


def purge_legacy_tags(
    session: Session,
    legacy_prefixes: Sequence[str],
    managed_prefix: str,
    *,
    dry_run: bool = False,
) -> dict:
    """Remove legacy-prefixed tags from ``media_state.tags_applied``.

    This is the *local index* half of the legacy migration. The outward half —
    stripping the same prefixes from Jellyfin/Sonarr/Radarr — already happens in
    ``set_managed_tags()`` on every item a scan processes. Rows the scan never
    reaches keep their pre-migration record forever: an item whose file vanished,
    or whose ffprobe fails, is skipped before ``_process_one_item()`` and so is
    never re-written. That residue is what this clears.

    Returns a report dict; with ``dry_run`` nothing is committed.
    """
    prefixes = [p for p in legacy_prefixes if p]
    report: dict = {
        "prefixes": prefixes,
        "managed_prefix": managed_prefix,
        "rows_examined": 0,
        "rows_changed": 0,
        "tags_removed": 0,
        "by_tag": {},
        "dry_run": dry_run,
    }
    if not prefixes:
        return report

    # Superset prefilter so a clean index costs one indexless LIKE scan instead of
    # materialising every row. A prefix holding a LIKE wildcard only over-selects;
    # partition_legacy_tags() is the authority on what is actually removed.
    rows = session.query(MediaState).filter(or_(*[MediaState.tags_applied.like(f'%"{p}%') for p in prefixes])).all()
    report["rows_examined"] = len(rows)

    counter: Counter[str] = Counter()
    for row in rows:
        try:
            tags = json.loads(row.tags_applied or "[]")
        except (TypeError, ValueError):
            continue
        if not isinstance(tags, list):
            continue
        kept, removed = partition_legacy_tags((t for t in tags if isinstance(t, str)), prefixes, managed_prefix)
        if not removed:
            continue
        counter.update(removed)
        report["rows_changed"] += 1
        if not dry_run:
            row.tags_applied = json.dumps(kept)

    report["tags_removed"] = sum(counter.values())
    report["by_tag"] = dict(counter.most_common())
    if dry_run:
        session.rollback()
    elif report["rows_changed"]:
        session.commit()
    return report


def get_all_media(session: Session) -> list[dict]:
    rows = session.query(MediaState).order_by(MediaState.last_scanned.desc()).all()
    return [_media_to_dict(r) for r in rows]


def get_media_filtered(
    session: Session,
    resolution: str = "",
    language: str = "",
    page: int = 1,
    per_page: int = 50,
) -> tuple[int, list[dict]]:
    query = session.query(MediaState)
    if resolution:
        query = query.filter(MediaState.resolution == resolution)
    if language:
        query = query.filter(MediaState.languages.like(f'%"{language.upper()}"%'))
    total = query.count()
    rows = query.order_by(MediaState.last_scanned.desc()).offset((page - 1) * per_page).limit(per_page).all()
    return total, [_media_to_dict(r) for r in rows]


def _media_to_dict(row: MediaState) -> dict:
    return {
        "item_id": row.item_id,
        "source": row.source,
        "file_path": row.file_path,
        "resolution": row.resolution,
        "languages": json.loads(row.languages or "[]"),
        "tags_applied": json.loads(row.tags_applied or "[]"),
        "image_path": row.image_path,
        "last_scanned": row.last_scanned.isoformat() if row.last_scanned else None,
        "file_mtime": row.file_mtime,
        "video_codec": row.video_codec,
        "hdr_type": row.hdr_type,
        "audio_tracks": json.loads(row.audio_tracks or "[]"),
        "subtitle_tracks": json.loads(row.subtitle_tracks or "[]"),
        "content_rating": row.content_rating,
    }


def get_meta(session: Session, key: str) -> str | None:
    row = session.get(AppMeta, key)
    return row.value if row else None


def set_meta(session: Session, key: str, value: str) -> None:
    row = session.get(AppMeta, key)
    if row is None:
        row = AppMeta(key=key, value=value)
        session.add(row)
    else:
        row.value = value
    session.commit()


def start_scan_run(session: Session, scan_type: str) -> ScanRun:
    run = ScanRun(started_at=datetime.utcnow(), scan_type=scan_type)
    session.add(run)
    session.commit()
    return run


def finish_scan_run(session: Session, run: ScanRun, scanned: int, tagged: int, images: int) -> None:
    run.completed_at = datetime.utcnow()
    run.items_scanned = scanned
    run.items_tagged = tagged
    run.items_image_modified = images
    session.commit()
    # Prune: keep only the 50 most recent scan runs
    cutoff = session.query(ScanRun.id).order_by(ScanRun.started_at.desc()).offset(50).limit(1).scalar()
    if cutoff:
        session.query(ScanRun).filter(ScanRun.id <= cutoff).delete()
        session.commit()


def get_last_scan(session: Session) -> ScanRun | None:
    return session.query(ScanRun).order_by(ScanRun.completed_at.desc()).first()


def get_recent_scans(session: Session, limit: int = 10) -> list[dict]:
    runs = session.query(ScanRun).order_by(ScanRun.started_at.desc()).limit(limit).all()
    result = []
    for r in runs:
        duration = None
        if r.completed_at and r.started_at:
            duration = int((r.completed_at - r.started_at).total_seconds())
        result.append(
            {
                "id": r.id,
                "scan_type": r.scan_type,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                "items_scanned": r.items_scanned,
                "items_tagged": r.items_tagged,
                "items_image_modified": r.items_image_modified,
                "duration_seconds": duration,
            }
        )
    return result


def upsert_scan_error(
    session: Session,
    item_id: str,
    item_name: str,
    file_path: str,
    error_type: str,
) -> None:
    now = datetime.utcnow()
    existing = session.query(ScanError).filter_by(item_id=item_id, error_type=error_type).first()
    if existing:
        existing.last_seen = now
        existing.scan_count = (existing.scan_count or 1) + 1
        existing.file_path = file_path
        existing.item_name = item_name
    else:
        session.add(
            ScanError(
                item_id=item_id,
                item_name=item_name,
                file_path=file_path,
                error_type=error_type,
                first_seen=now,
                last_seen=now,
                scan_count=1,
            )
        )
    session.commit()


def get_scan_errors(session: Session) -> list[dict]:
    rows = session.query(ScanError).order_by(ScanError.last_seen.desc()).all()
    return [
        {
            "id": r.id,
            "item_id": r.item_id,
            "item_name": r.item_name,
            "file_path": r.file_path,
            "error_type": r.error_type,
            "first_seen": r.first_seen.isoformat() if r.first_seen else None,
            "last_seen": r.last_seen.isoformat() if r.last_seen else None,
            "scan_count": r.scan_count,
        }
        for r in rows
    ]


def clear_scan_errors(session: Session) -> None:
    session.query(ScanError).delete()
    session.commit()


def get_stats(session: Session) -> dict:
    total = session.query(MediaState).count()
    with_images = session.query(MediaState).filter(MediaState.image_path.isnot(None)).count()
    last = get_last_scan(session)
    return {
        "total_tagged": total,
        "images_modified": with_images,
        "last_scan_at": last.completed_at.isoformat() if last and last.completed_at else None,
        "last_scan_type": last.scan_type if last else None,
    }
