from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator


class JellyfinConfig(BaseModel):
    url: str = "http://jellyfin:8096"
    api_key: str = ""
    library_ids: list[str] = Field(default_factory=list)


class ArrInstance(BaseModel):
    name: str
    url: str
    api_key: str = ""


class SonarrConfig(BaseModel):
    instances: list[ArrInstance] = Field(default_factory=list)


class RadarrConfig(BaseModel):
    instances: list[ArrInstance] = Field(default_factory=list)


class ScanConfig(BaseModel):
    schedule: str = "0 3 * * *"
    incremental: bool = True
    path_filters: list[str] = Field(default_factory=list)
    max_workers: int = 4

    @field_validator("schedule")
    @classmethod
    def _validate_schedule(cls, v: str) -> str:
        if not v:
            return v
        try:
            from apscheduler.triggers.cron import CronTrigger

            CronTrigger.from_crontab(v)
        except Exception as exc:
            raise ValueError(f"Invalid cron expression '{v}': {exc}") from exc
        return v


class TagDestinations(BaseModel):
    # Each list contains the destinations this category's tags are sent to.
    # Valid values: "poster", "jellyfin", "sonarr", "radarr"
    video: list[str] = Field(default_factory=lambda: ["poster", "jellyfin", "sonarr", "radarr"])
    audio: list[str] = Field(default_factory=lambda: ["poster", "jellyfin", "sonarr", "radarr"])
    subtitles: list[str] = Field(default_factory=lambda: ["poster", "jellyfin"])
    rating: list[str] = Field(default_factory=lambda: ["poster"])


class TagsConfig(BaseModel):
    managed_prefix: str = "xt-"
    legacy_prefixes: list[str] = Field(default_factory=lambda: ["mf-"])
    dual_audio_tag: str = "dual-audio"
    multi_audio_tag: str = "multi-audio"
    destinations: TagDestinations = Field(default_factory=TagDestinations)


class ImageConfig(BaseModel):
    targets: list[str] = Field(default_factory=lambda: ["poster.jpg", "poster.png", "folder.jpg", "folder.png"])
    backup_suffix: str = ".orig"
    badge_position: str = "bottom-left"
    # Badge fill opacity. This is a CONTRAST control, not just a cosmetic one:
    # the pill is filled at this alpha, so anything below 1.0 lets the poster
    # show through and drops the rendered contrast of the label against the
    # fill. Measured over black/white/grey backdrops with the palette below
    # (scripts/measure_badge_contrast.py): 1.0 -> 9.4:1 worst case, 0.89 is the
    # floor for AAA, 0.73 the floor for AA, and 0.65 -- the default until
    # roadmap B1 -- rendered 3.7:1 and failed both.
    badge_opacity: float = 1.0
    badge_size: Literal["desktop", "tv", "tv_plus"] = "tv"

    @field_validator("badge_size", mode="before")
    @classmethod
    def _migrate_badge_size(cls, v: object) -> object:
        return {"small": "desktop", "medium": "tv", "large": "tv_plus"}.get(str(v), v)

    badge_text_color: str = "#ffffff"

    # Per-category badge colors. Each ratio below is the OPAQUE hex against
    # white text (WCAG 2.x; AAA is ≥7:1). That equals what actually renders
    # only while badge_opacity is 1.0 -- at a lower opacity the pill is
    # translucent and the rendered ratio is lower than the figure quoted here.
    # Re-measure with scripts/measure_badge_contrast.py; do not trust the
    # comment. (Roadmap B1: these were annotated "verified WCAG AAA ≥7:1"
    # while the shipped default rendered 3.7-5.3:1 -- true of the hex, false of
    # the render, and that is why it went unnoticed for so long.)
    video_badge_color: str = "#134e4a"  # dark teal    opaque  9.5:1
    audio_badge_color: str = "#1e3a8a"  # deep navy    opaque 10.4:1
    sub_badge_color: str = "#7c2d12"  # deep rust      opaque  9.4:1
    rating_badge_color: str = "#4c1d95"  # deep violet opaque 11.0:1

    # Show/hide categories on poster
    show_video_badges: bool = True
    show_audio_badges: bool = True
    show_sub_badges: bool = True
    show_rating_badge: bool = True

    # Pad non-portrait images to 2:3 before applying badges so they aren't
    # cropped when Jellyfin displays the poster in a portrait slot.
    normalize_portrait: bool = True


class AuthConfig(BaseModel):
    username: str = "admin"
    password_hash: str = ""
    secret_key: str = ""


class WebhooksConfig(BaseModel):
    secret: str = ""  # if set, require matching X-Webhook-Token header or ?token= query param


class AppConfig(BaseModel):
    jellyfin: JellyfinConfig = Field(default_factory=JellyfinConfig)
    sonarr: SonarrConfig = Field(default_factory=SonarrConfig)
    radarr: RadarrConfig = Field(default_factory=RadarrConfig)
    scan: ScanConfig = Field(default_factory=ScanConfig)
    tags: TagsConfig = Field(default_factory=TagsConfig)
    image: ImageConfig = Field(default_factory=ImageConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    webhooks: WebhooksConfig = Field(default_factory=WebhooksConfig)
    log_level: str = "INFO"


_config: AppConfig | None = None
_config_path: Path | None = None


def load_config(path: str | Path | None = None) -> AppConfig:
    global _config, _config_path
    resolved = Path(path or os.environ.get("CONFIG_PATH", "/config/config.yml"))
    _config_path = resolved
    data: dict = {}
    if resolved.exists():
        with open(resolved) as f:
            data = yaml.safe_load(f) or {}
    _config = AppConfig.model_validate(data)
    return _config


def get_config() -> AppConfig:
    if _config is None:
        return load_config()
    return _config


def save_config(new_yaml: str) -> AppConfig:
    """Validate and persist a new YAML config string, then hot-reload."""
    data = yaml.safe_load(new_yaml) or {}
    validated = AppConfig.model_validate(data)
    if _config_path:
        _config_path.parent.mkdir(parents=True, exist_ok=True)
        _config_path.write_text(new_yaml)
    global _config
    _config = validated
    return validated


def save_config_from_dict(data: dict) -> AppConfig:
    """Validate and persist config from a dict (structured settings API)."""
    validated = AppConfig.model_validate(data)
    as_yaml = yaml.dump(validated.model_dump(), default_flow_style=False, allow_unicode=True)
    if _config_path:
        _config_path.parent.mkdir(parents=True, exist_ok=True)
        _config_path.write_text(as_yaml)
    global _config
    _config = validated
    return validated


def save_auth(auth: AuthConfig) -> None:
    """Persist only the auth section (used by bootstrap and password change)."""
    cfg = get_config()
    cfg.auth = auth
    as_yaml = yaml.dump(cfg.model_dump(), default_flow_style=False, allow_unicode=True)
    if _config_path:
        _config_path.parent.mkdir(parents=True, exist_ok=True)
        _config_path.write_text(as_yaml)
    global _config
    _config = cfg


def config_as_yaml() -> str:
    if _config_path and _config_path.exists():
        return _config_path.read_text()
    return ""


def config_as_dict_safe() -> dict:
    """Return config as dict with password_hash stripped (safe for frontend)."""
    d = get_config().model_dump()
    d.get("auth", {}).pop("password_hash", None)
    return d
