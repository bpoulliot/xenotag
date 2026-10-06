"""Roadmap B17: a Jellyfin tag write is recorded from what Jellyfin reads back, not from what was sent.

In B9's re-tag Jellyfin re-saved 22 of 9,340 items with their old tags shortly
after the refresh xenotag requests, and 8 writes timed out client-side; all 30
were recorded as tagged. Now each batch's writes are read back by ``Ids=``
after a short settle delay: a match is recorded, a mismatch is written once
more and read again, and a write -- or a read-back -- that raises records
nothing, so the next scan retries the item.

These drive a real ``JellyfinClient`` over ``httpx.MockTransport``, so the
requests asserted on are the ones the client really sends.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from PIL import Image
from sqlalchemy.orm import sessionmaker

from app import arr_sync, metrics, migrate, pipeline, state
from app.arr_sync import ArrTagSync
from app.clients.jellyfin import JellyfinClient
from app.config import AppConfig
from app.scanner import AudioTrack, MediaInfo
from app.state import MediaState, ScanRun

URL = "http://jf.test"
WRITTEN = ["xt-1080p", "xt-H264", "xt-EN", "xt-AAC"]  # what the default config builds for _info()


class Server:
    """A Jellyfin that keeps what it is sent -- except ``undo[id]`` of that item's tag writes.

    An undone write returns 204 and leaves the item's tags as they were, which
    is what B17's 22 films looked like from xenotag. ``timeout_writes`` makes
    every tag write time out client-side; ``fail_ids_from`` fails every ``Ids=``
    read from that one on (1-based).
    """

    def __init__(
        self,
        listing: list[dict],
        undo: dict[str, int] | None = None,
        timeout_writes: bool = False,
        fail_ids_from: int | None = None,
        drop_from_readback: tuple[str, ...] = (),
    ):
        self.listing = listing
        self.current = {i["Id"]: list(i.get("Tags") or []) for i in listing}
        self.undo = dict(undo or {})
        self.timeout_writes = timeout_writes
        self.fail_ids_from = fail_ids_from
        self.drop_from_readback = drop_from_readback
        self.log: list[tuple[str, str, dict]] = []
        self.writes: list[tuple[str, dict]] = []
        self.ids_reads: list[list[str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        query = {k: v[0] for k, v in parse_qs(urlsplit(str(request.url)).query).items()}
        path = request.url.path
        self.log.append((request.method, path, query))
        if request.method == "GET" and path == "/Items" and "Ids" in query:
            ids = query["Ids"].split(",")
            self.ids_reads.append(ids)
            if self.fail_ids_from is not None and len(self.ids_reads) >= self.fail_ids_from:
                return httpx.Response(500)
            hidden = self.drop_from_readback if len(self.ids_reads) > 1 else ()
            items = [{"Id": i, "Tags": self.current[i]} for i in ids if i in self.current and i not in hidden]
            return httpx.Response(200, json={"Items": items, "TotalRecordCount": len(items)})
        if request.method == "GET" and path == "/Items":
            return httpx.Response(200, json={"Items": self.listing, "TotalRecordCount": len(self.listing)})
        if request.method == "POST" and path.endswith("/Refresh"):
            return httpx.Response(204)
        if request.method == "POST" and path.startswith("/Items/"):
            if self.timeout_writes:
                raise httpx.ReadTimeout("timed out", request=request)
            item_id = path.split("/")[2]
            body = json.loads(request.content)
            self.writes.append((item_id, body))
            if self.undo.get(item_id, 0) > 0:
                self.undo[item_id] -= 1
            else:
                self.current[item_id] = body["Tags"]
            return httpx.Response(204)
        return httpx.Response(404)


def _client(server: Server) -> JellyfinClient:
    return JellyfinClient(URL, "k", transport=httpx.MockTransport(server))


def _ids(n: int) -> list[str]:
    return [uuid.uuid4().hex for _ in range(n)]


def _info():
    return MediaInfo("1080p", ["EN"], ["eng"], "H.264", None, [AudioTrack("EN", "AAC")], [])


def _mismatch(result: str) -> float:
    return metrics.REGISTRY.get_sample_value("xenotag_tag_writeback_mismatch_total", {"result": result}) or 0.0


@pytest.fixture
def scan(tmp_path, monkeypatch):
    db = tmp_path / "state.db"
    migrate.upgrade_to_head(db)
    engine = migrate._engine(db)
    maker = sessionmaker(bind=engine)
    monkeypatch.setattr(pipeline, "probe_file", lambda p: _info())
    monkeypatch.setattr(pipeline, "get_session", maker)
    monkeypatch.setattr(pipeline, "run_deleted_items", lambda c, **kw: None)
    monkeypatch.setenv("ARR_SYNC_REPORT", str(tmp_path / "arr-sync-report.json"))
    monkeypatch.setattr(arr_sync, "_last_report", None)

    def movie(item_id: str, tags: list[str], poster: bool = False) -> dict:
        media = tmp_path / item_id / "film.mkv"
        media.parent.mkdir()
        media.write_bytes(b"")
        if poster:
            Image.new("RGB", (400, 600), (40, 60, 90)).save(media.parent / "poster.jpg")
        return {
            "Id": item_id,
            "Name": f"Film {item_id[:6]}",
            "Type": "Movie",
            "Path": str(media.parent),
            "MediaSources": [{"Path": str(media)}],
            "OfficialRating": "",
            "Tags": list(tags),
        }

    def run(server: Server) -> None:
        monkeypatch.setattr(pipeline, "_clients_from_config", lambda c: (_client(server), [], []))
        assert pipeline.progress.try_start()
        pipeline._run_scan(AppConfig(), incremental=False)

    def row(item_id: str) -> MediaState | None:
        s = maker()
        try:
            return s.get(MediaState, f"jellyfin:{item_id}")
        finally:
            s.close()

    def seed(item_id: str, tags: list[str]) -> None:
        s = maker()
        state.upsert_media_state(s, f"jellyfin:{item_id}", "jellyfin", "/old", "SD", [], tags, None, 0.0)
        s.close()

    def runs() -> list[ScanRun]:
        s = maker()
        try:
            return s.query(ScanRun).all()
        finally:
            s.close()

    yield movie, run, row, seed, runs
    engine.dispose()
    pipeline.progress.running = False


def _tags(row: MediaState | None) -> list[str] | None:
    return None if row is None or row.tags_applied is None else json.loads(row.tags_applied)


def _did_not_stick(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if "did not stick" in r.getMessage()]


# ── a write that sticks ─────────────────────────────────────────────────────
def test_a_write_that_sticks_is_recorded_as_written(scan, caplog):
    movie, run, row, _, _ = scan
    [a] = _ids(1)
    item = movie(a, ["favourite"])
    server = Server([item])
    before = (_mismatch("fixed"), _mismatch("unresolved"))
    with caplog.at_level(logging.WARNING, logger="app.pipeline"):
        run(server)
    r = row(a)
    assert _tags(r) == WRITTEN
    assert r.file_mtime == pytest.approx((pipeline.Path(item["MediaSources"][0]["Path"])).stat().st_mtime)
    assert len(server.writes) == 1
    assert server.ids_reads == [[a], [a]]  # B18's read before the write, B17's after it
    assert (_mismatch("fixed"), _mismatch("unresolved")) == before
    assert not [r for r in caplog.records if "B17" in r.getMessage()]


def test_the_happy_path_sends_the_same_write_calls_and_reads_back_after_the_refresh(scan):
    """No extra write, the same body; the read-back is the last request, after the poster's refresh."""
    movie, run, _, _, _ = scan
    [a] = _ids(1)
    server = Server([movie(a, ["favourite", "xt-SD"], poster=True)])
    run(server)
    calls = [(m, p) for m, p, q in server.log]
    assert calls == [
        ("GET", "/Items"),  # the listing
        ("GET", "/Items"),  # B18: current tags by Ids=
        ("POST", f"/Items/{a}"),
        ("POST", f"/Items/{a}/Refresh"),
        ("GET", "/Items"),  # B17: the read-back by Ids=
    ]
    assert "Ids" in server.log[1][2] and "Ids" in server.log[4][2]
    [(_, body)] = server.writes
    assert body["Tags"] == ["favourite", *WRITTEN]


# ── a write Jellyfin undoes ─────────────────────────────────────────────────
def test_a_write_undone_once_is_written_again_and_recorded(scan, caplog):
    movie, run, row, _, _ = scan
    [a] = _ids(1)
    server = Server([movie(a, ["favourite", "xt-H.264"])], undo={a: 1})
    before = _mismatch("fixed")
    with caplog.at_level(logging.WARNING, logger="app.pipeline"):
        run(server)
    assert [i for i, _ in server.writes] == [a, a]
    # The retry is built from the tags just read: the non-managed one survives.
    assert server.writes[1][1]["Tags"] == ["favourite", *WRITTEN]
    assert server.current[a] == ["favourite", *WRITTEN]
    assert server.ids_reads == [[a], [a], [a]]
    assert _tags(row(a)) == WRITTEN
    assert _mismatch("fixed") == before + 1
    assert _did_not_stick(caplog) == []


def test_a_write_undone_twice_records_what_was_read_and_warns_once(scan, caplog):
    movie, run, row, _, _ = scan
    [a] = _ids(1)
    server = Server([movie(a, ["favourite", "xt-H.264", "xt-1080p"])], undo={a: 2})
    before = _mismatch("unresolved")
    with caplog.at_level(logging.WARNING, logger="app.pipeline"):
        run(server)
    assert len(server.writes) == 2  # one retry, no more
    assert len(server.ids_reads) == 3
    assert _tags(row(a)) == ["xt-H.264", "xt-1080p"]  # Jellyfin's managed tags, not "favourite"
    [line] = _did_not_stick(caplog)
    assert f"Film {a[:6]} ({a})" in line
    assert "lacks ['xt-AAC', 'xt-EN', 'xt-H264']" in line and "has ['xt-H.264'] not written" in line
    assert "\n" not in line
    assert _mismatch("unresolved") == before + 1


def test_only_the_undone_items_are_written_again_and_re_read(scan, monkeypatch):
    movie, run, row, _, _ = scan
    a, b, c = _ids(3)
    server = Server([movie(a, []), movie(b, []), movie(c, [])], undo={b: 1})
    run(server)
    assert sorted(i for i, _ in server.writes) == sorted([a, b, b, c])
    # One read-back for the whole batch; the second read covers b alone.
    assert [sorted(r) for r in server.ids_reads] == [sorted([a, b, c]), sorted([a, b, c]), [b]]
    assert [_tags(row(i)) for i in (a, b, c)] == [WRITTEN] * 3


# ── nothing is recorded when the write or the read fails ────────────────────
def test_a_write_that_raises_records_nothing(scan, caplog):
    """A client timeout: the row keeps its old tags and mtime, so the next scan retries it."""
    movie, run, row, seed, runs = scan
    old, new = _ids(2)
    seed(old, ["xt-H.264"])
    server = Server([movie(old, ["xt-H.264"]), movie(new, [])], timeout_writes=True)
    with caplog.at_level(logging.WARNING, logger="app.pipeline"):
        run(server)
    r = row(old)
    assert _tags(r) == ["xt-H.264"] and r.file_mtime == 0.0 and r.file_path == "/old"
    assert row(new) is None
    assert len(server.ids_reads) == 1  # nothing written, nothing to read back
    assert sum("Jellyfin tag error" in r.getMessage() for r in caplog.records) == 2
    assert runs()[-1].completed_at is not None


def test_a_read_back_that_raises_records_nothing_and_the_scan_finishes(scan, caplog):
    movie, run, row, seed, runs = scan
    a, b = _ids(2)
    seed(a, ["xt-H.264"])
    server = Server([movie(a, []), movie(b, [])], fail_ids_from=2)
    with caplog.at_level(logging.WARNING, logger="app.pipeline"):
        run(server)
    assert len(server.writes) == 2
    assert _tags(row(a)) == ["xt-H.264"] and row(a).file_mtime == 0.0
    assert row(b) is None
    assert any("Tag read-back failed for 2 item(s)" in r.getMessage() for r in caplog.records)
    assert any("2 not recorded" in r.getMessage() for r in caplog.records)  # the scan's summary
    assert runs()[-1].completed_at is not None
    assert not pipeline.progress.running


def test_a_retry_whose_read_back_raises_records_nothing(scan):
    movie, run, row, _, _ = scan
    [a] = _ids(1)
    server = Server([movie(a, [])], undo={a: 1}, fail_ids_from=3)
    run(server)
    assert len(server.writes) == 2
    assert row(a) is None


def test_an_item_the_read_back_does_not_return_is_not_recorded(scan):
    movie, run, row, _, _ = scan
    a, b = _ids(2)
    server = Server([movie(a, []), movie(b, [])], drop_from_readback=(b,))
    run(server)
    assert _tags(row(a)) == WRITTEN
    assert row(b) is None


# ── the settle delay ────────────────────────────────────────────────────────
def test_the_read_back_waits_a_bounded_settle_delay_each_time(scan, monkeypatch):
    movie, run, _, _, _ = scan
    monkeypatch.setattr(pipeline, "TAG_READBACK_SETTLE_S", 2.0)
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    a, b = _ids(2)
    run(Server([movie(a, []), movie(b, [])], undo={b: 1}))
    assert len(sleeps) == 2  # before the batch's read-back, and before the retry's
    assert all(0 < s <= 2.0 for s in sleeps)


# ── a single item (the webhook path) ────────────────────────────────────────
def test_a_single_item_is_read_back_and_recorded_before_it_returns(tmp_path):
    db = tmp_path / "state.db"
    migrate.upgrade_to_head(db)
    engine = migrate._engine(db)
    session = sessionmaker(bind=engine)()
    [a] = _ids(1)
    folder = tmp_path / a
    folder.mkdir()
    item = {"Id": a, "Name": "Film", "Type": "Movie", "Path": str(folder), "Tags": ["xt-H.264"]}
    server = Server([item], undo={a: 1})
    jf = _client(server)
    cfg = AppConfig()
    try:
        pipeline._process_one_item(
            jf, ArrTagSync(cfg, [], []), session, cfg, item, str(folder / "f.mkv"), "", 7.0, _info()
        )
        r = session.get(MediaState, f"jellyfin:{a}")
        assert json.loads(r.tags_applied) == WRITTEN and r.file_mtime == 7.0
        assert len(server.writes) == 2 and len(server.ids_reads) == 2
    finally:
        jf.close()
        session.close()
        engine.dispose()
