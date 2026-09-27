#!/usr/bin/env python3
"""Which live language tags does B7's ISO 639-2 table rename? (roadmap B7)

xenotag's index (``state.db``) stores each track's language LABEL, never the raw
code, and the old rule's labels are ambiguous: `jav` and `jpn` were both `JA`. So
the raw codes come from a copy of Jellyfin's own database, whose
``MediaStreamInfos.Language`` is ffprobe's tag verbatim (``"eng"`` quoted included),
and external subtitle languages from the file names beside each video, exactly as
the scanner reads them. Each index row is then labelled twice -- by the pre-B7 rule
(reproduced here) and by ``scanner._lang3_to_lang2()`` (imported, never retyped) --
and the language-derived tags compared.

    python3 scripts/measure_language_renames.py --self-test
    python3 scripts/measure_language_renames.py --state-db COPY/state.db \\
        --jellyfin-db COPY/jellyfin.db --media-map /media=/mnt/media \\
        --dual-tag dual-audio --multi-tag multi-audio [--out /tmp/renames.json]

Copy both databases with their ``-wal``/``-shm`` first; both are opened ``mode=ro``.
Nothing is written anywhere but ``--out``.

Self-check (live): the pre-B7 rule must reproduce the language labels the index
actually stores on at least 95% of the rows it can see, or the instrument is
reading the wrong thing and the script refuses to report. ``--self-test`` plants a
rename and an unchanged row in synthetic databases and requires both to be seen.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import scanner  # noqa: E402

_AUDIO, _SUBTITLE = 0, 2  # Jellyfin's MediaStreamType

# The map as it stood before B7 (app/scanner.py at 5867ba6), and its fallback.
PRE_B7 = {
    **{"eng": "EN", "jpn": "JA", "fre": "FR", "fra": "FR", "ger": "DE", "deu": "DE", "spa": "ES"},
    **{"ita": "IT", "por": "PT", "rus": "RU", "chi": "ZH", "zho": "ZH", "kor": "KO", "ara": "AR"},
    **{"hin": "HI", "pol": "PL", "nld": "NL", "swe": "SV", "nor": "NO", "dan": "DA", "fin": "FI"},
    **{"tur": "TR", "heb": "HE", "hun": "HU", "ces": "CS", "cze": "CS", "ron": "RO", "rum": "RO"},
    **{"tha": "TH", "vie": "VI", "ind": "ID", "msa": "MS", "ukr": "UK", "hrv": "HR", "bul": "BG"},
    **{"cat": "CA", "slk": "SK", "slo": "SK", "slv": "SL", "lit": "LT", "lav": "LV", "est": "ET"},
}


def old_label(code: str) -> str:
    code = (code or "").lower().strip()
    if code in ("", "und", "unknown"):
        return "UND"
    return PRE_B7.get(code, code[:2].upper() if len(code) >= 2 else code.upper())


def new_label(code: str) -> str:
    return scanner._lang3_to_lang2(code or "")


def old_external(video: Path) -> list[str]:
    """``_detect_external_subs()`` before B7: the first 2-3 character part, whatever it is."""
    labels = []
    for f in _sub_files(video):
        label = "UND"
        for part in f.stem[len(video.stem) :].lstrip(".").split("."):
            part = part.lower().strip()
            if part and len(part) in (2, 3):
                label = old_label(part) if len(part) == 3 else part.upper()
                break
        labels.append(label)
    return labels


def _sub_files(video: Path) -> list[Path]:
    try:
        return [
            f
            for f in video.parent.iterdir()
            if f.suffix.lower() in scanner._EXTERNAL_SUB_EXTS and f.stem.startswith(video.stem)
        ]
    except OSError:
        return []


def language_tags(audio: list[str], subs: list[str], dual: str, multi: str) -> set[str]:
    """The language-derived tag labels ``tagger`` builds: every audio language (UND
    included), dual/multi by the count of known ones, and ``sub-XX`` for known subs."""
    tags = set(audio)
    known = {lang for lang in audio if lang != "UND"}
    if len(known) == 2:
        tags.add(dual)
    elif len(known) >= 3:
        tags.add(multi)
    return tags | {f"sub-{lang}" for lang in subs if lang != "UND"}


def measure(state_db: Path, jellyfin_db: Path, media_map: tuple[str, str], dual: str, multi: str) -> dict:
    logging.getLogger("app.scanner").setLevel(logging.ERROR)
    st = sqlite3.connect(f"file:{state_db}?mode=ro", uri=True)
    jf = sqlite3.connect(f"file:{jellyfin_db}?mode=ro", uri=True)
    by_path = dict(jf.execute("select Path, Id from BaseItems where Path is not null"))
    streams: dict[str, list[tuple[int, str]]] = {}
    for item, kind, lang in jf.execute(
        "select ItemId, StreamType, Language from MediaStreamInfos"
        " where StreamType in (?, ?) and IsExternal = 0 order by ItemId, StreamIndex",
        (_AUDIO, _SUBTITLE),
    ):
        streams.setdefault(item, []).append((kind, lang or ""))

    out: dict = {"rows": 0, "no_jellyfin_item": 0, "no_file": 0, "self_check_agree": 0, "self_check_seen": 0}
    removed: Counter = Counter()
    added: Counter = Counter()
    pairs: Counter = Counter()
    raw_codes: Counter = Counter()
    changed_rows = []
    src, dst = media_map
    for item_id, path, audio_json, subs_json in st.execute(
        "select item_id, file_path, audio_tracks, subtitle_tracks from media_state"
    ):
        out["rows"] += 1
        jf_id = by_path.get(path)
        if jf_id is None:
            out["no_jellyfin_item"] += 1
            continue
        video = Path(dst + path[len(src) :]) if path.startswith(src) else Path(path)
        if not video.exists():
            out["no_file"] += 1
            continue
        s = streams.get(jf_id, [])
        a_raw = [lang for kind, lang in s if kind == _AUDIO]
        s_raw = [lang for kind, lang in s if kind == _SUBTITLE]
        old_a = [old_label(c) for c in a_raw]
        new_a = [new_label(c) for c in a_raw]
        old_s = [old_label(c) for c in s_raw] + old_external(video)
        new_s = [new_label(c) for c in s_raw] + [t.lang for t in scanner._detect_external_subs(video)]

        # Self-check: the old rule must reproduce what the index stores.
        stored_a = {t["lang"] for t in json.loads(audio_json or "[]")}
        stored_s = {t["lang"] for t in json.loads(subs_json or "[]")}
        out["self_check_seen"] += 1
        if stored_a == set(old_a) and stored_s == set(old_s):
            out["self_check_agree"] += 1

        before = language_tags(old_a, old_s, dual, multi)
        after = language_tags(new_a, new_s, dual, multi)
        if before != after:
            gone, new = sorted(before - after), sorted(after - before)
            removed.update(gone)
            added.update(new)
            pairs[(" ".join(gone), " ".join(new))] += 1
            raw = a_raw + s_raw + [f"(file name) {lang}" for lang in old_s[len(s_raw) :]]
            for c_old, c_new, c in zip(old_a + old_s, new_a + new_s, raw, strict=True):
                if c_old != c_new:
                    raw_codes[(c, c_old, c_new)] += 1
            changed_rows.append({"item": item_id, "path": path, "removed": gone, "added": new})
    out["changed_rows"] = len(changed_rows)
    out["removed"] = dict(removed.most_common())
    out["added"] = dict(added.most_common())
    out["renames"] = [{"removed": g, "added": n, "rows": k} for (g, n), k in pairs.most_common()]
    out["codes"] = [{"raw": c, "old": o, "new": n, "tracks": k} for (c, o, n), k in raw_codes.most_common()]
    out["items"] = changed_rows
    return out


def _check(out: dict) -> None:
    seen, agree = out["self_check_seen"], out["self_check_agree"]
    if not seen or agree / seen < 0.95:
        sys.exit(f"REFUSING: the pre-B7 rule reproduces the index on {agree}/{seen} rows (< 95%)")


def self_test() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        media = root / "mnt"
        (media / "a").mkdir(parents=True)
        (media / "b").mkdir()
        for name in ("a/A.mkv", "a/A.10.srt", "b/B.mkv", "b/B.en.srt"):
            (media / name).write_bytes(b"")
        st = sqlite3.connect(root / "state.db")
        st.execute("create table media_state (item_id, file_path, audio_tracks, subtitle_tracks)")
        rows = [
            # A: khm (renamed KH -> KM) + eng = dual either way; external `.10.` drops `sub-10`
            ("jellyfin:a", "/media/a/A.mkv", [{"lang": "EN"}, {"lang": "KH"}], [{"lang": "10"}]),
            # B: unchanged
            ("jellyfin:b", "/media/b/B.mkv", [{"lang": "JA"}], [{"lang": "EN"}]),
        ]
        st.executemany(
            "insert into media_state values (?, ?, ?, ?)", [(i, p, json.dumps(a), json.dumps(s)) for i, p, a, s in rows]
        )
        st.commit()
        jf = sqlite3.connect(root / "jellyfin.db")
        jf.execute("create table BaseItems (Id, Path)")
        jf.execute("create table MediaStreamInfos (ItemId, StreamIndex, StreamType, Language, IsExternal)")
        jf.executemany("insert into BaseItems values (?, ?)", [("A", "/media/a/A.mkv"), ("B", "/media/b/B.mkv")])
        jf.executemany(
            "insert into MediaStreamInfos values (?, ?, ?, ?, ?)",
            [("A", 1, _AUDIO, "eng", 0), ("A", 2, _AUDIO, "khm", 0), ("B", 1, _AUDIO, "jpn", 0)],
        )
        jf.commit()
        out = measure(root / "state.db", root / "jellyfin.db", ("/media", str(media)), "dual", "multi")
        ok = (
            out["self_check_agree"] == 2
            and out["changed_rows"] == 1
            and out["renames"] == [{"removed": "KH sub-10", "added": "KM", "rows": 1}]
            and out["items"][0]["item"] == "jellyfin:a"
        )
        # ...and the self-check must refuse an index the old rule does not reproduce.
        st.execute("update media_state set audio_tracks = '[{\"lang\": \"FR\"}]' where item_id = 'jellyfin:b'")
        st.execute("update media_state set audio_tracks = '[{\"lang\": \"FR\"}]' where item_id = 'jellyfin:a'")
        st.commit()
        bad = measure(root / "state.db", root / "jellyfin.db", ("/media", str(media)), "dual", "multi")
        try:
            _check(bad)
            refused = False
        except SystemExit:
            refused = True
    print(f"self-test: planted rename seen={ok}, unreproducible index refused={refused}")
    return 0 if ok and refused else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--state-db", type=Path)
    ap.add_argument("--jellyfin-db", type=Path)
    ap.add_argument("--media-map", default="/media=/mnt/media", help="container prefix=host prefix")
    ap.add_argument("--dual-tag", default="dual-audio")
    ap.add_argument("--multi-tag", default="multi-audio")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()
    if not (args.state_db and args.jellyfin_db):
        ap.error("--state-db and --jellyfin-db are required")
    src, _, dst = args.media_map.partition("=")
    out = measure(args.state_db, args.jellyfin_db, (src, dst), args.dual_tag, args.multi_tag)
    _check(out)
    summary = {k: v for k, v in out.items() if k != "items"}
    print(json.dumps(summary, indent=1))
    if args.out:
        args.out.write_text(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
