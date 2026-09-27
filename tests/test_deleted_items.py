"""Deleted Jellyfin items (roadmap U2): drop the index row, strip the *arr tags it owned.

The fake Jellyfin below runs behind ``httpx.MockTransport`` and drives the real
``JellyfinClient``, so the listing's pagination and the lookup by id go through
real URLs; the *arr side reuses B5's fake. Every title and path is invented.
"""

from __future__ import annotations

import json
from datetime import datetime

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app import deleted_items, pipeline
from app.clients.jellyfin import JellyfinClient
from app.clients.readonly import ReadOnlyTransport, ReadOnlyViolation
from app.config import AppConfig
from app.deleted_items import (
    DeletedItemsPass,
    PassAborted,
    complete_listing,
    confirm_deleted,
    row_folders,
)
from app.state import Base, MediaState, ScanError
from tests.test_arr_sync import FakeArr, client_for, jf_movie, jf_series, movie, series


class FakeJellyfin:
    """``GET /Items``: the paginated listing, the ``Limit=0`` count, and ``Ids=`` lookups."""

    def __init__(self, items, *, also_exists=()):
        self.items = list(items)
        self.also_exists = list(also_exists)  # answer a lookup by id, absent from the listing
        self.requests: list[tuple[str, dict]] = []
        self.page_hook = None  # (page number, payload) -> payload
        self.recount: int | None = None
        self.lookup_answers_nothing = False
        self.fail_on_page: int | None = None
        self.pages = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        self.requests.append((request.method, params))
        if request.method != "GET" or request.url.path != "/Items":
            return httpx.Response(404)
        if "Ids" in params:
            wanted = set(params["Ids"].split(","))
            found = (
                [] if self.lookup_answers_nothing else [i for i in self.items + self.also_exists if i["Id"] in wanted]
            )
            return httpx.Response(200, json={"Items": found, "TotalRecordCount": len(found)})
        if params.get("Limit") == "0":
            n = len(self.items) if self.recount is None else self.recount
            return httpx.Response(200, json={"Items": [], "TotalRecordCount": n})
        self.pages += 1
        if self.fail_on_page == self.pages:
            return httpx.Response(500, json={"message": "fake failure"})
        start, limit = int(params.get("StartIndex", 0)), int(params["Limit"])
        payload = {"Items": self.items[start : start + limit], "TotalRecordCount": len(self.items)}
        if self.page_hook:
            payload = self.page_hook(self.pages, payload)
        return httpx.Response(200, json=payload)

    def client(self, *, guarded: bool = True) -> JellyfinClient:
        transport: httpx.BaseTransport = httpx.MockTransport(self.handle)
        if guarded:
            transport = ReadOnlyTransport(transport)
        return JellyfinClient("http://jf", "key", transport=transport)


def index_db(tmp_path, rows, errors=()):
    """A state.db file: ``rows`` are (item id, file path, image path | None, last_scanned)."""
    db = tmp_path / "state.db"
    engine = create_engine(f"sqlite:///{db}")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    for item_id, file_path, image_path, last_scanned in rows:
        s.add(
            MediaState(
                item_id=f"jellyfin:{item_id}",
                source="jellyfin",
                file_path=file_path,
                image_path=image_path,
                resolution="1080p",
                tags_applied=json.dumps(["xt-1080p"]),
                last_scanned=last_scanned,
                file_mtime=1_700_000_000.0,
            )
        )
    for item_id, error_type, last_seen in errors:
        s.add(ScanError(item_id=item_id, item_name=item_id, error_type=error_type, last_seen=last_seen))
    s.commit()
    s.close()
    engine.dispose()
    return db


def row_ids(db) -> set[str]:
    engine = create_engine(f"sqlite:///{db}")
    with engine.connect() as conn:
        ids = {r[0].split(":", 1)[1] for r in conn.exec_driver_sql("SELECT item_id FROM media_state")}
    engine.dispose()
    return ids


def cfg(mode="report", arr_mode="live", max_fraction=1.0) -> AppConfig:
    return AppConfig.model_validate(
        {"arr_sync": {"mode": arr_mode}, "deleted_items": {"mode": mode, "max_fraction": max_fraction}}
    )


def labels_of(fake: FakeArr, object_id: int) -> set[str]:
    by_id = {i: label for label, i in fake.tags.items()}
    return {by_id[t] for t in fake.objects[object_id]["tags"]}


_OLD = datetime(2026, 6, 2, 4, 0)
_ERR = datetime(2026, 9, 26, 9, 0)

TAGS = {"luxe": 1, "xt-1080p": 2, "xt-en": 3, "xt-720p": 4, "xt-4k": 5, "anime": 6}


def scenario(tmp_path):
    """Live items, deleted items, and one B11 item Jellyfin still lists but the scan cannot read.

    d-kept    film re-encoded in place: its old item is gone, a live item sits in the same folder
    d-film    film gone, its Radarr movie remains, carrying a user tag and two managed ones
    d-show    series gone (no poster recorded: its folder comes from the episode path)
    d-none    film gone, no object in any instance
    d-plain   film gone, its movie carries no managed tag
    u-unread  still listed by Jellyfin; its file is gone and its scan error is newer than its row
    """
    live = [
        jf_movie("l-kept", "Kept", "/m/Kept (2020)/Kept.av1.mkv", Tmdb="1"),
        jf_series("l-show", "Live Show", "/tv/Live Show", Tvdb="50"),
        jf_movie("u-unread", "Unread", "/m/Unread (2001)/Unread.mkv", Tmdb="4"),
    ]
    rows = [
        ("l-kept", "/m/Kept (2020)/Kept.av1.mkv", "/m/Kept (2020)/poster.jpg", _ERR),
        ("l-show", "/tv/Live Show/Season 01/e1.mkv", "/tv/Live Show/poster.jpg", _ERR),
        ("u-unread", "/m/Unread (2001)/Unread.mkv", "/m/Unread (2001)/poster.jpg", _OLD),
        ("d-kept", "/m/Kept (2020)/Kept.x264.mkv", "/m/Kept (2020)/poster.jpg", _OLD),
        ("d-film", "/m/Gone (2019)/Gone.mkv", "/m/Gone (2019)/poster.jpg", _OLD),
        ("d-show", "/tv/Old Show/Season 01/e1.mkv", None, _OLD),
        ("d-none", "/m/Nowhere (1999)/n.mkv", "/m/Nowhere (1999)/poster.jpg", _OLD),
        ("d-plain", "/m/Plain (2018)/p.mkv", "/m/Plain (2018)/poster.jpg", _OLD),
    ]
    db = index_db(tmp_path, rows, errors=[("u-unread", "no_file", _ERR)])
    radarr = FakeArr(
        "radarr",
        [
            movie(1, "Kept", 1, "/m/Kept (2020)", tags=[2, 3]),
            movie(2, "Gone", 2, "/m/Gone (2019)", tags=[1, 2, 3]),
            movie(3, "Plain", 3, "/m/Plain (2018)", tags=[1]),
            movie(4, "Unread", 4, "/m/Unread (2001)", tags=[2]),
        ],
        tags=dict(TAGS),
    )
    sonarr = FakeArr(
        "sonarr",
        [series(1, "Old Show", 60, "/tv/Old Show", tags=[4, 6]), series(2, "Live Show", 50, "/tv/Live Show", tags=[2])],
        tags=dict(TAGS),
    )
    return FakeJellyfin(live), db, sonarr, radarr


def run(monkeypatch, c, jf, db, sonarrs=(), radarrs=()):
    """``run_deleted_items`` on a copied index with fake servers; the real client wiring otherwise."""
    seen: dict = {}

    def fake_build(cfg_, *, arr_writable, guards):
        seen["arr_writable"] = arr_writable
        client = jf.client(guarded=True)
        guards.append(client._client._transport)
        made = {"sonarr": [], "radarr": []}
        for fake in (*sonarrs, *radarrs):
            arr = client_for(fake, f"{fake.kind[0]}{len(made[fake.kind])}", read_only=not arr_writable)
            if not arr_writable:
                guards.append(arr._client._transport)
            made[fake.kind].append(arr)
        return client, made["sonarr"], made["radarr"]

    monkeypatch.setattr(deleted_items, "_build_clients", fake_build)
    report = deleted_items.run_deleted_items(c, db_path=db, store=False)
    return report, seen


# ── the shipped default ─────────────────────────────────────────────────────
def test_shipped_default_is_report_only():
    assert AppConfig().deleted_items.mode == "report"
    assert AppConfig().deleted_items.max_fraction == 0.15


def test_the_mode_forces_no_re_tag():
    """Turning removal on must not change the tag-config hash: it removes, it does not re-tag."""
    assert pipeline._tag_config_hash(cfg("remove", "dry_run")) == pipeline._tag_config_hash(AppConfig())
    assert pipeline._tag_config_hash(AppConfig()) == "d0c577fe5620e689"


@pytest.mark.parametrize(
    "mode, arr_mode, writable",
    [("report", "live", False), ("report", "dry_run", False), ("remove", "dry_run", False), ("remove", "live", True)],
)
def test_arr_clients_can_write_only_when_removing_and_arr_writes_are_live(mode, arr_mode, writable):
    guards: list = []
    c = AppConfig.model_validate(
        {
            "arr_sync": {"mode": arr_mode},
            "deleted_items": {"mode": mode},
            "sonarr": {"instances": [{"name": "a", "url": "http://a"}]},
            "radarr": {"instances": [{"name": "b", "url": "http://b"}]},
        }
    )
    jf, sonarrs, radarrs = deleted_items._build_clients(
        c, arr_writable=mode == "remove" and arr_mode == "live", guards=guards
    )
    try:
        assert isinstance(jf._client._transport, ReadOnlyTransport)  # U2 never writes Jellyfin
        for client in sonarrs + radarrs:
            assert isinstance(client._client._transport, ReadOnlyTransport) is not writable
        assert len(guards) == (1 if writable else 3)
    finally:
        jf.close()
        for client in sonarrs + radarrs:
            client.close()


@pytest.mark.parametrize(
    "mode, arr_mode, writable",
    [("report", "live", False), ("remove", "dry_run", False), ("remove", "live", True)],
)
def test_run_asks_for_writable_arr_clients_only_when_removing_live(tmp_path, monkeypatch, mode, arr_mode, writable):
    jf, db, sonarr, radarr = scenario(tmp_path)
    _, seen = run(monkeypatch, cfg(mode, arr_mode), jf, db, [sonarr], [radarr])
    assert seen["arr_writable"] is writable


# ── report mode changes nothing, structurally ──────────────────────────────
def test_report_mode_reports_the_plan_and_changes_nothing(tmp_path, monkeypatch):
    jf, db, sonarr, radarr = scenario(tmp_path)
    before = db.read_bytes()
    report, _ = run(monkeypatch, cfg("report"), jf, db, [sonarr], [radarr])
    assert report["status"] == "ok" and report["mode"] == "report"
    assert sonarr.writes == [] and radarr.writes == [] and db.read_bytes() == before
    assert report["guard"]["blocked"] == [] and set(report["guard"]["sent"]) == {"GET"}
    ix = report["index"]
    assert (ix["rows"], ix["deleted_rows"], ix["rows_deleted"]) == (8, 5, 0)
    assert report["rows"] == {"kept_live_item": 1, "strip": 2, "no_arr_object": 1, "nothing_to_strip": 1}
    r, s = report["instances"]["radarr/r0"], report["instances"]["sonarr/s0"]
    assert (r["matched"], r["kept_live_item"], r["strip_objects"], r["strip_tags"]) == (3, 1, 1, 2)
    assert r["labels"] == {"xt-1080p": 1, "xt-en": 1}
    assert r["to_strip"] == [
        {"object_id": 2, "title": "Gone", "path": "/m/Gone (2019)", "labels": ["xt-1080p", "xt-en"]}
    ]
    assert (s["matched"], s["strip_objects"], s["strip_tags"], s["labels"]) == (1, 1, 1, {"xt-720p": 1})
    assert r["stripped_objects"] == s["stripped_objects"] == 0


def test_a_strip_attempt_in_report_mode_raises_before_sending(tmp_path):
    jf, db, sonarr, radarr = scenario(tmp_path)
    arr = client_for(radarr, "r0", read_only=True)
    session = deleted_items.read_only_session(db)
    try:
        dpass = DeletedItemsPass(cfg("report"), jf.client(), [], [arr], session)
        dpass.plan(dpass.find_deleted())
        plan = dpass.objects[("radarr/r0", 2)]
        assert plan.remove_labels == ["xt-1080p", "xt-en"]
        with pytest.raises(ReadOnlyViolation):
            dpass.strip(plan)
    finally:
        session.close()
    assert radarr.writes == []  # the editor PUT never reached the server
    assert labels_of(radarr, 2) == {"luxe", "xt-1080p", "xt-en"}


def test_a_row_delete_in_report_mode_is_refused_by_sqlite(tmp_path):
    jf, db, _, _ = scenario(tmp_path)
    session = deleted_items.read_only_session(db)
    try:
        dpass = DeletedItemsPass(cfg("report"), jf.client(), [], [], session)
        with pytest.raises(OperationalError, match="readonly"):
            dpass.delete_rows(["d-film"])
    finally:
        session.close()
    assert "d-film" in row_ids(db)


def test_run_opens_the_index_read_only_in_report_mode(tmp_path, monkeypatch):
    """The session run_deleted_items hands a report-mode pass cannot delete, whatever the pass does."""
    jf, db, _, _ = scenario(tmp_path)
    outcome = {}

    def rogue_run(self):
        try:
            self.delete_rows(["d-film"])
        except OperationalError as exc:
            outcome["refused"] = str(exc)
        self._abort("stopped by the test")  # the report of a pass that never listed anything
        return self.report()

    monkeypatch.setattr(DeletedItemsPass, "run", rogue_run)
    run(monkeypatch, cfg("report"), jf, db)
    assert "readonly" in outcome["refused"] and "d-film" in row_ids(db)


def test_remove_refuses_to_run_in_report_mode(tmp_path):
    jf, db, _, _ = scenario(tmp_path)
    session = deleted_items.read_only_session(db)
    try:
        dpass = DeletedItemsPass(cfg("report"), jf.client(), [], [], session)
        with pytest.raises(RuntimeError, match="outside remove mode"):
            dpass.remove()
    finally:
        session.close()


# ── remove mode (built and tested; ships off) ──────────────────────────────
def test_remove_strips_only_managed_tags_and_deletes_the_rows(tmp_path, monkeypatch):
    jf, db, sonarr, radarr = scenario(tmp_path)
    untouched = {oid: dict(radarr.objects[oid]) for oid in (1, 3, 4)}
    report, _ = run(monkeypatch, cfg("remove", "live"), jf, db, [sonarr], [radarr])
    assert report["status"] == "ok" and report["halted"] is None
    assert labels_of(radarr, 2) == {"luxe"}  # the operator's tag survives
    assert labels_of(sonarr, 1) == {"anime"}
    assert {oid: radarr.objects[oid] for oid in (1, 3, 4)} == untouched  # kept, nothing to strip, B11
    assert labels_of(sonarr, 2) == {"xt-1080p"}  # a live series is never touched
    edits = [w for w in radarr.writes + sonarr.writes if w[0] == "PUT"]
    assert edits == [("PUT", "/movie/editor"), ("PUT", "/series/editor")] or edits == [
        ("PUT", "/series/editor"),
        ("PUT", "/movie/editor"),
    ]
    assert not [w for w in radarr.writes + sonarr.writes if w[0] != "PUT"]  # no label created or deleted
    assert row_ids(db) == {"l-kept", "l-show", "u-unread"}
    assert report["index"]["rows_deleted"] == 5
    r = report["instances"]["radarr/r0"]
    assert (r["stripped_objects"], r["stripped_tags"], r["readback_failures"]) == (1, 2, 0)


def test_a_strip_is_logged_so_it_can_be_put_back(tmp_path):
    jf, db, _, radarr = scenario(tmp_path)
    log_file = tmp_path / "removed.jsonl"
    session = sessionmaker(bind=create_engine(f"sqlite:///{db}"))()
    try:
        dpass = DeletedItemsPass(
            cfg("remove"), jf.client(), [], [client_for(radarr, "r0")], session, removal_log=log_file
        )
        dpass.run()
    finally:
        session.close()
    records = [json.loads(line) for line in log_file.read_text().splitlines()]
    assert records == [
        {
            "at": records[0]["at"],
            "instance": "radarr/r0",
            "object_id": 2,
            "title": "Gone",
            "path": "/m/Gone (2019)",
            "tag_ids": [2, 3],
            "labels": ["xt-1080p", "xt-en"],
            "rows": ["d-film"],
        }
    ]


def test_with_arr_writes_off_rows_that_need_a_strip_are_kept(tmp_path, monkeypatch):
    jf, db, sonarr, radarr = scenario(tmp_path)
    report, _ = run(monkeypatch, cfg("remove", "dry_run"), jf, db, [sonarr], [radarr])
    assert sonarr.writes == [] and radarr.writes == []
    assert report["arr_writes"] is False
    # d-film and d-show still own tags: their rows stay to drive the strip once writes are live.
    assert row_ids(db) == {"l-kept", "l-show", "u-unread", "d-film", "d-show"}
    assert report["index"]["rows_kept"] == {"arr_writes_off": 2}


def test_a_readback_mismatch_halts_and_keeps_every_row(tmp_path, monkeypatch):
    jf, db, sonarr, radarr = scenario(tmp_path)
    radarr.on_edit = lambda obj: obj.update(monitored=True)  # something else changed under the write
    report, _ = run(monkeypatch, cfg("remove", "live"), jf, db, [sonarr], [radarr])
    assert report["halted"] and "read-back mismatch" in report["halted"]
    assert report["instances"]["radarr/r0"]["readback_failures"] == 1
    assert len(row_ids(db)) == 8  # nothing deleted after a halt


def test_an_object_moved_since_the_preload_is_not_stripped(tmp_path):
    jf, db, _, radarr = scenario(tmp_path)
    session = sessionmaker(bind=create_engine(f"sqlite:///{db}"))()
    try:
        dpass = DeletedItemsPass(cfg("remove"), jf.client(), [], [client_for(radarr, "r0")], session)
        dpass.plan(dpass.find_deleted())
        radarr.objects[2]["path"] = "/m/Elsewhere (2019)"
        dpass.remove()
    finally:
        session.close()
    assert labels_of(radarr, 2) == {"luxe", "xt-1080p", "xt-en"}
    assert "d-film" in row_ids(db) and "d-none" not in row_ids(db)
    assert dpass.stats["radarr/r0"].changed_since_preload == 1


# ── deleted, never unreachable (B11) ───────────────────────────────────────
def test_unreachable_is_not_deleted_and_deleted_is(tmp_path, monkeypatch):
    """B11's item (listed, file gone, error newer than its row) keeps its row and its tags."""
    live = [jf_movie("u", "Unread", "/m/U/u.mkv", Tmdb="1")]
    db = index_db(
        tmp_path,
        [("u", "/m/U/u.mkv", "/m/U/poster.jpg", _OLD), ("d", "/m/D/d.mkv", "/m/D/poster.jpg", _OLD)],
        errors=[("u", "no_file", _ERR), ("u", "probe_failed", _ERR)],
    )
    radarr = FakeArr("radarr", [movie(1, "U", 1, "/m/U", tags=[2]), movie(2, "D", 2, "/m/D", tags=[2])], tags=TAGS)
    report, _ = run(monkeypatch, cfg("remove"), FakeJellyfin(live), db, [], [radarr])
    assert row_ids(db) == {"u"}
    assert labels_of(radarr, 1) == {"xt-1080p"} and labels_of(radarr, 2) == set()
    assert report["existence"]["candidates"] == 1 and report["index"]["deleted_rows"] == 1


# ── what "gone" needs ──────────────────────────────────────────────────────
def _items(n):
    return [jf_movie(f"i{k:04d}", f"T{k}", f"/m/T{k}/t.mkv") for k in range(n)]


def test_the_listing_reads_every_page_and_recounts():
    jf = FakeJellyfin(_items(1234))
    listing = complete_listing(jf.client(), page_size=500)
    assert (len(listing.items), listing.total, listing.pages, listing.recount) == (1234, 1234, 3, 1234)
    assert len({i["Id"] for i in listing.items}) == 1234


@pytest.mark.parametrize(
    "hook, match",
    [
        (lambda n, p: {**p, "Items": []} if n == 2 else p, "came back empty"),
        (lambda n, p: {**p, "TotalRecordCount": p["TotalRecordCount"] + 1} if n == 2 else p, "changed between pages"),
        (lambda n, p: {**p, "Items": p["Items"][:-1] + p["Items"][:1]} if n == 1 else p, "twice"),
        (lambda n, p: {"Items": p["Items"]}, "no TotalRecordCount"),
        (lambda n, p: {**p, "Items": p["Items"] + p["Items"][:1] if n == 3 else p["Items"]}, "twice"),
    ],
    ids=["short-page", "count-moves", "duplicate", "no-count", "duplicate-last-page"],
)
def test_a_listing_that_does_not_add_up_aborts(hook, match):
    jf = FakeJellyfin(_items(1234))
    jf.page_hook = hook
    with pytest.raises(PassAborted, match=match):
        complete_listing(jf.client(), page_size=500)


def test_a_recount_that_disagrees_aborts():
    jf = FakeJellyfin(_items(10))
    jf.recount = 11
    with pytest.raises(PassAborted, match="recount"):
        complete_listing(jf.client())


def test_a_listing_error_aborts_the_pass_and_changes_nothing_even_in_remove_mode(tmp_path, monkeypatch):
    jf, db, sonarr, radarr = scenario(tmp_path)
    jf.fail_on_page = 1
    report, _ = run(monkeypatch, cfg("remove", "live"), jf, db, [sonarr], [radarr])
    assert report["status"] == "aborted" and "500" in report["reason"]
    assert sonarr.writes == [] and radarr.writes == [] and len(row_ids(db)) == 8


def test_a_short_page_is_read_on_from_where_it_stopped():
    """The next page starts at the count read so far, so a page cut short loses nothing."""
    jf = FakeJellyfin(_items(1200))
    jf.page_hook = lambda n, p: {**p, "Items": p["Items"][:-10]} if n == 2 else p
    listing = complete_listing(jf.client(), page_size=500)
    assert len({i["Id"] for i in listing.items}) == 1200 and listing.pages == 3


def test_a_listing_that_adds_up_but_misses_live_items_is_caught_by_the_lookup(tmp_path, monkeypatch):
    """The failure the guards exist for: ten live items missing from a self-consistent listing
    (a library the listing did not reach) would read as ten deleted items."""
    items = _items(1200)
    db = index_db(tmp_path, [(i["Id"], i["Path"], None, _OLD) for i in items])
    jf = FakeJellyfin(items[:600] + items[610:], also_exists=items[600:610])
    report, _ = run(monkeypatch, cfg("remove"), jf, db)
    assert report["status"] == "ok" and report["existence"]["answered_by_id"] == 10
    assert report["index"]["deleted_rows"] == 0 and len(row_ids(db)) == 1200


def test_the_lookup_by_id_must_find_its_controls():
    jf = FakeJellyfin(_items(20))
    jf.lookup_answers_nothing = True
    with pytest.raises(PassAborted, match="control"):
        confirm_deleted(jf.client(), ["gone-1", "gone-2"], [i["Id"] for i in jf.items])


def test_the_lookup_by_id_confirms_in_batches_with_controls():
    jf = FakeJellyfin(_items(50))
    gone = [f"gone-{k}" for k in range(250)]
    result = confirm_deleted(jf.client(), gone, [i["Id"] for i in jf.items], batch=100)
    assert result.deleted == gone and result.answered == []
    assert result.calls == 3 and result.controls_sent == result.controls_answered == 15


def test_a_candidate_that_answers_by_id_is_not_deleted(tmp_path, monkeypatch):
    """Missing from the Movie/Series listing but still on the server (another type): not gone."""
    live = [jf_movie("a", "A", "/m/A/a.mkv")]
    db = index_db(tmp_path, [("a", "/m/A/a.mkv", None, _OLD), ("x", "/m/X/x.mkv", None, _OLD)])
    jf = FakeJellyfin(live, also_exists=[{"Id": "x", "Type": "Video"}])
    report, _ = run(monkeypatch, cfg("remove"), jf, db)
    assert report["existence"]["answered_by_id"] == 1 and report["index"]["deleted_rows"] == 0
    assert row_ids(db) == {"a", "x"}


def test_a_server_that_lists_nothing_aborts(tmp_path, monkeypatch):
    db = index_db(tmp_path, [("a", "/m/A/a.mkv", None, _OLD)])
    report, _ = run(monkeypatch, cfg("remove", max_fraction=1.0), FakeJellyfin([]), db)
    assert report["status"] == "aborted" and "control" in report["reason"] and row_ids(db) == {"a"}


@pytest.mark.parametrize("mode", ["report", "remove"])
def test_over_the_bound_removal_refuses_and_the_report_says_so(tmp_path, monkeypatch, mode):
    jf, db, sonarr, radarr = scenario(tmp_path)
    report, _ = run(monkeypatch, cfg(mode, "live", max_fraction=0.5), jf, db, [sonarr], [radarr])
    assert report["index"]["over_bound"] is True and report["index"]["fraction"] == 0.625
    assert sonarr.writes == [] and radarr.writes == [] and len(row_ids(db)) == 8
    if mode == "remove":
        assert report["status"] == "refused" and "max_fraction" in report["reason"]
    else:
        assert report["status"] == "ok"
        assert any("would refuse" in line for line in deleted_items.summary_lines(report))


def test_an_instance_that_cannot_be_read_aborts(tmp_path, monkeypatch):
    jf, db, sonarr, radarr = scenario(tmp_path)
    radarr.fail_status = {"GET": 503}
    report, _ = run(monkeypatch, cfg("remove"), jf, db, [sonarr], [radarr])
    assert report["status"] == "aborted" and "radarr/r0" in report["reason"]
    assert sonarr.writes == [] and len(row_ids(db)) == 8


# ── ownership: B5's folder rule ─────────────────────────────────────────────
def test_row_folders():
    def row(file_path, image_path=None):
        return MediaState(item_id="jellyfin:x", file_path=file_path, image_path=image_path)

    assert row_folders(row("/m/A (1)/a.mkv", "/m/A (1)/poster.jpg")) == ["/m/A (1)"]
    assert row_folders(row("/tv/S/Season 01/e.mkv", "/tv/S/poster.jpg")) == ["/tv/S"]
    assert row_folders(row("/tv/S/Season 01/e.mkv")) == ["/tv/S/Season 01", "/tv/S"]
    assert row_folders(row("/m/A/a.mkv")) == ["/m/A", "/m"]
    assert row_folders(row("")) == [] and row_folders(row(None)) == []


def test_the_twin_in_the_other_instance_keeps_its_tags(tmp_path, monkeypatch):
    """The 4K copy's item is gone; the HD copy (same id, other folder, live item) is untouched."""
    live = [jf_movie("hd", "Film", "/m/Film (2000)/f.mkv", Tmdb="7")]
    db = index_db(
        tmp_path,
        [
            ("hd", "/m/Film (2000)/f.mkv", "/m/Film (2000)/poster.jpg", _OLD),
            ("uhd", "/4k/Film (2000)/f.mkv", "/4k/Film (2000)/poster.jpg", _OLD),
        ],
    )
    general = FakeArr("radarr", [movie(1, "Film", 7, "/m/Film (2000)", tags=[2])], tags=TAGS)
    uhd = FakeArr("radarr", [movie(1, "Film", 7, "/4k/Film (2000)", tags=[5, 3])], tags=TAGS)
    run(monkeypatch, cfg("remove"), FakeJellyfin(live), db, [], [general, uhd])
    assert labels_of(general, 1) == {"xt-1080p"} and general.writes == []
    assert labels_of(uhd, 1) == set()
    assert row_ids(db) == {"hd"}


def test_only_the_deepest_folder_match_is_the_owner(tmp_path, monkeypatch):
    """A row with no poster tries the file's folder, then its parent: never both."""
    db = index_db(tmp_path, [("d", "/lib/Show/Film (2001)/f.mkv", None, _OLD), ("l", "/m/L/l.mkv", None, _OLD)])
    sonarr = FakeArr("sonarr", [series(1, "Show", 1, "/lib/Show", tags=[4])], tags=TAGS)
    radarr = FakeArr("radarr", [movie(1, "Film", 1, "/lib/Show/Film (2001)", tags=[2])], tags=TAGS)
    live = [jf_movie("l", "L", "/m/L/l.mkv")]
    run(monkeypatch, cfg("remove"), FakeJellyfin(live), db, [sonarr], [radarr])
    assert labels_of(radarr, 1) == set() and labels_of(sonarr, 1) == {"xt-720p"} and sonarr.writes == []


def test_two_objects_claiming_one_folder_in_one_instance_are_refused(tmp_path, monkeypatch):
    live = [jf_movie("l", "L", "/m/L/l.mkv")]
    db = index_db(tmp_path, [("d", "/m/D/d.mkv", "/m/D/poster.jpg", _OLD), ("l", "/m/L/l.mkv", None, _OLD)])
    radarr = FakeArr("radarr", [movie(1, "D", 1, "/m/D", tags=[2]), movie(2, "D2", 2, "/m/D/", tags=[2])], tags=TAGS)
    report, _ = run(monkeypatch, cfg("remove"), FakeJellyfin(live), db, [], [radarr])
    assert radarr.writes == [] and "d" in row_ids(db)
    assert report["rows"] == {"ambiguous": 1} and report["index"]["rows_kept"] == {"ambiguous": 1}


# ── the scan runs it ────────────────────────────────────────────────────────
class _ScanJellyfin:
    def get_items(self, library_ids=None):
        return []

    def close(self):
        pass


@pytest.mark.parametrize("cancelled", [False, True])
def test_a_scan_runs_the_pass_unless_cancelled(monkeypatch, cancelled):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    calls = []
    monkeypatch.setattr(pipeline, "get_session", lambda: sessionmaker(bind=engine)())
    monkeypatch.setattr(pipeline, "_clients_from_config", lambda c: (_ScanJellyfin(), [], []))
    monkeypatch.setattr(pipeline, "run_deleted_items", lambda c, **kw: calls.append(kw))
    assert pipeline.progress.try_start()
    pipeline.progress.cancelled = cancelled
    try:
        pipeline._run_scan(AppConfig(), incremental=True)
    finally:
        pipeline.progress.cancelled = False
    assert pipeline.progress.running is False
    # The tag-config hash of a fresh index forces this scan to full; like the *arr
    # report, the label stays the scan's own.
    assert calls == ([] if cancelled else [{"source": "incremental scan", "emit": pipeline.progress.emit}])
