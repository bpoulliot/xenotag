from __future__ import annotations

from .arr import ArrClient


class SonarrClient(ArrClient):
    KIND = "sonarr"
    COLLECTION = "series"
    EDITOR_IDS = "seriesIds"
    # Tvdb is Sonarr's own key; Tmdb and Imdb are carried too (v4).
    MATCH_KEYS = (("Tvdb", "tvdbId"), ("Tmdb", "tmdbId"), ("Imdb", "imdbId"))
    # Sonarr 4.0.18.2978 accepted `xt-h.265`, `xt-dd+ atmos` and `xt-hdr10+`.
    LABEL_PATTERN = None
