"""Roadmap B8: a Sonarr/Radarr webhook resolves its Jellyfin item by folder, never by provider id.

Jellyfin (10.11.10 and 12.1.0) ignores every provider-id filter on ``/Items``
and returns the whole library, so the old ``AnyProviderIdEquals`` lookup
processed whichever item came first. These tests drive the real
``JellyfinClient`` over a mock Jellyfin that honours ``IncludeItemTypes`` and
``Ids=`` only -- as the real one does -- and record every request.
"""

from __future__ import annotations

import logging

import httpx
import pytest

from app import pipeline
from app.clients.jellyfin import ITEM_FIELDS, JellyfinClient
from app.config import AppConfig
from app.scanner import AudioTrack, MediaInfo

HD = {"Id": "hd", "Name": "Heat", "Type": "Movie", "Path": "/media/movies/Heat (1995)/Heat.mkv"}
UHD = {"Id": "uhd", "Name": "Heat", "Type": "Movie", "Path": "/media/movies-4k/Heat (1995)/Heat.mkv"}
SHOW = {"Id": "show", "Name": "Firefly", "Type": "Series", "Path": "/media/tv/Firefly"}
SHOW_4K = {"Id": "show4k", "Name": "Firefly", "Type": "Series", "Path": "/media/tv-4k/Firefly"}
# A series item whose folder is where a film payload will point -- it must never match.
DECOY = {"Id": "decoy", "Name": "Decoy", "Type": "Series", "Path": "/media/movies/Alien (1979)"}
for _item in (HD, UHD):
    _item["ProviderIds"] = {"Tmdb": "949"}
for _item in (SHOW, SHOW_4K):
    _item["ProviderIds"] = {"Tvdb": "78874"}


class Jellyfin:
    def __init__(self, items):
        self.items = items
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        params = request.url.params
        if request.url.path != "/Items":
            return httpx.Response(404)
        if "Ids" in params:
            wanted = params["Ids"].split(",")
            found = [dict(i) for i in self.items if i["Id"] in wanted]
        else:
            types = params.get("IncludeItemTypes", "Movie,Series").split(",")
            found = [{"Id": i["Id"], "Type": i["Type"], "Path": i["Path"]} for i in self.items if i["Type"] in types]
        return httpx.Response(200, json={"Items": found, "TotalRecordCount": len(found)})

    def provider_queries(self):
        return [r for r in self.requests if any("provider" in k.lower() or k.endswith("Id") for k in r.url.params)]


def _resolve(items, source, payload):
    server = Jellyfin(items)
    with JellyfinClient("http://jf", "k", transport=httpx.MockTransport(server)) as jf:
        return pipeline._resolve_webhook_jf_item(jf, source, payload), server


ALL = [HD, UHD, SHOW, SHOW_4K, DECOY]


@pytest.mark.parametrize(
    "source,payload,expected",
    [
        ("radarr", {"movie": {"tmdbId": 949, "folderPath": "/media/movies/Heat (1995)"}}, "hd"),
        ("radarr", {"movie": {"tmdbId": 949, "folderPath": "/media/movies-4k/Heat (1995)"}}, "uhd"),
        ("sonarr", {"series": {"tvdbId": 78874, "path": "/media/tv/Firefly"}}, "show"),
        ("sonarr", {"series": {"tvdbId": 78874, "path": "/media/tv-4k/Firefly"}}, "show4k"),
    ],
)
def test_twins_with_one_provider_id_resolve_to_the_payloads_folder(source, payload, expected):
    item, server = _resolve(ALL, source, payload)
    assert item["Id"] == expected
    assert server.provider_queries() == []


def test_a_trailing_slash_on_the_payload_path_still_matches():
    item, _ = _resolve(ALL, "sonarr", {"series": {"path": "/media/tv/Firefly/"}})
    assert item["Id"] == "show"
    item, _ = _resolve(ALL, "radarr", {"movie": {"folderPath": "/media/movies/Heat (1995)//"}})
    assert item["Id"] == "hd"


def test_a_film_folder_never_matches_a_series_item_or_the_reverse():
    item, server = _resolve(ALL, "radarr", {"movie": {"folderPath": "/media/movies/Alien (1979)"}})
    assert item is None
    [listing] = server.requests
    assert listing.url.params["IncludeItemTypes"] == "Movie"
    assert listing.url.params["Fields"] == "Path"  # never MediaSources (38.7 MB on production)

    item, server = _resolve(ALL, "sonarr", {"series": {"path": "/media/movies/Heat (1995)"}})
    assert item is None
    assert server.requests[0].url.params["IncludeItemTypes"] == "Series"


def test_a_folder_with_no_item_returns_none_without_a_provider_id_lookup(caplog):
    payload = {"movie": {"tmdbId": 949, "imdbId": "tt0113277", "folderPath": "/media/movies/Ronin (1998)"}}
    with caplog.at_level(logging.INFO, logger="app.pipeline"):
        item, server = _resolve(ALL, "radarr", payload)
    assert item is None
    assert len(server.requests) == 1 and server.provider_queries() == []
    assert any("not in Jellyfin yet" in r.getMessage() for r in caplog.records)


def test_more_than_one_item_in_the_folder_processes_none(caplog):
    twin = {**SHOW, "Id": "dup"}
    with caplog.at_level(logging.WARNING, logger="app.pipeline"):
        item, server = _resolve([SHOW, twin], "sonarr", {"series": {"path": "/media/tv/Firefly"}})
    assert item is None
    assert not any("Ids" in r.url.params for r in server.requests)
    assert any("2 Jellyfin items" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize(
    "source,payload",
    [("sonarr", {"series": {"tvdbId": 78874}}), ("radarr", {"movie": {"tmdbId": 949, "folderPath": ""}})],
)
def test_a_payload_with_no_path_returns_none(source, payload):
    item, server = _resolve(ALL, source, payload)
    assert item is None and server.requests == []


def test_the_jellyfin_branch_asks_for_the_scans_fields_including_path():
    item, server = _resolve(ALL, "jellyfin", {"ItemId": "hd"})
    assert item["Path"] == HD["Path"]
    [read] = server.requests
    assert read.url.params["Ids"] == "hd" and read.url.params["Fields"] == ITEM_FIELDS
    assert "Path" in ITEM_FIELDS.split(",")


def test_find_item_by_provider_id_is_gone():
    assert not hasattr(JellyfinClient, "find_item_by_provider_id")


@pytest.mark.parametrize("source", ["jellyfin", "radarr"])
def test_handle_webhook_processes_the_resolved_items_file(tmp_path, monkeypatch, source):
    """End to end: the item reaches ``_process_one_item`` with its own file (part 2 of B8)."""
    film = tmp_path / "Heat (1995)" / "Heat.mkv"
    film.parent.mkdir()
    film.write_bytes(b"")
    server = Jellyfin([{**HD, "Path": str(film)}, {**UHD, "Path": str(tmp_path / "4k" / "Heat.mkv")}])
    jf = JellyfinClient("http://jf", "k", transport=httpx.MockTransport(server))
    processed: list[tuple[str, str]] = []

    def process(jf_, arr, session, cfg, item, file_path, item_root, mtime, info):
        processed.append((item["Id"], file_path))
        return False

    info = MediaInfo("1080p", ["EN"], ["eng"], "H.264", None, [AudioTrack("EN", "AAC")], [])
    monkeypatch.setattr(pipeline, "_clients_from_config", lambda c: (jf, [], []))
    monkeypatch.setattr(pipeline, "probe_file", lambda p: info)
    monkeypatch.setattr(pipeline, "_process_one_item", process)
    monkeypatch.setattr(pipeline, "get_session", lambda: type("S", (), {"close": lambda self: None})())

    payload = {"ItemId": "hd"} if source == "jellyfin" else {"movie": {"folderPath": str(film.parent)}}
    pipeline.handle_webhook(AppConfig(), source, payload)
    assert processed == [("hd", str(film))]
