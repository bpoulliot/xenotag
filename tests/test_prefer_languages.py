"""Roadmap P7, ordering half: ``image.prefer_languages``.

One list of language codes, applied to the audio and subtitle badge rows: pills
carrying a listed language come first, in list order, and everything else keeps
today's order after them. It changes pill ORDER only -- never which pills exist
-- and an empty list (the default) must render byte for byte as before.

Renders go through the real ``pipeline._make_badge_groups()`` and
``overlay.render_badge_groups()``; nothing here touches a library file.
"""

from __future__ import annotations

import io
import json
import random

import pytest
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import pipeline
from app.config import AppConfig, ImageConfig
from app.overlay import BadgeGroup, apply_overlay, order_pills_by_language, prefer_order, render_badge_groups
from app.scanner import AudioTrack, MediaInfo, SubTrack
from app.state import Base, MediaState, get_language_counts, upsert_media_state

# ---------------------------------------------------------------------------
# The pre-P7 row builders, verbatim, as the reference for "byte-identical".
# ---------------------------------------------------------------------------


def _pre_p7_groups(info: MediaInfo, content_rating: str | None, cfg: AppConfig):
    img_cfg = cfg.image
    dest = cfg.tags.destinations
    groups: list[BadgeGroup] = []
    if img_cfg.show_video_badges and "poster" in dest.video:
        video_labels: list[str] = []
        if info.resolution and info.resolution != "unknown":
            video_labels.append(info.resolution)
        if info.video_codec:
            video_labels.append(info.video_codec)
        if info.hdr_type:
            video_labels.append(info.hdr_type)
        if video_labels:
            groups.append(BadgeGroup(video_labels, img_cfg.video_badge_color, img_cfg.badge_text_color))
    if img_cfg.show_audio_badges and "poster" in dest.audio:
        codec_langs: dict[str, list[str]] = {}
        for t in info.audio_tracks:
            langs = codec_langs.setdefault(t.codec, [])
            if t.lang and t.lang != "UND":
                langs.append(t.lang)
        audio_labels = [f"{codec} {' '.join(langs)}" if langs else codec for codec, langs in codec_langs.items()]
        if audio_labels:
            groups.append(BadgeGroup(audio_labels, img_cfg.audio_badge_color, img_cfg.badge_text_color))
    if img_cfg.show_sub_badges and "poster" in dest.subtitles:
        fmt_langs: dict[str, list[str]] = {}
        seen_lang_fmt: set[tuple[str, str]] = set()
        for t in info.subtitle_tracks:
            key = (t.lang, t.format)
            if key in seen_lang_fmt:
                continue
            seen_lang_fmt.add(key)
            langs = fmt_langs.setdefault(t.format, [])
            if t.lang and t.lang != "UND":
                langs.append(t.lang)
        sub_labels = [f"{fmt} {' '.join(langs)}" if langs else fmt for fmt, langs in fmt_langs.items()]
        if sub_labels:
            groups.append(BadgeGroup(sub_labels, img_cfg.sub_badge_color, img_cfg.badge_text_color))
    rating_group = None
    if img_cfg.show_rating_badge and content_rating and "poster" in dest.rating:
        rating_group = BadgeGroup([f"Rated {content_rating}"], img_cfg.rating_badge_color, img_cfg.badge_text_color)
    return groups, rating_group


def _info(audio: list[tuple[str, str]], subs: list[tuple[str, str]], res="1080p", codec="H.264", hdr=None):
    return MediaInfo(
        resolution=res,
        languages=[],
        raw_audio_langs=[],
        video_codec=codec,
        hdr_type=hdr,
        audio_tracks=[AudioTrack(lang=lang, codec=c) for lang, c in audio],
        subtitle_tracks=[SubTrack(lang=lang, format=f, embedded=True) for lang, f in subs],
    )


# Varied: empty rows, UND, codecs with spaces, duplicate (lang, fmt), many languages.
TAG_SETS = [
    _info([], []),
    _info([("EN", "AAC")], [("EN", "SRT")]),
    _info([("UND", "AC-3")], [("UND", "PGS")], res="unknown", codec=None),
    _info(
        [("EN", "TrueHD Atmos"), ("JA", "DTS-HD"), ("FR", "AAC"), ("DE", "DTS-HD")],
        [("EN", "PGS"), ("JA", "PGS"), ("EN", "PGS"), ("FR", "SRT"), ("DE", "SRT"), ("ES", "SRT")],
        res="4K",
        codec="H.265",
        hdr="DV",
    ),
    _info(
        [("JA", "FLAC"), ("EN", "AAC")],
        [(code, "PGS") for code in ("PT", "NL", "SV", "DA", "FI", "NO", "PL", "CS", "HU", "EL", "TR", "JA", "EN")]
        + [("EN", "SRT"), ("UND", "ASS")],
    ),
]

LAYOUTS = [
    ImageConfig(),
    ImageConfig(badge_size="desktop", badge_position="top-right", rating_position="top-right"),
    ImageConfig(badge_size="tv_plus", badge_position="bottom-right", rating_position="bottom-left"),
]


def _cfg(image: ImageConfig, prefer: list[str]) -> AppConfig:
    return AppConfig(image=image.model_copy(update={"prefer_languages": prefer}))


def _pre_p6(groups):
    """What a pre-P6 BadgeGroup held: the builder copy above predates the
    backup fill (roadmap P6), which only renders with adapt_badge_colors on."""
    if groups is None:
        return None
    if isinstance(groups, BadgeGroup):
        return (groups.labels, groups.fill_color, groups.text_color)
    return [_pre_p6(g) for g in groups]


def _render(groups, rating, image: ImageConfig, size=(600, 900)) -> bytes:
    base = Image.new("RGBA", size, (90, 120, 150, 255))
    return render_badge_groups(base, groups, rating, image).tobytes()


# ---------------------------------------------------------------------------
# Default empty -> byte-identical to today
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("image", LAYOUTS, ids=["default", "desktop-tr", "tvplus-br"])
@pytest.mark.parametrize("info", TAG_SETS)
def test_empty_prefer_renders_byte_identical_to_pre_p7(info, image):
    cfg = _cfg(image, [])
    old_groups, old_rating = _pre_p7_groups(info, "PG-13", cfg)
    new_groups, new_rating = pipeline._make_badge_groups(info, "PG-13", cfg)
    assert _pre_p6(new_groups) == _pre_p6(old_groups)
    assert _pre_p6(new_rating) == _pre_p6(old_rating)
    assert _render(new_groups, new_rating, cfg.image) == _render(old_groups, old_rating, cfg.image)


def test_the_byte_comparison_can_see_a_reorder():
    # Self-test for the test above: a preference that moves a pill must change
    # the bytes, or "identical" would prove nothing.
    info = TAG_SETS[3]
    old_groups, rating = _pre_p7_groups(info, "PG-13", _cfg(ImageConfig(), []))
    cfg = _cfg(ImageConfig(), ["DE"])
    new_groups, _ = pipeline._make_badge_groups(info, "PG-13", cfg)
    assert _pre_p6(new_groups) != _pre_p6(old_groups)
    assert _render(new_groups, rating, cfg.image) != _render(old_groups, rating, cfg.image)


def test_the_default_config_prefers_nothing():
    assert ImageConfig().prefer_languages == []


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


def _labels(info, prefer):
    groups, _ = pipeline._make_badge_groups(info, None, _cfg(ImageConfig(), prefer))
    video, audio, subs = groups
    return audio.labels, subs.labels


def test_preferred_languages_lead_in_list_order():
    audio, subs = _labels(TAG_SETS[3], ["JA", "EN"])
    # JA's pill first, then EN's; the rest keep today's order (AAC before its sibling).
    assert audio == ["DTS-HD JA DE", "TrueHD Atmos EN", "AAC FR"]
    # Within a pill the preferred languages lead too; SRT carries neither.
    assert subs == ["PGS JA EN", "SRT FR DE ES"]


def test_unlisted_pills_keep_todays_order():
    audio, subs = _labels(TAG_SETS[3], ["ES"])
    assert audio == ["TrueHD Atmos EN", "DTS-HD JA DE", "AAC FR"]
    assert subs == ["SRT ES FR DE", "PGS EN JA"]


def test_a_code_absent_from_the_item_changes_nothing():
    assert _labels(TAG_SETS[3], ["ZH"]) == _labels(TAG_SETS[3], [])


def test_the_heavy_item_surfaces_the_preferred_language_before_truncation():
    # The p99-shaped item: EN sits 13th of 13 PGS languages today, so a clamped
    # row cuts it. Preferred, it leads the pill.
    _, subs = _labels(TAG_SETS[4], ["EN"])
    assert subs[0].startswith("PGS EN ")
    _, subs_today = _labels(TAG_SETS[4], [])
    assert subs_today[0].endswith(" JA EN")


def test_tags_are_untouched(monkeypatch):
    cfg_a = _cfg(ImageConfig(), [])
    cfg_b = _cfg(ImageConfig(), ["JA", "EN"])
    assert pipeline._tag_config_hash(cfg_a) == pipeline._tag_config_hash(cfg_b)


@pytest.mark.parametrize("seed", range(40))
def test_order_only_never_which_pills(seed):
    rng = random.Random(seed)  # noqa: S311 -- a seeded test fixture, not a secret
    codes = ["EN", "JA", "DE", "FR", "ES", "IT", "KO", "ZH", "UND"]
    codecs = ["AAC", "AC-3", "DTS-HD", "TrueHD Atmos"]
    fmts = ["PGS", "SRT", "ASS"]
    info = _info(
        [(rng.choice(codes), rng.choice(codecs)) for _ in range(rng.randint(0, 6))],
        [(rng.choice(codes), rng.choice(fmts)) for _ in range(rng.randint(0, 20))],
    )
    prefer = rng.sample(codes[:-1], rng.randint(1, 4))
    before, _ = pipeline._make_badge_groups(info, None, _cfg(ImageConfig(), []))
    after, _ = pipeline._make_badge_groups(info, None, _cfg(ImageConfig(), prefer))
    assert len(before) == len(after)
    for b, a in zip(before, after, strict=True):
        # Same pills, each with the same languages -- only the order moved.
        assert sorted(sorted(label.split()) for label in b.labels) == sorted(
            sorted(label.split()) for label in a.labels
        )


def test_prefer_order_is_stable():
    assert prefer_order(["DE", "EN", "FR", "JA"], ["JA", "EN"]) == ["JA", "EN", "DE", "FR"]
    assert prefer_order(["DE", "FR"], []) == ["DE", "FR"]
    assert order_pills_by_language({"AAC": [], "PGS": ["EN"]}, ["EN"]) == ["PGS EN", "AAC"]


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (["en", " ja ", "EN", "und", ""], ["EN", "JA"]),
        ("en, ja de", ["EN", "JA", "DE"]),
        (None, []),
        (42, []),
    ],
)
def test_the_setting_is_normalised_and_never_stops_a_load(raw, expected):
    assert ImageConfig(prefer_languages=raw).prefer_languages == expected


# ---------------------------------------------------------------------------
# Backups: a re-render in a new order still keeps the ORIGINAL .orig
# ---------------------------------------------------------------------------


def test_a_reordered_rerender_keeps_the_original_backup(tmp_path):
    poster = tmp_path / "poster.jpg"
    Image.new("RGB", (400, 600), (40, 80, 120)).save(poster, "JPEG")
    original = poster.read_bytes()

    info = TAG_SETS[3]
    first, rating = pipeline._make_badge_groups(info, "R", _cfg(ImageConfig(), []))
    apply_overlay(tmp_path, first, rating, ImageConfig())
    backup = tmp_path / "poster.jpg.orig"
    assert backup.read_bytes() == original
    badged_once = poster.read_bytes()
    assert badged_once != original

    cfg = _cfg(ImageConfig(), ["DE", "FR"])
    second, rating = pipeline._make_badge_groups(info, "R", cfg)
    apply_overlay(tmp_path, second, rating, cfg.image)
    # The backup is still the clean original, not the first badged render...
    assert backup.read_bytes() == original
    # ...and the new render was drawn on it, not on top of the old badges.
    expected = render_badge_groups(Image.open(io.BytesIO(original)).convert("RGBA"), second, rating, cfg.image)
    got = Image.open(poster).convert("RGB")
    ref = Image.open(io.BytesIO(_jpeg(expected))).convert("RGB")
    assert got.tobytes() == ref.tobytes()
    assert poster.read_bytes() != badged_once


def _jpeg(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=92)  # apply_overlay's own setting
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Pick-list source
# ---------------------------------------------------------------------------


def _session():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _add(session, item_id, audio, subs):
    upsert_media_state(
        session,
        item_id=item_id,
        source="jellyfin",
        file_path=f"/x/{item_id}.mkv",
        resolution="1080p",
        languages=[],
        tags_applied=[],
        image_path=None,
        file_mtime=0.0,
        audio_tracks=[{"lang": lang, "codec": "AAC"} for lang in audio],
        subtitle_tracks=[{"lang": lang, "format": "SRT", "embedded": True} for lang in subs],
    )


def test_language_counts_are_items_per_code_from_the_index():
    Session = _session()
    s = Session()
    _add(s, "a", ["EN", "JA"], ["EN", "EN", "FR"])
    _add(s, "b", ["EN", "UND"], ["", "DE"])
    _add(s, "c", [], [])
    s.add(MediaState(item_id="d", source="jellyfin", audio_tracks="not json", subtitle_tracks=json.dumps({})))
    s.commit()
    assert get_language_counts(s) == {"EN": 2, "JA": 1, "FR": 1, "DE": 1}


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.web import routes

    Session = _session()
    monkeypatch.setattr(routes, "_require_user", lambda request: "admin")
    monkeypatch.setattr(routes, "get_session", lambda: Session())
    monkeypatch.setattr(routes, "get_config", lambda: AppConfig(image=ImageConfig(prefer_languages=["JA"])))
    monkeypatch.setattr(routes, "_language_cache", {"key": None, "value": None})
    return TestClient(app), Session


def test_the_languages_route_lists_codes_most_items_first_and_follows_the_index(client):
    http, Session = client
    s = Session()
    _add(s, "a", ["JA"], ["EN", "DE"])
    _add(s, "b", ["EN"], ["DE"])
    s.commit()
    r = http.get("/api/languages")
    assert r.status_code == 200
    body = r.json()
    assert body["languages"] == [{"code": "DE", "items": 2}, {"code": "EN", "items": 2}, {"code": "JA", "items": 1}]
    assert body["preferred"] == ["JA"]

    # Cached until the index changes -- a new row must show up.
    _add(s, "c", ["KO"], [])
    s.commit()
    codes = [row["code"] for row in http.get("/api/languages").json()["languages"]]
    assert "KO" in codes


def test_the_preview_honours_the_setting(client):
    from app.web.routes import _preview_order

    http, _ = client
    assert _preview_order(["EN DTS-HD", "JA AAC", "FR AAC"], ["FR", "JA"]) == ["FR AAC", "JA AAC", "EN DTS-HD"]
    assert _preview_order(["EN DTS-HD", "JA AAC"], []) == ["EN DTS-HD", "JA AAC"]
    r = http.get("/preview/image", params={"audio": "EN DTS-HD,JA AAC", "prefer_languages": "ja"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
