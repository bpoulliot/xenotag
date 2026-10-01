"""Roadmap B18: the tags a write keeps, and the drift check reads, come from ``/Items?Ids=``.

Production's recursive ``/Items`` listing served pre-re-tag ``Tags`` for every
item changed since some point, while ``Ids=`` was current. These tests drive
the real ``JellyfinClient`` over a mock transport whose listing copy of the
item is stale and whose ``Ids=`` read is current, and read the POST body back.
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import pipeline
from app.arr_sync import ArrTagSync
from app.clients.jellyfin import JellyfinClient
from app.config import AppConfig
from app.scanner import AudioTrack, MediaInfo
from app.state import Base, MediaState

ROW = ["xt-1080p", "xt-H264", "xt-EN", "xt-AAC"]


class Server:
    """``GET /Items?Ids=`` answers from ``current``; ``POST /Items/{id}`` is recorded."""

    def __init__(self, current: dict[str, list[str]]):
        self.current = current
        self.id_reads: list[list[str]] = []
        self.posts: dict[str, dict] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/Items":
            ids = request.url.params["Ids"].split(",")
            assert request.url.params["Fields"] == "Tags"
            self.id_reads.append(ids)
            items = [{"Id": i, "Tags": self.current[i]} for i in ids if i in self.current]
            return httpx.Response(200, json={"Items": items, "TotalRecordCount": len(items)})
        if request.method == "POST" and request.url.path.startswith("/Items/"):
            self.posts[request.url.path.removeprefix("/Items/")] = json.loads(request.content)
            return httpx.Response(204)
        return httpx.Response(404)


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _process(session, tmp_path, listing_tags, current):
    server = Server(current)
    jf = JellyfinClient("http://jf", "k", transport=httpx.MockTransport(server))
    item = {"Id": "a", "Name": "Film a", "Type": "Movie", "Path": "/m/a", "Tags": list(listing_tags)}
    info = MediaInfo("1080p", ["EN"], ["eng"], "H.264", None, [AudioTrack("EN", "AAC")], [])
    cfg = AppConfig()
    folder = tmp_path / "a"
    folder.mkdir(exist_ok=True)
    pipeline._process_one_item(jf, ArrTagSync(cfg, [], []), session, cfg, item, str(folder / "f.mkv"), "", 1.0, info)
    jf.close()
    return server


def _drift_lines(caplog):
    return [r for r in caplog.records if r.levelno == logging.WARNING and "Tag drift" in r.getMessage()]


def test_the_write_keeps_the_current_tags_not_the_listings(session, tmp_path):
    # Stale listing: the operator's "favourite" is not in it yet and "old-list" is long gone.
    server = _process(session, tmp_path, ["old-list", "xt-H.264"], {"a": ["luxe", "favourite", "xt-H264"]})
    assert server.id_reads == [["a"]]
    tags = server.posts["a"]["Tags"]
    assert "favourite" in tags and "luxe" in tags
    assert "old-list" not in tags
    assert {t for t in tags if t.startswith("xt-")} == set(ROW)


def test_an_item_the_id_read_does_not_return_is_not_written(session, tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="app.pipeline"):
        server = _process(session, tmp_path, ["luxe"], {})
    assert server.posts == {}
    assert any("Jellyfin tag error for Film a" in r.getMessage() for r in caplog.records)


def test_a_stale_listing_gives_no_false_drift(session, tmp_path, caplog):
    """The listing still shows the pre-re-tag spelling; Jellyfin itself matches the row."""
    session.add(MediaState(item_id="jellyfin:a", source="jellyfin", tags_applied=json.dumps(ROW)))
    session.commit()
    with caplog.at_level(logging.WARNING, logger="app.pipeline"):
        _process(session, tmp_path, ["xt-1080p", "xt-H.264", "xt-EN", "xt-AAC"], {"a": [*ROW, "luxe"]})
    assert _drift_lines(caplog) == []


def test_drift_the_listing_hides_is_reported(session, tmp_path, caplog):
    """B12's shape, seen only by id: Jellyfin dropped the tags after the listing went stale."""
    session.add(MediaState(item_id="jellyfin:a", source="jellyfin", tags_applied=json.dumps(ROW)))
    session.commit()
    with caplog.at_level(logging.WARNING, logger="app.pipeline"):
        _process(session, tmp_path, [*ROW, "luxe"], {"a": ["luxe", "heist"]})
    [line] = _drift_lines(caplog)
    assert f"lacks {sorted(ROW)}" in line.getMessage()


def test_get_current_tags_batches_and_omits_missing_ids():
    ids = [f"id{n:03d}" for n in range(250)]
    server = Server({i: [f"t-{i}"] for i in ids if i != "id007"})
    with JellyfinClient("http://jf", "k", transport=httpx.MockTransport(server)) as jf:
        current = jf.get_current_tags(ids)
    assert [len(r) for r in server.id_reads] == [100, 100, 50]
    assert [i for r in server.id_reads for i in r] == ids
    assert "id007" not in current and len(current) == 249
    assert current["id123"] == ["t-id123"]
