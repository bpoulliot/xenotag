"""The resolution class (roadmap B13, operator decision 2026-10-05).

Width OR height within 5% of the class, for the HD classes (4K 3840x2160, 1080p
1920x1080, 720p 1280x720); below 720p, width alone with the same tolerance, so DVD
frames stay SD. Before B13 it was width alone, exact: a 3836x1604 scope crop and a
3584x2160 open matte were 1080p, and a 1440x1080 frame was 720p.

The rule changes the class of existing files, and media_state stores the class, not
the frame size -- so ``RESOLUTION_RULE_VERSION`` is part of the tag-config hash and
the upgrade's first scan is one full re-tag.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

from app import migrate, pipeline, scanner, state
from app.config import AppConfig
from app.scanner import AudioTrack, MediaInfo
from app.state import MediaState


def resolution(w: int | None, h: int | None) -> str:
    stream = {"codec_type": "video", "codec_name": "hevc"}
    if w is not None:
        stream["width"] = w
    if h is not None:
        stream["height"] = h
    return scanner._detect_resolution([{"codec_type": "audio"}, stream])


# ── the filed cases and the measured table's commonest moves ───────────────
@pytest.mark.parametrize(
    "w,h,want,before",
    [
        (3836, 1604, "4K", "1080p"),  # Return to Silent Hill, WEBDL-2160p scope crop (filed)
        (3584, 2160, "4K", "1080p"),  # Dr. Strangelove, Bluray-2160p open matte (filed)
        (2960, 2160, "4K", "1080p"),  # the table's third 1080p->4K (1.37:1)
        (1440, 1080, "1080p", "720p"),  # 131 items: 4:3 HD / anamorphic HDV
        (1792, 1080, "1080p", "720p"),  # 26 items: open matte
        (1904, 1072, "1080p", "720p"),  # 26 items
        (1918, 1080, "1080p", "720p"),  # 18 items
        (1916, 1080, "1080p", "720p"),
        (1904, 1024, "1080p", "720p"),
        (1918, 802, "1080p", "720p"),
        (960, 720, "720p", "480p"),
    ],
)
def test_a_cropped_frame_keeps_its_class(w, h, want, before):
    assert resolution(w, h) == want
    assert _measure().before_b13(w, h) == before  # and it did move


@pytest.mark.parametrize("w,h", [(720, 480), (640, 480), (704, 480), (720, 576), (704, 576), (352, 240)])
def test_dvd_frames_stay_sd(w, h):
    """Height is never used below 720p (decision 2: DVDs stay SD)."""
    assert resolution(w, h) == "SD"


@pytest.mark.parametrize(
    "w,h,want",
    [
        (3840, 2160, "4K"),
        (1920, 1080, "1080p"),
        (1280, 720, "720p"),
        (854, 480, "480p"),
        (1920, 800, "1080p"),
        (1280, 536, "720p"),
    ],
)
def test_full_frames_are_unchanged(w, h, want):
    assert resolution(w, h) == want


@pytest.mark.parametrize(
    "w,h,want",
    [
        # 5% of 3840 is 192 and of 2160 is 108: either reaching it is enough.
        (3648, 1520, "4K"),
        (3647, 1520, "1080p"),
        (2800, 2052, "4K"),
        (2800, 2051, "1080p"),
        (1824, 760, "1080p"),
        (1823, 760, "720p"),
        (1400, 1026, "1080p"),
        (1400, 1025, "720p"),
        (1216, 500, "720p"),
        (1215, 500, "480p"),
        (900, 684, "720p"),
        (900, 683, "480p"),
        # 480p: 854 less 5% is 811.3 -- width only, and the integer test is exact.
        (812, 400, "480p"),
        (811, 400, "SD"),
        (811, 683, "SD"),
    ],
)
def test_the_tolerance_edges(w, h, want):
    assert resolution(w, h) == want


def test_highest_class_wins():
    assert resolution(4096, 2160) == "4K"
    assert resolution(1920, 2160) == "4K"  # a 2160-line frame is 4K whatever its width


def test_no_video_and_missing_sizes():
    assert scanner._detect_resolution([{"codec_type": "audio"}]) == "unknown"
    assert scanner._detect_resolution([]) == "unknown"
    assert resolution(None, None) == "SD"
    assert resolution(1920, None) == "1080p"
    assert resolution(None, 1080) == "1080p"


def test_only_the_first_video_stream_counts():
    streams = [
        {"codec_type": "video", "width": 720, "height": 480},
        {"codec_type": "video", "width": 3840, "height": 2160},
    ]
    assert scanner._detect_resolution(streams) == "SD"


def test_the_class_names_are_unchanged():
    """No new tag label: all five are legal in Radarr's [a-z0-9-] (B9)."""
    assert [label for _, _, label in scanner.RESOLUTION_CLASSES] == ["4K", "1080p", "720p", "480p"]


# ── the shipped rule is the measured one ────────────────────────────────────
def _measure():
    path = Path(__file__).resolve().parent.parent / "scripts" / "measure_resolution_thresholds.py"
    spec = importlib.util.spec_from_file_location("measure_resolution_thresholds", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_shipped_rule_is_the_decided_row_of_the_measurement():
    """The roadmap's 745 was counted by the probe's `hw-5%-hd`; ship exactly that."""
    m = _measure()
    decided = m.RULES["hw-5%-hd"]
    grid = [(w, h) for w in range(0, 4200, 3) for h in range(0, 2300, 11)]
    assert [p for p in grid if m.shipped(*p) != decided(*p)] == []
    # and the comparison can fail: the 1% row and the DVD-moving row are not it
    assert any(m.shipped(*p) != m.RULES["hw-1%-hd"](*p) for p in grid)
    assert any(m.shipped(*p) != m.h_or_w_tol(5)(*p) for p in grid)


def test_the_measurement_self_test_passes():
    assert _measure().self_test() == 0


# ── delivery: one full re-tag ───────────────────────────────────────────────
def test_the_rule_version_is_part_of_the_tag_config_hash(monkeypatch):
    before = pipeline._tag_config_hash(AppConfig())
    monkeypatch.setattr(pipeline, "RESOLUTION_RULE_VERSION", pipeline.RESOLUTION_RULE_VERSION + 1)
    assert pipeline._tag_config_hash(AppConfig()) != before
    monkeypatch.undo()
    assert pipeline._tag_config_hash(AppConfig()) == before


class _Jellyfin:
    def __init__(self, items):
        self.items = items
        self.tag_writes: dict[str, list[list[str]]] = {}

    def get_items(self, library_ids=None):
        return self.items

    def set_managed_tags(self, item_id, item, prefix, tags, fallback_rating="", legacy_prefixes=()):
        self.tag_writes.setdefault(item_id, []).append(list(tags))

    def get_current_tags(self, item_ids):
        return {i: self.tag_writes[i][-1] for i in item_ids if i in self.tag_writes}

    def refresh_item(self, item_id):
        pass

    def close(self):
        pass


def _two_incremental_scans(tmp_path, monkeypatch, stored_rule_version: int):
    """A row the pre-B13 rule wrote (1440x1080 -> 720p) with its file unchanged, then two
    incremental scans on unchanged config. Returns (paths probed, tag writes, final class)."""
    from app import arr_sync

    media = tmp_path / "Film (1999)" / "film.mkv"
    media.parent.mkdir()
    media.write_bytes(b"")
    mtime = media.stat().st_mtime
    db = tmp_path / "state.db"
    migrate.upgrade_to_head(db)
    engine = migrate._engine(db)
    maker = sessionmaker(bind=engine)

    s = maker()
    state.upsert_media_state(s, "jellyfin:f", "jellyfin", str(media), "720p", ["EN"], ["xt-720p"], None, mtime)
    monkeypatch.setattr(pipeline, "RESOLUTION_RULE_VERSION", stored_rule_version)
    state.set_meta(s, pipeline._TAG_CONFIG_KEY, pipeline._tag_config_hash(AppConfig()))
    monkeypatch.undo()
    s.close()

    probed: list[str] = []

    def probe(path):
        probed.append(path)
        return MediaInfo(resolution(1440, 1080), ["EN"], ["eng"], "H.264", None, [AudioTrack("EN", "AAC")], [])

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
        s = maker()
        final = s.get(MediaState, "jellyfin:f").resolution
        s.close()
    finally:
        engine.dispose()
    return probed, jf.tag_writes, final


def test_the_upgrade_re_tags_an_unchanged_file_once_and_then_not_again(tmp_path, monkeypatch):
    """Same config, new code: the first scan is a full re-tag that re-probes the file and
    moves it to 1080p; the second is incremental again and leaves it alone."""
    probed, writes, final = _two_incremental_scans(tmp_path, monkeypatch, pipeline.RESOLUTION_RULE_VERSION - 1)
    assert len(probed) == 1
    assert len(writes["f"]) == 1
    assert "xt-1080p" in writes["f"][0] and "xt-720p" not in writes["f"][0]
    assert final == "1080p"


def test_without_a_rule_bump_the_unchanged_file_is_not_re_tagged(tmp_path, monkeypatch):
    """The control: the same index stored under the CURRENT rule version is skipped, so
    the test above is measuring the version term, not some other re-tag."""
    probed, writes, final = _two_incremental_scans(tmp_path, monkeypatch, pipeline.RESOLUTION_RULE_VERSION)
    assert probed == []
    assert writes == {}
    assert final == "720p"
