"""scripts/check_roadmap_numbers.py: two roadmap items never share an ID (roadmap I20).

Stdlib `unittest`, loaded by path and independent of conftest.py, so the
docs-only CI lane runs this file with the runner's bare `python3`
(`tests/docs_lane.txt`); pytest collects it as usual in the full run.
"""

from __future__ import annotations

import importlib.util
import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("check_roadmap_numbers", ROOT / "scripts" / "check_roadmap_numbers.py")
crn = importlib.util.module_from_spec(_spec)
sys.modules["check_roadmap_numbers"] = crn
_spec.loader.exec_module(crn)

HEADER = "| ID | Defect | Value | Readiness |\n|----|--------|:-----:|-----------|\n"


def table(*ids: str) -> str:
    return HEADER + "".join(f"| {i} | something | 3 | **READY** |\n" for i in ids)


class CheckRoadmapNumbers(unittest.TestCase):
    def test_distinct_ids_pass(self):
        self.assertEqual(crn.check(table("B1", "B2", "I1", "U1", "P1")), [])

    def test_a_duplicate_fails(self):
        problems = crn.check(table("B1", "B2", "B1"))
        self.assertEqual(len(problems), 1)
        self.assertIn("B1 is used by 2 items, on lines 3, 5", problems[0])

    def test_a_duplicate_across_two_tables_fails(self):
        text = table("U1", "U2") + "\nprose between\n\n" + table("U2")
        self.assertTrue(any(p.startswith("U2 is used by 2") for p in crn.check(text)))

    def test_sub_letters_are_their_own_ids(self):
        self.assertEqual(crn.check(table("B12(a)", "B12(b)", "U9", "U9a", "U9b")), [])

    def test_a_repeated_sub_letter_fails(self):
        self.assertTrue(crn.check(table("B12(a)", "B12(a)")))

    def test_leading_zeros_are_the_same_number(self):
        self.assertTrue(any("B1 is used by 2" in p for p in crn.check(table("B01", "B1"))))

    def test_decorated_ids_are_read(self):
        # strikethrough or bold around the ID does not hide a duplicate
        self.assertTrue(crn.check(table("~~I6~~", "**I6**")))

    def test_same_number_in_two_categories_passes(self):
        self.assertEqual(crn.check(table("B3", "I3", "P3", "U3")), [])

    def test_a_row_that_is_not_an_id_fails(self):
        problems = crn.check(table("B1", "Bogus"))
        self.assertTrue(any("not an item ID" in p for p in problems))

    def test_tables_without_an_id_header_are_not_items(self):
        text = table("B1") + "\n| Feature | Reason |\n|---|---|\n| P1 — moved here | deferred |\n"
        self.assertEqual(crn.check(text), [])

    def test_no_item_rows_fails(self):
        self.assertTrue(any("no item rows" in p for p in crn.check("# Roadmap\n\nnothing here\n")))

    def test_an_allowlisted_duplicate_passes(self):
        self.assertEqual(crn.check(table("B1", "B1"), allowed={"B1": "reason"}), [])

    def test_the_real_roadmap_passes(self):
        text = (ROOT / "ROADMAP.md").read_text(encoding="utf-8")
        self.assertEqual(crn.check(text), [])
        ids, _ = crn.parse_ids(text)
        self.assertGreater(len(ids), 50)

    def test_the_real_roadmap_with_a_duplicated_row_fails(self):
        lines = (ROOT / "ROADMAP.md").read_text(encoding="utf-8").splitlines(keepends=True)
        b1 = next(i for i, line in enumerate(lines) if line.startswith("| B1 |"))
        mutated = "".join(lines[: b1 + 1] + [lines[b1]] + lines[b1 + 1 :])
        self.assertTrue(any(p.startswith("B1 is used by 2") for p in crn.check(mutated)))

    def test_the_allowlist_holds_no_stale_entry(self):
        # every allowlisted ID is still duplicated on the real file, with a reason
        ids, _ = crn.parse_ids((ROOT / "ROADMAP.md").read_text(encoding="utf-8"))
        names = [i for i, _ in ids]
        for item_id, reason in crn.ALLOWED_DUPLICATES.items():
            self.assertGreater(names.count(item_id), 1, item_id)
            self.assertTrue(reason.strip(), item_id)

    def test_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as d:
            good, bad = Path(d, "good.md"), Path(d, "bad.md")
            good.write_text(table("B1", "B2"), encoding="utf-8")
            bad.write_text(table("B1", "B1"), encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(crn.main(["x", str(good)]), 0)
                self.assertEqual(crn.main(["x", str(bad)]), 1)
            r = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "check_roadmap_numbers.py"), str(bad)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(r.returncode, 1)
            self.assertIn("B1 is used by 2", r.stdout)


if __name__ == "__main__":
    unittest.main()
