"""Audio and subtitle language labels (roadmap B7).

Before B7 the scanner mapped 42 ISO 639-2 codes and cut anything else to its first
two letters, so Khmer (`khm`) was tagged `KH`, Persian (`per`) `PE`, "no linguistic
content" (`zxx`) `ZX`, and a malformed tag (`"eng"`) `"E`. The operator's decision
(2026-09-26): a complete ISO 639-2 -> 639-1 table, bibliographic and terminologic,
generated from the Library of Congress list; the 3-letter form only where there is
no 2-letter one; `zxx` and anything not 2-3 ASCII letters -> `UND` with a WARNING.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path

import pytest

from app import scanner
from app.config import TagsConfig
from app.iso639 import ISO639_2_TO_1
from app.scanner import _lang3_to_lang2
from app.tagger import tag_label
from scripts import generate_iso639_table as gen
from tests.test_tag_vocabulary import RATINGS, language_labels, non_language_vocabulary

# The map as it stood before B7, verbatim. Every entry must keep its output.
PRE_B7 = {
    **{"eng": "EN", "jpn": "JA", "fre": "FR", "fra": "FR", "ger": "DE", "deu": "DE", "spa": "ES"},
    **{"ita": "IT", "por": "PT", "rus": "RU", "chi": "ZH", "zho": "ZH", "kor": "KO", "ara": "AR"},
    **{"hin": "HI", "pol": "PL", "nld": "NL", "swe": "SV", "nor": "NO", "dan": "DA", "fin": "FI"},
    **{"tur": "TR", "heb": "HE", "hun": "HU", "ces": "CS", "cze": "CS", "ron": "RO", "rum": "RO"},
    **{"tha": "TH", "vie": "VI", "ind": "ID", "msa": "MS", "ukr": "UK", "hrv": "HR", "bul": "BG"},
    **{"cat": "CA", "slk": "SK", "slo": "SK", "slv": "SL", "lit": "LT", "lav": "LV", "est": "ET"},
}

# Every raw code production's index got from the old fallback, traced by probing
# the files (2026-09-27): code -> (what the fallback said, what B7 says).
TRACED = {
    "khm": ("KH", "KM"),
    "per": ("PE", "FA"),
    "fas": ("FA", "FA"),
    "tam": ("TA", "TA"),
    "tel": ("TE", "TE"),
    "zul": ("ZU", "ZU"),
    "srp": ("SR", "SR"),
    "glg": ("GL", "GL"),
    "gla": ("GL", "GD"),  # Scottish Gaelic, not Galician
    "bos": ("BO", "BS"),  # Bosnian, not Tibetan
    "bur": ("BU", "MY"),
    "mya": ("MY", "MY"),
    "mao": ("MA", "MI"),
    "may": ("MA", "MS"),
    "mac": ("MA", "MK"),
    "mkd": ("MK", "MK"),
    "mal": ("MA", "ML"),
    "egy": ("EG", "EGY"),  # Egyptian (Ancient): no 2-letter code
    "dut": ("DU", "NL"),
    "gre": ("GR", "EL"),
    "ell": ("EL", "EL"),
    "ice": ("IC", "IS"),
    "isl": ("IS", "IS"),
    "baq": ("BA", "EU"),
    "eus": ("EU", "EU"),
    "ben": ("BE", "BN"),
    "kan": ("KA", "KN"),
    "kat": ("KA", "KA"),
    "kaz": ("KA", "KK"),
    "kir": ("KI", "KY"),
    "mon": ("MO", "MN"),
    "aze": ("AZ", "AZ"),
    "sqi": ("SQ", "SQ"),
    "hye": ("HY", "HY"),
    "zxx": ("ZX", "UND"),
}


def _old_fallback(code: str) -> str:
    """The pre-B7 rule, for the both-directions check."""
    code = code.lower().strip()
    if code in ("", "und", "unknown"):
        return "UND"
    return PRE_B7.get(code, code[:2].upper() if len(code) >= 2 else code.upper())


# ── the table ───────────────────────────────────────────────────────────────
def test_the_table_is_the_whole_standard_and_well_formed():
    assert len(ISO639_2_TO_1) == 506
    assert sum(1 for two in ISO639_2_TO_1.values() if two) == 203
    assert all(re.fullmatch(r"[a-z]{3}", code) for code in ISO639_2_TO_1)
    assert all(two == "" or re.fullmatch(r"[a-z]{2}", two) for two in ISO639_2_TO_1.values())


def test_bibliographic_and_terminologic_codes_agree():
    for bib, term in [("fre", "fra"), ("ger", "deu"), ("per", "fas"), ("chi", "zho"), ("bur", "mya"), ("tib", "bod")]:
        assert ISO639_2_TO_1[bib] == ISO639_2_TO_1[term] != ""


def test_every_pre_b7_mapping_keeps_its_output():
    assert {code: _lang3_to_lang2(code) for code in PRE_B7} == PRE_B7


@pytest.mark.parametrize("code, old_new", TRACED.items())
def test_every_code_traced_on_production(code, old_new):
    old, new = old_new
    assert _old_fallback(code) == old  # the probe reproduces what is live...
    assert _lang3_to_lang2(code) == new  # ...and B7 changes it where it was wrong


def test_the_traced_codes_can_tell_the_rules_apart():
    """Both directions: the old rule and B7 must disagree somewhere, or the table proves nothing."""
    changed = {code for code, (old, new) in TRACED.items() if old != new}
    assert {"khm", "per", "zxx", "gla", "bos", "egy"} <= changed
    assert changed != set(TRACED)


# ── the rule ────────────────────────────────────────────────────────────────
def test_case_and_whitespace_do_not_matter():
    assert _lang3_to_lang2(" KHM ") == _lang3_to_lang2("Khm") == "KM"


def test_a_language_with_no_2_letter_code_keeps_its_3_letters():
    assert [_lang3_to_lang2(c) for c in ("egy", "mul", "ace", "fil")] == ["EGY", "MUL", "ACE", "FIL"]


def test_a_well_formed_code_outside_iso_639_2_keeps_its_letters():
    """ISO 639-3 codes (`yue` Cantonese) are real languages; the decision sends only malformed ones to UND."""
    assert _lang3_to_lang2("yue") == "YUE"
    assert _lang3_to_lang2("xx") == "XX"
    assert _lang3_to_lang2("en") == "EN"


@pytest.mark.parametrize("code", ["", "und", "UND", "unknown", "  "])
def test_no_language_is_und_quietly(code, caplog):
    with caplog.at_level(logging.WARNING, logger="app.scanner"):
        assert _lang3_to_lang2(code, "/m/a.mkv") == "UND"
    assert caplog.records == []


@pytest.mark.parametrize("code", ['"eng"', '"e', "e", "en-us", "eng1", "engl", "ééé", "12", "e n"])
def test_a_malformed_code_is_und_with_a_warning_naming_the_file(code, caplog):
    with caplog.at_level(logging.WARNING, logger="app.scanner"):
        assert _lang3_to_lang2(code, "/m/The Film (2018).mkv") == "UND"
    assert len(caplog.records) == 1
    assert "/m/The Film (2018).mkv" in caplog.text
    assert repr(code) in caplog.text


# Roadmap B20, decided (b): an old `.ogm` file's `Name[xxx]` is read as its bracketed code,
# which then goes through the same rule as any code. A bare name is not a code.
@pytest.mark.parametrize(
    "tag, label",
    [("English[eng]", "EN"), ("Japanese[jpn]", "JA"), ("ENGLISH[ENG]", "EN"), (" Persian [per] ", "FA")]
    + [("Cantonese[yue]", "YUE"), ("English[en]", "EN"), ("Egyptian[egy]", "EGY")],
)
def test_a_name_with_a_bracketed_code_reads_the_code(tag, label, caplog):
    with caplog.at_level(logging.WARNING, logger="app.scanner"):
        assert _lang3_to_lang2(tag, "/m/ep01.ogm") == label
    assert caplog.records == []


@pytest.mark.parametrize("tag", ["Unknown[und]", "Unknown[unknown]"])
def test_a_bracketed_no_language_is_und_quietly(tag, caplog):
    """`[unknown]` is not 2-3 letters, so it is malformed, not `und` -- see the next test."""
    with caplog.at_level(logging.WARNING, logger="app.scanner"):
        result = _lang3_to_lang2(tag, "/m/ep01.ogm")
    assert result == "UND"
    assert len(caplog.records) == (0 if tag == "Unknown[und]" else 1)


@pytest.mark.parametrize("tag", ["Foo[aac]", "Foo[dts]", "None[zxx]"])
def test_a_bracketed_code_the_rule_refuses_is_und_with_a_warning(tag, caplog):
    with caplog.at_level(logging.WARNING, logger="app.scanner"):
        assert _lang3_to_lang2(tag, "/m/ep01.ogm") == "UND"
    assert len(caplog.records) == 1
    assert "/m/ep01.ogm" in caplog.text


@pytest.mark.parametrize(
    "tag",
    # bare names stay UND (B20 decided not (c)); and only exactly `<name>[<2-3 letters>]` is read
    ["English", "Japanese", "[eng]", "Foo[eng][jpn]", "English[]", "English[e]", "English[engl]"]
    + ["English[eng", "English eng]", "English[eng]x", "[eng]English", "English[e1]", "English[[eng]]"],
)
def test_anything_else_shaped_like_a_name_is_und_with_a_warning(tag, caplog):
    with caplog.at_level(logging.WARNING, logger="app.scanner"):
        assert _lang3_to_lang2(tag, "/m/ep01.ogm") == "UND"
    assert len(caplog.records) == 1
    assert repr(tag) in caplog.text and "/m/ep01.ogm" in caplog.text


def test_zxx_is_und_with_a_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="app.scanner"):
        assert _lang3_to_lang2("zxx", "/m/b.mkv") == "UND"
    assert "zxx" in caplog.text and "/m/b.mkv" in caplog.text


@pytest.mark.parametrize("code", ["aac", "dts", "pcm", "hlg", "AAC"])
def test_an_unknown_code_that_spells_a_codec_is_und(code, caplog):
    with caplog.at_level(logging.WARNING, logger="app.scanner"):
        assert _lang3_to_lang2(code, "/m/c.mkv") == "UND"
    assert "/m/c.mkv" in caplog.text


# ── acceptance: the vocabulary ───────────────────────────────────────────────
def _other_labels() -> set[str]:
    """Every emitted label that is not a language label, spelled and lowercased as an *arr stores it."""
    return {tag_label(label).lower() for label in non_language_vocabulary(TagsConfig())}


def test_no_3_letter_fallback_collides_with_any_other_tag():
    three = {label for label in language_labels() if len(label) == 3 and label != "UND"}
    assert len(three) >= 300  # the table's no-2-letter languages, not a vacuous set
    assert {label.lower() for label in three} & _other_labels() == set()


def test_the_collision_check_can_fail():
    """Both directions: a fallback that spelled a codec or a rating would be caught, and
    the old first-two-letters rule is caught colliding (`snd` -> `SN` no, `sdh` -> `SD` yes)."""
    assert {"aac", "dts", "hlg", "pg-13", "sd", "dv"} <= _other_labels()
    assert {_old_fallback(c).lower() for c in ("sdh", "dvd")} <= _other_labels()


def test_the_2_letter_codes_that_are_also_other_tags_are_known():
    """ISO 639-1 is the standard and is not second-guessed: Sindhi is `SD`, Divehi `DV`,
    South Ndebele `NR`. Pinned so a new overlap is seen, and filed as roadmap B19."""
    two = {label.lower() for label in language_labels() if len(label) == 2}
    assert two & _other_labels() == {"sd", "dv", "nr"}
    assert {"SD", "DV"} <= scanner._NON_LANGUAGE_LABELS and "NR" in RATINGS


def test_every_language_tag_is_legal_in_radarr():
    assert all(re.fullmatch(r"[a-z0-9-]+", f"xt-sub-{tag_label(label)}".lower()) for label in language_labels())


# ── the call sites ───────────────────────────────────────────────────────────
def _stream(kind: str, lang: str | None, codec: str = "aac") -> dict:
    s: dict = {"codec_type": kind, "codec_name": codec}
    if lang is not None:
        s["tags"] = {"language": lang}
    return s


def test_probe_file_maps_once_and_warns_once_per_bad_track(tmp_path, monkeypatch, caplog):
    film = tmp_path / "The Film (2018).mkv"
    film.write_bytes(b"")
    streams = [
        {"codec_type": "video", "codec_name": "hevc", "width": 1920},
        _stream("audio", '"eng"'),
        _stream("audio", "per", "ac3"),
        _stream("audio", "fas"),
        _stream("audio", "zxx"),
        _stream("audio", "khm"),
        _stream("subtitle", "gre", "subrip"),
        _stream("subtitle", "e", "subrip"),
    ]
    out = json.dumps({"streams": streams})
    monkeypatch.setattr(
        scanner.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=out, stderr="")
    )
    with caplog.at_level(logging.WARNING, logger="app.scanner"):
        info = scanner.probe_file(film)
    assert [(t.lang, t.codec) for t in info.audio_tracks] == [("UND", "AAC"), ("FA", "DD"), ("KM", "AAC")]
    assert info.languages == ["FA", "KM"]
    assert info.raw_audio_langs == ['"eng"', "per", "fas", "zxx", "khm"]
    assert [t.lang for t in info.subtitle_tracks] == ["EL", "UND"]
    # one per bad track: two audio ("eng" quoted, zxx) and one subtitle -- not doubled
    assert len(caplog.records) == 3
    assert all(str(film) in r.getMessage() for r in caplog.records)


def test_external_subtitle_names(tmp_path, caplog):
    film = tmp_path / "Film.mkv"
    film.write_bytes(b"")
    for name in ("Film.en.srt", "Film.per.srt", "Film.10.srt", "Film.2.zh.srt", "Film.srt", "Film.dut.forced.srt"):
        (tmp_path / name).write_bytes(b"")
    with caplog.at_level(logging.WARNING, logger="app.scanner"):
        langs = sorted(t.lang for t in scanner._detect_external_subs(film))
    # `.10.` is a track number, not a malformed language: no language, and no warning
    assert langs == sorted(["EN", "FA", "UND", "ZH", "UND", "NL"])
    assert caplog.records == []


# ── the generator ────────────────────────────────────────────────────────────
MODULE = Path(gen.OUT)


def _rows_from_module(text: str) -> list[tuple[str, str, str]]:
    return [
        (m.group(1), m.group(2), m.group(3))
        for m in re.finditer(r'^    "([a-z]{3})": "([a-z]{0,2})",  # (.*)$', text, re.MULTILINE)
    ]


def test_the_committed_module_is_the_generators_output_unedited():
    text = MODULE.read_text(encoding="utf-8")
    rows = _rows_from_module(text)
    assert len(rows) == 506
    sha = gen._header_field(text, "sha256")
    retrieved = gen._header_field(text, "retrieved")
    assert gen.render(rows, sha, retrieved) == text
    gen.validate(rows)
    # and the check fails on an edit: one wrong code
    edited = [(c, "kh" if c == "khm" else a, e) for c, a, e in rows]
    assert gen.render(edited, sha, retrieved) != text
    with pytest.raises(ValueError, match="khm"):
        gen.validate(edited)


def test_the_generator_refuses_a_malformed_source():
    good = "eng||en|English|anglais\n"
    with pytest.raises(ValueError, match="fields"):
        gen.parse("eng|en|English\n")
    with pytest.raises(ValueError, match="3-letter"):
        gen.parse('"en||en|English|anglais\n')
    with pytest.raises(ValueError, match="2-letter"):
        gen.parse("eng||e|English|anglais\n")
    assert gen.parse("﻿" + good + "qaa-qtz|||Reserved for local use|réservée\n") == [("eng", "en", "English")]
    assert gen.parse("fre|fra|fr|French|français\n") == [("fre", "fr", "French"), ("fra", "fr", "French")]
    with pytest.raises(ValueError, match="twice"):
        gen.validate(gen.parse(good + good))
    with pytest.raises(ValueError):
        gen.validate(gen.parse(good))  # too small, and the known pairs are missing
