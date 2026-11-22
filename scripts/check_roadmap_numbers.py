#!/usr/bin/env python3
"""Refuse two roadmap items that carry the same ID (roadmap I20, ~/docker I295).

    python3 scripts/check_roadmap_numbers.py [ROADMAP.md]

`ROADMAP.md` is the only item-number registry, and items are table rows, not
headings: every table whose header's first cell is `ID` lists one item per row,
and that row's first cell is the item's ID -- a category letter (B, U, I, P), a
number and an optional sub-letter, `B13`, `U9a` or `B12(a)`. The detail notes
below the tables (`**B25 — FILED ...**`) and the Deferred table's `P1 — ...`
rows refer to items; they do not define them, so they are not read.

The check fails when:
  * two rows carry the same ID. `B01` and `B1` are one ID; `B12(a)` and
    `B12(b)`, or `U9` and `U9a`, are different IDs;
  * a row of an `ID` table has a first cell that is not an ID (the format
    moved, so a duplicate could hide in it);
  * the file has no item rows at all (the format moved further still).

Exit 0 and a one-line summary on success; exit 1 and one line per problem.
Stdlib only: CI runs it with the runner's own `python3` in both lanes, a
docs-only PR included, before anything is installed.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Duplicates already on `main` when this check landed, each with its reason.
# Never renumber someone's item to make this pass (~/docker RULES_HEAD
# "Numbers"): file the collision and list it here instead. Empty on purpose.
ALLOWED_DUPLICATES: dict[str, str] = {}

_ROW = re.compile(r"^\|(.*)\|\s*$")
_SEPARATOR = re.compile(r"^:?-+:?$")
# Optional bold/strikethrough decoration around the ID, as ROADMAP rows use.
_ID = re.compile(r"^(?:~~)?(?:\*\*)?([BUIP])(\d+)([a-z]|\([a-z0-9]+\))?(?:\*\*)?(?:~~)?$")


def _cells(line: str) -> list[str] | None:
    m = _ROW.match(line)
    if not m:
        return None
    return [c.strip() for c in m.group(1).split("|")]


def parse_ids(text: str) -> tuple[list[tuple[str, int]], list[str]]:
    """The (ID, line number) of every item row, and every problem found on the way."""
    ids: list[tuple[str, int]] = []
    problems: list[str] = []
    in_id_table = False
    for n, line in enumerate(text.splitlines(), 1):
        cells = _cells(line)
        if cells is None:
            in_id_table = False
            continue
        first = cells[0]
        if first == "ID":
            in_id_table = True
            continue
        if not in_id_table or all(_SEPARATOR.match(c) for c in cells if c):
            continue
        m = _ID.match(first)
        if not m:
            problems.append(f"line {n}: an ID table row whose first cell is not an item ID: {first[:60]!r}")
            continue
        letter, number, sub = m.groups()
        ids.append((f"{letter}{int(number)}{sub or ''}", n))
    return ids, problems


def check(text: str, allowed: dict[str, str] | None = None) -> list[str]:
    """Every problem with the file's item IDs; an empty list means it passes."""
    allowed = ALLOWED_DUPLICATES if allowed is None else allowed
    ids, problems = parse_ids(text)
    if not ids:
        problems.append("no item rows found: no table with an `ID` header lists any item")
    seen: dict[str, list[int]] = {}
    for item_id, n in ids:
        seen.setdefault(item_id, []).append(n)
    for item_id, lines in seen.items():
        if len(lines) > 1 and item_id not in allowed:
            problems.append(f"{item_id} is used by {len(lines)} items, on lines {', '.join(map(str, lines))}")
    return problems


def main(argv: list[str]) -> int:
    path = Path(argv[1] if len(argv) > 1 else "ROADMAP.md")
    text = path.read_text(encoding="utf-8")
    problems = check(text)
    if problems:
        for p in problems:
            print(f"{path}: {p}")
        return 1
    ids, _ = parse_ids(text)
    print(f"{path}: {len(ids)} item IDs, no duplicates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
