"""Legacy-prefix sweep of the local tag index (roadmap U1).

The asymmetry these tests exist for: removing a legacy tag is tidying, but
removing a *managed* tag by accident would silently drop the record of what
xenotag actually wrote to the media server. So the rule under test is not
"strip anything matching `legacy_prefixes`" — it is "strip anything matching
`legacy_prefixes` **that is not a managed tag**", and the managed prefix wins
even when the two overlap.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.state import Base, MediaState, partition_legacy_tags, purge_legacy_tags
from scripts.audit_legacy_tags import count_tag_lists, self_test

MANAGED = "xt-"
LEGACY = ["mf-"]


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def seed(session, rows: dict[str, list[str]]) -> None:
    for item_id, tags in rows.items():
        session.add(MediaState(item_id=item_id, source="jellyfin", tags_applied=json.dumps(tags)))
    session.commit()


def tags_of(session, item_id: str) -> list[str]:
    session.expire_all()
    return json.loads(session.get(MediaState, item_id).tags_applied)


# ── partition_legacy_tags ────────────────────────────────────────────────────
def test_legacy_removed_managed_kept():
    kept, removed = partition_legacy_tags(["mf-1080p", "xt-1080p", "anime"], LEGACY, MANAGED)
    assert kept == ["xt-1080p", "anime"]
    assert removed == ["mf-1080p"]


def test_prefix_is_not_a_namespace():
    """`mf-` must not match a tag that merely starts with those characters.

    Checked against the live library on 2026-09-24: of 15,553 distinct
    non-managed Jellyfin tags, none begins with `mf` or `xt` at all — so the
    prefix test is safe there today. It is not safe by construction, which is
    why this is pinned.
    """
    kept, removed = partition_legacy_tags(["mfx-keepme", "mf", "mf-gone", "MF-CASE"], LEGACY, MANAGED)
    assert kept == ["mfx-keepme", "mf", "MF-CASE"]
    assert removed == ["mf-gone"]


def test_managed_prefix_wins_over_an_overlapping_legacy_prefix():
    """A legacy prefix of `x` prefix-matches every `xt-` tag. The managed one wins."""
    kept, removed = partition_legacy_tags(["xt-2160p", "x-ancient", "keepme"], ["x"], MANAGED)
    assert kept == ["xt-2160p", "keepme"]
    assert removed == ["x-ancient"]


def test_empty_legacy_prefixes_is_a_no_op():
    kept, removed = partition_legacy_tags(["mf-1080p", "xt-1080p"], [], MANAGED)
    assert kept == ["mf-1080p", "xt-1080p"]
    assert removed == []


def test_blank_prefix_entry_is_ignored():
    """A `""` entry would prefix-match every tag in the library."""
    kept, removed = partition_legacy_tags(["mf-1080p", "xt-1080p", "anime"], ["", "mf-"], MANAGED)
    assert kept == ["xt-1080p", "anime"]
    assert removed == ["mf-1080p"]


def test_duplicates_count_as_applications_not_distinct_tags():
    _, removed = partition_legacy_tags(["mf-1080p", "mf-1080p"], LEGACY, MANAGED)
    assert removed == ["mf-1080p", "mf-1080p"]


# ── purge_legacy_tags ────────────────────────────────────────────────────────
def test_purge_strips_legacy_and_preserves_everything_else(session):
    seed(
        session,
        {
            "a": ["mf-1080p", "mf-EN"],
            "b": ["mf-1080p", "xt-1080p", "anime"],
            "c": ["xt-720p", "luxe"],
        },
    )
    report = purge_legacy_tags(session, LEGACY, MANAGED)
    assert report["rows_changed"] == 2
    assert report["tags_removed"] == 3
    assert report["by_tag"] == {"mf-1080p": 2, "mf-EN": 1}
    assert tags_of(session, "a") == []
    assert tags_of(session, "b") == ["xt-1080p", "anime"]
    assert tags_of(session, "c") == ["xt-720p", "luxe"]


def test_purge_is_idempotent(session):
    seed(session, {"a": ["mf-1080p", "xt-1080p"]})
    first = purge_legacy_tags(session, LEGACY, MANAGED)
    second = purge_legacy_tags(session, LEGACY, MANAGED)
    assert first["rows_changed"] == 1
    assert second["rows_changed"] == 0
    assert second["tags_removed"] == 0
    assert tags_of(session, "a") == ["xt-1080p"]


def test_dry_run_reports_but_writes_nothing(session):
    seed(session, {"a": ["mf-1080p", "xt-1080p"]})
    report = purge_legacy_tags(session, LEGACY, MANAGED, dry_run=True)
    assert report["dry_run"] is True
    assert report["rows_changed"] == 1
    assert report["tags_removed"] == 1
    assert tags_of(session, "a") == ["mf-1080p", "xt-1080p"]


def test_purge_never_touches_managed_tags_even_with_an_overlapping_prefix(session):
    seed(session, {"a": ["xt-1080p", "xt-EN", "x-ancient"], "b": ["xt-720p"]})
    report = purge_legacy_tags(session, ["x"], MANAGED)
    assert report["tags_removed"] == 1
    assert tags_of(session, "a") == ["xt-1080p", "xt-EN"]
    assert tags_of(session, "b") == ["xt-720p"]


def test_empty_prefix_list_short_circuits(session):
    seed(session, {"a": ["mf-1080p"]})
    report = purge_legacy_tags(session, [], MANAGED)
    assert report == {
        "prefixes": [],
        "managed_prefix": MANAGED,
        "rows_examined": 0,
        "rows_changed": 0,
        "tags_removed": 0,
        "by_tag": {},
        "dry_run": False,
    }
    assert tags_of(session, "a") == ["mf-1080p"]


def test_malformed_tags_applied_is_skipped_not_fatal(session):
    session.add(MediaState(item_id="bad", source="jellyfin", tags_applied="{not json"))
    session.add(MediaState(item_id="dict", source="jellyfin", tags_applied='{"mf-1080p": 1}'))
    session.add(MediaState(item_id="null", source="jellyfin", tags_applied=None))
    session.add(MediaState(item_id="ok", source="jellyfin", tags_applied=json.dumps(["mf-1080p"])))
    session.commit()
    report = purge_legacy_tags(session, LEGACY, MANAGED)
    assert report["rows_changed"] == 1
    assert tags_of(session, "ok") == []


def test_non_string_entries_are_dropped_from_the_rewrite(session):
    seed(session, {"a": ["mf-1080p", "xt-1080p"]})
    session.get(MediaState, "a").tags_applied = json.dumps(["mf-1080p", 7, "xt-1080p"])
    session.commit()
    purge_legacy_tags(session, LEGACY, MANAGED)
    assert tags_of(session, "a") == ["xt-1080p"]


# ── the audit probe ──────────────────────────────────────────────────────────
def test_audit_self_test_passes():
    assert self_test() == 0


def test_count_tag_lists_separates_the_three_buckets():
    c = count_tag_lists([["mf-1080p", "xt-1080p", "anime"], ["mf-1080p"]], LEGACY, MANAGED)
    assert c["rows"] == 2
    assert (c["legacy_distinct"], c["legacy_applications"], c["legacy_items"]) == (1, 2, 2)
    assert (c["managed_distinct"], c["managed_applications"], c["managed_items"]) == (1, 1, 1)
    assert c["other_distinct"] == 1
