#!/usr/bin/env python3
"""CI's docs-only detector: is every file this PR changes prose? (roadmap I20, ~/docker I295)

    python3 scripts/ci_docs_only.py <base sha> <head sha>

Prints `docs_only=true` or `docs_only=false` and appends the same line to
`$GITHUB_OUTPUT` when that is set. The diff is the PR's own, from the merge
base (`base...head`), with rename detection off so a `x.py -> x.md` rename
still lists the `.py` it removed.

It answers `true` only for a non-empty diff of `.md` files that no code reads.
A `.md` file under a directory in `CODE_MD_PREFIXES` counts as code: `app/`
is copied whole into the image (the Dockerfile's `COPY app/`), and anything
under `tests/`, `scripts/` or `.github/` sits beside the code that runs. No
other `.md` file in the repo is opened by code, a test or a checker except
`ROADMAP.md`, whose only reader (`scripts/check_roadmap_numbers.py`) runs in
both lanes. `tests/test_ci_docs_only.py` fails when a source file names a `.md`
path this module does not account for.

Every other outcome is `false`, the full run: a git error, a sha that does not
resolve, an empty diff, any exception. It always exits 0. A broken detector
therefore costs CI time and never skips a check, and cannot turn the `changes`
job red.

Stdlib only: the `changes` job runs it with the runner's own `python3` before
anything is installed.
"""

from __future__ import annotations

import os
import subprocess
import sys

# A `.md` file under one of these is code, not prose: see the module docstring.
CODE_MD_PREFIXES = ("app/", "tests/", "scripts/", ".github/")


def is_prose(path: str) -> bool:
    """True for a Markdown file that nothing in CI or the image reads."""
    return path.endswith(".md") and not path.startswith(CODE_MD_PREFIXES)


def changed_files(base: str, head: str) -> list[str] | None:
    """The files `base...head` changes, or None when git cannot say."""
    if not base or not head:
        return None
    r = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", f"{base}...{head}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        sys.stderr.write(f"ci_docs_only: git diff failed: {r.stderr.strip()}\n")
        return None
    return [line for line in r.stdout.splitlines() if line]


def docs_only_files(files: list[str] | None) -> bool:
    """The decision on a list of changed paths: an empty or unknown list is never docs-only."""
    if not files:
        return False
    return all(is_prose(f) for f in files)


def docs_only(base: str, head: str) -> bool:
    return docs_only_files(changed_files(base, head))


def main(argv: list[str]) -> int:
    try:
        answer = docs_only(*(argv[1:3] + ["", ""])[:2])
    except Exception as exc:  # noqa: BLE001 -- fail open to the full run, whatever broke
        sys.stderr.write(f"ci_docs_only: {exc!r}; answering false\n")
        answer = False
    line = f"docs_only={'true' if answer else 'false'}"
    print(line)
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
