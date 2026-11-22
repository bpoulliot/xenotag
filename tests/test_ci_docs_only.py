"""scripts/ci_docs_only.py: CI's docs-only detector, and the guards that keep it honest (roadmap I20).

Stdlib `unittest`, loaded by path and independent of conftest.py, so the
docs-only CI lane runs this file with the runner's bare `python3`
(`tests/docs_lane.txt`); pytest collects it as usual in the full run.
"""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("ci_docs_only", ROOT / "scripts" / "ci_docs_only.py")
cdo = importlib.util.module_from_spec(_spec)
sys.modules["ci_docs_only"] = cdo
_spec.loader.exec_module(cdo)

DOCS_LANE = ROOT / "tests" / "docs_lane.txt"

# Source files that NAME a repo `.md` file without opening it. Anything else
# that names one must be in the docs lane or read Markdown only CI treats as
# code. Each entry is (naming file, named file): reason.
NAMED_NOT_READ = {
    ("app/reconcile.py", "docs/measurements/b12-tag-loss.md"): "cited in the module docstring",
    ("scripts/measure_i59_ghcr_pull.py", "docs/measurements/i59-image-registry.md"): "cited in the docstring",
}
# Read in both lanes by scripts/check_roadmap_numbers.py, so it stays prose.
READ_IN_BOTH_LANES = {"ROADMAP.md"}

_MD_LITERAL = re.compile(r"[A-Za-z0-9_./-]*[A-Za-z0-9_]\.md\b")


def docs_lane_files() -> list[str]:
    lines = DOCS_LANE.read_text(encoding="utf-8").splitlines()
    return [s for s in (line.strip() for line in lines) if s and not s.startswith("#")]


def git(cwd: str, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
    }
    return subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=True).stdout.strip()


class Decision(unittest.TestCase):
    def test_markdown_only_is_docs_only(self):
        self.assertTrue(cdo.docs_only_files(["README.md", "ROADMAP.md", "docs/measurements/x.md"]))

    def test_markdown_plus_python_is_not(self):
        self.assertFalse(cdo.docs_only_files(["ROADMAP.md", "app/overlay.py"]))

    def test_markdown_plus_any_other_file_is_not(self):
        self.assertFalse(cdo.docs_only_files(["README.md", "assets/readme/example.png"]))

    def test_markdown_that_code_reads_is_not(self):
        # app/ is copied whole into the image; tests/, scripts/, .github/ sit beside code
        for path in ("app/migrations/README.md", "tests/fixtures/x.md", "scripts/notes.md", ".github/x.md"):
            with self.subTest(path=path):
                self.assertFalse(cdo.docs_only_files(["ROADMAP.md", path]))

    def test_an_empty_or_unknown_diff_is_not(self):
        self.assertFalse(cdo.docs_only_files([]))
        self.assertFalse(cdo.docs_only_files(None))

    def test_a_suffix_that_only_contains_md_is_not(self):
        self.assertFalse(cdo.docs_only_files(["notes.mdx"]))
        self.assertFalse(cdo.docs_only_files(["README.md.bak"]))


class AgainstGit(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name
        git(self.repo, "init", "-q", "-b", "main")
        Path(self.repo, "README.md").write_text("one\n", encoding="utf-8")
        Path(self.repo, "x.py").write_text("x = 1\n", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "base")
        self.base = git(self.repo, "rev-parse", "HEAD")
        self._cwd = os.getcwd()
        os.chdir(self.repo)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def commit(self, msg: str) -> str:
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", msg)
        return git(self.repo, "rev-parse", "HEAD")

    def run_main(self, base: str, head: str) -> str:
        out = Path(self.repo, ".gh_output")
        out.unlink(missing_ok=True)
        old = os.environ.get("GITHUB_OUTPUT")
        os.environ["GITHUB_OUTPUT"] = str(out)
        try:
            with open(os.devnull, "w") as null:
                saved, sys.stdout, sys.stderr = (sys.stdout, sys.stderr), null, null
                try:
                    self.assertEqual(cdo.main(["ci_docs_only.py", base, head]), 0)
                finally:
                    sys.stdout, sys.stderr = saved
        finally:
            if old is None:
                os.environ.pop("GITHUB_OUTPUT", None)
            else:
                os.environ["GITHUB_OUTPUT"] = old
        return out.read_text(encoding="utf-8")

    def test_a_markdown_commit_is_docs_only(self):
        Path(self.repo, "README.md").write_text("two\n", encoding="utf-8")
        head = self.commit("docs")
        self.assertEqual(self.run_main(self.base, head), "docs_only=true\n")

    def test_a_code_commit_is_not(self):
        Path(self.repo, "README.md").write_text("two\n", encoding="utf-8")
        Path(self.repo, "x.py").write_text("x = 2\n", encoding="utf-8")
        head = self.commit("code")
        self.assertEqual(self.run_main(self.base, head), "docs_only=false\n")

    def test_a_rename_from_python_to_markdown_is_not(self):
        git(self.repo, "mv", "x.py", "x.md")
        head = self.commit("rename")
        self.assertEqual(self.run_main(self.base, head), "docs_only=false\n")

    def test_an_empty_diff_fails_open(self):
        self.assertEqual(self.run_main(self.base, self.base), "docs_only=false\n")

    def test_an_unknown_sha_fails_open(self):
        self.assertEqual(self.run_main(self.base, "0" * 40), "docs_only=false\n")

    def test_a_missing_sha_fails_open(self):
        self.assertEqual(self.run_main("", ""), "docs_only=false\n")

    def test_an_exception_fails_open(self):
        real = cdo.changed_files
        cdo.changed_files = lambda base, head: 1 / 0
        try:
            self.assertEqual(self.run_main(self.base, self.base), "docs_only=false\n")
        finally:
            cdo.changed_files = real


class Guards(unittest.TestCase):
    """Keep CODE_MD_PREFIXES and tests/docs_lane.txt in step with the code that reads Markdown."""

    def sources(self):
        for sub in ("app", "tests", "scripts"):
            yield from sorted((ROOT / sub).rglob("*.py"))
        yield from sorted((ROOT / ".github" / "workflows").glob("*.yml"))
        yield ROOT / "Dockerfile"
        yield ROOT / "pyproject.toml"

    def test_every_named_markdown_file_is_accounted_for(self):
        lane = set(docs_lane_files())
        unaccounted = []
        for src in self.sources():
            rel = src.relative_to(ROOT).as_posix()
            if rel in lane:
                continue  # runs in the docs lane: it may read any Markdown it likes
            for literal in set(_MD_LITERAL.findall(src.read_text(encoding="utf-8"))):
                target = literal.lstrip("./")
                if not (ROOT / target).is_file():
                    continue
                if not cdo.is_prose(target) or target in READ_IN_BOTH_LANES:
                    continue
                if (rel, target) in NAMED_NOT_READ:
                    continue
                unaccounted.append(f"{rel} names {target}")
        self.assertEqual(
            unaccounted,
            [],
            "a source file names a Markdown file a docs-only PR may change: put the reader in "
            "tests/docs_lane.txt, count the file as code in CODE_MD_PREFIXES, or list it in NAMED_NOT_READ",
        )

    def test_the_named_not_read_entries_are_still_named(self):
        for (src, target), reason in NAMED_NOT_READ.items():
            with self.subTest(src=src):
                self.assertIn(target, (ROOT / src).read_text(encoding="utf-8"))
                self.assertTrue(reason)

    def test_the_docs_lane_list_is_real(self):
        files = docs_lane_files()
        self.assertIn("tests/test_check_roadmap_numbers.py", files)
        self.assertIn("tests/test_ci_docs_only.py", files)
        for f in files:
            with self.subTest(f=f):
                self.assertRegex(f, r"^tests/test_\w+\.py$")
                self.assertTrue((ROOT / f).is_file())

    def test_the_docs_lane_files_run_without_the_project_installed(self):
        # The docs lane runs them with the runner's bare python3: no third-party import allowed.
        stdlib = set(sys.stdlib_module_names)
        for f in docs_lane_files():
            for mod in re.findall(r"^(?:from|import)\s+([A-Za-z_]\w*)", (ROOT / f).read_text(), re.M):
                with self.subTest(f=f, mod=mod):
                    self.assertIn(mod, stdlib | {"__future__"})

    def test_the_workflow_runs_the_docs_lane_list(self):
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        for needle in (
            "tests/docs_lane.txt",
            "python3 scripts/ci_docs_only.py",
            "python3 scripts/check_roadmap_numbers.py",
        ):
            with self.subTest(needle=needle):
                self.assertTrue(needle in ci, f"ci.yml does not run {needle}")


if __name__ == "__main__":
    unittest.main()
