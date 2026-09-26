"""Shared Sonarr/Radarr v3 client (roadmap B5).

Two measured facts shape the tag handling here (Sonarr 4.0.18.2978 and Radarr
6.3.0.10514, the versions production runs, checked on the dev instances):

* **Both store every tag label lowercased.** ``POST /tag {"label": "xt-HEVC"}``
  creates ``xt-hevc``, and POSTing ``xt-HEVC`` again is a **409** (UNIQUE
  constraint), not a lookup. So labels are compared lowercase, always.
* **Radarr rejects any label outside ``[a-z0-9-]``** with a 400 ("Allowed
  characters a-z, 0-9 and -"); Sonarr accepts ``xt-h.265`` and
  ``xt-dd+ atmos``. ``LABEL_PATTERN`` records that per kind, so a label the
  instance would refuse is reported rather than attempted.

Writes go through the bulk editor (``PUT /series/editor`` / ``PUT
/movie/editor``) with ``applyTags`` ``add`` or ``remove`` and the managed tag
ids only. That endpoint loads the object from the *arr's own database and
changes nothing but its tags -- unlike ``PUT /series/{id}``, which writes back
whatever object it is handed, so a cached copy silently reverts anything the
operator changed since it was fetched (demonstrated on dev: an edited
``monitored`` flag and quality profile were both reverted).
"""

from __future__ import annotations

import logging
import re
from typing import Any, ClassVar

import httpx

log = logging.getLogger(__name__)


class ArrClient:
    KIND: ClassVar[str] = ""
    COLLECTION: ClassVar[str] = ""  # "series" | "movie"
    EDITOR_IDS: ClassVar[str] = ""  # "seriesIds" | "movieIds"
    # (Jellyfin ProviderIds key, *arr object field), in order of authority.
    MATCH_KEYS: ClassVar[tuple[tuple[str, str], ...]] = ()
    # Labels the instance accepts, lowercased; None = anything.
    LABEL_PATTERN: ClassVar[re.Pattern[str] | None] = None

    def __init__(self, url: str, api_key: str, name: str = "", transport: httpx.BaseTransport | None = None) -> None:
        self.base = url.rstrip("/")
        self.name = name or url
        self._client = httpx.Client(headers={"X-Api-Key": api_key}, timeout=30, transport=transport)
        self._objects: dict[int, dict] | None = None
        self._tags: dict[str, int] | None = None  # lowercased label -> id

    @property
    def label(self) -> str:
        return f"{self.KIND}/{self.name}"

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def _get(self, path: str, **params) -> Any:
        r = self._client.get(f"{self.base}/api/v3{path}", params=params)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, json: dict) -> Any:
        r = self._client.post(f"{self.base}/api/v3{path}", json=json)
        r.raise_for_status()
        return r.json()

    def _put(self, path: str, json: dict) -> Any:
        r = self._client.put(f"{self.base}/api/v3{path}", json=json)
        r.raise_for_status()
        return r.json()

    def health(self) -> dict:
        """Return {"ok": bool, "status": str, "message": str}."""
        try:
            r = self._client.get(f"{self.base}/ping", timeout=5)
            if r.status_code == 200:
                return {"ok": True, "status": "healthy", "message": "Healthy"}
            if r.status_code in (401, 403):
                return {"ok": False, "status": "auth_error", "message": "Invalid API key"}
            return {"ok": False, "status": "error", "message": f"HTTP {r.status_code}"}
        except httpx.TimeoutException:
            return {"ok": False, "status": "timeout", "message": "Timed out"}
        except Exception:
            return {"ok": False, "status": "unreachable", "message": "Unreachable"}

    # --- catalogue ---

    def preload(self) -> None:
        """Fetch every series/movie and every tag label (two GETs)."""
        self._objects = {o["id"]: o for o in self._get(f"/{self.COLLECTION}")}
        self._tags = self._load_tags()

    @property
    def objects(self) -> dict[int, dict]:
        if self._objects is None:
            raise RuntimeError(f"[{self.label}] preload() has not run")
        return self._objects

    def get_object(self, object_id: int) -> dict:
        """A fresh copy from the *arr, not the preload cache."""
        return self._get(f"/{self.COLLECTION}/{object_id}")

    # --- tags ---

    def _load_tags(self) -> dict[str, int]:
        return {t["label"].lower(): t["id"] for t in self._get("/tag")}

    @property
    def tags(self) -> dict[str, int]:
        if self._tags is None:
            self._tags = self._load_tags()
        return self._tags

    def label_accepted(self, label: str) -> bool:
        return self.LABEL_PATTERN is None or bool(self.LABEL_PATTERN.fullmatch(label.lower()))

    def create_tag(self, label: str) -> int:
        """Create ``label`` (a WRITE). Returns its id; 409 means it already exists."""
        label = label.lower()
        try:
            result = self._post("/tag", {"label": label})
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 409:
                raise
            self._tags = self._load_tags()
            if label not in self._tags:
                raise
            return self._tags[label]
        self.tags[label] = result["id"]
        log.info("[%s] Created tag: %s (id=%s)", self.label, label, result["id"])
        return result["id"]

    def edit_tags(self, object_id: int, tag_ids: list[int], apply: str) -> None:
        """Add or remove exactly ``tag_ids`` on one object (a WRITE). Nothing else changes."""
        if apply not in ("add", "remove"):
            raise ValueError(f"apply must be 'add' or 'remove', not {apply!r}")
        self._put(f"/{self.COLLECTION}/editor", {self.EDITOR_IDS: [object_id], "tags": tag_ids, "applyTags": apply})
