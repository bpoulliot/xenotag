#!/usr/bin/env python3
"""Generate ``app/iso639.py`` -- the ISO 639-2 -> ISO 639-1 table (roadmap B7).

The table is data, and a wrong entry is silent: it names the wrong language on
every item that carries the code. So it is never typed by hand. This script reads
the Library of Congress's own machine-readable list -- the ISO 639-2 Registration
Authority -- and writes the module; the module is committed, and nothing at
runtime downloads anything.

    python scripts/generate_iso639_table.py --fetch            # download, write
    python scripts/generate_iso639_table.py --source FILE      # a saved copy, write
    python scripts/generate_iso639_table.py --source FILE --check   # exit 1 if stale

The source is ``https://www.loc.gov/standards/iso639-2/ISO-639-2_utf-8.txt``: one
language per line, ``bibliographic|terminologic|alpha2|English|French``, the
terminologic field empty unless it differs (``fre|fra|fr``). Both 3-letter codes go
in the table, because ffprobe reports whichever the muxer wrote (Matroska writes
the bibliographic ``fre``, others the terminologic ``fra``).

The source is validated before anything is written, and the script refuses to
write a table that fails: every line has five fields, every code is 3 lowercase
ASCII letters (the one range, ``qaa-qtz``, is skipped), every alpha2 is 2, no code
appears twice, and a handful of pairs are what the standard says they are.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

URL = "https://www.loc.gov/standards/iso639-2/ISO-639-2_utf-8.txt"
OUT = Path(__file__).resolve().parent.parent / "app" / "iso639.py"

_ALPHA3 = re.compile(r"[a-z]{3}")
_ALPHA2 = re.compile(r"[a-z]{2}")

# Checked against the parsed source, both directions: pairs that must map, and
# codes that must have no 2-letter form. If LoC ever changes one, a human looks.
_KNOWN = {"eng": "en", "fre": "fr", "fra": "fr", "khm": "km", "per": "fa", "fas": "fa", "tam": "ta", "bur": "my"}
_KNOWN_NONE = {"zxx", "und", "mul", "mis", "egy"}


def parse(text: str) -> list[tuple[str, str, str]]:
    """``(code, alpha2, English name)`` for every 3-letter code, B and T alike."""
    rows: list[tuple[str, str, str]] = []
    for n, line in enumerate(text.lstrip("﻿").splitlines(), 1):
        if not line.strip():
            continue
        fields = line.split("|")
        if len(fields) != 5:
            raise ValueError(f"line {n}: {len(fields)} fields, expected 5: {line!r}")
        bib, term, alpha2, english, _french = fields
        if bib == "qaa-qtz":  # a reserved range, not a code
            continue
        for code in (bib, term) if term else (bib,):
            if not _ALPHA3.fullmatch(code):
                raise ValueError(f"line {n}: {code!r} is not a 3-letter code")
            rows.append((code, alpha2, english))
        if alpha2 and not _ALPHA2.fullmatch(alpha2):
            raise ValueError(f"line {n}: {alpha2!r} is not a 2-letter code")
    return rows


def validate(rows: list[tuple[str, str, str]]) -> dict[str, str]:
    table: dict[str, str] = {}
    for code, alpha2, _ in rows:
        if code in table:
            raise ValueError(f"{code!r} appears twice")
        table[code] = alpha2
    for code, alpha2 in _KNOWN.items():
        if table.get(code) != alpha2:
            raise ValueError(f"{code!r} -> {table.get(code)!r}, expected {alpha2!r}")
    for code in _KNOWN_NONE:
        if code not in table or table[code]:
            raise ValueError(f"{code!r} should be present with no 2-letter code, got {table.get(code)!r}")
    if not (450 <= len(table) <= 600) or not (180 <= sum(1 for a in table.values() if a) <= 230):
        raise ValueError(f"implausible size: {len(table)} codes, {sum(1 for a in table.values() if a)} mapped")
    return table


def render(rows: list[tuple[str, str, str]], sha256: str, retrieved: str) -> str:
    mapped = sum(1 for _, a, _ in rows if a)
    lines = [
        '"""ISO 639-2 -> ISO 639-1 (roadmap B7). GENERATED -- do not edit by hand.',
        "",
        "Written by ``scripts/generate_iso639_table.py`` from the Library of Congress list",
        f"{URL}",
        f"(retrieved {retrieved}, sha256 {sha256}).",
        '"""',
        "",
        "# Every ISO 639-2 code, bibliographic and terminologic, -> its ISO 639-1 code, or",
        f'# "" where the language has none. {len(rows)} codes, {mapped} with a 2-letter code.',
        "ISO639_2_TO_1: dict[str, str] = {",
    ]
    for code, alpha2, english in sorted(rows):
        lines.append(f'    "{code}": "{alpha2}",  # {english}')
    lines.append("}")
    return "\n".join(lines) + "\n"


def _header_field(text: str, name: str) -> str | None:
    m = re.search(rf"{name} ([^,)\s]+)", text)
    return m.group(1) if m else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--fetch", action="store_true", help=f"download {URL}")
    src.add_argument("--source", type=Path, help="a saved copy of that file")
    ap.add_argument("--check", action="store_true", help="compare with the committed module; write nothing")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)

    if args.fetch:
        with urllib.request.urlopen(URL, timeout=30) as resp:  # noqa: S310 -- a fixed https URL
            raw = resp.read()
    else:
        raw = args.source.read_bytes()
    sha256 = hashlib.sha256(raw).hexdigest()
    rows = parse(raw.decode("utf-8"))
    validate(rows)

    committed = args.out.read_text(encoding="utf-8") if args.out.exists() else ""
    # Re-generating from the same bytes keeps the committed retrieval date, so a
    # --check against a saved copy compares tables, not calendars.
    same_source = _header_field(committed, "sha256") == sha256
    retrieved = _header_field(committed, "retrieved") if same_source else datetime.now(UTC).date().isoformat()
    text = render(rows, sha256, retrieved or "")

    if args.check:
        if text != committed:
            print(f"{args.out} is stale against this source (sha256 {sha256})", file=sys.stderr)
            return 1
        print(f"{args.out}: up to date ({len(rows)} codes)")
        return 0
    args.out.write_text(text, encoding="utf-8")
    print(f"wrote {args.out}: {len(rows)} codes, {sum(1 for _, a, _ in rows if a)} with a 2-letter code")
    return 0


if __name__ == "__main__":
    sys.exit(main())
