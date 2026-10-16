from __future__ import annotations

from pydantic import BaseModel


class ScanStatusResponse(BaseModel):
    running: bool
    cancelled: bool = False
    total: int
    done: float
    current_item: str
    error: str | None = None


class StatsResponse(BaseModel):
    total_tagged: int
    images_modified: int
    last_scan_at: str | None
    last_scan_type: str | None
    next_scan_at: str | None


class MediaItem(BaseModel):
    item_id: str
    source: str
    file_path: str | None
    resolution: str | None
    languages: list[str]
    tags_applied: list[str]
    image_path: str | None
    last_scanned: str | None
    # What the media browser's loadMedia() reads (roadmap B30). Pydantic drops a key the model does
    # not declare, so each of these must be here or the page never receives it. Defaults let a row
    # from before the columns were probed (NULL / '[]') validate.
    video_codec: str | None = None
    hdr_type: str | None = None
    audio_tracks: list[dict] = []
    subtitle_tracks: list[dict] = []
    content_rating: str | None = None
    field_order: str | None = None
    file_mtime: float | None = None


class ScanRunItem(BaseModel):
    id: int
    scan_type: str | None
    started_at: str | None
    completed_at: str | None
    items_scanned: int
    items_tagged: int
    items_image_modified: int
    duration_seconds: int | None


class ConfigResponse(BaseModel):
    yaml: str


class ConfigSaveRequest(BaseModel):
    yaml: str


class HealthResponse(BaseModel):
    status: str
    # None for an unauthenticated caller (the container healthcheck): it gets
    # overall up/down only, never a dependency verdict it did not measure.
    jellyfin: dict | None = None  # {ok, status, message}
    sonarr: list[dict] | None = None  # [{name, ok, status, message}, ...]
    radarr: list[dict] | None = None
