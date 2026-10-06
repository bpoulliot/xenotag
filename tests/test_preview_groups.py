"""Roadmap B14: the Preview page paints what a scan paints.

The page's sample profiles (``PREVIEW_PROFILES`` in ``index.html``) send label
strings ("EN DTS-HD,JA AAC", "PG-13"). ``routes.preview_image()`` used to split
them into pills as-is, so the preview showed ``EN DTS-HD`` and ``PG-13`` where a
poster gets ``DTS-HD EN`` and ``Rated PG-13``. It now parses them into a
``MediaInfo`` and builds the groups through ``pipeline._make_badge_groups()``.

Each profile is checked byte-for-byte against ``generate_preview_bytes()`` over
the groups a scan builds for the same streams, written out here by hand.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from pathlib import Path

import pytest

from app.config import AppConfig
from app.overlay import BadgeGroup, generate_preview_bytes
from app.pipeline import _make_badge_groups
from app.scanner import AudioTrack, MediaInfo, SubTrack
from app.web import routes

_TEMPLATE = Path(__file__).resolve().parent.parent / "app" / "web" / "templates" / "index.html"


def _template_profiles() -> list[dict]:
    """``PREVIEW_PROFILES`` as the page defines it -- read, not retyped."""
    block = re.search(r"const PREVIEW_PROFILES = \[(.*?)\n\];", _TEMPLATE.read_text(), re.S)
    assert block, "PREVIEW_PROFILES not found in index.html"
    rows = re.findall(r"\{(.*?)\}", block.group(1))
    return [json.loads("{" + re.sub(r"(\w+):", r'"\1":', row) + "}") for row in rows]


def _media(resolution, codec, hdr, audio, subs) -> MediaInfo:
    return MediaInfo(
        resolution=resolution,
        languages=list(dict.fromkeys(lang for lang, _ in audio)),
        raw_audio_langs=[],
        video_codec=codec,
        hdr_type=hdr,
        audio_tracks=[AudioTrack(lang, c) for lang, c in audio],
        subtitle_tracks=[SubTrack(lang, f, True) for lang, f in subs],
    )


# What a scan would see for each profile, by label: the streams written out as
# structured tracks, plus the pills that must come out. If the page's profiles
# change, this table has to change with them -- the first test says so.
_EXPECTED = {
    "4K HDR · EN DTS-HD": (
        _media("4K", "H.265", "HDR10", [("EN", "DTS-HD")], [("EN", "PGS")]),
        "PG-13",
        [["4K", "H.265", "HDR10"], ["DTS-HD EN"], ["PGS EN"]],
        ["Rated PG-13"],
    ),
    "1080p · dual-audio": (
        _media("1080p", "H.265", None, [("EN", "DTS-HD"), ("JA", "AAC")], [("EN", "PGS"), ("JA", "PGS")]),
        "TV-14",
        [["1080p", "H.265"], ["DTS-HD EN", "AAC JA"], ["PGS EN JA"]],
        ["Rated TV-14"],
    ),
    "4K DV · multi-audio": (
        _media(
            "4K",
            "AV1",
            "DV",
            [("EN", "TrueHD Atmos"), ("JA", "DTS-HD"), ("FR", "AAC")],
            [("EN", "SRT"), ("FR", "SRT"), ("DE", "SRT"), ("ES", "SRT")],
        ),
        "R",
        [["4K", "AV1", "DV"], ["TrueHD Atmos EN", "DTS-HD JA", "AAC FR"], ["SRT EN FR DE ES"]],
        ["Rated R"],
    ),
    "720p · minimal": (
        _media("720p", "H.264", None, [("EN", "AAC")], []),
        None,
        [["720p", "H.264"], ["AAC EN"]],
        None,
    ),
}


@pytest.fixture
def no_auth(monkeypatch):
    monkeypatch.setattr(routes, "_require_user", lambda request: "admin")


def _preview(profile: dict, **params) -> bytes:
    fields = {k: profile[k] for k in ("resolution", "video_codec", "hdr_type", "audio", "subtitles", "rating")}
    resp = asyncio.run(routes.preview_image(None, **fields, **params))
    return resp.body


def test_the_table_covers_every_page_profile():
    assert sorted(p["label"] for p in _template_profiles()) == sorted(_EXPECTED)


@pytest.mark.parametrize("profile", _template_profiles(), ids=lambda p: p["label"])
def test_the_preview_is_byte_identical_to_the_scan_render(no_auth, profile):
    info, rating, pills, rating_pills = _EXPECTED[profile["label"]]
    cfg_img = routes._image_config_from_params()
    groups, rating_group = _make_badge_groups(info, rating, AppConfig(image=cfg_img))

    # The scan's own labels, so the byte comparison is not two copies of one mistake.
    assert [g.labels for g in groups] == pills
    assert (rating_group.labels if rating_group else None) == rating_pills

    assert _preview(profile) == generate_preview_bytes(groups, rating_group, cfg_img)


@pytest.mark.parametrize("profile", _template_profiles(), ids=lambda p: p["label"])
def test_preferred_languages_order_the_preview_like_a_scan(no_auth, profile):
    info, rating, _, _ = _EXPECTED[profile["label"]]
    cfg_img = routes._image_config_from_params(prefer_languages="fr,ja")
    assert cfg_img.prefer_languages == ["FR", "JA"]
    groups, rating_group = _make_badge_groups(info, rating, AppConfig(image=cfg_img))
    assert _preview(profile, prefer_languages="fr,ja") == generate_preview_bytes(groups, rating_group, cfg_img)


def test_the_labels_the_bug_showed_are_gone(no_auth, monkeypatch):
    seen = {}

    def capture(groups, rating_group, cfg_img, base_image_bytes=None):
        seen["labels"] = [label for g in groups for label in g.labels]
        seen["rating"] = rating_group.labels if rating_group else None
        return b""

    monkeypatch.setattr(routes, "generate_preview_bytes", capture)
    asyncio.run(routes.preview_image(None, audio="EN DTS-HD,JA AAC", subtitles="EN PGS,JA PGS", rating="PG-13"))
    assert "EN DTS-HD" not in seen["labels"] and "EN PGS" not in seen["labels"]
    assert {"DTS-HD EN", "AAC JA", "PGS EN JA"} <= set(seen["labels"])
    assert seen["rating"] == ["Rated PG-13"]


def test_the_preview_builds_its_groups_through_the_scan_path(no_auth, monkeypatch):
    """Fails if preview_image() ever assembles BadgeGroups itself again."""
    sentinel = ([BadgeGroup(["SENTINEL"], "#000000", "#ffffff")], None)
    calls = []

    def spy(info, content_rating, cfg):
        calls.append((info, content_rating, cfg))
        return sentinel

    rendered = {}

    def capture(groups, rating_group, cfg_img, base_image_bytes=None):
        rendered["groups"] = (groups, rating_group)
        return b""

    monkeypatch.setattr(routes, "_make_badge_groups", spy)
    monkeypatch.setattr(routes, "generate_preview_bytes", capture)
    asyncio.run(routes.preview_image(None, audio="EN AAC", rating="R"))

    assert len(calls) == 1
    assert calls[0][1] == "R"
    assert rendered["groups"] is sentinel or rendered["groups"] == sentinel
    assert "BadgeGroup(" not in inspect.getsource(routes.preview_image)


def test_a_one_word_track_is_a_codec_with_no_language():
    info = routes._preview_media_info("1080p", "", "", "AAC", "SRT")
    assert info.audio_tracks == [AudioTrack("UND", "AAC")]
    assert info.subtitle_tracks == [SubTrack("UND", "SRT", True)]
    assert info.video_codec is None and info.hdr_type is None
