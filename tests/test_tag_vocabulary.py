"""Tag spelling (roadmap B9): one spelling legal in every destination.

Radarr 6.3 refuses any tag label outside ``[a-z0-9-]``, and xenotag's tags were
the scanner's display names verbatim (``xt-H.264``, ``xt-DD+``), so 4,591 label
applications never reached production Radarr. A tag now spells its display name
differently from the badge: ``.`` dropped, ``+`` -> ``plus``, space -> ``-``.

The acceptance test runs over the FULL vocabulary the tagger can emit, and the
same checks are shown to fail on the obvious fix -- strip the illegal characters
-- which collides HDR10+ with HDR10 and DD+ with DD.
"""

from __future__ import annotations

import json
import re

import httpx

from app import pipeline, scanner
from app.clients.jellyfin import JellyfinClient
from app.config import AppConfig, TagsConfig
from app.iso639 import ISO639_2_TO_1
from app.scanner import AudioTrack, MediaInfo, SubTrack
from app.tagger import build_tags, tag_label

LEGAL = re.compile(r"[a-z0-9-]+")  # Radarr 6.3.0.10514's rule, applied after its own lowercasing

# Content ratings come from Jellyfin, so they are open-ended: this is every rating
# on production's index that was not already legal (2026-09-27), plus common ones.
RATINGS = (
    *("G", "PG", "PG-13", "R", "NC-17", "NR", "Unrated", "TV-Y7-FV", "TV-14", "TV-MA"),
    *("Not Rated", "MA15+", "MA 15+", "R18+", "0+", "6+", "12+", "14+", "15+", "16+", "18+"),
)

# The six codec labels the operator's decision names, and what every *arr stores.
DECIDED = {
    "H.264": "xt-h264",
    "H.265": "xt-h265",
    "DD+": "xt-ddplus",
    "DD+ Atmos": "xt-ddplus-atmos",
    "HDR10+": "xt-hdr10plus",
    "TrueHD Atmos": "xt-truehd-atmos",
}


def _hdr_types() -> set[str]:
    """Every label ``_detect_hdr()`` returns, one stream per branch."""
    streams = (
        {"side_data_list": [{"type": "DOVI configuration record"}]},
        {"side_data_list": [{"type": "HDR Dynamic Metadata SMPTE2094-40 (HDR10+)"}]},
        {"color_space": "bt2020nc", "color_transfer": "smpte2084"},
        {"color_space": "bt2020nc", "color_transfer": "arib-std-b67"},
    )
    return {scanner._detect_hdr([{"codec_type": "video", **s}]) for s in streams}


def _audio_codecs() -> set[str]:
    """The codec tables plus each special branch of ``_normalize_audio_codec()``."""
    streams = (
        {"codec_name": "truehd", "side_data_list": [{"type": "Dolby TrueHD + Dolby Atmos"}]},
        {"codec_name": "eac3", "bitstream_id": 16, "channel_layout": "7.1"},
        {"codec_name": "dts", "side_data_list": [{"type": "DTS:X"}]},
        {"codec_name": "dts", "profile": "DTS-HD MA"},
    )
    driven = {scanner._normalize_audio_codec(s) for s in streams}
    return driven | set(scanner._AUDIO_CODEC_MAP.values()) | set(scanner._AUDIO_QUALITY_RANK)


def language_labels() -> set[str]:
    """Every language label the scanner can emit from its ISO 639-2 table (B7)."""
    return {scanner._lang3_to_lang2(code) for code in ISO639_2_TO_1}


def non_language_vocabulary(tags: TagsConfig) -> set[str]:
    """Every label that is not a language: built on its own, so a language label that
    spells the same string as one of these is still visible (B7)."""
    return (
        {label for _, label in scanner.RESOLUTION_THRESHOLDS}
        | {"SD"}
        | set(scanner._VIDEO_CODEC_MAP.values())
        | _hdr_types()
        | _audio_codecs()
        | {tags.dual_audio_tag, tags.multi_audio_tag}
        | set(RATINGS)
    )


def emitted_vocabulary(tags: TagsConfig) -> set[str]:
    """Every label (the part after the prefix) the tagger can emit from its own tables."""
    langs = language_labels()
    return non_language_vocabulary(tags) | langs | {f"sub-{lang}" for lang in langs - {"UND"}}


def collisions(spell, vocabulary) -> dict[str, set[str]]:
    """Labels that stop being distinct once spelled -- and lowercased, as every *arr stores them."""
    groups: dict[str, set[str]] = {}
    for label in vocabulary:
        groups.setdefault(spell(label).lower(), set()).add(label)
    return {out: labels for out, labels in groups.items() if len(labels) > 1}


def illegal(spell, vocabulary, prefix: str = "xt-") -> list[str]:
    return sorted(label for label in vocabulary if not LEGAL.fullmatch((prefix + spell(label)).lower()))


def naive_strip(label: str) -> str:
    """The obvious fix the B9 filing warned about: delete whatever Radarr refuses."""
    return re.sub(r"[^A-Za-z0-9-]", "", label)


VOCAB = emitted_vocabulary(TagsConfig())


# ── acceptance ──────────────────────────────────────────────────────────────
def test_the_vocabulary_is_not_vacuous():
    """The checks below are only as good as the list they run over."""
    assert set(DECIDED) <= VOCAB
    assert {"HDR10", "DD", "DV", "HLG", "4K", "SD", "DTS-X", "UND", "sub-EN", "dual-audio"} <= VOCAB
    assert _hdr_types() == {"DV", "HDR10+", "HDR10", "HLG"}
    assert len(VOCAB) > 100


def test_every_emitted_label_is_legal_in_radarr():
    assert illegal(tag_label, VOCAB) == []


def test_no_two_emitted_labels_share_a_spelling():
    assert collisions(tag_label, VOCAB) == {}


def test_the_checks_fail_on_the_naive_strip_map_and_on_the_old_spelling():
    """Both directions: each check must be able to fail, or its pass proves nothing."""
    found = collisions(naive_strip, VOCAB)
    assert found["hdr10"] == {"HDR10", "HDR10+"}
    assert found["dd"] == {"DD", "DD+"}
    assert illegal(naive_strip, VOCAB) == []  # legal, and still wrong -- hence the collision check
    old = illegal(lambda label: label, VOCAB)  # v1: display names verbatim
    assert {"H.264", "H.265", "DD+", "DD+ Atmos", "HDR10+", "TrueHD Atmos", "Not Rated", "MA15+"} <= set(old)


def test_the_decided_spellings():
    assert {name: ("xt-" + tag_label(name)).lower() for name in DECIDED} == DECIDED


def test_a_configured_audio_tag_is_spelled_too():
    tags = TagsConfig(dual_audio_tag="dual audio", multi_audio_tag="multi+")
    assert illegal(tag_label, emitted_vocabulary(tags)) == []
    assert collisions(tag_label, emitted_vocabulary(tags)) == {}


# ── the code path ───────────────────────────────────────────────────────────
def _everything_everywhere() -> TagsConfig:
    every = ["poster", "jellyfin", "sonarr", "radarr"]
    return TagsConfig.model_validate(
        {"destinations": {"video": every, "audio": every, "subtitles": every, "rating": every}}
    )


def _info() -> MediaInfo:
    return MediaInfo(
        resolution="4K",
        languages=["EN", "JA"],
        raw_audio_langs=["eng", "jpn"],
        video_codec="H.265",
        hdr_type="HDR10+",
        audio_tracks=[AudioTrack("EN", "TrueHD Atmos"), AudioTrack("JA", "DD+")],
        subtitle_tracks=[SubTrack("EN", "PGS", True)],
    )


def test_build_tags_spells_every_category_and_keeps_case():
    info = _info()
    tags = build_tags(
        info.resolution,
        info.video_codec,
        info.hdr_type,
        info.audio_tracks,
        info.subtitle_tracks,
        "MA 15+",
        _everything_everywhere(),
        destination="jellyfin",
    )
    assert tags == [
        "xt-4K",
        "xt-H265",
        "xt-HDR10plus",
        "xt-EN",
        "xt-TrueHD-Atmos",
        "xt-JA",
        "xt-DDplus",
        "xt-dual-audio",
        "xt-sub-EN",
        "xt-MA-15plus",
    ]
    assert all(LEGAL.fullmatch(t.lower()) for t in tags)


def test_build_tags_keeps_a_configured_prefix_verbatim():
    tags = TagsConfig(managed_prefix="x.t+ ")
    assert build_tags("1080p", "H.264", None, [], [], None, tags, destination="radarr") == ["x.t+ 1080p", "x.t+ H264"]


def test_badge_text_keeps_the_display_names():
    """Tags only: the poster still says H.265, HDR10+, TrueHD Atmos, DD+ and MA 15+."""
    c = AppConfig.model_validate({"tags": _everything_everywhere().model_dump()})
    groups, rating = pipeline._make_badge_groups(_info(), "MA 15+", c)
    assert [g.labels for g in groups] == [["4K", "H.265", "HDR10+"], ["TrueHD Atmos EN", "DD+ JA"], ["PGS EN"]]
    assert rating.labels == ["Rated MA 15+"]


def test_a_full_re_tag_replaces_the_old_spelling_on_jellyfin_and_keeps_user_tags():
    """The managed-prefix sweep is what removes `xt-H.264`: prove it on the real client."""
    posted: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posted.append(json.loads(request.content))
        return httpx.Response(204)

    jf = JellyfinClient("http://jf", "key", transport=httpx.MockTransport(handler))
    item = {"Id": "a", "Name": "A", "Tags": ["luxe", "xt-1080p", "xt-H.264", "xt-DD+", "xt-Not Rated", "mf-H.264"]}
    jf.set_managed_tags("a", item, "xt-", ["xt-1080p", "xt-H264", "xt-DDplus", "xt-Not-Rated"], legacy_prefixes=["mf-"])
    assert posted[0]["Tags"] == ["luxe", "xt-1080p", "xt-H264", "xt-DDplus", "xt-Not-Rated"]


def test_the_vocabulary_is_part_of_the_tag_config_hash(monkeypatch):
    """A respelling in code, with no config change, must still force one full re-tag."""
    before = pipeline._tag_config_hash(AppConfig())
    monkeypatch.setattr(pipeline, "TAG_VOCABULARY", pipeline.TAG_VOCABULARY + 1)
    assert pipeline._tag_config_hash(AppConfig()) != before
