"""Roadmap B32: take back what the 2026-10-11 reconciliation pass wrote from stale rows.

The first scheduled B12(b) pass (2026-10-11 05:00 container time) trusted every
row's ``tags_applied`` and wrote six series' stale tags to Jellyfin: ``xt-720p``
beside the scan's ``xt-1080p`` (a pre-B13 row whose newer write timed out
unrecorded), and the June-August tags of series whose files no longer probe,
spelled before B9 (``xt-DD+``, ``xt-H.264``). B32's fix stops the pass doing
that again; this tool removes, from the items it is given, exactly two classes
of managed tag and nothing else (operator decision 2026-10-14):

  (i)  a resolution tag the pass restored beside another resolution tag: the
       item carries two or more, and one of them is the row's own resolution
       and in the row's ``tags_applied`` -- the only resolution tag the pass
       could have written;
  (ii) a retired spelling: one ``tagger.tag_label()`` would respell (B9:
       ``xt-DD+``, ``xt-H.264``, ``xt-H.265``) or a language label B7 retired
       (``KH``, ``PE``, ``ZX``, also as ``sub-``).

Read-only unless ``--apply``: every Jellyfin request then goes through
``ReadOnlyTransport``, and the index is opened ``mode=ro`` (``--db`` takes a
copy). Items are read by ``Ids=``, never the recursive listing (B18, B26).

The plan REFUSES -- writes nothing, exits 1 -- when an item is not returned,
has no row, would still carry two resolution tags after (i), or carries a
legacy-prefix tag (``mf-``), which ``set_managed_tags()`` would drop as a side
effect: a removal outside the two classes.

``--apply`` writes through ``JellyfinClient.set_managed_tags()`` and records
through the pass's own read-back step (``pipeline._record_after_readback()``,
B17), so each row's ``tags_applied`` is what Jellyfin carries afterwards; no
other column moves, ``last_scanned`` included (a probe-failed row must stay
unreachable to B11). It runs only with ``--expect-plan <digest>`` equal to the
digest of the plan it has just re-read: the plan measured GET-only is the plan
applied, and any change in between (a tag planted, a scan rewrite) refuses. It
writes one item, reads it back, and only then the rest; a write that does not
read back stops it.

    python -m app.reconcile_cleanup --item ID [--item ID ...]              # dry run
    python -m app.reconcile_cleanup --item ID ... --apply --expect-plan SHA
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from pathlib import Path

from .clients.jellyfin import JellyfinClient
from .clients.readonly import ReadOnlyTransport
from .config import AppConfig
from .reconcile import expected_tags
from .scanner import RESOLUTION_CLASSES
from .state import MediaState, ScanRun, _utcnow, get_session, init_db, read_only_session
from .tagger import tag_label

log = logging.getLogger(__name__)

CLASS_RESOLUTION = "i"
CLASS_RETIRED = "ii"

# Every resolution label the scanner writes (scanner.RESOLUTION_CLASSES, then "SD").
RESOLUTION_LABELS = frozenset(label.casefold() for *_, label in RESOLUTION_CLASSES) | {"sd"}

# Language labels B7 retired (tagger.TAG_VOCABULARY 3): the old first-two-letters
# fallback's `khm`/`per`/`zxx`. None of them is an ISO 639-1 code, so nothing
# writes them today.
B7_RETIRED_LANGUAGES = frozenset({"KH", "PE", "ZX"})

# A scan or pass whose run has no completed_at and started this recently is
# taken as running; the apply refuses rather than race its writes.
RUNNING_WINDOW = timedelta(hours=6)


class Refused(RuntimeError):
    """The plan or the apply refused; nothing (more) was written."""


@dataclass
class ItemPlan:
    item_id: str
    name: str = ""
    row_resolution: str | None = None
    row_tags: list[str] = field(default_factory=list)
    before: list[str] = field(default_factory=list)  # managed tags as read
    remove: list[tuple[str, str]] = field(default_factory=list)  # (tag, class)
    after: list[str] = field(default_factory=list)
    refusals: list[str] = field(default_factory=list)


@dataclass
class CleanupPlan:
    items: list[ItemPlan]

    @property
    def refusals(self) -> list[str]:
        return [f"{p.name or p.item_id} ({p.item_id}): {r}" for p in self.items for r in p.refusals]

    @property
    def writes(self) -> list[ItemPlan]:
        return [p for p in self.items if p.remove and not p.refusals]

    def digest(self) -> str:
        """sha256 of what the plan read and would remove, case folded (the *arrs' NFO merge only lowercases)."""
        canon = sorted(
            [p.item_id, sorted(t.casefold() for t in p.before), sorted([t.casefold(), c] for t, c in p.remove)]
            for p in self.items
        )
        return hashlib.sha256(json.dumps(canon, separators=(",", ":")).encode()).hexdigest()

    def as_dict(self) -> dict:
        return {"digest": self.digest(), "refusals": self.refusals, "items": [asdict(p) for p in self.items]}


def is_retired(tag: str, prefix: str) -> bool:
    """Class (ii): a managed tag in a spelling the vocabulary has retired (B9, B7)."""
    if not tag.startswith(prefix):
        return False
    body = tag[len(prefix) :]
    if tag_label(body) != body:
        return True
    return body.removeprefix("sub-").upper() in B7_RETIRED_LANGUAGES and len(body.removeprefix("sub-")) == 2


def _is_resolution(tag: str, prefix: str) -> bool:
    return tag.startswith(prefix) and tag[len(prefix) :].casefold() in RESOLUTION_LABELS


def plan_item(item_id: str, item: dict | None, row: MediaState | None, cfg: AppConfig) -> ItemPlan:
    """What to remove from one item, and why the plan would refuse it. Pure: no I/O."""
    prefix = cfg.tags.managed_prefix
    p = ItemPlan(item_id=item_id)
    if row is None:
        p.refusals.append("no state.db row: not an item the pass could have written")
    else:
        p.row_resolution = row.resolution
        p.row_tags = expected_tags(row.tags_applied, prefix)
    if item is None:
        p.refusals.append("not returned by /Items?Ids=")
        return p
    p.name = item.get("Name") or ""
    tags = [t for t in item.get("Tags") or [] if isinstance(t, str)]
    p.before = [t for t in tags if t.startswith(prefix)]
    legacy = [t for t in tags if not t.startswith(prefix) and any(t.startswith(lp) for lp in cfg.tags.legacy_prefixes)]
    if legacy:
        p.refusals.append(f"carries legacy-prefix tag(s) {legacy} that the write path would also remove")

    removed: set[str] = set()
    for t in p.before:
        if is_retired(t, prefix):
            p.remove.append((t, CLASS_RETIRED))
            removed.add(t)
    resolutions = [t for t in p.before if _is_resolution(t, prefix) and t not in removed]
    if len({t.casefold() for t in resolutions}) > 1 and row is not None:
        restored = f"{prefix}{row.resolution}".casefold()
        row_cf = {t.casefold() for t in p.row_tags}
        for t in resolutions:
            if t.casefold() == restored and restored in row_cf:
                p.remove.append((t, CLASS_RESOLUTION))
                removed.add(t)
    left = {t.casefold() for t in p.before if _is_resolution(t, prefix) and t not in removed}
    if len(left) > 1:
        p.refusals.append(f"would still carry {len(left)} resolution tags {sorted(left)}: cannot tell which is right")
    p.after = [t for t in p.before if t not in removed]
    return p


def build_plan(jf: JellyfinClient, session: object, cfg: AppConfig, item_ids: list[str]) -> tuple[CleanupPlan, dict]:
    """Read every item by ``Ids=`` (full fields: the apply posts the whole item) and plan each one."""
    if len(set(item_ids)) != len(item_ids):
        raise Refused("an item id is given twice")
    items = jf.get_items_by_ids(item_ids)
    plans = [plan_item(i, items.get(i), session.get(MediaState, f"jellyfin:{i}"), cfg) for i in item_ids]
    return CleanupPlan(plans), items


def self_test() -> list[str]:
    """Prove the classifier both ways on invented items; return the failures (empty = pass)."""
    cfg = AppConfig()
    failures: list[str] = []
    row = MediaState(item_id="jellyfin:t", resolution="720p", tags_applied=json.dumps(["xt-720p", "xt-H265", "xt-EN"]))

    def check(what: str, tags: list[str], remove: set[str], refused: bool, row: MediaState | None = row) -> None:
        p = plan_item("t", {"Id": "t", "Name": "T", "Tags": tags}, row, cfg)
        got = {t for t, _ in p.remove}
        if got != remove or bool(p.refusals) != refused:
            failures.append(f"{what}: removed {sorted(got)}, refused {p.refusals} (wanted {sorted(remove)}, {refused})")

    check("stray resolution", ["kw", "xt-1080p", "xt-720p", "xt-H265"], {"xt-720p"}, False)
    check("one resolution is not stray", ["xt-720p", "xt-H265"], set(), False)
    check(
        "retired spellings",
        ["xt-1080p", "xt-DD+", "xt-H.264", "xt-sub-KH", "xt-PE"],
        {"xt-DD+", "xt-H.264", "xt-sub-KH", "xt-PE"},
        False,
    )
    check(
        "current spellings kept", ["xt-1080p", "xt-DDplus", "xt-H264", "xt-KM", "xt-sub-EN", "xt-TV-14"], set(), False
    )
    check("a planted third resolution", ["xt-1080p", "xt-720p", "xt-4K"], {"xt-720p"}, True)
    check("a resolution the row never wrote", ["xt-1080p", "xt-4K"], set(), True)
    check("a planted legacy tag", ["xt-1080p", "mf-1080p"], set(), True)
    check("no row", ["xt-1080p", "xt-720p"], set(), True, row=None)
    unwritten = MediaState(item_id="jellyfin:t", resolution="720p", tags_applied=json.dumps(["xt-H265"]))
    check("the row's resolution, never in its tags", ["xt-1080p", "xt-720p"], set(), True, row=unwritten)
    return failures


# ---------------------------------------------------------------------------
# The apply
# ---------------------------------------------------------------------------


def _scan_running(session: object) -> ScanRun | None:
    since = _utcnow() - RUNNING_WINDOW
    return (
        session.query(ScanRun)
        .filter(ScanRun.completed_at.is_(None), ScanRun.started_at >= since)
        .order_by(ScanRun.started_at.desc())
        .first()
    )


def apply(jf: JellyfinClient, session: object, cfg: AppConfig, item_ids: list[str], expect: str) -> dict:
    """Re-read, check the digest, then write the first item, read it back, then the rest."""
    # Imported here: pipeline imports the whole scan stack, which the dry run does not need.
    from .pipeline import _PendingRecord, _Readback, _record_after_readback

    running = _scan_running(session)
    if running is not None:
        raise Refused(f"a {running.scan_type} run started {running.started_at} has not finished; try again later")
    plan, items = build_plan(jf, session, cfg, item_ids)
    if plan.refusals:
        raise Refused("the plan refuses: " + "; ".join(plan.refusals))
    if plan.digest() != expect:
        raise Refused(f"the plan read now ({plan.digest()}) is not the plan measured ({expect}); re-run the dry run")
    prefix = cfg.tags.managed_prefix
    done: list[dict] = []
    for n, p in enumerate(plan.writes):
        item = items[p.item_id]
        readback = _Readback()
        jf.set_managed_tags(p.item_id, item, prefix, p.after, legacy_prefixes=cfg.tags.legacy_prefixes)
        pending = _PendingRecord(
            item=item,
            written=p.after,
            fallback_rating="",
            record=None,
            settled_from=time.monotonic(),
            tags_row=f"jellyfin:{p.item_id}",
        )
        _record_after_readback(jf, session, cfg, [pending], readback)
        ok = readback.stuck + readback.fixed == 1
        done.append({"item_id": p.item_id, "name": p.name, "after": p.after, "read_back": ok})
        log.info("B32 clean-up: %s (%s) -> %s; read back %s", p.name, p.item_id, p.after, "OK" if ok else "WRONG")
        if not ok:
            raise Refused(
                f"{p.name} ({p.item_id}) did not read back as written (write {n + 1} of {len(plan.writes)}); "
                f"stopped, {len(plan.writes) - n - 1} not written. Written so far: {done}"
            )
    return {"digest": plan.digest(), "written": done}


# ---------------------------------------------------------------------------
# CLI: python -m app.reconcile_cleanup
# ---------------------------------------------------------------------------


def _print_plan(plan: CleanupPlan) -> None:
    for p in plan.items:
        print(f"- {p.name or '?'} ({p.item_id}) row: resolution={p.row_resolution} tags={p.row_tags}")
        print(f"    before: {p.before}")
        print(f"    remove: {[f'{t} ({c})' for t, c in p.remove] or 'nothing'}")
        print(f"    after:  {p.after}")
        for r in p.refusals:
            print(f"    REFUSED: {r}")
    print(f"items: {len(plan.items)}, to write: {len(plan.writes)}, refusals: {len(plan.refusals)}")
    print(f"plan digest: {plan.digest()}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m app.reconcile_cleanup",
        description="Roadmap B32: remove the stray resolution tag and retired spellings the 2026-10-11 "
        "reconciliation pass wrote. Dry run (GET-only) unless --apply.",
    )
    ap.add_argument("--item", action="append", required=True, help="a Jellyfin item id (repeat)")
    ap.add_argument("--config", type=Path, help="config.yml (default: $CONFIG_PATH)")
    ap.add_argument("--db", type=Path, help="dry run only: a state.db COPY, opened read-only. Default: the app's own")
    ap.add_argument("--url", help="Jellyfin URL (default: the config's)")
    ap.add_argument("--out", type=Path, help="write the plan as JSON here")
    ap.add_argument("--apply", action="store_true", help="write the plan (needs --expect-plan)")
    ap.add_argument("--expect-plan", help="the digest the dry run printed; --apply refuses any other plan")
    args = ap.parse_args(argv)
    if args.apply and not args.expect_plan:
        ap.error("--apply needs --expect-plan <digest> from a dry run")
    if args.apply and args.db:
        ap.error("--apply records into the app's own state.db; --db is for a dry run on a copy")

    from .clients.readonly import self_test as guard_self_test

    failures = guard_self_test() + self_test()
    print("self-test (read-only guard + classifier):", "PASS" if not failures else "FAIL")
    for failure in failures:
        print("  -", failure)
    if failures:
        return 2

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s — %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    from .config import load_config

    cfg = load_config(args.config)
    url = args.url or cfg.jellyfin.url
    guard = None if args.apply else ReadOnlyTransport()
    jf = JellyfinClient(url, cfg.jellyfin.api_key, transport=guard)
    if args.apply:
        init_db()
        session = get_session()
    else:
        session = (
            read_only_session(args.db) if args.db else read_only_session(os.environ.get("STATE_DB", "/config/state.db"))
        )
    try:
        if args.apply:
            result = apply(jf, session, cfg, args.item, args.expect_plan)
            print(json.dumps(result, indent=2))
            print(f"written: {len(result['written'])}")
            return 0
        plan, _ = build_plan(jf, session, cfg, args.item)
        _print_plan(plan)
        print(f"transport guard: sent {guard.sent}, blocked {len(guard.blocked)}")
        if args.out:
            args.out.write_text(
                json.dumps({**plan.as_dict(), "guard": {"sent": guard.sent, "blocked": guard.blocked}}, indent=2)
            )
            print(f"plan: {args.out}")
        return 1 if plan.refusals or guard.blocked else 0
    except Refused as exc:
        print(f"REFUSED: {exc}")
        return 1
    finally:
        session.close()
        jf.close()


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    sys.exit(main())
