"""Roadmap U9, drift-detection half: warn when Jellyfin's ``xt-`` tags are not what xenotag wrote.

The check compares the item's current managed tags with the row's
``tags_applied`` just before the write, logs one WARNING line naming the item
and the added/missing tags, counts it, and changes nothing about the write.
"""

from __future__ import annotations

import json
import logging

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import metrics, pipeline
from app.arr_sync import ArrTagSync
from app.clients.jellyfin import JellyfinClient
from app.config import AppConfig
from app.scanner import AudioTrack, MediaInfo
from app.state import Base, MediaState

ROW = ["xt-1080p", "xt-H264", "xt-EN", "xt-AAC", "xt-PG"]


class FakeJellyfin:
    """Records every write; ``get_tags`` is the real client's (it reads the item dict).

    ``current`` is what ``/Items?Ids=`` serves (B18); ``_process`` makes it the item's own tags.
    """

    get_tags = JellyfinClient.get_tags

    def __init__(self, current=None):
        self.current = dict(current or {})
        self.writes: list[tuple] = []

    def get_current_tags(self, item_ids):
        return {i: list(self.current[i]) for i in item_ids if i in self.current}

    def set_managed_tags(self, item_id, item, prefix, tags, fallback_rating="", legacy_prefixes=()):
        self.writes.append(("tags", item_id, tuple(item.get("Tags") or []), prefix, tuple(tags), fallback_rating))

    def refresh_item(self, item_id):
        self.writes.append(("refresh", item_id))


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _row(session, item_id, tags):
    session.add(MediaState(item_id=f"jellyfin:{item_id}", source="jellyfin", tags_applied=json.dumps(tags)))
    session.commit()


def _item(item_id, tags, path):
    return {"Id": item_id, "Name": f"Film {item_id}", "Type": "Movie", "Path": path, "Tags": list(tags)}


def _info(codec="H.264"):
    return MediaInfo("1080p", ["EN"], ["eng"], codec, None, [AudioTrack("EN", "AAC")], [])


def _process(session, item, tmp_path, info=None):
    cfg = AppConfig()
    arr = ArrTagSync(cfg, [], [])
    jf = FakeJellyfin({item["Id"]: item["Tags"]})
    folder = tmp_path / item["Id"]
    folder.mkdir(exist_ok=True)
    pipeline._process_one_item(jf, arr, session, cfg, item, str(folder / "f.mkv"), "", 1.0, info or _info())
    return jf


def _drift_lines(caplog):
    return [r for r in caplog.records if r.levelno == logging.WARNING and "Tag drift" in r.getMessage()]


def _drift_count():
    return metrics.REGISTRY.get_sample_value("xenotag_tag_drift_total") or 0.0


def test_drift_is_warned_once_naming_the_item_and_the_tags(session, tmp_path, caplog):
    _row(session, "a", ROW)
    # B12's shape plus the *arrs' NFO route: two tags gone, a lowercase variant added.
    item = _item("a", ["xt-1080p", "xt-H264", "xt-aac", "xt-PG", "luxe", "heist"], "/m/a")
    before = _drift_count()
    with caplog.at_level(logging.WARNING, logger="app.pipeline"):
        _process(session, item, tmp_path)
    [line] = _drift_lines(caplog)
    msg = line.getMessage()
    assert "Film a (a)" in msg
    assert "['xt-aac']" in msg and "['xt-AAC', 'xt-EN']" in msg
    assert "luxe" not in msg and "heist" not in msg
    assert "\n" not in msg
    assert _drift_count() == before + 1


def test_every_managed_tag_lost_is_drift(session, tmp_path, caplog):
    """B12: five films carried no ``xt-`` tag at all; the next scan to reach one says so."""
    _row(session, "a", ROW)
    with caplog.at_level(logging.WARNING, logger="app.pipeline"):
        _process(session, _item("a", ["luxe", "av1", "nav1s"], "/m/a"), tmp_path)
    [line] = _drift_lines(caplog)
    assert f"lacks {sorted(ROW)}" in line.getMessage()


def test_no_drift_is_silent(session, tmp_path, caplog):
    _row(session, "a", ROW)
    before = _drift_count()
    with caplog.at_level(logging.WARNING, logger="app.pipeline"):
        _process(session, _item("a", list(reversed(ROW)), "/m/a"), tmp_path)
    assert _drift_lines(caplog) == []
    assert _drift_count() == before


def test_a_difference_outside_the_managed_prefix_is_not_drift(session, tmp_path, caplog):
    _row(session, "a", [*ROW, "anime"])  # a stray non-xt- entry in a row is ignored too
    item = _item("a", [*ROW, "luxe", "dual-audio", "mf-1080p", "XT-EN"], "/m/a")
    with caplog.at_level(logging.WARNING, logger="app.pipeline"):
        _process(session, item, tmp_path)
    assert _drift_lines(caplog) == []


@pytest.mark.parametrize("row", [None, "null-tags"])
def test_a_first_write_is_not_drift(session, tmp_path, caplog, row):
    if row:
        session.add(MediaState(item_id="jellyfin:a", source="jellyfin", tags_applied=None))
        session.commit()
    with caplog.at_level(logging.WARNING, logger="app.pipeline"):
        _process(session, _item("a", ["xt-4K", "xt-HEVC"], "/m/a"), tmp_path)
    assert _drift_lines(caplog) == []


def test_a_vocabulary_change_is_not_drift(session, tmp_path, caplog):
    """After a respelling (B9) the plan differs from the row; Jellyfin still equals the row.

    The check compares Jellyfin with the row, never with the plan, so the full
    re-tag a vocabulary change forces warns only for items someone else touched.
    """
    old = ["xt-1080p", "xt-H.264", "xt-EN", "xt-AAC"]
    _row(session, "a", old)
    _row(session, "b", old)
    with caplog.at_level(logging.WARNING, logger="app.pipeline"):
        jf = _process(session, _item("a", old, "/m/a"), tmp_path)
        _process(session, _item("b", ["xt-1080p", "xt-EN", "xt-AAC"], "/m/b"), tmp_path)
    written = jf.writes[0][4]
    assert "xt-H.264" not in written and written != tuple(old)  # the plan did change
    [line] = _drift_lines(caplog)  # only b, which lost a tag, is reported
    assert "(b)" in line.getMessage() and "['xt-H.264']" in line.getMessage()


def test_the_write_is_identical_with_and_without_drift(tmp_path, caplog):
    def run(jf_tags):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        s = sessionmaker(bind=engine)()
        _row(s, "a", ROW)
        jf = _process(s, _item("a", jf_tags, "/m/a"), tmp_path)
        row = s.get(MediaState, "jellyfin:a")
        stored = json.loads(row.tags_applied)
        s.close()
        return jf.writes, stored

    with caplog.at_level(logging.WARNING, logger="app.pipeline"):
        clean_writes, clean_row = run([*ROW, "luxe"])
        assert _drift_lines(caplog) == []
        drift_writes, drift_row = run(["xt-4K", "luxe"])
        assert len(_drift_lines(caplog)) == 1

    # The tags handed to Jellyfin, the fallback rating and every other call are the
    # same; only the item's own current tags (the input) differ.
    def strip_input(writes):
        return [w[:2] + w[3:] if w[0] == "tags" else w for w in writes]

    assert strip_input(clean_writes) == strip_input(drift_writes)
    assert len(clean_writes) >= 1 and clean_writes[0][0] == "tags"
    assert clean_row == drift_row


def test_a_broken_check_never_stops_the_write(session, tmp_path, monkeypatch):
    _row(session, "a", ROW)

    def boom(*a, **k):
        raise RuntimeError("drift check exploded")

    monkeypatch.setattr(pipeline, "_tag_drift", boom)
    jf = _process(session, _item("a", ["xt-4K"], "/m/a"), tmp_path)
    assert jf.writes and jf.writes[0][0] == "tags"


def test_tag_drift_unit():
    row = MediaState(item_id="jellyfin:x", source="jellyfin", tags_applied=json.dumps(["xt-A", "xt-B"]))
    assert pipeline._tag_drift(["xt-B", "xt-A", "other"], row, "xt-") is None
    assert pipeline._tag_drift(["xt-A", "xt-C"], row, "xt-") == (["xt-C"], ["xt-B"])
    assert pipeline._tag_drift(["xt-A"], None, "xt-") is None
    assert pipeline._tag_drift(["xt-A"], MediaState(tags_applied="not json"), "xt-") is None
    assert pipeline._tag_drift([], MediaState(tags_applied="[]"), "xt-") is None
    assert pipeline._tag_drift(["xt-A"], MediaState(tags_applied="[]"), "xt-") == (["xt-A"], [])
