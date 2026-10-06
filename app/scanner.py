from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .iso639 import ISO639_2_TO_1

log = logging.getLogger(__name__)

# ISO 639-2 (3-letter) -> ISO 639-1 (2-letter uppercase), from the generated
# table (roadmap B7): every code with a 2-letter form, bibliographic and
# terminologic alike (`fre`/`fra` -> `FR`, `per`/`fas` -> `FA`).
LANG_MAP: dict[str, str] = {code: two.upper() for code, two in ISO639_2_TO_1.items() if two}

# Resolution classes, highest first: (width, height, label). A stream is in a class
# when its width OR its height reaches the class's less RESOLUTION_TOLERANCE_PCT, and
# the highest class it reaches wins (roadmap B13, operator decision 2026-10-05), so a
# 3836x1604 scope crop and a 3584x2160 open matte are 4K and a 1440x1080 or 1904x1072
# frame is 1080p. Height counts for the HD classes only: 480p has no height, so it is
# width alone (854 less 5% = 811.3), and 720x480, 640x480 and 720x576 DVD frames stay SD.
RESOLUTION_CLASSES: list[tuple[int, int | None, str]] = [
    (3840, 2160, "4K"),
    (1920, 1080, "1080p"),
    (1280, 720, "720p"),
    (854, None, "480p"),
]
RESOLUTION_TOLERANCE_PCT = 5

# Bump when the rule above moves an existing file to another class. The class is
# stored in media_state, not the frame size, so an unchanged file keeps its old class
# until it is probed again: pipeline._tag_config_hash() folds this in, so the
# upgrade's first scan is a full re-tag that re-probes everything.
# 1: width alone, exact (`>= 3840` -> 4K); 2: width or height, 5%, HD classes only (B13).
RESOLUTION_RULE_VERSION = 2

_VIDEO_CODEC_MAP = {
    "h264": "H.264",
    "avc": "H.264",
    "hevc": "H.265",
    "h265": "H.265",
    "av1": "AV1",
    "vp9": "VP9",
    "mpeg2video": "MPEG-2",
    "mpeg4": "MPEG-4",
    "vc1": "VC-1",
}

_AUDIO_CODEC_MAP = {
    "truehd": "TrueHD",
    "eac3": "DD+",
    "ac3": "DD",
    "dts": "DTS",
    "aac": "AAC",
    "mp3": "MP3",
    "flac": "FLAC",
    "opus": "Opus",
    "vorbis": "Vorbis",
    "pcm_s16le": "PCM",
    "pcm_s24le": "PCM",
    "pcm_s32le": "PCM",
}

_AUDIO_QUALITY_RANK: dict[str, int] = {
    label: idx
    for idx, label in enumerate(
        [
            "TrueHD Atmos",
            "TrueHD",
            "DTS-X",
            "DTS-HD",
            "DD+ Atmos",
            "DTS",
            "DD+",
            "DD",
            "FLAC",
            "Opus",
            "AAC",
            "Vorbis",
            "MP3",
            "PCM",
        ]
    )
}

_SUB_FORMAT_MAP = {
    "hdmv_pgs_subtitle": "PGS",
    "pgssub": "PGS",
    "dvd_subtitle": "VOB",
    "dvdsub": "VOB",
    "subrip": "SRT",
    "srt": "SRT",
    "ass": "SSA",
    "ssa": "SSA",
    "webvtt": "VTT",
    "vtt": "VTT",
    "mov_text": "TX3G",
    "microdvd": "SUB",
}

_EXTERNAL_SUB_EXTS = {".srt", ".ass", ".ssa", ".sub", ".vtt", ".sup"}

# ffprobe's `field_order` values that mean interlaced (roadmap U5): top or bottom
# field first, coded in the same or the opposite order. `progressive` is not, and
# `unknown` -- or no field_order at all -- says nothing, so it is never tagged.
INTERLACED_FIELD_ORDERS = frozenset({"tt", "bb", "tb", "bt"})


@dataclass
class AudioTrack:
    lang: str  # "EN", "JA", "UND"
    codec: str  # "DTS-HD", "TrueHD Atmos", "AAC", etc.


@dataclass
class SubTrack:
    lang: str  # "EN", "FR", "UND"
    format: str  # "PGS", "SRT", "ASS", "VOB", "VTT"
    embedded: bool  # True = stream in container; False = external sidecar


@dataclass
class MediaInfo:
    resolution: str
    languages: list[str]  # ordered unique ISO 639-1 codes (backward compat)
    raw_audio_langs: list[str]  # raw 3-letter codes from ffprobe (backward compat)
    video_codec: str | None  # "H.265", "H.264", "AV1", etc.
    hdr_type: str | None  # "HDR10", "HLG", "HDR10+", "DV", or None
    audio_tracks: list[AudioTrack]  # deduped: best codec per language
    subtitle_tracks: list[SubTrack]  # all embedded + external subs
    # ffprobe's, lowercased: "progressive", "tt", "bb", "tb", "bt" or "unknown" (also
    # when the stream has none); None only when there is no video stream (U5).
    field_order: str | None = None

    @property
    def interlaced(self) -> bool:
        return is_interlaced(self.field_order)


def is_interlaced(field_order: str | None) -> bool:
    """True only for a field order that says interlaced; unknown or missing is not (U5)."""
    return (field_order or "").lower() in INTERLACED_FIELD_ORDERS


def probe_file(path: str | Path) -> MediaInfo | None:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-probesize",
        "10M",
        "-analyzeduration",
        "5000000",
        "-print_format",
        "json",
        "-show_streams",
        str(path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except FileNotFoundError:
        log.error("ffprobe not found — is ffmpeg installed?")
        return None
    except subprocess.TimeoutExpired:
        log.warning("ffprobe timed out on %s", path)
        return None

    if result.returncode != 0:
        stderr = result.stderr.strip()[:500]
        log.warning("ffprobe failed on %s:\n%s", path, stderr or "(no stderr output)")
        return None

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        log.warning("ffprobe returned invalid JSON for %s", path)
        return None

    streams = data.get("streams", [])
    resolution = _detect_resolution(streams)
    raw = _raw_audio_langs(streams)
    video_codec = _detect_video_codec(streams)
    hdr_type = _detect_hdr(streams)
    audio_tracks = _detect_audio_tracks(streams, path)
    # The audio tracks keep each language's first occurrence in stream order, so
    # this is the ordered unique list -- mapped once, by the same rule as the tags.
    languages = [t.lang for t in audio_tracks if t.lang != "UND"]
    subtitle_tracks = _detect_subtitle_tracks(streams, path)
    subtitle_tracks += _detect_external_subs(Path(path))

    return MediaInfo(
        resolution=resolution,
        languages=languages,
        raw_audio_langs=raw,
        video_codec=video_codec,
        hdr_type=hdr_type,
        audio_tracks=audio_tracks,
        subtitle_tracks=subtitle_tracks,
        field_order=_detect_field_order(streams),
    )


def _reaches(size: int, class_size: int) -> bool:
    """``size`` is at least ``class_size`` less the tolerance (integer arithmetic, no rounding)."""
    return size * 100 >= class_size * (100 - RESOLUTION_TOLERANCE_PCT)


def _detect_resolution(streams: list[dict]) -> str:
    """The first video stream's class (see RESOLUTION_CLASSES); ``unknown`` with no video stream."""
    for s in streams:
        if s.get("codec_type") == "video":
            width = s.get("width") or 0
            height = s.get("height") or 0
            for class_width, class_height, label in RESOLUTION_CLASSES:
                if _reaches(width, class_width) or (class_height is not None and _reaches(height, class_height)):
                    return label
            return "SD"
    return "unknown"


def _raw_audio_langs(streams: list[dict]) -> list[str]:
    return [
        ((s.get("tags") or {}).get("language") or "").lower().strip() for s in streams if s.get("codec_type") == "audio"
    ]


def _detect_video_codec(streams: list[dict]) -> str | None:
    for s in streams:
        if s.get("codec_type") == "video":
            name = (s.get("codec_name") or "").lower()
            return _VIDEO_CODEC_MAP.get(name)
    return None


def _detect_field_order(streams: list[dict]) -> str | None:
    # The first video stream, as for the codec. Stored as ffprobe spelled it, so the
    # index can tell "probed, no answer" (unknown) from "not probed since U5" (NULL).
    for s in streams:
        if s.get("codec_type") == "video":
            return str(s.get("field_order") or "").strip().lower() or "unknown"
    return None


def _detect_hdr(streams: list[dict]) -> str | None:
    for s in streams:
        if s.get("codec_type") != "video":
            continue
        side_data = s.get("side_data_list") or []
        side_types = [str(d.get("type", "")).lower() for d in side_data]

        # Priority: DV > HDR10+ > HDR10 > HLG
        for t in side_types:
            if "dovi" in t or "dolby vision" in t:
                return "DV"
        for t in side_types:
            if "smpte2094-40" in t or "hdr dynamic" in t:
                return "HDR10+"

        color_space = (s.get("color_space") or "").lower()
        color_transfer = (s.get("color_transfer") or "").lower()
        is_bt2020 = "bt2020" in color_space

        if is_bt2020 and color_transfer == "smpte2084":
            return "HDR10"
        if is_bt2020 and color_transfer == "arib-std-b67":
            return "HLG"
    return None


def _normalize_audio_codec(s: dict) -> str:
    name = (s.get("codec_name") or "").lower()
    profile = (s.get("profile") or "").lower()
    channel_layout = (s.get("channel_layout") or "").lower()
    side_data = s.get("side_data_list") or []
    side_types = [str(d.get("type", "")).lower() for d in side_data]

    if name == "truehd":
        if any("atmos" in t for t in side_types):
            return "TrueHD Atmos"
        return "TrueHD"

    if name == "eac3":
        # Atmos over EAC3: bitstream_id 16 + 7.x channel layout
        bitstream_id = s.get("bitstream_id", 0)
        if bitstream_id == 16 and "7" in channel_layout:
            return "DD+ Atmos"
        return "DD+"

    if name == "dts":
        for t in side_types:
            if "dts:x" in t:
                return "DTS-X"
        if profile in ("dts-hd ma", "dts_ma", "dts_hra", "dts-hd hra"):
            return "DTS-HD"
        return "DTS"

    return _AUDIO_CODEC_MAP.get(name, name.upper() if name else "?")


_LANG_CODE = re.compile(r"[a-z]{2,3}")  # the shape of an ISO 639-1 or 639-2 code, ASCII only

# Every label a language tag shares the `xt-` namespace with that the scanner itself
# emits. A 3-letter code with no 2-letter form is kept as itself, and one outside
# ISO 639-2 (`aac`, `dts`) must not turn into one of these; the table's own codes
# are checked against the same set by a test (none collide). The 2-letter codes
# are the standard's and are not second-guessed here, though two of them are also
# scanner labels -- Sindhi `SD`, Divehi `DV` (roadmap B19).
_NON_LANGUAGE_LABELS = frozenset(
    label.upper()
    for label in (
        *(label for _, _, label in RESOLUTION_CLASSES),
        "SD",
        *_VIDEO_CODEC_MAP.values(),
        *("DV", "HDR10+", "HDR10", "HLG"),
        *_AUDIO_CODEC_MAP.values(),
        *_AUDIO_QUALITY_RANK,
    )
)


def _lang3_to_lang2(lang3: str, source: str | Path | None = None) -> str:
    """A stream's language tag as the label xenotag tags it with (roadmap B7).

    ISO 639-1 uppercase where the language has one (`per`/`fas` -> `FA`, `khm` ->
    `KM`); otherwise the 3-letter code uppercase (`egy` -> `EGY`); a 2-letter code
    as itself. `und`/`unknown`/nothing is `UND`. So is `zxx` ("no linguistic
    content") and anything that is not 2-3 ASCII letters (`"eng"`), with a WARNING
    naming ``source`` -- the file -- because those are metadata to fix, not a
    language to guess.
    """
    code = lang3.lower().strip()
    if code in ("", "und", "unknown"):
        return "UND"
    if not _LANG_CODE.fullmatch(code):
        log.warning("Language tag %r is not an ISO 639 code; tagged UND: %s", lang3, source)
        return "UND"
    if code == "zxx":
        log.warning("Language tag 'zxx' (no linguistic content); tagged UND: %s", source)
        return "UND"
    if len(code) == 2:
        return code.upper()
    if code in LANG_MAP:
        return LANG_MAP[code]
    label = code.upper()
    if label in _NON_LANGUAGE_LABELS:
        log.warning("Language tag %r would read as the %s tag; tagged UND: %s", lang3, label, source)
        return "UND"
    return label


def _detect_audio_tracks(streams: list[dict], source: str | Path | None = None) -> list[AudioTrack]:
    # Collect all audio tracks
    all_tracks: list[tuple[str, str]] = []  # (lang2, codec)
    for s in streams:
        if s.get("codec_type") != "audio":
            continue
        tags = s.get("tags") or {}
        lang2 = _lang3_to_lang2(tags.get("language") or "", source)
        codec = _normalize_audio_codec(s)
        all_tracks.append((lang2, codec))

    # Dedup: for each language keep the highest-ranked codec
    best: dict[str, str] = {}
    for lang2, codec in all_tracks:
        if lang2 not in best:
            best[lang2] = codec
        else:
            current_rank = _AUDIO_QUALITY_RANK.get(best[lang2], 999)
            new_rank = _AUDIO_QUALITY_RANK.get(codec, 999)
            if new_rank < current_rank:
                best[lang2] = codec

    # Preserve original track order (first occurrence of each lang)
    seen: dict[str, None] = {}
    ordered: list[AudioTrack] = []
    for lang2, _ in all_tracks:
        if lang2 not in seen:
            seen[lang2] = None
            ordered.append(AudioTrack(lang=lang2, codec=best[lang2]))
    return ordered


def _detect_subtitle_tracks(streams: list[dict], source: str | Path | None = None) -> list[SubTrack]:
    tracks: list[SubTrack] = []
    for s in streams:
        if s.get("codec_type") != "subtitle":
            continue
        tags = s.get("tags") or {}
        lang2 = _lang3_to_lang2(tags.get("language") or "", source)
        codec_name = (s.get("codec_name") or "").lower()
        fmt = _SUB_FORMAT_MAP.get(codec_name, codec_name.upper() if codec_name else "?")
        tracks.append(SubTrack(lang=lang2, format=fmt, embedded=True))
    return tracks


def _detect_external_subs(video_path: Path) -> list[SubTrack]:
    tracks: list[SubTrack] = []
    try:
        parent = video_path.parent
        stem = video_path.stem
        for f in parent.iterdir():
            if f.suffix.lower() not in _EXTERNAL_SUB_EXTS:
                continue
            if not f.stem.startswith(stem):
                continue
            fmt = _SUB_FORMAT_MAP.get(f.suffix.lower().lstrip("."), f.suffix.upper().lstrip("."))
            # Infer language from filename suffix: Movie.en.srt → "en". Only a part
            # shaped like a language code counts: `Movie.10.srt` is a track number.
            remainder = f.stem[len(stem) :].lstrip(".")
            parts = remainder.split(".")
            lang2 = "UND"
            for part in parts:
                part = part.lower().strip()
                if _LANG_CODE.fullmatch(part):
                    lang2 = _lang3_to_lang2(part, video_path)
                    break
            tracks.append(SubTrack(lang=lang2, format=fmt, embedded=False))
    except Exception as exc:
        log.debug("External sub scan failed for %s: %s", video_path, exc)
    return tracks
