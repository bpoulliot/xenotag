from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

ITEM_FIELDS = (
    "MediaStreams,Tags,Path,Overview,ParentId,OfficialRating,ProviderIds,Genres,Studios,Taglines,LockData,LockedFields"
)


class JellyfinClient:
    def __init__(self, url: str, api_key: str, transport: httpx.BaseTransport | None = None) -> None:
        self.base = url.rstrip("/")
        self._client = httpx.Client(
            headers={"Authorization": f'MediaBrowser Token="{api_key}"'},
            timeout=30,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> JellyfinClient:
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def _get(self, path: str, **params) -> Any:
        r = self._client.get(f"{self.base}{path}", params=params)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, json: dict | None = None, **params) -> httpx.Response:
        r = self._client.post(f"{self.base}{path}", json=json, params=params)
        r.raise_for_status()
        return r

    def _delete(self, path: str, **params) -> httpx.Response:
        r = self._client.delete(f"{self.base}{path}", params=params)
        r.raise_for_status()
        return r

    def health(self) -> dict:
        """Return {"ok": bool, "status": "healthy"|"auth_error"|"unreachable"|"timeout", "message": str}."""
        try:
            r = self._client.get(f"{self.base}/health", timeout=5)
            if r.status_code == 200:
                return {"ok": True, "status": "healthy", "message": "Healthy"}
            if r.status_code in (401, 403):
                return {"ok": False, "status": "auth_error", "message": "Invalid API key"}
            return {"ok": False, "status": "error", "message": f"HTTP {r.status_code}"}
        except httpx.TimeoutException:
            return {"ok": False, "status": "timeout", "message": "Timed out"}
        except Exception:
            return {"ok": False, "status": "unreachable", "message": "Unreachable"}

    def get_libraries(self) -> list[dict]:
        data = self._get("/Library/VirtualFolders")
        return data if isinstance(data, list) else []

    def get_sample_items(self, limit: int = 12) -> list[dict]:
        """Return a small set of items that have a primary poster image, for preview use."""
        data = self._get(
            "/Items",
            Recursive="true",
            IncludeItemTypes="Movie,Series",
            Fields="ImageTags",
            SortBy="DateCreated",
            SortOrder="Descending",
            Limit=50,
        )
        items = data.get("Items", [])
        return [i for i in items if i.get("ImageTags", {}).get("Primary")][:limit]

    def get_diverse_sample_items(self, count: int = 4) -> list[dict]:
        """Return a mix of Movies and TV Series with Primary images."""
        results: list[dict] = []
        per_type = (count + 1) // 2
        for media_type in ("Movie", "Series"):
            data = self._get(
                "/Items",
                Recursive="true",
                IncludeItemTypes=media_type,
                Fields="ImageTags",
                SortBy="DateCreated",
                SortOrder="Descending",
                Limit=per_type * 6,
            )
            items = [i for i in data.get("Items", []) if i.get("ImageTags", {}).get("Primary")]
            results.extend(items[:per_type])
        return results[:count]

    def get_items(self, library_ids: list[str] | None = None) -> list[dict]:
        if not library_ids:
            return self._fetch_items(parent_id=None)
        if len(library_ids) == 1:
            return self._fetch_items(parent_id=library_ids[0])
        # ParentId accepts only one ID — fetch each library separately and merge
        seen: set[str] = set()
        merged: list[dict] = []
        for lib_id in library_ids:
            for item in self._fetch_items(parent_id=lib_id):
                item_id = item.get("Id", "")
                if item_id not in seen:
                    seen.add(item_id)
                    merged.append(item)
        return merged

    def _fetch_items(self, parent_id: str | None = None) -> list[dict]:
        params: dict[str, Any] = {
            "Recursive": "true",
            "IncludeItemTypes": "Movie,Series",
            "Fields": ITEM_FIELDS,
            "Limit": 500,
            "StartIndex": 0,
        }
        if parent_id:
            params["ParentId"] = parent_id
        all_items: list[dict] = []
        while True:
            data = self._get("/Items", **params)
            page = data.get("Items", [])
            all_items.extend(page)
            if len(all_items) >= data.get("TotalRecordCount", 0) or not page:
                break
            params["StartIndex"] = len(all_items)
        return all_items

    def get_item(self, item_id: str) -> dict:
        return self._get(f"/Items/{item_id}", Fields=ITEM_FIELDS)

    def get_media_info(self, item_id: str) -> dict:
        return self._get(f"/Items/{item_id}/PlaybackInfo", UserId="")

    def get_tags(self, item: dict) -> list[str]:
        return item.get("Tags") or []

    def get_item_by_id(self, item_id: str) -> dict:
        """Fetch full item metadata via list endpoint (direct /Items/{id} requires extra auth in 10.9+)."""
        data = self._get("/Items", Ids=item_id, Fields="Tags,Genres,Studios,ProviderIds,Overview,OfficialRating")
        items = data.get("Items", [])
        return items[0] if items else {}

    def set_managed_tags(
        self,
        item_id: str,
        item: dict,
        prefix: str,
        new_tags: list[str],
        fallback_rating: str = "",
        legacy_prefixes: list[str] = (),
    ) -> None:
        """Replace all prefix-managed tags on the item with new_tags (Jellyfin 10.9+ PUT approach).

        fallback_rating is written to OfficialRating only when the item's own field is blank.
        Pass it from arr cert data when Jellyfin had no rating and arr provided one.
        """
        existing = self.get_tags(item)
        all_prefixes = (prefix, *legacy_prefixes)
        user_tags = [t for t in existing if not any(t.startswith(p) for p in all_prefixes)]
        merged = user_tags + new_tags

        # POST /Items/{id} with a minimal UpdateRequest DTO (Jellyfin 10.9+)
        update_body = {
            "Name": item.get("Name", ""),
            "OriginalTitle": item.get("OriginalTitle", "") or "",
            "ForcedSortName": item.get("ForcedSortName", "") or "",
            "ProductionYear": item.get("ProductionYear"),
            "OfficialRating": item.get("OfficialRating") or fallback_rating or "",
            "Tags": merged,
            "Genres": item.get("Genres") or [],
            "Studios": [{"Name": s} if isinstance(s, str) else s for s in (item.get("Studios") or [])],
            "Taglines": item.get("Taglines") or [],
            "People": [],
            "LockData": item.get("LockData", False),
            "LockedFields": item.get("LockedFields") or [],
            "ProviderIds": item.get("ProviderIds") or {},
        }
        self._post(f"/Items/{item_id}", json=update_body)
        log.debug("Jellyfin %s: tags set to %s", item_id, merged)

    def refresh_item(self, item_id: str) -> None:
        try:
            self._post(
                f"/Items/{item_id}/Refresh",
                ReplaceAllMetadata="false",
                ReplaceAllImages="false",
            )
        except Exception as exc:
            log.warning("Jellyfin refresh failed for %s: %s", item_id, exc)

    def find_item_by_provider_id(self, provider: str, value: str) -> dict | None:
        """Find a Movie or Series by provider ID (e.g. provider='Tvdb', value='81189')."""
        data = self._get(
            "/Items",
            Recursive="true",
            IncludeItemTypes="Movie,Series",
            Fields=ITEM_FIELDS,
            AnyProviderIdEquals=f"{provider}.{value}",
        )
        items = data.get("Items", [])
        return items[0] if items else None

    def upload_image(self, item_id: str, image_bytes: bytes, content_type: str = "image/jpeg") -> None:
        """Upload image bytes directly to Jellyfin as the Primary image (API fallback)."""
        r = self._client.post(
            f"{self.base}/Items/{item_id}/Images/Primary",
            content=image_bytes,
            headers={"Content-Type": content_type},
            timeout=30,
        )
        r.raise_for_status()
