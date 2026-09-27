from __future__ import annotations

import logging

from .config import TagsConfig
from .scanner import AudioTrack, SubTrack

log = logging.getLogger(__name__)

# Tag spelling (roadmap B9). Tags are built from the scanner's display names
# ("H.264", "DD+ Atmos"), which are also the badge text -- but Radarr refuses any
# label outside [a-z0-9-]. So a tag drops ".", spells "+" as "plus" and turns a
# space into "-": `xt-H264`, `xt-DDplus-Atmos`, `xt-HDR10plus`. "+" is spelled
# out, never stripped, or HDR10+ and DD+ would collide with HDR10 and DD. Case is
# left alone: every *arr lowercases a label itself. Badge text is not touched.
_TAG_SPELLING = str.maketrans({".": "", "+": "plus", " ": "-"})

# Bump when the spelling above renames a tag that already exists somewhere.
# pipeline._tag_config_hash() folds it in, so the upgrade's first scan is a full
# re-tag, which replaces the old spellings on Jellyfin and on every *arr.
# The language labels count too (scanner._lang3_to_lang2), since they are tags.
# 1: display names verbatim (`xt-H.264`); 2: the spelling above; 3: the complete
# ISO 639-2 table (B7: `xt-KH` -> `xt-KM`, `xt-PE` -> `xt-FA`, `xt-ZX` -> `xt-UND`).
TAG_VOCABULARY = 3


def tag_label(name: str) -> str:
    """A display name as a tag label: `H.264` -> `H264`, `DD+ Atmos` -> `DDplus-Atmos`."""
    return name.translate(_TAG_SPELLING)


def _video_tags(prefix: str, resolution: str, video_codec: str | None, hdr_type: str | None) -> list[str]:
    tags = []
    if resolution and resolution != "unknown":
        tags.append(f"{prefix}{resolution}")
    if video_codec:
        tags.append(f"{prefix}{video_codec}")
    if hdr_type:
        tags.append(f"{prefix}{hdr_type}")
    return tags


def _audio_tags(
    prefix: str,
    audio_tracks: list[AudioTrack],
    dual_tag: str,
    multi_tag: str,
) -> list[str]:
    tags = []
    for t in audio_tracks:
        if t.lang and t.lang != "UND":
            tags.append(f"{prefix}{t.lang}")
        elif not t.lang or t.lang == "UND":
            tags.append(f"{prefix}UND")
        tags.append(f"{prefix}{t.codec}")
    langs = [t.lang for t in audio_tracks if t.lang and t.lang != "UND"]
    if len(langs) == 2:
        tags.append(f"{prefix}{dual_tag}")
    elif len(langs) >= 3:
        tags.append(f"{prefix}{multi_tag}")
    return list(dict.fromkeys(tags))  # dedup preserving order


def _subtitle_tags(prefix: str, subtitle_tracks: list[SubTrack]) -> list[str]:
    return list(dict.fromkeys(f"{prefix}sub-{t.lang}" for t in subtitle_tracks if t.lang and t.lang != "UND"))


def _rating_tag(prefix: str, content_rating: str | None) -> list[str]:
    return [f"{prefix}{content_rating}"] if content_rating else []


def build_tags(
    resolution: str,
    video_codec: str | None,
    hdr_type: str | None,
    audio_tracks: list[AudioTrack],
    subtitle_tracks: list[SubTrack],
    content_rating: str | None,
    cfg: TagsConfig,
    destination: str,  # "jellyfin" | "sonarr" | "radarr" | "poster"
) -> list[str]:
    p = cfg.managed_prefix
    d = cfg.destinations
    tags: list[str] = []
    if destination in d.video:
        tags += _video_tags(p, resolution, video_codec, hdr_type)
    if destination in d.audio:
        tags += _audio_tags(p, audio_tracks, cfg.dual_audio_tag, cfg.multi_audio_tag)
    if destination in d.subtitles:
        tags += _subtitle_tags(p, subtitle_tracks)
    if destination in d.rating:
        tags += _rating_tag(p, content_rating)
    # Every tag above is prefix + label; the prefix is config and kept verbatim.
    return [p + tag_label(t[len(p) :]) for t in tags]
