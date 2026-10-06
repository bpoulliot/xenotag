"""Roadmap B8: a webhook resolves its Jellyfin item by folder, never by provider id.

Jellyfin (10.11.10 and 12.1.0) ignores every provider-id filter and returns the
whole library, so the old ``AnyProviderIdEquals`` lookup processed whichever
item came first. Sonarr/Radarr payloads now resolve by the folder they carry,
matched with B5's ``item_folders()``; a Jellyfin payload's ``ItemId`` is fetched
with ``ITEM_FIELDS`` so the handler gets a ``Path``.

These drive a real ``JellyfinClient`` over ``httpx.MockTransport``, so the
requests asserted on are the ones the client really sends.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app import pipeline
from app.clients.jellyfin import ITEM_FIELDS, JellyfinClient

URL = "http://jf.test"

# HD and 4K twins share one provider id; only the folder tells them apart.
FILM_HD = {
    "Id": "film-hd",
    "Type": "Movie",
    "Path": "/media/movies/Serenity (2005)/Serenity (2005).mkv",
    "ProviderIds": {"Tmdb": "16320"},
}
FILM_4K = {
    "Id": "film-4k",
    "Type": "Movie",
    "Path": "/media/movies-4k/Serenity (2005)/Serenity (2005).mkv",
    "ProviderIds": {"Tmdb": "16320"},
}
SERIES_HD = {"Id": "series-hd", "Type": "Series", "Path": "/media/tv/Firefly", "ProviderIds": {"Tvdb": "78874"}}
SERIES_4K = {"Id": "series-4k", "Type": "Series", "Path": "/media/tv-4k/Firefly", "ProviderIds": {"Tvdb": "78874"}}
# A series whose folder is the same as a film's -- must never cross types.
SERIES_IN_FILM_FOLDER = {"Id": "series-x", "Type": "Series", "Path": "/media/movies/Serenity (2005)"}
LIBRARY = [FILM_HD, FILM_4K, SERIES_HD, SERIES_4K]


class FakeJellyfin:
    def __init__(self, library: list[dict]):
        self.library = library
        self.log: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        query = {k: v[0] for k, v in parse_qs(urlsplit(str(request.url)).query).items()}
        self.log.append(query)
        assert request.method == "GET" and request.url.path == "/Items"
        if "Ids" in query:
            ids = query["Ids"].split(",")
            items = [dict(i, Tags=[]) for i in self.library if i["Id"] in ids]
        else:
            types = query["IncludeItemTypes"].split(",")
            items = [{"Id": i["Id"], "Path": i["Path"]} for i in self.library if i["Type"] in types]
        return httpx.Response(200, json={"Items": items, "TotalRecordCount": len(items)})


def _client(library: list[dict]) -> tuple[JellyfinClient, FakeJellyfin]:
    server = FakeJellyfin(library)
    return JellyfinClient(URL, "key", transport=httpx.MockTransport(server)), server


def _resolve(library: list[dict], source: str, payload: dict) -> tuple[dict | None, FakeJellyfin]:
    jf, server = _client(library)
    try:
        return pipeline._resolve_webhook_jf_item(jf, source, payload), server
    finally:
        jf.close()


def _assert_no_provider_lookup(server: FakeJellyfin) -> None:
    for query in server.log:
        assert not any(word in key for key in query for word in ("Provider", "Tvdb", "Tmdb")), query


@pytest.mark.parametrize(
    ("source", "payload", "expected"),
    [
        ("radarr", {"movie": {"tmdbId": 16320, "folderPath": "/media/movies/Serenity (2005)"}}, "film-hd"),
        ("radarr", {"movie": {"tmdbId": 16320, "folderPath": "/media/movies-4k/Serenity (2005)"}}, "film-4k"),
        ("sonarr", {"series": {"tvdbId": 78874, "path": "/media/tv/Firefly"}}, "series-hd"),
        ("sonarr", {"series": {"tvdbId": 78874, "path": "/media/tv-4k/Firefly"}}, "series-4k"),
    ],
)
def test_twins_resolve_to_the_payloads_folder(source, payload, expected):
    item, server = _resolve(LIBRARY, source, payload)
    assert item is not None and item["Id"] == expected
    _assert_no_provider_lookup(server)


@pytest.mark.parametrize(
    ("source", "payload", "expected"),
    [
        ("radarr", {"movie": {"folderPath": "/media/movies-4k/Serenity (2005)/"}}, "film-4k"),
        ("sonarr", {"series": {"path": "/media/tv/Firefly/"}}, "series-hd"),
    ],
)
def test_trailing_slash_still_matches(source, payload, expected):
    item, _ = _resolve(LIBRARY, source, payload)
    assert item is not None and item["Id"] == expected


def test_a_film_folder_never_matches_a_series():
    library = [SERIES_IN_FILM_FOLDER, SERIES_HD]
    item, server = _resolve(library, "radarr", {"movie": {"folderPath": "/media/movies/Serenity (2005)"}})
    assert item is None
    assert [q["IncludeItemTypes"] for q in server.log if "IncludeItemTypes" in q] == ["Movie"]


def test_a_series_folder_never_matches_a_film():
    item, server = _resolve([FILM_HD], "sonarr", {"series": {"path": "/media/movies/Serenity (2005)"}})
    assert item is None
    assert [q["IncludeItemTypes"] for q in server.log if "IncludeItemTypes" in q] == ["Series"]


def test_a_folder_with_no_item_returns_none_without_a_provider_lookup(caplog):
    payload = {"series": {"tvdbId": 78874, "path": "/media/tv/Not Imported Yet"}}
    with caplog.at_level("INFO", logger="app.pipeline"):
        item, server = _resolve(LIBRARY, "sonarr", payload)
    assert item is None
    assert "not in Jellyfin yet" in caplog.text
    assert len(server.log) == 1 and server.log[0]["Fields"] == "Path"
    _assert_no_provider_lookup(server)


def test_more_than_one_match_is_refused_not_guessed(caplog):
    dupe = dict(FILM_HD, Id="film-hd-dupe")
    with caplog.at_level("WARNING", logger="app.pipeline"):
        item, server = _resolve([FILM_HD, dupe], "radarr", {"movie": {"folderPath": "/media/movies/Serenity (2005)"}})
    assert item is None
    assert "matches 2 Jellyfin items" in caplog.text
    assert not any("Ids" in q for q in server.log)


@pytest.mark.parametrize(
    ("source", "payload"),
    [
        ("sonarr", {"series": {"tvdbId": 78874}}),
        ("radarr", {"movie": {"tmdbId": 16320, "folderPath": ""}}),
        ("radarr", {}),
    ],
)
def test_a_payload_with_no_path_returns_none_without_a_request(source, payload):
    item, server = _resolve(LIBRARY, source, payload)
    assert item is None
    assert server.log == []


def test_the_jellyfin_branch_gets_an_item_with_a_path():
    item, server = _resolve(LIBRARY, "jellyfin", {"ItemId": "film-4k"})
    assert item is not None and item["Path"] == FILM_4K["Path"]
    assert server.log[-1]["Fields"] == ITEM_FIELDS
    assert "Path" in ITEM_FIELDS.split(",")


def test_the_provider_id_lookup_is_gone():
    assert not hasattr(JellyfinClient, "find_item_by_provider_id")
