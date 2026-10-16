"""Roadmap B32: the clean-up of what the 2026-10-11 reconciliation pass wrote.

The tool removes two classes of managed tag and nothing else: (i) a resolution
tag the pass restored beside another one, (ii) a retired spelling. The dry run
sends nothing but GETs; the apply runs only for the plan the dry run measured
(its digest), writes through ``set_managed_tags()``, writes one item and reads
it back before the rest, and records ``tags_applied`` alone.

Driven through a real ``JellyfinClient`` over ``httpx.MockTransport`` against
``test_reconcile``'s fake, which rejects a POST that would blank non-tag fields.
Every id, title and tag set here is invented, shaped like the six series.
"""

from __future__ import annotations

import json
from datetime import datetime

import httpx
import pytest
from sqlalchemy.orm import sessionmaker

from app import migrate, reconcile_cleanup, state
from app.clients.jellyfin import JellyfinClient
from app.clients.readonly import ReadOnlyTransport, ReadOnlyViolation
from app.config import AppConfig
from tests.test_reconcile import URL, FakeJellyfin, _full_item

PIECE, SUNNY, HOURS, FRONT, DEMONS, PLAN = (f"{n:032x}" for n in range(1, 7))
OLD = datetime(2026, 8, 9, 9, 1)

# (id, row resolution, row tags_applied, Jellyfin tags now)
SIX = [
    (
        PIECE,
        "720p",
        ["xt-720p", "xt-H265", "xt-JA", "xt-EN", "xt-sub-EN"],
        ["kw", "xt-1080p", "xt-H265", "xt-JA", "xt-EN", "xt-sub-EN", "xt-720p"],
    ),
    (SUNNY, "720p", ["xt-720p", "xt-AV1", "xt-EN"], ["kw", "xt-1080p", "xt-AV1", "xt-EN", "xt-720p"]),
    (HOURS, "1080p", ["xt-1080p", "xt-AV1", "xt-DD+"], ["kw", "xt-1080p", "xt-AV1", "xt-DD+"]),
    (FRONT, "1080p", ["xt-1080p", "xt-H265", "xt-AAC"], ["xt-aac", "xt-1080p", "xt-h265"]),
    (DEMONS, "1080p", ["xt-1080p", "xt-H.265", "xt-AAC"], ["xt-1080p", "xt-H.265", "xt-AAC"]),
    (PLAN, "1080p", ["xt-1080p", "xt-AV1", "xt-AAC"], ["xt-1080p", "xt-AV1", "xt-AAC"]),
]
IDS = [i for i, *_ in SIX]
WANTED = {
    PIECE: {("xt-720p", "i")},
    SUNNY: {("xt-720p", "i")},
    HOURS: {("xt-DD+", "ii")},
    DEMONS: {("xt-H.265", "ii")},
}


@pytest.fixture
def lab(tmp_path):
    db = tmp_path / "state.db"
    migrate.upgrade_to_head(db)
    engine = migrate._engine(db)
    maker = sessionmaker(bind=engine)
    s = maker()
    for item_id, res, row, _ in SIX:
        state.upsert_media_state(s, f"jellyfin:{item_id}", "jellyfin", "/media/x/s", res, [], row, None, 1.0)
        s.get(state.MediaState, f"jellyfin:{item_id}").last_scanned = OLD
    s.commit()
    s.close()
    server = FakeJellyfin([_full_item(i, tags) for i, _, _, tags in SIX])
    yield server, maker
    engine.dispose()


def _jf(server, read_only: bool = False) -> JellyfinClient:
    transport = httpx.MockTransport(server)
    return JellyfinClient(URL, "k", transport=ReadOnlyTransport(transport, quiet=True) if read_only else transport)


def _plan(server, maker, ids=IDS):
    s = maker()
    try:
        return reconcile_cleanup.build_plan(_jf(server, read_only=True), s, AppConfig(), ids)[0]
    finally:
        s.close()


def _apply(server, maker, digest: str, ids=IDS) -> dict:
    s = maker()
    try:
        return reconcile_cleanup.apply(_jf(server), s, AppConfig(), ids, digest)
    finally:
        s.close()


def _row(maker, item_id: str) -> state.MediaState:
    s = maker()
    try:
        row = s.get(state.MediaState, f"jellyfin:{item_id}")
        s.expunge(row)
        return row
    finally:
        s.close()


def _posts(server) -> list[str]:
    return [p.split("/")[2] for m, p, _ in server.log if m == "POST"]


# ── the plan ────────────────────────────────────────────────────────────────
def test_the_plan_removes_only_the_two_classes(lab):
    server, maker = lab
    plan = _plan(server, maker)
    assert plan.refusals == []
    assert {p.item_id: set(p.remove) for p in plan.items if p.remove} == WANTED
    piece = next(p for p in plan.items if p.item_id == PIECE)
    assert piece.after == ["xt-1080p", "xt-H265", "xt-JA", "xt-EN", "xt-sub-EN"]
    assert [p.item_id for p in plan.writes] == [PIECE, SUNNY, HOURS, DEMONS]


def test_the_dry_run_sends_only_gets(lab):
    server, maker = lab
    _plan(server, maker)
    assert {m for m, _, _ in server.log} == {"GET"}
    reads = [q for m, p, q in server.log if p == "/Items"]
    assert len(reads) == 1 and set(reads[0]["Ids"].split(",")) == set(IDS)  # by Ids=, never the listing
    with pytest.raises(ReadOnlyViolation):  # and the client it uses could not have sent one
        _jf(server, read_only=True).set_managed_tags(PIECE, server.items[PIECE], "xt-", ["xt-1080p"])
    assert server.writes == []


def test_the_digest_ignores_case_and_order_but_not_content(lab):
    server, maker = lab
    first = _plan(server, maker).digest()
    server.items[FRONT]["Tags"] = list(reversed(["xt-" + t[3:].upper() for t in server.items[FRONT]["Tags"]]))
    assert _plan(server, maker).digest() == first
    server.items[PLAN]["Tags"].append("xt-Foo")
    assert _plan(server, maker).digest() != first


def test_a_planted_retired_spelling_is_removed(lab):
    """The positive direction: a tag in class (ii) the measurement did not see is still class (ii)."""
    server, maker = lab
    server.items[PLAN]["Tags"].append("xt-DTS-HD.MA")
    plan = _plan(server, maker)
    assert next(p for p in plan.items if p.item_id == PLAN).remove == [("xt-DTS-HD.MA", "ii")]


def test_a_planted_third_resolution_is_not_removed_and_the_plan_refuses(lab):
    """The negative direction: a tag outside both classes stays, and the plan will not guess."""
    server, maker = lab
    server.items[PIECE]["Tags"].append("xt-4K")
    plan = _plan(server, maker)
    piece = next(p for p in plan.items if p.item_id == PIECE)
    assert "xt-4K" in piece.after and ("xt-4K", "i") not in piece.remove
    assert plan.refusals and "resolution tags" in plan.refusals[0]
    with pytest.raises(reconcile_cleanup.Refused, match="the plan refuses"):
        _apply(server, maker, plan.digest())
    assert server.writes == []


def test_a_legacy_prefix_tag_refuses(lab):
    """set_managed_tags() drops legacy-prefix tags too: a removal outside the classes."""
    server, maker = lab
    server.items[HOURS]["Tags"].append("mf-1080p")
    assert any("legacy-prefix" in r for r in _plan(server, maker).refusals)


def test_an_unknown_or_unreturned_id_refuses(lab):
    server, maker = lab
    plan = _plan(server, maker, [*IDS, "f" * 32])
    assert any("not returned" in r for r in plan.refusals) and any("no state.db row" in r for r in plan.refusals)


# ── the apply ───────────────────────────────────────────────────────────────
def test_apply_writes_the_measured_plan_one_item_first(lab):
    server, maker = lab
    digest = _plan(server, maker).digest()
    server.log.clear()
    result = _apply(server, maker, digest)
    assert [w["item_id"] for w in result["written"]] == [PIECE, SUNNY, HOURS, DEMONS]
    assert all(w["read_back"] for w in result["written"])
    assert server.rejected == []  # the whole item posted, no field blanked
    # The first item is written AND read back before the second is written.
    order = [(m, q.get("Ids") or p.split("/")[2]) for m, p, q in server.log]
    first_readback = next(n for n, (m, ids) in enumerate(order) if m == "GET" and ids == PIECE and n > 1)
    assert order.index(("POST", SUNNY)) > first_readback
    assert server.items[PIECE]["Tags"] == ["kw", "xt-1080p", "xt-H265", "xt-JA", "xt-EN", "xt-sub-EN"]
    assert server.items[HOURS]["Tags"] == ["kw", "xt-1080p", "xt-AV1"]
    assert server.items[FRONT]["Tags"] == ["xt-aac", "xt-1080p", "xt-h265"]  # nothing to remove: not written
    assert set(_posts(server)) == set(WANTED)


def test_apply_records_tags_applied_alone(lab):
    server, maker = lab
    _apply(server, maker, _plan(server, maker).digest())
    for item_id, res, row_tags, _ in SIX:
        row = _row(maker, item_id)
        managed = [t for t in server.items[item_id]["Tags"] if t.startswith("xt-")]
        if item_id in WANTED:
            assert json.loads(row.tags_applied) == managed  # the row and Jellyfin agree
        else:
            assert json.loads(row.tags_applied) == row_tags
        assert row.last_scanned == OLD and row.resolution == res  # B11 still sees a stale row as stale


def test_apply_refuses_a_plan_that_changed_since_the_dry_run(lab):
    """A tag planted after the measurement -- outside the classes -- is not removed and nothing is written."""
    server, maker = lab
    digest = _plan(server, maker).digest()
    server.items[PLAN]["Tags"].append("xt-Foo")
    with pytest.raises(reconcile_cleanup.Refused, match="not the plan measured"):
        _apply(server, maker, digest)
    assert server.writes == [] and "xt-Foo" in server.items[PLAN]["Tags"]


def test_apply_stops_when_the_first_write_does_not_read_back(lab):
    server, maker = lab
    digest = _plan(server, maker).digest()
    server.undo = {PIECE: 2}  # Jellyfin undoes the write and the one retry (B17)
    with pytest.raises(reconcile_cleanup.Refused, match="did not read back"):
        _apply(server, maker, digest)
    assert set(_posts(server)) == {PIECE}  # nothing after it
    assert json.loads(_row(maker, PIECE).tags_applied) == [
        t for t in server.items[PIECE]["Tags"] if t.startswith("xt-")
    ]  # recorded as read (B17), so the row still agrees


def test_apply_refuses_while_a_scan_runs(lab):
    server, maker = lab
    digest = _plan(server, maker).digest()
    s = maker()
    state.start_scan_run(s, "incremental")
    s.close()
    with pytest.raises(reconcile_cleanup.Refused, match="has not finished"):
        _apply(server, maker, digest)
    assert server.log[-1][0] == "GET" and server.writes == []


def test_the_cli_will_not_apply_without_a_digest_or_on_a_copy():
    with pytest.raises(SystemExit):
        reconcile_cleanup.main(["--item", PIECE, "--apply"])
    with pytest.raises(SystemExit):
        reconcile_cleanup.main(["--item", PIECE, "--apply", "--expect-plan", "x", "--db", "copy.db"])


# ── the self-test fails both ways ───────────────────────────────────────────
def test_the_self_test_passes():
    assert reconcile_cleanup.self_test() == []


@pytest.mark.parametrize("broken", [lambda tag, prefix: False, lambda tag, prefix: tag.startswith(prefix)])
def test_the_self_test_catches_a_classifier_that_removes_too_little_or_too_much(monkeypatch, broken):
    monkeypatch.setattr(reconcile_cleanup, "is_retired", broken)
    assert reconcile_cleanup.self_test() != []


def test_the_self_test_catches_a_resolution_rule_that_never_refuses(monkeypatch):
    monkeypatch.setattr(reconcile_cleanup, "_is_resolution", lambda tag, prefix: False)
    assert reconcile_cleanup.self_test() != []
