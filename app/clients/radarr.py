from __future__ import annotations

import re

from .arr import ArrClient


class RadarrClient(ArrClient):
    KIND = "radarr"
    COLLECTION = "movie"
    EDITOR_IDS = "movieIds"
    MATCH_KEYS = (("Tmdb", "tmdbId"), ("Imdb", "imdbId"))
    # Radarr 6.3.0.10514 answers 400 "Allowed characters a-z, 0-9 and -" for
    # `xt-H.265`, `xt-DD+ Atmos`, `xt-HDR10+` and `xt-TrueHD Atmos`.
    LABEL_PATTERN = re.compile(r"[a-z0-9-]+")
