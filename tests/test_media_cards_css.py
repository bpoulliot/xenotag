"""P4: below 600 px the media browser's rows are cards, driven by column position.

The card CSS hides and labels columns by `:nth-child`, so it is only right while the table's columns are
in the order it was written for. A column added or moved in `loadMedia()` or the header would hide the
wrong one with no error; this test pins the order and the rule.
"""

import re
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent.parent / "app" / "web" / "templates" / "index.html"
HTML = TEMPLATE.read_text(encoding="utf-8")

COLUMNS = ["Item", "Source", "Video", "Audio", "Rating", "Tags", "Scanned"]


def test_media_table_columns_match_the_card_css():
    table = re.search(r'<table class="media-table">\s*<thead><tr>(.*?)</tr></thead>', HTML, re.S)
    assert table, "the media table lost its media-table class"
    assert re.findall(r"<th>(.*?)</th>", table.group(1)) == COLUMNS

    media = re.search(r"@media\(max-width:600px\)\{\s*\.media-table thead,(.*?)\n  \}", HTML, re.S)
    assert media, "the media-table card rule is gone"
    css = media.group(0)
    # Source, Audio and Scanned are hidden: columns 2, 4 and 7
    hidden = css.split("{display:none}")[0].rsplit("\n", 1)[-1]
    assert set(re.findall(r"td:nth-child\((\d+)\)", hidden)) == {"2", "4", "7"}
    # Video, Rating and Tags are labelled: columns 3, 5 and 6
    labels = dict(re.findall(r"td:nth-child\((\d+)\)::before\{content:'(\w+)'\}", css))
    assert labels == {"3": "Video", "5": "Rating", "6": "Tags"}
    for n, name in labels.items():
        assert COLUMNS[int(n) - 1] == name


def test_media_row_cells_follow_the_header_order():
    """loadMedia() emits seven cells per row; the card CSS counts them."""
    row = re.search(r"return `<tr>(.*?)</tr>`", HTML[HTML.index("async function loadMedia") :], re.S)
    assert row
    assert len(re.findall(r"<td[ >]", row.group(1))) == len(COLUMNS)
