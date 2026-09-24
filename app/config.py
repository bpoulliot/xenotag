from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

log = logging.getLogger(__name__)


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


class ConfigError(RuntimeError):
    """The environment describes a config value that could not be read."""


# ---------------------------------------------------------------------------
# Environment overrides for secrets
#
# config.yml is read-write application state: every save_* below rewrites the
# whole file. That makes it a hostile place for a secret rendered by an outside
# pipeline -- the next Settings save would clobber it. So instead of asking the
# renderer to own the file, we let it own individual FIELDS: the environment
# supplies the value at load time, and the save_* functions never write an
# environment-supplied field back.
#
# Each entry maps a dotted path into AppConfig to the environment variable that
# may supply it. Every variable also has a `<NAME>_FILE` twin holding a *path*
# to read the value from -- the convention the surrounding stack's SOPS + age
# pipeline renders into (Docker file-secrets, `*_FILE` in the compose file).
#
# Precedence, deliberately: `<NAME>_FILE` beats `<NAME>`. The file form is the
# one a secrets pipeline writes on purpose; a bare variable is the form that
# leaks in by accident, from a shared compose `env_file` or an inherited shell
# environment. When both are present the deliberate source wins.
# ---------------------------------------------------------------------------
ENV_OVERRIDABLE: dict[str, str] = {
    # The Jellyfin key is per-consumer by design: it is NOT the same key the
    # host's own scripts use. Same renderer, different key -- so one leak does
    # not force a stack-wide rotation.
    "jellyfin.api_key": "JELLYFIN_API_KEY",
    "auth.secret_key": "XENOTAG_SECRET_KEY",
    "webhooks.secret": "XENOTAG_WEBHOOK_SECRET",
}

# Not here, and deliberately: `auth.password_hash` is a verifier rather than a
# secret, and the first-run bootstrap has to be able to write it. The per-
# instance Sonarr/Radarr keys live in a LIST, so they need an addressing scheme
# a dotted path cannot express — roadmap I9.

# Dotted path -> the environment variable that supplied it, for the fields the
# environment is supplying right now. Refreshed on every load and every save.
_env_overrides: dict[str, str] = {}


def _read_env_value(var: str) -> tuple[str, str] | None:
    """
    Resolve one overridable field from the environment.

    Returns ``(value, source_variable_name)``, or ``None`` when the environment
    says nothing about this field.

    An override that is silently ignored is worse than no override at all -- it
    makes an operator believe a secret rotated when it did not. So:

    * ``<VAR>_FILE`` set but unreadable, or naming an empty file, is fatal.
      Naming a file is unambiguous intent; carrying on with the stale value in
      config.yml would be exactly the silent failure we are trying to prevent.
    * ``<VAR>`` set to an empty string is ignored with a WARNING. Empty is the
      signature of an unpopulated compose interpolation, not of intent, and
      blanking a working key on that basis would break the deployment.
    """
    file_var = f"{var}_FILE"
    raw_path = os.environ.get(file_var, "").strip()
    if raw_path:
        try:
            value = Path(raw_path).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ConfigError(f"{file_var} is set but its file could not be read: {exc}") from exc
        if not value:
            raise ConfigError(f"{file_var} is set but the file it names is empty")
        return value, file_var
    if file_var in os.environ:
        log.warning("%s is set but empty — ignoring it; unset it or point it at a secret file", file_var)

    raw = os.environ.get(var)
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        log.warning("%s is set but empty — ignoring it; the value in config.yml is still in use", var)
        return None
    return value, var


def _set_nested(data: dict, dotted: str, value: object) -> None:
    """Set ``dotted`` in ``data``, creating intermediate mappings as needed."""
    section, _, leaf = dotted.rpartition(".")
    node = data
    for part in filter(None, section.split(".")):
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[leaf] = value


def _has_nested(data: dict, dotted: str) -> bool:
    section, _, leaf = dotted.rpartition(".")
    node: object = data
    for part in filter(None, section.split(".")):
        if not isinstance(node, dict):
            return False
        node = node.get(part)
    return isinstance(node, dict) and leaf in node


def _drop_nested(data: dict, dotted: str) -> bool:
    """Remove ``dotted`` from ``data``. Returns True if something was removed."""
    section, _, leaf = dotted.rpartition(".")
    node: object = data
    for part in filter(None, section.split(".")):
        if not isinstance(node, dict):
            return False
        node = node.get(part)
    if isinstance(node, dict) and leaf in node:
        del node[leaf]
        return True
    return False


def _apply_env_overrides(data: dict) -> dict:
    """
    Overlay environment-supplied values onto parsed YAML ``data`` *in place*,
    and record which fields the environment supplied.

    Called on every load and every save, so a rotated secret file takes effect
    without a restart, and so a value typed into Settings can never displace an
    environment-supplied one in memory.
    """
    global _env_overrides
    resolved = {path: _read_env_value(var) for path, var in ENV_OVERRIDABLE.items()}
    _env_overrides = {path: found[1] for path, found in resolved.items() if found is not None}
    for path, found in resolved.items():
        if found is not None:
            _set_nested(data, path, found[0])
    return data


def overridden_fields() -> dict[str, str]:
    """Dotted config paths the environment is supplying -> the variable supplying each."""
    return dict(_env_overrides)


def log_env_overrides() -> None:
    """Log which fields the environment supplied, by NAME only — never by value."""
    if not _env_overrides:
        log.info("No config fields overridden by the environment — config.yml supplies all of them")
        return
    for path, var in sorted(_env_overrides.items()):
        log.info("Config field %s supplied by %s — it will not be written to config.yml", path, var)


def _dump(data: dict) -> str:
    return yaml.dump(data, default_flow_style=False, allow_unicode=True)


def _persistable(cfg: AppConfig) -> dict:
    """``cfg`` as a dict with every environment-supplied field removed."""
    data = cfg.model_dump()
    for path in _env_overrides:
        _drop_nested(data, path)
    return data


def _write(text: str) -> None:
    if _config_path:
        _config_path.parent.mkdir(parents=True, exist_ok=True)
        _config_path.write_text(text)


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
    _config = AppConfig.model_validate(_apply_env_overrides(data))
    return _config


def get_config() -> AppConfig:
    if _config is None:
        return load_config()
    return _config


def save_config(new_yaml: str) -> AppConfig:
    """
    Validate and persist a new YAML config string, then hot-reload.

    The submitted text is written verbatim -- it is the raw editor, and the
    operator's formatting is theirs -- unless it carries a field the
    environment supplies. In that case the field is dropped and the file is
    re-dumped, so a raw edit cannot reintroduce a secret the file did not
    supply. (This is also how a stale secret already sitting in config.yml gets
    purged: enable the override, save once.)
    """
    data = yaml.safe_load(new_yaml) or {}
    submitted = [path for path in ENV_OVERRIDABLE if _has_nested(data, path)]
    validated = AppConfig.model_validate(_apply_env_overrides(data))
    shadowed = [path for path in submitted if path in _env_overrides]
    for path in shadowed:
        log.warning(
            "Dropping %s from config.yml — it is supplied by %s and is not persisted here",
            path,
            _env_overrides[path],
        )
    _write(_dump(_persistable(validated)) if shadowed else new_yaml)
    global _config
    _config = validated
    return validated


def save_config_from_dict(data: dict) -> AppConfig:
    """Validate and persist config from a dict (structured settings API)."""
    validated = AppConfig.model_validate(_apply_env_overrides(data))
    _write(_dump(_persistable(validated)))
    global _config
    _config = validated
    return validated


def save_auth(auth: AuthConfig) -> None:
    """Persist only the auth section (used by bootstrap and password change)."""
    cfg = get_config()
    cfg.auth = auth
    _write(_dump(_persistable(cfg)))
    global _config
    _config = cfg


def config_as_yaml() -> str:
    if not (_config_path and _config_path.exists()):
        return ""
    text = _config_path.read_text()
    if not _env_overrides:
        return text
    # The file may still hold a stale value for an overridden field; never hand
    # that to the browser. Re-dumping loses comments, but only in the case
    # where the alternative is leaking a secret the app no longer uses.
    data = yaml.safe_load(text) or {}
    # Materialise the list: _drop_nested has a side effect, so any() would
    # stop dropping at the first hit and leave later fields in the output.
    dropped = [_drop_nested(data, path) for path in _env_overrides]
    if not any(dropped):
        return text
    return _dump(data)


def config_as_dict_safe() -> dict:
    """
    Return config as a dict safe for the frontend: the password hash removed,
    every environment-supplied field blanked, and their paths listed under
    ``env_managed_fields`` so the UI can render them read-only.
    """
    d = get_config().model_dump()
    d.get("auth", {}).pop("password_hash", None)
    for path in _env_overrides:
        _set_nested(d, path, "")
    d["env_managed_fields"] = sorted(_env_overrides)
    return d
