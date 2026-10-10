"""Roadmap B12(b): the Jellyfin tag reconciliation pass.

A replace-all metadata refresh (2026-09-04) or an item Jellyfin re-creates drops
xenotag's ``xt-`` tags, and the mtime-driven scan never returns to an unchanged
file. The pass reads every tracked item by ``Ids=``, re-writes the ones missing
a managed tag their row says xenotag wrote (regardless of case, B12(a)), and
reads the write back (B17). Past ``scan.reconcile_write_threshold`` candidates a
SCHEDULED pass writes nothing and raises ``xenotag_reconcile_halted``; the
manual rescan bypasses the threshold.

These drive a real ``JellyfinClient`` over ``httpx.MockTransport`` against a
fake Jellyfin that, like the real one, answers a ``Fields=Tags`` read with a
partial item -- and that REJECTS a write whose non-tag fields differ from the
item's, because ``set_managed_tags()`` posts the whole UpdateRequest and a body
built from a partial read would blank a live item's title, genres and locks.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app import arr_sync, metrics, migrate, reconcile, scheduler, state
from app.clients import radarr, sonarr
from app.clients.jellyfin import TAG_READ_BATCH, JellyfinClient
from app.config import AppConfig
from app.state import ScanRun

URL = "http://jf.test"
WRITTEN = ["xt-1080p", "xt-H264", "xt-EN", "xt-AAC"]

# What set_managed_tags() posts besides Tags, as the fake checks them.
NON_TAG_FIELDS = (
    "Name",
    "OriginalTitle",
    "ProductionYear",
    "OfficialRating",
    "Genres",
    "Taglines",
    "LockData",
    "LockedFields",
    "ProviderIds",
)


def _full_item(item_id: str, tags: list[str]) -> dict:
    return {
        "Id": item_id,
        "Name": f"Film {item_id[:6]}",
        "Type": "Movie",
        "OriginalTitle": "Le Film",
        "ProductionYear": 1999,
        "OfficialRating": "PG-13",
        "Genres": ["Drama", "Mystery"],
        "Studios": [{"Name": "Studio A", "Id": "s1"}],
        "Taglines": ["It was all a dream."],
        "LockData": True,
        "LockedFields": ["Name", "Genres"],
        "ProviderIds": {"Tmdb": "123", "Imdb": "tt0000123"},
        "Path": f"/media/{item_id}",
        "Tags": list(tags),
    }


class FakeJellyfin:
    """Keeps items whole; answers ``Fields=Tags`` with a partial item; rejects a lossy POST.

    ``respell`` lowercases a stored write (the *arrs' NFO merge, B12(a)); ``undo``
    leaves an item's tags unchanged for that many writes (B17).
    """

    def __init__(self, items: list[dict], respell: tuple[str, ...] = (), undo: dict[str, int] | None = None):
        self.items = {i["Id"]: i for i in items}
        self.respell = respell
        self.undo = dict(undo or {})
        self.log: list[tuple[str, str, dict]] = []
        self.writes: list[tuple[str, dict]] = []
        self.rejected: list[tuple[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        query = {k: v[0] for k, v in parse_qs(urlsplit(str(request.url)).query).items()}
        path = request.url.path
        self.log.append((request.method, path, query))
        if request.method == "GET" and path == "/Items" and "Ids" in query:
            fields = query.get("Fields", "")
            out = []
            for i in query["Ids"].split(","):
                item = self.items.get(i)
                if item is None:
                    continue
                if "Genres" in fields:
                    out.append(json.loads(json.dumps(item)))
                else:  # Name and Id always come; Fields=Tags adds only the tags
                    out.append({"Id": item["Id"], "Name": item["Name"], "Tags": list(item["Tags"])})
            return httpx.Response(200, json={"Items": out, "TotalRecordCount": len(out)})
        if request.method == "POST" and path.startswith("/Items/") and path.count("/") == 2:
            item_id = path.split("/")[2]
            item = self.items[item_id]
            body = json.loads(request.content)
            for f in NON_TAG_FIELDS:
                if body.get(f) != item.get(f):
                    self.rejected.append((item_id, f))
            if [s.get("Name") for s in body.get("Studios") or []] != [s["Name"] for s in item["Studios"]]:
                self.rejected.append((item_id, "Studios"))
            if any(r[0] == item_id for r in self.rejected):
                return httpx.Response(400, json={"error": "would blank fields"})
            self.writes.append((item_id, body))
            if self.undo.get(item_id, 0) > 0:
                self.undo[item_id] -= 1
            elif item_id in self.respell:
                item["Tags"] = [t.lower() for t in body["Tags"]]
            else:
                item["Tags"] = list(body["Tags"])
            return httpx.Response(204)
        return httpx.Response(404)


def _ids(n: int) -> list[str]:
    return [uuid.uuid4().hex for _ in range(n)]


def _value(name: str, **labels: str) -> float:
    return metrics.REGISTRY.get_sample_value(name, labels) or 0.0


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = tmp_path / "state.db"
    migrate.upgrade_to_head(db)
    engine = migrate._engine(db)
    maker = sessionmaker(bind=engine)
    monkeypatch.setattr(reconcile, "get_session", maker)
    monkeypatch.setenv("RECONCILE_REPORT", str(tmp_path / "reconcile-report.json"))
    holder: dict[str, FakeJellyfin] = {}
    monkeypatch.setattr(
        reconcile,
        "JellyfinClient",
        lambda url, key: JellyfinClient(URL, "k", transport=httpx.MockTransport(holder["server"])),
    )
    metrics.reconcile_halted.set(0)
    metrics.reconcile_candidates.set(0)

    def seed(item_id: str, tags: list[str], file_path: str = "/media/x/film.mkv") -> None:
        s = maker()
        state.upsert_media_state(s, f"jellyfin:{item_id}", "jellyfin", file_path, "1080p", [], tags, None, 1.0)
        s.close()

    def serve(server: FakeJellyfin) -> FakeJellyfin:
        holder["server"] = server
        return server

    def run(trigger: str = reconcile.SCHEDULED, cfg: AppConfig | None = None) -> dict:
        assert reconcile.progress.try_start()
        return reconcile._run_reconcile(cfg or AppConfig(), trigger)

    def runs() -> list[ScanRun]:
        s = maker()
        try:
            return s.query(ScanRun).all()
        finally:
            s.close()

    yield seed, serve, run, runs, maker
    engine.dispose()
    reconcile.progress.running = False
    reconcile.progress.cancelled = False


def _cfg(threshold: int = 500, **scan) -> AppConfig:
    return AppConfig.model_validate({"scan": {"reconcile_write_threshold": threshold, **scan}})


# ── the plan: pure ──────────────────────────────────────────────────────────
def test_plan_a_case_only_difference_is_not_a_candidate():
    p = reconcile.plan([("a", WRITTEN)], {"a": {"Id": "a", "Tags": [t.lower() for t in WRITTEN]}})
    assert p.candidates == [] and p.checked == 1


def test_plan_a_missing_tag_is_a_candidate_spelled_as_the_row():
    p = reconcile.plan([("a", WRITTEN)], {"a": {"Id": "a", "Name": "A", "Tags": ["xt-1080p", "xt-h264", "fav"]}})
    assert [(c.item_id, c.name, c.missing) for c in p.candidates] == [("a", "A", ("xt-EN", "xt-AAC"))]


def test_plan_extra_managed_tags_are_not_repaired():
    p = reconcile.plan([("a", WRITTEN)], {"a": {"Id": "a", "Tags": [*WRITTEN, "xt-DTS"]}})
    assert p.candidates == []


def test_plan_an_id_ids_does_not_return_is_skipped_and_counted():
    p = reconcile.plan([("a", WRITTEN), ("b", WRITTEN)], {"a": {"Id": "a", "Tags": WRITTEN}})
    assert p.not_returned == ["b"] and p.checked == 1 and p.candidates == []


def test_expected_tags_are_the_managed_ones_only():
    assert reconcile.expected_tags(json.dumps(["fav", *WRITTEN, "mf-old"]), "xt-") == WRITTEN
    assert reconcile.expected_tags(None, "xt-") == []
    assert reconcile.expected_tags("not json", "xt-") == []


# ── the threshold ───────────────────────────────────────────────────────────
def _lost(seed, n: int) -> list[dict]:
    """n tracked items whose xt-AAC and xt-EN Jellyfin dropped (kept: a user tag and a respelled one)."""
    items = []
    for item_id in _ids(n):
        seed(item_id, WRITTEN)
        items.append(_full_item(item_id, ["favourite", "xt-1080p", "xt-h264"]))
    return items


def test_n_candidates_on_a_schedule_are_all_written(env):
    seed, serve, run, runs, _ = env
    server = serve(FakeJellyfin(_lost(seed, 3)))
    before = _value("xenotag_reconcile_writes_total", result="written")
    report = run(reconcile.SCHEDULED, _cfg(threshold=3))
    assert len(server.writes) == 3 and server.rejected == []
    for item in server.items.values():
        assert sorted(item["Tags"]) == sorted(["favourite", "xt-1080p", "xt-h264", "xt-EN", "xt-AAC"])
    assert report["outcome"] == "written" and report["halted"] is False and report["candidates"] == 3
    assert report["writes"] == {"written": 3, "readback_fixed": 0, "unresolved": 0, "error": 0}
    assert _value("xenotag_reconcile_halted") == 0
    assert _value("xenotag_reconcile_candidates") == 3
    assert _value("xenotag_reconcile_writes_total", result="written") == before + 3
    [r] = runs()
    assert (r.scan_type, r.items_scanned, r.items_tagged, r.completed_at is not None) == ("reconcile", 3, 3, True)


def test_n_plus_1_on_a_schedule_writes_nothing_and_raises_the_alarm(env, caplog):
    seed, serve, run, runs, _ = env
    items = _lost(seed, 4)
    server = serve(FakeJellyfin(items))
    with caplog.at_level(logging.WARNING, logger="app.reconcile"):
        report = run(reconcile.SCHEDULED, _cfg(threshold=3))
    assert server.writes == [] and not any(m == "POST" for m, _, _ in server.log)
    assert _value("xenotag_reconcile_halted") == 1
    assert _value("xenotag_reconcile_candidates") == 4
    [warning] = [r.getMessage() for r in caplog.records if "HALTED" in r.getMessage()]
    assert "4 item(s) need a write" in warning and "threshold of 3" in warning
    assert items[0]["Name"] in warning and "xt-AAC" in warning  # the sample
    assert report["halted"] is True and report["outcome"] == "halted" and report["threshold"] == 3
    assert {s["item_id"] for s in report["sample"]} == {i["Id"] for i in items}
    assert json.loads(reconcile.report_path().read_text())["halted"] is True
    [r] = runs()
    assert (r.scan_type, r.items_tagged) == ("reconcile", 0)


def test_the_sample_is_capped(env):
    seed, serve, run, _, _ = env
    serve(FakeJellyfin(_lost(seed, reconcile.SAMPLE_SIZE + 5)))
    report = run(reconcile.SCHEDULED, _cfg(threshold=1))
    assert report["halted"] and len(report["sample"]) == reconcile.SAMPLE_SIZE
    assert report["candidates"] == reconcile.SAMPLE_SIZE + 5


def test_n_plus_1_by_hand_bypasses_the_threshold_and_clears_the_alarm(env):
    seed, serve, run, _, _ = env
    metrics.reconcile_halted.set(1)  # the scheduled run before it halted
    server = serve(FakeJellyfin(_lost(seed, 4)))
    report = run(reconcile.MANUAL, _cfg(threshold=3))
    assert len(server.writes) == 4 and server.rejected == []
    assert report["halted"] is False and report["trigger"] == "manual"
    assert _value("xenotag_reconcile_halted") == 0


# ── what counts as missing, and the write ───────────────────────────────────
def test_a_case_only_difference_is_not_written(env):
    seed, serve, run, _, _ = env
    [a] = _ids(1)
    seed(a, WRITTEN)
    server = serve(FakeJellyfin([_full_item(a, ["favourite", *(t.lower() for t in WRITTEN)])]))
    report = run()
    assert server.writes == [] and report["candidates"] == 0 and report["outcome"] == "clean"
    assert _value("xenotag_reconcile_halted") == 0


def test_a_missing_tag_is_written_and_read_back(env):
    seed, serve, run, _, maker = env
    [a] = _ids(1)
    seed(a, WRITTEN)
    server = serve(FakeJellyfin([_full_item(a, ["favourite", "xt-1080p", "xt-h264", "xt-EN", "xt-DTS"])]))
    report = run()
    [(item_id, body)] = server.writes
    assert item_id == a
    # Jellyfin's managed tags as they were (its respelling, the extra), the lost one added; the user tag kept.
    assert body["Tags"] == ["favourite", "xt-1080p", "xt-h264", "xt-EN", "xt-DTS", "xt-AAC"]
    reads = [q for m, p, q in server.log if m == "GET" and p == "/Items"]
    assert reads[-1]["Ids"] == a and reads[-1]["Fields"] == "Tags"  # the B17 read-back, after the write
    assert report["writes"]["written"] == 1
    # The row is what the pass restores, not something it rewrites.
    s = maker()
    try:
        assert json.loads(s.get(state.MediaState, f"jellyfin:{a}").tags_applied) == WRITTEN
    finally:
        s.close()


def test_a_write_jellyfin_undoes_is_retried_once(env):
    seed, serve, run, _, _ = env
    [a, b] = _ids(2)
    seed(a, WRITTEN)
    seed(b, WRITTEN)
    server = serve(FakeJellyfin([_full_item(a, ["xt-1080p"]), _full_item(b, ["xt-1080p"])], undo={a: 1, b: 2}))
    report = run()
    assert len(server.writes) == 4  # each written twice
    assert report["writes"] == {"written": 0, "readback_fixed": 1, "unresolved": 1, "error": 0}


def test_the_write_posts_the_whole_item_never_a_tags_only_read(env):
    """The trap: a body built from the ``Fields=Tags`` read would blank the item's other fields."""
    seed, serve, run, _, _ = env
    [a] = _ids(1)
    seed(a, WRITTEN)
    server = serve(FakeJellyfin([_full_item(a, ["xt-1080p"])]))
    run()
    assert server.rejected == [] and len(server.writes) == 1
    [(_, body)] = server.writes
    assert body["Genres"] == ["Drama", "Mystery"] and body["LockData"] is True and body["ProductionYear"] == 1999
    full_reads = [q for m, p, q in server.log if m == "GET" and "Genres" in q.get("Fields", "")]
    assert [q["Ids"] for q in full_reads] == [a]


def test_the_fake_rejects_a_body_built_from_a_tags_only_read():
    """The trap test's own self-test: without it, a fake that accepts anything proves nothing."""
    [a] = _ids(1)
    server = FakeJellyfin([_full_item(a, ["xt-1080p"])])
    jf = JellyfinClient(URL, "k", transport=httpx.MockTransport(server))
    partial = jf.get_current_tags([a])
    with pytest.raises(httpx.HTTPStatusError):
        jf.set_managed_tags(a, {"Id": a, "Name": server.items[a]["Name"], "Tags": partial[a]}, "xt-", WRITTEN)
    assert {f for _, f in server.rejected} >= {"Genres", "ProductionYear", "LockData", "ProviderIds", "Studios"}
    server.rejected.clear()
    jf.set_managed_tags(a, jf.get_items_by_ids([a])[a], "xt-", WRITTEN)
    assert server.rejected == []


def test_a_failed_write_is_counted_as_an_error(env):
    seed, serve, run, _, _ = env
    [a] = _ids(1)
    seed(a, WRITTEN)
    server = FakeJellyfin([_full_item(a, ["xt-1080p"])])

    def failing_writes(request):
        return httpx.Response(500) if request.method == "POST" else server(request)

    serve(failing_writes)
    report = run()
    assert report["writes"] == {"written": 0, "readback_fixed": 0, "unresolved": 0, "error": 1}
    assert server.items[a]["Tags"] == ["xt-1080p"]


def test_reads_are_batched_by_ids(env):
    seed, serve, run, _, _ = env
    items = []
    for item_id in _ids(TAG_READ_BATCH + 1):
        seed(item_id, WRITTEN)
        items.append(_full_item(item_id, WRITTEN))
    server = serve(FakeJellyfin(items))
    report = run()
    reads = [q for m, p, q in server.log if m == "GET" and p == "/Items"]
    assert [len(q["Ids"].split(",")) for q in reads] == [TAG_READ_BATCH, 1]
    assert all(q["Fields"] == "Tags" for q in reads)
    assert report["checked"] == TAG_READ_BATCH + 1 and report["candidates"] == 0


def test_an_item_jellyfin_no_longer_returns_is_skipped(env):
    seed, serve, run, _, _ = env
    [a, gone] = _ids(2)
    seed(a, WRITTEN)
    seed(gone, WRITTEN)
    server = serve(FakeJellyfin([_full_item(a, WRITTEN)]))
    report = run()
    assert report["not_returned"] == 1 and report["candidates"] == 0 and server.writes == []


def test_path_filters_limit_the_pass(env):
    seed, serve, run, _, _ = env
    [inside, outside] = _ids(2)
    seed(inside, WRITTEN, "/mnt/media/4K/a/film.mkv")
    seed(outside, WRITTEN, "/mnt/media/HD/b/film.mkv")
    server = serve(FakeJellyfin([_full_item(inside, []), _full_item(outside, [])]))
    run(cfg=_cfg(path_filters=["/mnt/media/4K"]))
    assert [w[0] for w in server.writes] == [inside]


# ── Jellyfin only ───────────────────────────────────────────────────────────
def test_no_arr_client_is_ever_built(env, monkeypatch):
    seed, serve, run, _, _ = env

    def boom(*a, **kw):
        raise AssertionError("the reconciliation pass touched an *arr")

    monkeypatch.setattr(sonarr.SonarrClient, "__init__", boom)
    monkeypatch.setattr(radarr.RadarrClient, "__init__", boom)
    monkeypatch.setattr(arr_sync.ArrTagSync, "__init__", boom)
    server = serve(FakeJellyfin(_lost(seed, 2)))
    cfg = AppConfig.model_validate(
        {
            "arr_sync": {"mode": "live"},
            "sonarr": {"instances": [{"name": "s", "url": "http://sonarr.test", "api_key": "k"}]},
            "radarr": {"instances": [{"name": "r", "url": "http://radarr.test", "api_key": "k"}]},
        }
    )
    run(reconcile.MANUAL, cfg)
    assert len(server.writes) == 2


# ── the lock ────────────────────────────────────────────────────────────────
def test_a_scheduled_run_skips_while_a_scan_holds_the_lock(env, caplog):
    seed, serve, _, runs, _ = env
    server = serve(FakeJellyfin(_lost(seed, 1)))
    assert reconcile.progress.try_start()  # a scan is running
    with caplog.at_level(logging.WARNING, logger="app.reconcile"):
        reconcile.run_scheduled_reconcile(AppConfig())
    assert server.log == [] and runs() == []
    assert any("skipping this scheduled run" in r.getMessage() for r in caplog.records)
    assert reconcile.progress.running is True  # still the scan's


def test_a_scheduled_run_takes_and_releases_the_lock(env):
    seed, serve, _, _, _ = env
    server = serve(FakeJellyfin(_lost(seed, 1)))
    reconcile.run_scheduled_reconcile(AppConfig())
    assert len(server.writes) == 1 and reconcile.progress.running is False


def test_an_exception_releases_the_lock(env, monkeypatch):
    seed, serve, _, _, _ = env
    seed(_ids(1)[0], WRITTEN)
    serve(lambda request: httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        reconcile.run_scheduled_reconcile(AppConfig())
    assert reconcile.progress.running is False and reconcile.progress.error
    assert reconcile.progress.try_start()  # the next run is not locked out


@pytest.fixture
def client(monkeypatch):
    from app.main import app
    from app.web import routes

    monkeypatch.setattr(routes, "_require_user", lambda request: "admin")
    monkeypatch.setattr(routes, "get_config", lambda: AppConfig())
    yield TestClient(app)
    reconcile.progress.running = False


def test_the_manual_route_answers_409_while_a_scan_runs(client, monkeypatch):
    started = []
    monkeypatch.setattr(reconcile, "_run_reconcile_recorded", lambda cfg, trigger: started.append(trigger))
    assert reconcile.progress.try_start()
    resp = client.post("/scan/reconcile")
    assert resp.status_code == 409 and started == []


def test_the_manual_route_starts_a_manual_pass(client, monkeypatch):
    started = []
    monkeypatch.setattr(reconcile, "_run_reconcile_recorded", lambda cfg, trigger: started.append(trigger))
    resp = client.post("/scan/reconcile")
    assert resp.status_code == 200 and resp.json() == {"status": "started", "type": "reconcile"}
    reconcile.progress.running = False
    for _ in range(100):
        if started:
            break
        time.sleep(0.01)
    assert started == [reconcile.MANUAL]


# ── the schedule ────────────────────────────────────────────────────────────
@pytest.fixture
def sched():
    yield scheduler
    scheduler.stop()
    scheduler._scheduler = None


def _job(job_id: str):
    return scheduler._scheduler.get_job(job_id)


def test_an_empty_reconcile_schedule_adds_no_job(sched):
    sched.start("0 3 * * *", lambda: None, "", lambda: None)
    assert _job(scheduler._JOB_ID) is not None
    assert _job(scheduler._RECONCILE_JOB_ID) is None
    assert sched.next_reconcile_time() is None


def test_the_default_schedule_adds_the_reconcile_job_at_five(sched):
    cfg = AppConfig()
    assert cfg.scan.reconcile_schedule == "0 5 * * *" and cfg.scan.reconcile_write_threshold == 500
    sched.start(cfg.scan.schedule, lambda: None, cfg.scan.reconcile_schedule, lambda: None)
    job = _job(scheduler._RECONCILE_JOB_ID)
    assert job is not None and str(job.trigger.fields[5]) == "5"  # hour
    assert sched.next_reconcile_time() is not None


def test_a_settings_save_reschedules_the_reconcile_job(sched, client, monkeypatch):
    from app.web import routes

    sched.start("0 3 * * *", lambda: None, "0 5 * * *", lambda: None)
    monkeypatch.setattr(routes, "save_config_from_dict", lambda body: AppConfig.model_validate(body))
    resp = client.put("/api/settings", json={"scan": {"schedule": "0 3 * * *", "reconcile_schedule": "30 6 * * *"}})
    assert resp.status_code == 200
    job = _job(scheduler._RECONCILE_JOB_ID)
    assert str(job.trigger.fields[5]) == "6" and str(job.trigger.fields[6]) == "30"
    resp = client.put("/api/settings", json={"scan": {"schedule": "0 3 * * *", "reconcile_schedule": ""}})
    assert resp.status_code == 200 and _job(scheduler._RECONCILE_JOB_ID) is None
    assert _job(scheduler._JOB_ID) is not None  # the scan's own job is untouched


@pytest.mark.parametrize("bad", ["not cron", "61 * * * *"])
def test_a_bad_reconcile_schedule_is_refused(bad):
    with pytest.raises(ValueError, match="Invalid cron"):
        AppConfig.model_validate({"scan": {"reconcile_schedule": bad}})


def test_the_threshold_must_be_at_least_one():
    with pytest.raises(ValueError):
        AppConfig.model_validate({"scan": {"reconcile_write_threshold": 0}})
    assert AppConfig.model_validate({"scan": {"reconcile_write_threshold": 1}}).scan.reconcile_write_threshold == 1


# ── a pass is not a scan ────────────────────────────────────────────────────
def test_a_pass_is_not_the_last_scan(env, monkeypatch):
    seed, serve, run, _, maker = env
    s = maker()
    scan = state.start_scan_run(s, "incremental")
    state.finish_scan_run(s, scan, scanned=10, tagged=1, images=0)
    s.close()
    serve(FakeJellyfin(_lost(seed, 1)))
    run()
    s = maker()
    try:
        assert state.get_last_scan(s).scan_type == "incremental"
        assert state.get_stats(s)["last_scan_type"] == "incremental"
        assert {r["scan_type"] for r in state.get_recent_scans(s)} == {"incremental", "reconcile"}
    finally:
        s.close()
    # The I5 seed reads the last SCAN, so a daily pass cannot hide a scan that keeps failing.
    monkeypatch.setattr(state, "get_session", maker)
    metrics.last_scanned.set(0)
    metrics.seed_from_disk()
    assert _value("xenotag_scan_last_items_scanned") == 10


def test_the_gauges_are_seeded_from_the_stored_report(env, monkeypatch):
    seed, serve, run, _, maker = env
    serve(FakeJellyfin(_lost(seed, 2)))
    run(reconcile.SCHEDULED, _cfg(threshold=1))
    metrics.reconcile_halted.set(0)
    metrics.reconcile_candidates.set(0)
    monkeypatch.setattr(state, "get_session", maker)
    metrics.seed_from_disk()
    assert _value("xenotag_reconcile_halted") == 1 and _value("xenotag_reconcile_candidates") == 2


def test_a_cancelled_pass_leaves_the_alarm_alone(env):
    seed, serve, _, _, _ = env
    serve(FakeJellyfin(_lost(seed, 2)))
    metrics.reconcile_halted.set(1)
    assert reconcile.progress.try_start()
    reconcile.progress.cancel()
    report = reconcile._run_reconcile(AppConfig(), reconcile.MANUAL)
    assert report["outcome"] == "cancelled" and _value("xenotag_reconcile_halted") == 1


def test_the_page_has_the_button_and_saves_both_settings():
    from pathlib import Path

    html = (Path(__file__).resolve().parent.parent / "app" / "web" / "templates" / "index.html").read_text()
    assert "triggerScan('reconcile')" in html  # -> POST /scan/reconcile, beside the scan buttons
    assert "reconcile_schedule: document.getElementById('s-reconcile-schedule')" in html
    assert "reconcile_write_threshold:" in html and 'id="s-reconcile-threshold"' in html
    assert "s.scan?.reconcile_schedule" in html and "s.scan?.reconcile_write_threshold" in html
