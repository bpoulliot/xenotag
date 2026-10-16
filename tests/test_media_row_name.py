"""B31: the media browser's Item column names an episode by series and season, not by its season folder.

`mediaRowName()` is a plain function in the dashboard template, so the test cuts it out of
`index.html` and runs the real text under node. Every path is invented.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

TEMPLATE = Path(__file__).resolve().parent.parent / "app" / "web" / "templates" / "index.html"

# (file_path, expected name). file_path None means the row has no path and falls back to its item id.
CASES = [
    # the decision: a Season folder shows the series above it and S + two digits
    ("/lib/tv/Example Show/Season 01/Example Show - S01E03.mkv", "Example Show · S01"),
    ("/lib/tv/Example Show/Season 12/Example Show - S12E01.mkv", "Example Show · S12"),
    ("/lib/tv/Example Show/Season 1/e.mkv", "Example Show · S01"),
    ("/lib/tv/Example Show/Season 0/e.mkv", "Example Show · S00"),
    ("/lib/tv/Example Show/Season 100/e.mkv", "Example Show · S100"),
    ("/lib/tv/Example Show (2019)/Season 02/e.mkv", "Example Show (2019) · S02"),
    # Specials
    ("/lib/tv/Example Show/Specials/e.mkv", "Example Show · Specials"),
    # a movie folder keeps today's rule
    ("/lib/movies/Sample Film (2020)/Sample Film (2020).mkv", "Sample Film (2020)"),
    # a file directly under a library root: the root is its parent folder
    ("/lib/tv/loose.mkv", "tv"),
    ("/loose.mkv", "loose.mkv"),
    ("loose.mkv", "loose.mkv"),
    # a Season folder with no series folder above it keeps today's rule
    ("/Season 01/e.mkv", "Season 01"),
    ("Season 03/e.mkv", "Season 03"),
    ("/Specials/e.mkv", "Specials"),
    # a folder that only resembles a season is a plain folder
    ("/lib/tv/Example Show/Season Finale/e.mkv", "Season Finale"),
    ("/lib/tv/Example Show/Season 01 Extras/e.mkv", "Season 01 Extras"),
    ("/lib/tv/Example Show/Season/e.mkv", "Season"),
    # no path at all
    (None, "item-123"),
    ("", "item-123"),
]


def _helper_source() -> str:
    m = re.search(r"^function mediaRowName\(item\) \{\n.*?^\}\n", TEMPLATE.read_text(), re.S | re.M)
    assert m, "mediaRowName() not found in index.html"
    return m.group(0)


def _run(paths: list) -> list:
    script = _helper_source() + (
        "\nconst paths = JSON.parse(process.argv[1]);"
        "\nconsole.log(JSON.stringify(paths.map(p => mediaRowName({file_path: p, item_id: 'item-123'}))));\n"
    )
    out = subprocess.run(
        ["node", "-e", script, json.dumps(paths)], capture_output=True, text=True, check=True, timeout=60
    )
    return json.loads(out.stdout)


@pytest.fixture(scope="module")
def node_available():
    if shutil.which("node"):
        return
    # GitHub's runners ship node. Skipping there would be a test that quietly stopped testing.
    if os.environ.get("CI"):
        pytest.fail("node is required in CI to test the template JS")
    pytest.skip("node not installed")


def test_row_names(node_available):
    got = _run([p for p, _ in CASES])
    assert got == [want for _, want in CASES]


def test_probe_detects_a_wrong_answer(node_available):
    """The probe is not vacuous: the pre-B31 rule (parent folder only) fails the Season cases."""
    old = "function mediaRowName(item) { return item.file_path.split('/').slice(-2,-1)[0]; }"
    script = old + "\nconsole.log(mediaRowName({file_path: '/lib/tv/Example Show/Season 01/e.mkv'}));"
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout.strip()
    assert out == "Season 01"
    assert out != CASES[0][1]
