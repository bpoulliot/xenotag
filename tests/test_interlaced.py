"""`xt-interlaced` (roadmap U5, operator decision 2026-09-26: interlacing only, present or absent).

The source is ffprobe's video-stream ``field_order``. ``tt``/``bb``/``tb``/``bt`` are
interlaced; ``progressive`` is not; ``unknown`` or no field_order at all says nothing
and is never tagged, either way. The value is stored in ``media_state.field_order``
(revision 0002), because the *arr dry run builds its tags from the row, not a probe.
"""

from __future__ import annotations

import json
import subprocess

import pytest
from sqlalchemy.orm import sessionmaker

from app import migrate, pipeline, scanner, state
from app.config import AppConfig, TagsConfig
from app.scanner import AudioTrack, MediaInfo
from app.state import MediaState
from app.tagger import build_tags

INTERLACED = ["tt", "bb", "tb", "bt"]
NOT_INTERLACED = ["progressive", "unknown"]


def _probe(monkeypatch, video: dict | None) -> MediaInfo:
    streams = [{"codec_type": "audio", "codec_name": "aac", "tags": {"language": "eng"}}]
    if video is not None:
        streams.insert(0, {"codec_type": "video", "codec_name": "mpeg2video", "width": 720, **video})
    out = json.dumps({"streams": streams})
    monkeypatch.setattr(
        scanner.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=out, stderr="")
    )
    info = scanner.probe_file("/nonexistent/dir/file.mkv")
    assert info is not None
    return info


def _tags(field_order, destination="radarr", tags_cfg=None):
    return build_tags(
        "SD", "MPEG-2", None, [], [], None, tags_cfg or TagsConfig(), destination, field_order=field_order
    )


@pytest.mark.parametrize("value", INTERLACED)
def test_an_interlaced_field_order_is_tagged(monkeypatch, value):
    info = _probe(monkeypatch, {"field_order": value})
    assert info.field_order == value and info.interlaced
    assert _tags(info.field_order) == ["xt-SD", "xt-MPEG-2", "xt-interlaced"]


@pytest.mark.parametrize("value", NOT_INTERLACED)
def test_progressive_and_unknown_are_not_tagged(monkeypatch, value):
    info = _probe(monkeypatch, {"field_order": value})
    assert info.field_order == value and not info.interlaced
    assert _tags(info.field_order) == ["xt-SD", "xt-MPEG-2"]


def test_a_stream_without_field_order_is_stored_as_unknown_and_not_tagged(monkeypatch):
    info = _probe(monkeypatch, {})
    assert info.field_order == "unknown" and not info.interlaced
    assert "xt-interlaced" not in _tags(info.field_order)


def test_no_video_stream_is_none(monkeypatch):
    assert _probe(monkeypatch, None).field_order is None


def test_the_first_video_stream_decides_and_case_does_not_matter(monkeypatch):
    streams = [
        {"codec_type": "video", "codec_name": "h264", "field_order": "TT"},
        {"codec_type": "video", "codec_name": "mjpeg", "field_order": "progressive"},
    ]
    assert scanner._detect_field_order(streams) == "tt"


@pytest.mark.parametrize("value", [None, "", "unknown", "progressive", "interlaced", "t"])
def test_nothing_else_reads_as_interlaced(value):
    assert not scanner.is_interlaced(value)


def test_the_tag_follows_the_video_destinations():
    only_jellyfin = TagsConfig.model_validate({"destinations": {"video": ["jellyfin"]}})
    assert "xt-interlaced" in _tags("tt", "jellyfin", only_jellyfin)
    assert _tags("tt", "radarr", only_jellyfin) == []
    assert _tags("tt", "sonarr", only_jellyfin) == []


def test_the_label_is_legal_in_radarr_and_keeps_a_configured_prefix():
    assert _tags("bb", tags_cfg=TagsConfig(managed_prefix="x.t+ "))[-1] == "x.t+ interlaced"


def test_no_badge_is_drawn_for_it():
    """Tags only: the video row still shows resolution, codec and HDR, nothing more."""
    info = MediaInfo("SD", ["EN"], ["eng"], "MPEG-2", None, [AudioTrack("EN", "AAC")], [], field_order="tt")
    groups, _ = pipeline._make_badge_groups(info, None, AppConfig())
    assert groups[0].labels == ["SD", "MPEG-2"]


# ── the index ───────────────────────────────────────────────────────────────
def test_a_row_from_before_the_migration_reads_none_and_tags_nothing(tmp_path):
    """Every existing row is NULL after 0002 until a scan re-probes it: no tag, no error."""
    db = tmp_path / "state.db"
    migrate.upgrade_to_head(db)
    engine = migrate._engine(db)
    with engine.connect() as conn, conn.begin():
        # What an image that knows only revision 0001 writes: no field_order column named.
        conn.exec_driver_sql(
            "INSERT INTO media_state (item_id, source, resolution, video_codec) "
            "VALUES ('jellyfin:old', 'jellyfin', 'SD', 'MPEG-2')"
        )
    engine.dispose()
    session = state.read_only_session(db)
    try:
        row = session.get(MediaState, "jellyfin:old")
        assert row.field_order is None
        tags = build_tags(row.resolution, row.video_codec, None, [], [], None, TagsConfig(), "radarr", field_order=None)
        assert tags == ["xt-SD", "xt-MPEG-2"]
    finally:
        session.close()


def test_upsert_stores_the_field_order(tmp_path):
    db = tmp_path / "state.db"
    migrate.upgrade_to_head(db)
    engine = migrate._engine(db)
    session = sessionmaker(bind=engine)()
    try:
        state.upsert_media_state(
            session, "jellyfin:a", "jellyfin", "/m/a.mkv", "SD", [], [], None, 1.0, field_order="bb"
        )
        state.upsert_media_state(session, "jellyfin:b", "jellyfin", "/m/b.mkv", "SD", [], [], None, 1.0)
        assert session.get(MediaState, "jellyfin:a").field_order == "bb"
        assert session.get(MediaState, "jellyfin:b").field_order is None
    finally:
        session.close()
        engine.dispose()


# ── the re-tag that fills the column ────────────────────────────────────────
class _Jellyfin:
    def __init__(self, items):
        self.items = items
        self.tag_writes: dict[str, list[str]] = {}

    def get_items(self, library_ids=None):
        return self.items

    def set_managed_tags(self, item_id, item, prefix, tags, fallback_rating="", legacy_prefixes=()):
        self.tag_writes[item_id] = list(tags)

    def get_current_tags(self, item_ids):
        return {i: self.tag_writes[i] for i in item_ids if i in self.tag_writes}

    def refresh_item(self, item_id):
        pass

    def close(self):
        pass


def test_the_first_scan_after_the_upgrade_re_probes_an_unchanged_file(tmp_path, monkeypatch):
    """A pre-U5 row whose file has not changed would be skipped by an incremental scan;
    the vocabulary bump turns that scan into a full one, which probes it and fills
    ``field_order``. The scan after that is incremental again and reuses the row."""
    from app import arr_sync

    media = tmp_path / "Film (1999)" / "film.mkv"
    media.parent.mkdir()
    media.write_bytes(b"")
    mtime = media.stat().st_mtime
    db = tmp_path / "state.db"
    migrate.upgrade_to_head(db)
    engine = migrate._engine(db)
    maker = sessionmaker(bind=engine)

    # The index as a pre-U5 release left it: row current, hash from vocabulary 3.
    s = maker()
    state.upsert_media_state(s, "jellyfin:f", "jellyfin", str(media), "SD", ["EN"], ["xt-SD"], None, mtime)
    monkeypatch.setattr(pipeline, "TAG_VOCABULARY", 3)
    state.set_meta(s, pipeline._TAG_CONFIG_KEY, pipeline._tag_config_hash(AppConfig()))
    monkeypatch.undo()
    s.close()

    probed: list[str] = []

    def probe(path):
        probed.append(path)
        return MediaInfo("SD", ["EN"], ["eng"], "MPEG-2", None, [AudioTrack("EN", "AC-3")], [], field_order="tt")

    item = {"Id": "f", "Name": "Film", "Type": "Movie", "Path": str(media), "MediaSources": [{"Path": str(media)}]}
    jf = _Jellyfin([item])
    monkeypatch.setattr(pipeline, "probe_file", probe)
    monkeypatch.setattr(pipeline, "get_session", maker)
    monkeypatch.setattr(pipeline, "run_deleted_items", lambda c, **kw: None)
    monkeypatch.setattr(pipeline, "_clients_from_config", lambda c: (jf, [], []))
    monkeypatch.setenv("ARR_SYNC_REPORT", str(tmp_path / "arr-sync-report.json"))
    monkeypatch.setattr(arr_sync, "_last_report", None)

    try:
        for _ in range(2):
            assert pipeline.progress.try_start()
            pipeline._run_scan(AppConfig(), incremental=True)
        assert probed == [str(media)]  # the first scan probed it; the second reused the row
        assert "xt-interlaced" in jf.tag_writes["f"]
        s = maker()
        assert s.get(MediaState, "jellyfin:f").field_order == "tt"
        s.close()
    finally:
        engine.dispose()
