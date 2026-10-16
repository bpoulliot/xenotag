"""Roadmap B30: ``/media`` must return the fields ``loadMedia()`` reads.

``MediaItem`` once declared 8 of the 15 keys ``get_media_filtered()`` returns, and Pydantic
silently drops an undeclared key, so the browser never received a codec, HDR type, audio track
or rating. Every title and path below is invented.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import AppConfig
from app.state import Base, MediaState, _media_to_dict
from app.web.schemas import MediaItem

INDEX_HTML = Path(__file__).resolve().parent.parent / "app" / "web" / "templates" / "index.html"

FULL = dict(
    item_id="full",
    source="jellyfin",
    file_path="/media/Invented Film (2020)/invented.mkv",
    resolution="2160p",
    languages=json.dumps(["EN", "JA"]),
    tags_applied=json.dumps(["dual-audio"]),
    image_path=None,
    last_scanned=datetime(2026, 1, 2, 3, 4, 5),
    file_mtime=1700000000.5,
    video_codec="HEVC",
    hdr_type="HDR10",
    audio_tracks=json.dumps([{"lang": "EN", "codec": "TrueHD"}, {"lang": "UND", "codec": "AC3"}]),
    subtitle_tracks=json.dumps([{"lang": "EN", "format": "subrip", "embedded": True}]),
    content_rating="R",
    field_order="progressive",
)

# A row as an older scan left it: none of the probed columns set.
BARE = dict(item_id="bare", source="jellyfin", file_path="/media/Bare/bare.mkv", resolution="1080p")


@pytest.fixture
def client(monkeypatch):
    from app.main import app
    from app.web import routes

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as s:
        s.add_all([MediaState(**FULL), MediaState(**BARE, last_scanned=datetime(2025, 1, 1))])
        s.commit()
    monkeypatch.setattr(routes, "get_session", factory)
    monkeypatch.setattr(routes, "get_config", lambda: AppConfig())
    monkeypatch.setattr(routes, "_current_user", lambda request: "admin")
    return TestClient(app)


def _rows(client):
    resp = client.get("/media")
    assert resp.status_code == 200
    return {i["item_id"]: i for i in resp.json()["items"]}


def test_media_returns_the_fields_the_browser_reads(client):
    row = _rows(client)["full"]
    assert row["video_codec"] == "HEVC"
    assert row["hdr_type"] == "HDR10"
    assert row["audio_tracks"] == [{"lang": "EN", "codec": "TrueHD"}, {"lang": "UND", "codec": "AC3"}]
    assert row["content_rating"] == "R"


def test_media_returns_the_remaining_stored_fields(client):
    row = _rows(client)["full"]
    assert row["subtitle_tracks"] == [{"lang": "EN", "format": "subrip", "embedded": True}]
    assert row["field_order"] == "progressive"
    assert row["file_mtime"] == 1700000000.5


def test_media_row_without_the_fields_still_validates(client):
    row = _rows(client)["bare"]
    assert row["video_codec"] is None
    assert row["hdr_type"] is None
    assert row["content_rating"] is None
    assert row["field_order"] is None
    assert row["file_mtime"] is None
    assert row["audio_tracks"] == []
    assert row["subtitle_tracks"] == []


def test_media_item_declares_every_key_the_state_layer_returns():
    # The defect itself: a key get_media_filtered() returns that MediaItem does not declare.
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        s.add(MediaState(**FULL))
        s.commit()
        d = _media_to_dict(s.query(MediaState).one())
    assert set(d) <= set(MediaItem.model_fields)
    assert set(MediaItem(**d).model_dump()) == set(d)


def test_dashboard_reads_the_response_names_as_returned(client):
    # loadMedia() must read the keys /media serves, not a renamed one.
    html = INDEX_HTML.read_text()
    body = html[html.index("async function loadMedia()") :]
    body = body[: body.index("function prevPage")]
    read = set(re.findall(r"\bitem\.([a-z_]+)", body))
    served = set(_rows(client)["full"])
    assert {"video_codec", "hdr_type", "audio_tracks", "content_rating"} <= read
    assert read <= served, read - served
    track_keys = set(re.findall(r"\bt\.([a-z_]+)", body[body.index("audioTracks.map") : body.index("ratingCell")]))
    assert track_keys == {"lang", "codec"}
