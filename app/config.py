from __future__ import annotations

import copy
import logging
import os
from pathlib import Path
from typing import Literal

import yaml
from PIL import ImageColor
from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

log = logging.getLogger(__name__)


class JellyfinConfig(BaseModel):
    url: str = "http://jellyfin:8096"
    api_key: str = ""
    library_ids: list[str] = Field(default_factory=list)


class ArrInstance(BaseModel):
    name: str
    url: str
    api_key: str = ""
    # Roadmap I9: a path to read api_key from instead (e.g. a Docker
    # file-secret). When set, the file supplies the key at load and api_key is
    # never written back to config.yml. The path itself is not a secret.
    api_key_file: str = ""

    @field_validator("api_key_file", mode="before")
    @classmethod
    def _blank_key_file(cls, v: object) -> object:
        # `api_key_file:` with nothing after it is YAML null: treat it as unset.
        if v is None:
            return ""
        return v.strip() if isinstance(v, str) else v


class SonarrConfig(BaseModel):
    instances: list[ArrInstance] = Field(default_factory=list)


class RadarrConfig(BaseModel):
    instances: list[ArrInstance] = Field(default_factory=list)


class ArrSyncConfig(BaseModel):
    # Roadmap B5. Until this existed nothing had ever been written to a
    # Sonarr/Radarr instance (the match key was one Jellyfin never sets), so
    # both switches below ship OFF and turning either on is a behaviour change
    # the operator makes on purpose, after reading the dry-run report.
    #
    #   "dry_run": match every item to its series/movie on each instance and
    #       record what WOULD be written. Nothing is sent to an *arr but GETs --
    #       the *arr clients are given a transport that refuses anything else.
    #   "live": write the managed tags (and create missing tag labels).
    mode: Literal["dry_run", "live"] = "dry_run"
    # Fill a blank Jellyfin rating from the matched series/movie's
    # certification. This writes to JELLYFIN (OfficialRating, and the rating
    # tag/badge wherever tags.destinations.rating sends them), not to an *arr.
    certification_fallback: bool = False


class DeletedItemsConfig(BaseModel):
    # Roadmap U2: when Jellyfin no longer has an item, its index row and the
    # managed tags on the Sonarr/Radarr object it owned (same folder) are stale.
    # Every scan works out what to remove; it ships as a report only.
    #
    #   "report": compute and report what would be removed, and change nothing.
    #       The index is opened read-only and every *arr client is given a
    #       transport that refuses anything but GET.
    #   "remove": delete the index rows, and strip the managed tags from the
    #       *arr objects -- the tags only while arr_sync.mode is "live" too.
    mode: Literal["report", "remove"] = "report"
    # Removal refuses when more than this fraction of the index would go. The
    # first pass over production faced 11.2% (1,187 of 10,585 rows, 2026-09-27),
    # a backlog nothing had ever cleared; losing a whole media mount would
    # exceed it. A deliberate mass deletion needs this raised for one scan.
    max_fraction: float = Field(default=0.15, gt=0, le=1)


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


BADGE_PALETTE_VERSION = 2

# The defaults shipped before palette 2 (roadmap B4/P10), per field. Only a
# value equal to its own field's old default is migrated.
_PALETTE_1_DEFAULTS = {
    "video_badge_color": "#134e4a",
    "audio_badge_color": "#1e3a8a",
    "sub_badge_color": "#7c2d12",
    "rating_badge_color": "#4c1d95",
}


# Validation-context key: set by the save paths so an unreadable colour is
# REFUSED there, while loading falls back to the field default (roadmap B6).
REFUSE_BAD_COLOURS = "refuse_bad_colours"

_COLOUR_FIELDS = (
    "badge_text_color",
    "video_badge_color",
    "audio_badge_color",
    "sub_badge_color",
    "rating_badge_color",
    "backup_video_badge_color",
    "backup_audio_badge_color",
    "backup_sub_badge_color",
    "backup_rating_badge_color",
)
_BARE_HEX6 = frozenset("0123456789abcdefABCDEF")


def normalize_color(value: object) -> str | None:
    """``value`` as lowercase ``#rrggbb``, or None if it is not a usable colour.

    Usable means Pillow's ``ImageColor.getrgb()`` reads it as RGB (``#fff``,
    ``red``, ``rgb(255,0,0)``, ...). A value Pillow reads WITH an alpha channel
    (``#rrggbbaa``, ``rgba(...)``) is refused rather than having its alpha
    dropped: opacity has its own field. One extra spelling is kept that Pillow
    rejects -- six bare hex digits (``203a30``) -- because the renderer drew it
    correctly before B6, so refusing it would change a working poster.
    """
    if not isinstance(value, str):
        return None
    if len(value) == 6 and set(value) <= _BARE_HEX6:
        return "#" + value.lower()
    try:
        rgb = ImageColor.getrgb(value)
    except ValueError:
        return None
    if len(rgb) != 3:
        return None
    return "#{:02x}{:02x}{:02x}".format(*rgb)


class ImageConfig(BaseModel):
    targets: list[str] = Field(default_factory=lambda: ["poster.jpg", "poster.png", "folder.jpg", "folder.png"])
    backup_suffix: str = ".orig"
    badge_position: str = "bottom-left"
    # Corner for the content-rating badge, independent of badge_position
    # (roadmap B10). Before this it was hardwired to top-left, so choosing
    # top-left for the tags drew them on top of it. If both share a corner they
    # stack, rating nearest the corner; see render_badge_groups().
    rating_position: Literal["top-left", "top-right", "bottom-left", "bottom-right"] = "top-left"

    @field_validator("rating_position", mode="before")
    @classmethod
    def _unknown_rating_position(cls, v: object) -> object:
        # A typo hand-edited into config.yml must not stop the app starting over
        # a cosmetic setting: fall back to the historical corner, and say so.
        corners = ("top-left", "top-right", "bottom-left", "bottom-right")
        if v not in corners:
            log.warning("image.rating_position %r is not one of %s; using top-left", v, corners)
            return "top-left"
        return v

    # Badge fill opacity. This is a CONTRAST control, not just a cosmetic one:
    # the pill is filled at this alpha, so anything below 1.0 lets the poster
    # show through and drops the rendered contrast of the label against the
    # fill. Measured over black/white/grey backdrops with the palette below
    # (scripts/measure_badge_contrast.py): 1.0 -> 7.48:1 worst case, 0.98 is
    # the floor for AAA, 0.80 the floor for AA -- on the poster as well as in
    # the probe since roadmap B21 (before it, the poster needed 0.99 / 0.87;
    # scripts/measure_pill_composite.py re-derives both). The palette-2 colours trade
    # headroom for separation -- under palette 1 these floors were 0.89/0.73 --
    # so there is almost no room below 1.0 before AAA goes. 0.65, the default
    # until roadmap B1, now renders 3.2:1 and fails both.
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
    #
    # Roadmap B4/P10: the categories must also be tellable APART, which is a
    # separate property from contrast -- the previous palette cleared AAA on
    # every badge while audio and rating were CIEDE2000 1.9 apart under
    # deuteranopia, i.e. the same colour. These are separated by lightness as
    # well as hue, so the difference survives without the red-green axis:
    # worst pair dE 12.3 across normal, protan, deutan and tritan vision.
    # Re-measure with scripts/measure_palette_separation.py.
    video_badge_color: str = "#203a30"  # deep forest  opaque 12.3:1
    audio_badge_color: str = "#312c4c"  # deep indigo  opaque 13.2:1
    sub_badge_color: str = "#50532f"  # dark olive     opaque  8.0:1
    rating_badge_color: str = "#73485b"  # muted plum  opaque  7.5:1

    # Roadmap P6: adapt badge colours to the poster. Off by default, and while
    # off nothing below is read. On, each badge row samples the poster under it
    # and, where that region would wash the label out (lighter than
    # overlay.ADAPT_LUMINANCE_THRESHOLD, for a light label), draws in the backup
    # palette instead. It only matters below badge_opacity 1.0: an opaque badge
    # hides the poster, so the region under it cannot change what renders.
    adapt_badge_colors: bool = False

    # The backup palette. Darker than the main one, and measured rather than
    # picked (scripts/measure_adaptive_palette.py): each holds WCAG AA (4.5:1)
    # on a WHITE poster at 65% opacity, keeps its category's hue within 30
    # degrees and the main palette's chroma, and stays at least CIEDE2000 5
    # (roadmap B4's bar) from every other category -- backup or main, since one
    # poster can show both -- under normal, protan, deutan and tritan vision.
    backup_video_badge_color: str = "#0c332d"
    backup_audio_badge_color: str = "#12061e"
    backup_sub_badge_color: str = "#332d0c"
    backup_rating_badge_color: str = "#210000"

    @field_validator(*_COLOUR_FIELDS, mode="before")
    @classmethod
    def _normalise_colour(cls, v: object, info: ValidationInfo) -> object:
        # Roadmap B6: anything but "#rrggbb" used to render BLACK, silently.
        # Loading must not stop the app over a cosmetic typo (the B10
        # precedent), so it falls back to the default and says so; a save is
        # refused instead, so the typo never reaches config.yml from the UI.
        hex_ = normalize_color(v)
        if hex_ is not None:
            return hex_
        name = f"image.{info.field_name}"
        if info.context and info.context.get(REFUSE_BAD_COLOURS):
            raise ValueError(
                f"{name} {v!r} is not a colour: use #rrggbb, #rgb, a colour name or rgb(r,g,b) -- no alpha"
            )
        default = cls.model_fields[info.field_name].default
        log.warning("%s %r is not a colour; using the default %s", name, v, default)
        return default

    # Which default palette this config has been migrated to. Persisted so the
    # migration below runs ONCE: without it, an operator who later picks one of
    # the old hexes on purpose would have it replaced on every load.
    badge_palette_version: int = BADGE_PALETTE_VERSION

    @model_validator(mode="before")
    @classmethod
    def _migrate_badge_palette(cls, data: object) -> object:
        """Move a colour still on its old shipped default to the new one.

        A colour the operator changed is left alone -- only a field whose value
        is exactly the previous default for THAT field moves. Configs saved
        before this existed carry no version and are treated as palette 1.
        """
        if not isinstance(data, dict):
            return data
        try:
            version = int(data.get("badge_palette_version", 1))
        except (TypeError, ValueError):
            version = 1
        if version >= BADGE_PALETTE_VERSION:
            return data
        data = dict(data)
        for field, old in _PALETTE_1_DEFAULTS.items():
            value = data.get(field)
            if isinstance(value, str) and value.strip().lower() == old:
                data[field] = cls.model_fields[field].default
        data["badge_palette_version"] = BADGE_PALETTE_VERSION
        return data

    # Show/hide categories on poster
    show_video_badges: bool = True
    show_audio_badges: bool = True
    show_sub_badges: bool = True
    show_rating_badge: bool = True

    # Roadmap P7: language codes whose audio/subtitle pills come first, in this
    # order; every other pill follows in its usual order. It reorders pills and
    # never hides one. Codes as the badges spell them (EN, JA, ...). Empty -- the
    # default -- renders exactly as before. Poster-only: tags are not affected,
    # so it is not in the tag-config hash and changing it forces no re-tag.
    prefer_languages: list[str] = Field(default_factory=list)

    @field_validator("prefer_languages", mode="before")
    @classmethod
    def _normalise_prefer_languages(cls, v: object) -> object:
        # A cosmetic setting must not stop the app starting (the B10 precedent):
        # a bare string is split, anything else unusable is dropped.
        if v is None:
            return []
        if isinstance(v, str):
            v = v.replace(",", " ").split()
        if not isinstance(v, list | tuple):
            log.warning("image.prefer_languages %r is not a list; ignoring it", v)
            return []
        out: list[str] = []
        for code in v:
            code = str(code).strip().upper()
            if code and code != "UND" and code not in out:
                out.append(code)
        return out

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
    arr_sync: ArrSyncConfig = Field(default_factory=ArrSyncConfig)
    deleted_items: DeletedItemsConfig = Field(default_factory=DeletedItemsConfig)
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
# instance Sonarr/Radarr keys live in a LIST, which a dotted path cannot
# address; each instance names its own key file instead (`api_key_file`,
# roadmap I9, below).

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


# ---------------------------------------------------------------------------
# Per-instance key files for Sonarr/Radarr (roadmap I9)
#
# The *arr keys live in a list, so the dotted-path table above cannot address
# them: an index detaches when an instance is reordered, a name when it is
# renamed. So the binding lives in the row itself -- `api_key_file` names the
# file, and wherever the row moves the file moves with it. The rest is I8's
# contract, unchanged: the file supplies the key on every load and every save,
# an unreadable or empty file is fatal, and `api_key` is never written back for
# a row that names a file. When a row carries both, the file wins -- it is the
# deliberate source.
# ---------------------------------------------------------------------------
_ARR_SECTIONS = ("sonarr", "radarr")

# (instance label, path) for every file-backed instance, as of the last load or
# save. Paths are not secrets; the values read from them never go in here.
_arr_key_files: list[tuple[str, str]] = []


def _raw_instances(data: dict):
    """Yield ``(label, instance dict)`` for every *arr instance in parsed ``data``."""
    for section in _ARR_SECTIONS:
        block = data.get(section)
        instances = block.get("instances") if isinstance(block, dict) else None
        if not isinstance(instances, list):
            continue
        for idx, inst in enumerate(instances):
            if isinstance(inst, dict):
                name = inst.get("name")
                label = f"{section} instance {name!r}" if name else f"{section} instance #{idx + 1} (unnamed)"
                yield label, inst


def _key_file(inst: dict) -> str:
    raw = inst.get("api_key_file")
    return raw.strip() if isinstance(raw, str) else ""


def _apply_arr_key_files(data: dict) -> dict:
    """
    Read each file-backed instance's key into ``data`` *in place*, and record
    which instances are file-backed. Raises ConfigError when a named file is
    unreadable or empty: naming a file is unambiguous intent, and carrying on
    with a stale key from config.yml is the silent failure I8 exists to prevent.
    """
    global _arr_key_files
    found: list[tuple[str, str]] = []
    for label, inst in _raw_instances(data):
        path = _key_file(inst)
        if not path:
            continue
        try:
            value = Path(path).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ConfigError(f"{label}: its api_key_file could not be read: {exc}") from exc
        if not value:
            raise ConfigError(f"{label}: its api_key_file {path} is empty")
        inst["api_key"] = value
        found.append((label, path))
    _arr_key_files = found
    return data


def _drop_file_backed_keys(data: dict) -> list[str]:
    """Remove ``api_key`` from every instance in ``data`` that names a key file.

    Returns the labels of the instances that had one to remove.
    """
    dropped = []
    for label, inst in _raw_instances(data):
        if _key_file(inst) and "api_key" in inst:
            del inst["api_key"]
            dropped.append(label)
    return dropped


def file_backed_instances() -> list[tuple[str, str]]:
    """``(instance label, path)`` for every *arr instance whose key comes from a file."""
    return list(_arr_key_files)


def _apply_external_secrets(data: dict) -> dict:
    """Both of the above: environment overrides, then per-instance key files."""
    return _apply_arr_key_files(_apply_env_overrides(data))


def log_env_overrides() -> None:
    """Log which secrets come from outside config.yml, by NAME only — never by value."""
    if not _env_overrides:
        log.info("No config fields overridden by the environment")
    for path, var in sorted(_env_overrides.items()):
        log.info("Config field %s supplied by %s — it will not be written to config.yml", path, var)
    if not _arr_key_files:
        log.info("No Sonarr/Radarr instance reads its API key from a file")
    for label, path in _arr_key_files:
        log.info("%s: API key read from %s — it will not be written to config.yml", label, path)


def _dump(data: dict) -> str:
    return yaml.dump(data, default_flow_style=False, allow_unicode=True)


def _persistable(cfg: AppConfig) -> dict:
    """``cfg`` as a dict with every environment- or file-supplied secret removed."""
    data = cfg.model_dump()
    for path in _env_overrides:
        _drop_nested(data, path)
    _drop_file_backed_keys(data)
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
    _config = AppConfig.model_validate(_apply_external_secrets(data))
    return _config


INITIAL_PASSWORD_FILE = "initial-password"  # noqa: S105 -- a filename, not a password


def initial_password_path() -> Path | None:
    """Where the first-run bootstrap writes a generated admin password (B16)."""
    return _config_path.parent / INITIAL_PASSWORD_FILE if _config_path else None


def get_config() -> AppConfig:
    if _config is None:
        return load_config()
    return _config


def save_config(new_yaml: str) -> AppConfig:
    """
    Validate and persist a new YAML config string, then hot-reload.

    The submitted text is written verbatim -- it is the raw editor, and the
    operator's formatting is theirs -- unless it carries a field the
    environment supplies, or an ``api_key`` beside an instance's
    ``api_key_file``. In that case the secret is dropped and the file is
    re-dumped, so a raw edit cannot reintroduce a secret the file did not
    supply. (This is also how a stale secret already sitting in config.yml gets
    purged: enable the override, save once.)
    """
    data = yaml.safe_load(new_yaml) or {}
    submitted = [path for path in ENV_OVERRIDABLE if _has_nested(data, path)]
    # Before the key files are read into `data`: which file-backed rows came
    # with an api_key of their own. The copy keeps the check off `data`.
    file_shadowed = _drop_file_backed_keys(copy.deepcopy(data))
    validated = AppConfig.model_validate(_apply_external_secrets(data), context={REFUSE_BAD_COLOURS: True})
    shadowed = [path for path in submitted if path in _env_overrides]
    for path in shadowed:
        log.warning(
            "Dropping %s from config.yml — it is supplied by %s and is not persisted here",
            path,
            _env_overrides[path],
        )
    for label in file_shadowed:
        log.warning("Dropping the api_key of %s from config.yml — it is read from its api_key_file", label)
    _write(_dump(_persistable(validated)) if shadowed or file_shadowed else new_yaml)
    global _config
    _config = validated
    return validated


def save_config_from_dict(data: dict) -> AppConfig:
    """Validate and persist config from a dict (structured settings API)."""
    validated = AppConfig.model_validate(_apply_external_secrets(data), context={REFUSE_BAD_COLOURS: True})
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
    if not _env_overrides and not _arr_key_files:
        return text
    # The file may still hold a stale value for an overridden field, or a stale
    # api_key beside an api_key_file; never hand that to the browser.
    # Re-dumping loses comments, but only in the case where the alternative is
    # leaking a secret the app no longer uses.
    data = yaml.safe_load(text) or {}
    # Materialise the list: _drop_nested has a side effect, so any() would
    # stop dropping at the first hit and leave later fields in the output.
    dropped = [_drop_nested(data, path) for path in _env_overrides]
    if not any(dropped) and not _drop_file_backed_keys(data):
        return text
    return _dump(data)


def config_as_dict_safe() -> dict:
    """
    Return config as a dict safe for the frontend: the password hash removed,
    every environment-supplied field blanked, and their paths listed under
    ``env_managed_fields`` so the UI can render them read-only. A file-backed
    *arr instance keeps its ``api_key_file`` and has its ``api_key`` blanked;
    the UI renders that row's key read-only.
    """
    d = get_config().model_dump()
    d.get("auth", {}).pop("password_hash", None)
    for path in _env_overrides:
        _set_nested(d, path, "")
    for _label, inst in _raw_instances(d):
        if _key_file(inst):
            inst["api_key"] = ""
    d["env_managed_fields"] = sorted(_env_overrides)
    return d
