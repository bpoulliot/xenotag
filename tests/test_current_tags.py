"""Roadmap B18: a scan reads each item's current tags by ``/Items?Ids=`` before writing.

On production the recursive ``/Items`` listing served stale ``Tags`` while
``Ids=`` was current. ``set_managed_tags()`` keeps the listing's non-managed
tags and U9's drift warning compares its managed ones, so both now see the
batched ``Ids=`` read; an id the read does not return keeps the listing's copy.

These drive a real ``JellyfinClient`` over ``httpx.MockTransport``, so the
requests asserted on are the ones the client really sends.
"""

from __future__ import annotations

import json
import logging
import uuid
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy.orm import sessionmaker

from app import arr_sync, migrate, pipeline, state
from app.clients import jellyfin as jf_module
from app.clients.jellyfin import TAG_READ_BATCH, JellyfinClient
from app.config import AppConfig
from app.scanner import AudioTrack, MediaInfo

URL = "http://jf.test"


class FakeServer:
    """A Jellyfin that serves a (possibly stale) listing and a current ``Ids=`` read."""

    def __init__(self, listing: list[dict], current: dict[str, list[str]], ids_status: int = 200):
        self.listing = listing
        self.current = current
        self.ids_status = ids_status
        self.log: list[tuple[str, str, dict]] = []  # (method, path, query)
        self.lines: list[int] = []  # request-line length of each Ids= read
        self.writes: dict[str, dict] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        query = {k: v[0] for k, v in parse_qs(urlsplit(str(request.url)).query).items()}
        path = request.url.path
        self.log.append((request.method, path, query))
        if request.method == "GET" and path == "/Items" and "Ids" in query:
            self.lines.append(len(f"GET {request.url.raw_path.decode()} HTTP/1.1"))
            if self.ids_status != 200:
                return httpx.Response(self.ids_status)
            ids = query["Ids"].split(",")
            items = [{"Id": i, "Tags": self.current[i]} for i in ids if i in self.current]
            return httpx.Response(200, json={"Items": items, "TotalRecordCount": len(items)})
        if request.method == "GET" and path == "/Items":
            return httpx.Response(200, json={"Items": self.listing, "TotalRecordCount": len(self.listing)})
        if request.method == "POST" and path.endswith("/Refresh"):
            return httpx.Response(204)
        if request.method == "POST" and path.startswith("/Items/"):
            body = json.loads(request.content)
            self.writes[path.split("/")[2]] = body
            self.current[path.split("/")[2]] = body["Tags"]  # a write that sticks (B17 reads it back)
            return httpx.Response(204)
        return httpx.Response(404)


def _client(server: FakeServer) -> JellyfinClient:
    return JellyfinClient(URL, "k", transport=httpx.MockTransport(server))


def _ids(n: int) -> list[str]:
    return [uuid.uuid4().hex for _ in range(n)]  # Jellyfin ids are 32 hex digits


def _ids_reads(server: FakeServer) -> list[list[str]]:
    return [q["Ids"].split(",") for m, p, q in server.log if m == "GET" and "Ids" in q]


# ── the client method ───────────────────────────────────────────────────────
@pytest.mark.parametrize("n, reads", [(0, 0), (1, 1), (TAG_READ_BATCH, 1), (TAG_READ_BATCH + 1, 2), (250, 3)])
def test_get_current_tags_batches_at_the_boundary(n, reads):
    ids = _ids(n)
    server = FakeServer([], {i: [f"t-{i}"] for i in ids})
    with _client(server) as jf:
        got = jf.get_current_tags(ids)
    assert got == {i: [f"t-{i}"] for i in ids}
    batches = _ids_reads(server)
    assert len(batches) == reads
    assert [i for b in batches for i in b] == ids  # every id once, in order
    assert all(len(b) <= TAG_READ_BATCH for b in batches)
    assert all(q.get("Fields") == "Tags" for m, p, q in server.log)
    assert all(m == "GET" for m, p, q in server.log)  # a read, never a write


def test_a_full_batch_fits_a_default_request_line():
    """The measurement behind TAG_READ_BATCH: 100 ids, URL-encoded, under Kestrel's 8 kB."""
    ids = _ids(TAG_READ_BATCH)
    server = FakeServer([], {})
    with _client(server) as jf:
        jf.get_current_tags(ids)
    [line] = server.lines
    assert 3_000 < line < 4_500, line  # ~3.6 kB: 100 x (32 hex + an encoded comma) + the rest
    assert line < 8_192


def test_get_current_tags_omits_an_id_jellyfin_does_not_return_and_dedupes():
    a, b = _ids(2)
    server = FakeServer([], {a: ["x"]})
    with _client(server) as jf:
        assert jf.get_current_tags([a, b, a, ""]) == {a: ["x"]}
    assert _ids_reads(server) == [[a, b]]


# ── a scan ──────────────────────────────────────────────────────────────────
def _info():
    return MediaInfo("1080p", ["EN"], ["eng"], "H.264", None, [AudioTrack("EN", "AAC")], [])


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

    def movie(item_id: str, tags: list[str]) -> dict:
        media = tmp_path / item_id / "film.mkv"
        media.parent.mkdir()
        media.write_bytes(b"")
        return {
            "Id": item_id,
            "Name": f"Film {item_id[:6]}",
            "Type": "Movie",
            "Path": str(media.parent),
            "MediaSources": [{"Path": str(media)}],
            "OfficialRating": "PG",
            "Genres": ["Drama"],
            "Tags": list(tags),
        }

    def row(item_id: str, tags: list[str]) -> None:
        s = maker()
        state.upsert_media_state(s, f"jellyfin:{item_id}", "jellyfin", "/old", "1080p", [], tags, None, 0.0)
        s.close()

    def run(server: FakeServer) -> None:
        monkeypatch.setattr(pipeline, "_clients_from_config", lambda c: (_client(server), [], []))
        assert pipeline.progress.try_start()
        pipeline._run_scan(AppConfig(), incremental=False)

    yield movie, row, run
    engine.dispose()


WRITTEN = ["xt-1080p", "xt-H264", "xt-EN", "xt-AAC"]  # what the default config builds for _info()


def test_the_write_keeps_the_current_non_managed_tag_not_the_listings(scan):
    movie, _, run = scan
    [a] = _ids(1)
    # Someone tagged it "favourite" after the listing went stale, and dropped "old".
    stale = movie(a, ["old", "xt-SD"])
    server = FakeServer([stale], {a: ["favourite", "xt-SD"]})
    run(server)
    assert server.writes[a]["Tags"] == ["favourite", *WRITTEN]


def test_the_write_is_otherwise_identical(scan):
    """Only Tags differ from the write a current listing would have produced."""
    movie, _, run = scan
    [a] = _ids(1)
    stale = movie(a, ["old"])
    stale_server = FakeServer([stale], {a: ["favourite"]})
    run(stale_server)
    current_server = FakeServer([{**stale, "Tags": ["favourite"]}], {a: ["favourite"]})
    run(current_server)
    assert stale_server.writes[a] == current_server.writes[a]
    # And the same requests, in the same order, as each other.
    assert [(m, p) for m, p, q in stale_server.log] == [(m, p) for m, p, q in current_server.log]


def _drift(caplog):
    return [r.getMessage() for r in caplog.records if "Tag drift" in r.getMessage()]


def test_drift_is_judged_on_the_current_read_not_the_listing(scan, caplog):
    movie, row, run = scan
    quiet, loud = _ids(2)
    # quiet: the listing is stale (old spelling), Jellyfin really carries what xenotag wrote.
    row(quiet, WRITTEN)
    # loud: the listing still agrees with the row, but Jellyfin has lost a tag since.
    row(loud, WRITTEN)
    server = FakeServer(
        [movie(quiet, ["xt-H.264"]), movie(loud, WRITTEN)],
        {quiet: list(WRITTEN), loud: WRITTEN[:-1]},
    )
    with caplog.at_level(logging.WARNING, logger="app.pipeline"):
        run(server)
    [line] = _drift(caplog)
    assert loud in line and "['xt-AAC']" in line
    assert quiet not in line


def test_scan_reads_in_batches_each_before_its_writes(scan, monkeypatch):
    movie, _, run = scan
    monkeypatch.setattr(pipeline, "TAG_READ_BATCH", 2)
    monkeypatch.setattr(jf_module, "TAG_READ_BATCH", 2)
    ids = _ids(5)
    server = FakeServer([movie(i, []) for i in ids], {i: [] for i in ids})
    run(server)
    reads = _ids_reads(server)
    # Each batch is read twice: before its writes (B18) and after them, the read-back (B17).
    assert sorted(len(b) for b in reads) == [1, 1, 2, 2, 2, 2]
    assert sorted(i for b in reads for i in b) == sorted(ids * 2)
    assert [sorted(b) for b in reads[0::2]] == [sorted(b) for b in reads[1::2]]
    assert set(server.writes) == set(ids)
    # Every write follows the Ids= read that covered it, and precedes the next read.
    seq = [("read", tuple(q["Ids"].split(","))) for m, p, q in server.log if "Ids" in q]
    order = [
        ("read", tuple(q["Ids"].split(","))) if "Ids" in q else ("write", p.split("/")[2])
        for m, p, q in server.log
        if "Ids" in q or (m == "POST" and not p.endswith("/Refresh"))
    ]
    assert len(seq) == 6
    current: tuple = ()
    for kind, what in order:
        if kind == "read":
            current = what
        else:
            assert what in current


def test_an_id_the_read_does_not_return_keeps_the_listing_copy_and_logs_once(scan, caplog):
    movie, _, run = scan
    a, b, c = _ids(3)
    server = FakeServer([movie(a, ["old"]), movie(b, ["kept"]), movie(c, ["also"])], {a: ["favourite"]})
    with caplog.at_level(logging.WARNING, logger="app.pipeline"):
        run(server)
    assert server.writes[a]["Tags"] == ["favourite", *WRITTEN]
    assert server.writes[b]["Tags"] == ["kept", *WRITTEN]
    assert server.writes[c]["Tags"] == ["also", *WRITTEN]
    unavailable = [r for r in caplog.records if "Current tags unavailable" in r.getMessage()]
    assert len(unavailable) == 1
    assert any("2 item(s) used the listing's copy" in r.getMessage() for r in caplog.records)


def test_a_failed_read_falls_back_without_failing_the_items(scan, caplog):
    movie, _, run = scan
    a, b = _ids(2)
    server = FakeServer([movie(a, ["old"]), movie(b, ["kept"])], {}, ids_status=500)
    with caplog.at_level(logging.WARNING, logger="app.pipeline"):
        run(server)
    assert server.writes[a]["Tags"] == ["old", *WRITTEN]
    assert server.writes[b]["Tags"] == ["kept", *WRITTEN]
    assert len([r for r in caplog.records if "Current tags unavailable" in r.getMessage()]) == 1
