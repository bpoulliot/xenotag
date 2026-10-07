"""Roadmap B23: the preview must honour the saved tags.destinations.

``preview_image()`` built its ``AppConfig`` from the image params alone, so it
always got the *default* ``TagDestinations`` -- a category whose saved
``tags.destinations`` drops ``"poster"`` still showed pills on the Preview page
that no scan paints (``pipeline._make_badge_groups()`` gates each group on
``"poster" in dest.<category>``). This pins the fix: the preview's groups must
come from the saved ``get_config().tags``, the same source a scan reads.
"""

from __future__ import annotations

import asyncio

import pytest

from app.config import AppConfig, TagDestinations, TagsConfig
from app.web import routes


@pytest.fixture
def no_auth(monkeypatch):
    monkeypatch.setattr(routes, "_require_user", lambda request: "admin")


def _captured_labels(monkeypatch) -> dict:
    seen: dict = {}

    def capture(groups, rating_group, cfg_img, base_image_bytes=None):
        seen["labels"] = [label for g in groups for label in g.labels]
        return b""

    monkeypatch.setattr(routes, "generate_preview_bytes", capture)
    return seen


def test_a_category_dropped_from_poster_destinations_shows_no_pills(no_auth, monkeypatch):
    cfg = AppConfig(tags=TagsConfig(destinations=TagDestinations(subtitles=["jellyfin"])))
    monkeypatch.setattr(routes, "get_config", lambda: cfg)
    seen = _captured_labels(monkeypatch)

    asyncio.run(
        routes.preview_image(None, resolution="1080p", audio="EN DTS-HD", subtitles="EN PGS")
    )

    assert not any("PGS" in label for label in seen["labels"])
    assert any("DTS-HD" in label for label in seen["labels"])


def test_the_default_config_still_paints_every_category(no_auth, monkeypatch):
    """The default TagDestinations keeps "poster" everywhere -- an unchanged
    config must render exactly what it did before this fix."""
    cfg = AppConfig()
    monkeypatch.setattr(routes, "get_config", lambda: cfg)
    seen = _captured_labels(monkeypatch)

    asyncio.run(
        routes.preview_image(None, resolution="1080p", audio="EN DTS-HD", subtitles="EN PGS")
    )

    assert any("PGS" in label for label in seen["labels"])
    assert any("DTS-HD" in label for label in seen["labels"])
