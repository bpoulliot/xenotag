from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import threading
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address

from .. import auth as _auth
from .. import deleted_items as _deleted_items
from ..arr_sync import load_report
from ..clients.jellyfin import JellyfinClient
from ..clients.radarr import RadarrClient
from ..clients.sonarr import SonarrClient
from ..config import (
    BADGE_PALETTE_VERSION,
    AppConfig,
    ImageConfig,
    config_as_dict_safe,
    config_as_yaml,
    get_config,
    initial_password_path,
    overridden_fields,
    save_auth,
    save_config,
    save_config_from_dict,
)
from ..contrast import badge_contrast
from ..overlay import clear_pill_cache, generate_preview_bytes
from ..pipeline import (
    _make_badge_groups,
    arr_dry_run_state,
    handle_webhook,
    progress,
    run_arr_dry_run_background,
    run_full_scan,
    run_incremental_scan,
)
from ..preview_samples import ensure_sample_posters
from ..scanner import AudioTrack, MediaInfo, SubTrack
from ..scheduler import next_run_time, reschedule
from ..state import (
    MediaState,
    clear_scan_errors,
    get_language_counts,
    get_last_scan,
    get_media_filtered,
    get_recent_scans,
    get_scan_errors,
    get_session,
    get_stats,
    purge_legacy_tags,
)
from .schemas import (
    ConfigResponse,
    ConfigSaveRequest,
    HealthResponse,
    MediaItem,
    ScanRunItem,
    ScanStatusResponse,
    StatsResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

_version_file = Path(__file__).parent.parent.parent / "VERSION"
_APP_VERSION = _version_file.read_text().strip() if _version_file.exists() else "dev"

SESSION_COOKIE = "xenotag_session"
_SECURE_COOKIE = os.environ.get("SECURE_COOKIES", "true").lower() not in ("false", "0", "no")
_limiter = Limiter(key_func=get_remote_address)

# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


def _current_user(request: Request) -> str | None:
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token:
        return None
    cfg = get_config()
    return _auth.get_session_user(token, cfg.auth.secret_key, cfg.auth.password_hash)


def _require_user(request: Request) -> str:
    user = _current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if _current_user(request):
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(request, "login.html", context={"error": None, "v": _APP_VERSION})


@router.post("/login")
@_limiter.limit("10/minute")
async def login(
    request: Request,
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
):
    cfg = get_config()
    # Always run verify_password to prevent username enumeration via timing side-channel
    pw_valid = _auth.verify_password(password, cfg.auth.password_hash)
    if pw_valid and username == cfg.auth.username:
        token = _auth.create_session(username, cfg.auth.secret_key, cfg.auth.password_hash)
        resp = RedirectResponse("/", status_code=302)
        resp.set_cookie(
            SESSION_COOKIE,
            token,
            httponly=True,
            samesite="lax",
            max_age=30 * 86400,
            secure=_SECURE_COOKIE,
        )
        return resp
    return templates.TemplateResponse(
        request, "login.html", context={"error": "Invalid username or password", "v": _APP_VERSION}, status_code=401
    )


@router.post("/logout")
async def logout(request: Request):
    token = request.cookies.get(SESSION_COOKIE, "")
    _auth.delete_session(token)
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


# ---------------------------------------------------------------------------
# HTML page
# ---------------------------------------------------------------------------


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    if not _current_user(request):
        return RedirectResponse("/login", status_code=302)
    # The colour inputs' initial values and JS fallbacks come from the model, so
    # the template can never drift from the shipped defaults again (roadmap B4:
    # the README had).
    return templates.TemplateResponse(
        request, "index.html", context={"v": _APP_VERSION, "badge_defaults": ImageConfig()}
    )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@router.get("/health", response_model=HealthResponse)
async def health(request: Request):
    if not _current_user(request):
        return HealthResponse(
            status="ok",
            jellyfin={"ok": False, "status": "unreachable", "message": "Not authenticated"},
            sonarr=[],
            radarr=[],
        )
    cfg = get_config()
    with JellyfinClient(cfg.jellyfin.url, cfg.jellyfin.api_key) as jf:
        jf_health = jf.health()
    sonarr_status = []
    for inst in cfg.sonarr.instances:
        with SonarrClient(inst.url, inst.api_key, inst.name) as sc:
            sonarr_status.append({"name": inst.name, **sc.health()})
    radarr_status = []
    for inst in cfg.radarr.instances:
        with RadarrClient(inst.url, inst.api_key, inst.name) as rc:
            radarr_status.append({"name": inst.name, **rc.health()})
    return HealthResponse(status="ok", jellyfin=jf_health, sonarr=sonarr_status, radarr=radarr_status)


# ---------------------------------------------------------------------------
# Stats + scan controls
# ---------------------------------------------------------------------------


@router.get("/stats", response_model=StatsResponse)
async def stats(request: Request):
    _require_user(request)
    session = get_session()
    try:
        s = get_stats(session)
        return StatsResponse(
            total_tagged=s["total_tagged"],
            images_modified=s["images_modified"],
            last_scan_at=s["last_scan_at"],
            last_scan_type=s["last_scan_type"],
            next_scan_at=next_run_time(),
        )
    finally:
        session.close()


@router.post("/scan/full")
async def trigger_full_scan(request: Request):
    _require_user(request)
    if progress.running:
        raise HTTPException(status_code=409, detail="Scan already in progress")
    cfg = get_config()
    threading.Thread(target=run_full_scan, args=(cfg,), daemon=True).start()
    return {"status": "started", "type": "full"}


@router.post("/scan/incremental")
async def trigger_incremental_scan(request: Request):
    _require_user(request)
    if progress.running:
        raise HTTPException(status_code=409, detail="Scan already in progress")
    cfg = get_config()
    threading.Thread(target=run_incremental_scan, args=(cfg,), daemon=True).start()
    return {"status": "started", "type": "incremental"}


@router.post("/scan/cancel")
async def cancel_scan(request: Request):
    _require_user(request)
    if not progress.running:
        raise HTTPException(status_code=409, detail="No scan in progress")
    progress.cancel()
    return {"status": "cancelling"}


@router.get("/scan/status", response_model=ScanStatusResponse)
async def scan_status(request: Request):
    _require_user(request)
    return ScanStatusResponse(
        running=progress.running,
        cancelled=progress.cancelled,
        total=progress.total,
        done=progress.done,
        current_item=progress.current_item,
        error=progress.error,
    )


@router.get("/scan/stream")
async def scan_stream(request: Request):
    _require_user(request)
    queue: asyncio.Queue[str] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def push(msg: str) -> None:
        asyncio.run_coroutine_threadsafe(queue.put(msg), loop)

    progress.subscribe(push)

    async def event_generator():
        try:
            for line in list(progress.log_lines):
                yield f"data: {json.dumps(line)}\n\n"
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=30)
                    yield f"data: {json.dumps(msg)}\n\n"
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            progress.unsubscribe(push)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Media browser
# ---------------------------------------------------------------------------


@router.get("/media")
async def media_list(
    request: Request,
    page: int = 1,
    per_page: int = 50,
    resolution: str = "",
    language: str = "",
):
    _require_user(request)
    session = get_session()
    try:
        total, items = get_media_filtered(
            session, resolution=resolution, language=language, page=page, per_page=per_page
        )
    finally:
        session.close()
    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "items": [MediaItem(**i) for i in items],
    }


# ---------------------------------------------------------------------------
# Scan errors
# ---------------------------------------------------------------------------


@router.get("/api/scan-errors")
async def scan_errors_list(request: Request):
    _require_user(request)
    session = get_session()
    try:
        return {"errors": get_scan_errors(session)}
    finally:
        session.close()


@router.delete("/api/scan-errors")
async def scan_errors_clear(request: Request):
    _require_user(request)
    session = get_session()
    try:
        clear_scan_errors(session)
    finally:
        session.close()
    return {"status": "cleared"}


@router.get("/api/legacy-tags")
async def legacy_tags_report(request: Request):
    """Dry run: what a legacy-prefix sweep would remove from the local index.

    Reports only; nothing is written. The outward tags on Jellyfin/*arr are
    already stripped by every scan that reaches an item -- this covers the rows
    a scan never reaches.
    """
    _require_user(request)
    cfg = get_config()
    session = get_session()
    try:
        return purge_legacy_tags(session, cfg.tags.legacy_prefixes, cfg.tags.managed_prefix, dry_run=True)
    finally:
        session.close()


@router.delete("/api/legacy-tags")
async def legacy_tags_purge(request: Request):
    """Apply the legacy-prefix sweep to the local index."""
    _require_user(request)
    cfg = get_config()
    session = get_session()
    try:
        return purge_legacy_tags(session, cfg.tags.legacy_prefixes, cfg.tags.managed_prefix)
    finally:
        session.close()


@router.get("/api/scan-runs", response_model=list[ScanRunItem])
async def scan_runs_list(request: Request):
    _require_user(request)
    session = get_session()
    try:
        return get_recent_scans(session)
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------

_WEBHOOK_SOURCES = {"sonarr", "radarr", "jellyfin"}
_SONARR_RADARR_EVENTS = {"Download", "Rename"}
_JELLYFIN_EVENTS = {"ItemAdded"}


@router.post("/webhook/{source}")
async def webhook(source: str, request: Request, background_tasks: BackgroundTasks):
    if source not in _WEBHOOK_SOURCES:
        raise HTTPException(status_code=400, detail=f"Unknown webhook source: {source}")

    cfg = get_config()
    body = await request.body()

    if cfg.webhooks.secret:
        token = request.query_params.get("token") or request.headers.get("X-Webhook-Token", "")
        if not hmac.compare_digest(token, cfg.webhooks.secret):
            raise HTTPException(status_code=403, detail="Invalid webhook token")

    try:
        payload = json.loads(body)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

    event_type = payload.get("eventType") or payload.get("NotificationType", "")

    if event_type == "Test":
        return {"status": "ok"}

    if source in ("sonarr", "radarr") and event_type not in _SONARR_RADARR_EVENTS:
        return {"status": "ignored", "eventType": event_type}
    if source == "jellyfin" and event_type not in _JELLYFIN_EVENTS:
        return {"status": "ignored", "eventType": event_type}

    if progress.running:
        logger.info("Webhook %s/%s skipped — scan already running", source, event_type)
        return {"status": "skipped", "reason": "scan running"}

    background_tasks.add_task(handle_webhook, cfg, source, payload)
    return {"status": "queued", "source": source, "eventType": event_type}


# ---------------------------------------------------------------------------
# Settings — structured JSON API
# ---------------------------------------------------------------------------


@router.get("/api/settings")
async def get_settings(request: Request):
    _require_user(request)
    return config_as_dict_safe()


@router.put("/api/settings")
async def save_settings(request: Request):
    _require_user(request)
    body = await request.json()
    # Preserve the existing password hash — frontend never sends it
    cfg = get_config()
    body.setdefault("auth", {})
    body["auth"]["password_hash"] = cfg.auth.password_hash
    try:
        validated = save_config_from_dict(body)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    reschedule(validated.scan.schedule, lambda: run_incremental_scan(get_config()))
    clear_pill_cache()
    return {"status": "saved"}


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@router.post("/api/auth/change-password")
async def change_password(request: Request, body: ChangePasswordRequest):
    _require_user(request)
    cfg = get_config()
    if not _auth.verify_password(body.current_password, cfg.auth.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if len(body.new_password) < 12:
        raise HTTPException(status_code=400, detail="Password must be at least 12 characters")
    cfg.auth.password_hash = _auth.hash_password(body.new_password)
    save_auth(cfg.auth)
    _auth.remove_initial_password(initial_password_path())
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Settings — advanced raw YAML
# ---------------------------------------------------------------------------


@router.get("/config", response_model=ConfigResponse)
async def get_config_yaml(request: Request):
    _require_user(request)
    return ConfigResponse(yaml=config_as_yaml())


@router.put("/config")
async def save_config_yaml(request: Request, body: ConfigSaveRequest):
    _require_user(request)
    try:
        validated = save_config(body.yaml)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    reschedule(validated.scan.schedule, lambda: run_incremental_scan(get_config()))
    clear_pill_cache()
    return {"status": "saved"}


# ---------------------------------------------------------------------------
# Library / root-folder discovery
# ---------------------------------------------------------------------------


@router.get("/api/jellyfin/libraries")
async def jellyfin_libraries(request: Request):
    _require_user(request)
    cfg = get_config()
    with JellyfinClient(cfg.jellyfin.url, cfg.jellyfin.api_key) as jf:
        try:
            libs = jf.get_libraries()
            return [
                {
                    "id": lib.get("ItemId", lib.get("Id", "")),
                    "name": lib.get("Name", ""),
                    "type": lib.get("CollectionType", ""),
                }
                for lib in libs
            ]
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc


class _ConnTestReq(BaseModel):
    url: str
    api_key: str


class _ArrTestReq(BaseModel):
    arr_type: str
    url: str
    api_key: str
    # Roadmap I9: a file-backed row sends its api_key_file, and its api_key blank.
    api_key_file: str = ""


def _arr_test_client(body: _ArrTestReq) -> SonarrClient | RadarrClient:
    """A client for a connection test, sending the key that will actually be used.

    For a file-backed row that is the key the configured instance read from the
    same file -- never a file named only by the request, so a test cannot be
    pointed at an arbitrary path the config does not already name.
    """
    if body.arr_type not in ("sonarr", "radarr"):
        raise HTTPException(status_code=400, detail="arr_type must be sonarr or radarr")
    api_key = body.api_key
    key_file = body.api_key_file.strip()
    if key_file:
        cfg = get_config()
        instances = cfg.sonarr.instances if body.arr_type == "sonarr" else cfg.radarr.instances
        inst = next((i for i in instances if i.api_key_file == key_file), None)
        if inst is None:
            raise HTTPException(
                status_code=400,
                detail="This instance's key is read from its api_key_file when the settings are saved — save first",
            )
        api_key = inst.api_key
    cls = SonarrClient if body.arr_type == "sonarr" else RadarrClient
    return cls(body.url, api_key, "test")


@router.post("/api/jellyfin/test")
async def jellyfin_test(request: Request, body: _ConnTestReq):
    """Test Jellyfin connectivity with provided credentials — does not save config."""
    _require_user(request)
    api_key = body.api_key
    if "jellyfin.api_key" in overridden_fields():
        # The field is read-only in the UI and a submitted value can never
        # become the active one, so test what will actually be used. Testing a
        # typed-in key here would report success for a key nothing ever sends.
        api_key = get_config().jellyfin.api_key
    libraries: list[dict] = []
    with JellyfinClient(body.url.rstrip("/"), api_key) as jf:
        h = jf.health()
        if h["ok"]:
            try:
                raw = jf.get_libraries()
                libraries = [
                    {
                        "id": lib.get("ItemId", lib.get("Id", "")),
                        "name": lib.get("Name", ""),
                        "type": lib.get("CollectionType", ""),
                    }
                    for lib in raw
                ]
            except Exception:
                logger.warning("Failed to fetch Jellyfin libraries for test endpoint", exc_info=True)
    return {"ok": h["ok"], "status": h["status"], "message": h["message"], "libraries": libraries}


@router.post("/api/arr/test")
async def arr_test(request: Request, body: _ArrTestReq):
    """Health-check an arr instance with provided credentials — does not save config."""
    _require_user(request)
    with _arr_test_client(body) as client:
        return client.health()


@router.post("/api/arr/rootfolders")
async def arr_rootfolders_test(request: Request, body: _ArrTestReq):
    """Fetch root folders from an arr instance with provided credentials — does not save config."""
    _require_user(request)
    with _arr_test_client(body) as client:
        try:
            folders = client._get("/rootfolder")
            return [{"path": f.get("path", ""), "freeSpace": f.get("freeSpace", 0)} for f in folders]
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/api/sonarr/{instance_name}/rootfolders")
async def sonarr_rootfolders(request: Request, instance_name: str):
    _require_user(request)
    cfg = get_config()
    inst = next((i for i in cfg.sonarr.instances if i.name == instance_name), None)
    if not inst:
        raise HTTPException(status_code=404, detail=f"Sonarr instance '{instance_name}' not found")
    with SonarrClient(inst.url, inst.api_key, inst.name) as client:
        try:
            folders = client._get("/rootfolder")
            return [{"path": f.get("path", ""), "freeSpace": f.get("freeSpace", 0)} for f in folders]
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/api/radarr/{instance_name}/rootfolders")
async def radarr_rootfolders(request: Request, instance_name: str):
    _require_user(request)
    cfg = get_config()
    inst = next((i for i in cfg.radarr.instances if i.name == instance_name), None)
    if not inst:
        raise HTTPException(status_code=404, detail=f"Radarr instance '{instance_name}' not found")
    with RadarrClient(inst.url, inst.api_key, inst.name) as client:
        try:
            folders = client._get("/rootfolder")
            return [{"path": f.get("path", ""), "freeSpace": f.get("freeSpace", 0)} for f in folders]
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Preview image endpoint
# ---------------------------------------------------------------------------

_PREVIEW_CACHE = Path(__file__).parent.parent / "static" / "preview_cache"


@router.get("/api/jellyfin/sample-items")
async def jellyfin_sample_items(request: Request, limit: int = 12):
    _require_user(request)
    cfg = get_config()
    with JellyfinClient(cfg.jellyfin.url, cfg.jellyfin.api_key) as jf:
        try:
            return jf.get_sample_items(limit)
        except Exception:
            return []


@router.get("/api/preview/sample-posters")
async def preview_sample_posters(request: Request, source: str = "synthetic"):
    """Return 4 poster sources for the preview grid.

    source=synthetic  — always return the 4 locally-generated test posters.
    source=jellyfin   — fetch a mix of Movies + Series from Jellyfin with Primary images.
                        Falls back to synthetics if Jellyfin is unreachable or has no items.
    """
    _require_user(request)

    if source == "jellyfin":
        try:
            cfg = get_config()
            with JellyfinClient(cfg.jellyfin.url, cfg.jellyfin.api_key) as jf:
                jf_items = jf.get_diverse_sample_items(8)
            if jf_items:
                return [
                    {"source": "jellyfin", "item_id": item["Id"], "name": item.get("Name", "")} for item in jf_items
                ]
        except Exception:  # noqa: S110
            pass
        # Fall through to synthetic on failure

    synthetics = ensure_sample_posters(_PREVIEW_CACHE)
    return [{"source": "synthetic", "sample": s["filename"], "name": s["label"]} for s in synthetics]


@router.get("/preview/image")
async def preview_image(
    request: Request,
    resolution: str = "1080p",
    video_codec: str = "",
    hdr_type: str = "",
    audio: str = "",
    subtitles: str = "",
    rating: str = "",
    position: str = "bottom-left",
    rating_position: str = "top-left",
    opacity: float = 1.0,
    badge_size: str = "tv",
    text_color: str = "#ffffff",
    video_color: str = "",
    audio_color: str = "",
    sub_color: str = "",
    rating_color: str = "",
    show_video: str = "true",
    show_audio: str = "true",
    show_subs: str = "true",
    show_rating: str = "true",
    prefer_languages: str = "",
    item_id: str = "",
    sample: str = "",
):
    _require_user(request)
    cfg_img = _image_config_from_params(
        position=position,
        rating_position=rating_position,
        opacity=opacity,
        badge_size=badge_size,
        text_color=text_color,
        video_color=video_color,
        audio_color=audio_color,
        sub_color=sub_color,
        rating_color=rating_color,
        show_video=show_video,
        show_audio=show_audio,
        show_subs=show_subs,
        show_rating=show_rating,
        prefer_languages=prefer_languages,
    )

    # The groups a scan would build for this sample (roadmap B14): the same
    # _make_badge_groups() call, never a second copy of the grouping. The
    # saved tags config comes along too (roadmap B23): a category whose
    # destinations drop "poster" must not show pills a scan never paints.
    info = _preview_media_info(resolution, video_codec, hdr_type, audio, subtitles)
    groups, rating_group = _make_badge_groups(info, rating or None, AppConfig(image=cfg_img, tags=get_config().tags))

    base_image_bytes: bytes | None = None
    if item_id:
        cfg = get_config()
        try:
            session = get_session()
            try:
                row = session.get(MediaState, f"jellyfin:{item_id}")
                if row and row.image_path:
                    orig_path = Path(row.image_path + cfg.image.backup_suffix)
                    if orig_path.exists() and orig_path.is_file():
                        base_image_bytes = orig_path.read_bytes()
            finally:
                session.close()
        except Exception:  # noqa: S110
            pass
        if base_image_bytes is None:
            try:
                with JellyfinClient(cfg.jellyfin.url, cfg.jellyfin.api_key) as jf:
                    r = jf._client.get(
                        f"{jf.base}/Items/{item_id}/Images/Primary",
                        timeout=10,
                    )
                    if r.status_code == 200:
                        base_image_bytes = r.content
            except Exception:  # noqa: S110
                pass
    elif sample:
        safe_name = Path(sample).name
        sample_path = _PREVIEW_CACHE / safe_name
        if sample_path.exists() and sample_path.is_file():
            base_image_bytes = sample_path.read_bytes()

    try:
        img_bytes = generate_preview_bytes(groups, rating_group, cfg_img, base_image_bytes=base_image_bytes)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return Response(content=img_bytes, media_type="image/jpeg")


_CORNERS = ("top-left", "top-right", "bottom-left", "bottom-right")


def _preview_media_info(resolution: str, video_codec: str, hdr_type: str, audio: str, subtitles: str) -> MediaInfo:
    """The ``MediaInfo`` a Preview sample profile describes (roadmap B14).

    The page sends one track per comma-separated "LANG CODEC" token
    ("EN DTS-HD,JA AAC", "EN PGS"); a token of one word is a codec with no
    language. Grouping, ordering and the rating prefix are left to
    ``pipeline._make_badge_groups()``, so the preview shows what a scan paints.
    """

    def tracks(spec: str) -> list[tuple[str, str]]:
        out = []
        for token in spec.split(","):
            words = token.split()
            if len(words) == 1:
                out.append(("UND", words[0]))
            elif words:
                out.append((words[0].upper(), " ".join(words[1:])))
        return out

    audio_tracks = [AudioTrack(lang, codec) for lang, codec in tracks(audio)]
    return MediaInfo(
        resolution=resolution,
        languages=list(dict.fromkeys(t.lang for t in audio_tracks if t.lang != "UND")),
        raw_audio_langs=[],
        video_codec=video_codec or None,
        hdr_type=hdr_type or None,
        audio_tracks=audio_tracks,
        subtitle_tracks=[SubTrack(lang, fmt, True) for lang, fmt in tracks(subtitles)],
    )


def _image_config_from_params(
    *,
    position: str = "bottom-left",
    rating_position: str = "top-left",
    opacity: float = 1.0,
    badge_size: str = "tv",
    text_color: str = "#ffffff",
    video_color: str = "",
    audio_color: str = "",
    sub_color: str = "",
    rating_color: str = "",
    show_video: str = "true",
    show_audio: str = "true",
    show_subs: str = "true",
    show_rating: str = "true",
    prefer_languages: str = "",
) -> ImageConfig:
    """The ImageConfig the Badge settings controls describe right now.

    Shared by the preview image and the contrast check, so the two can never
    be judging different badges.
    """
    default = ImageConfig()
    return ImageConfig(
        # These colours are what the operator is previewing right now, not a
        # legacy config: without the version, the palette migration would read
        # a deliberately chosen old default as unmigrated and swap it out.
        badge_palette_version=BADGE_PALETTE_VERSION,
        badge_position=position,
        # An unknown corner from the query string falls back rather than 422s,
        # the same way badge_size is clamped below.
        rating_position=rating_position if rating_position in _CORNERS else "top-left",
        badge_opacity=max(0.1, min(1.0, opacity)),
        badge_size=badge_size if badge_size in ("desktop", "tv", "tv_plus") else "tv",
        badge_text_color=text_color or "#ffffff",
        video_badge_color=video_color or default.video_badge_color,
        audio_badge_color=audio_color or default.audio_badge_color,
        sub_badge_color=sub_color or default.sub_badge_color,
        rating_badge_color=rating_color or default.rating_badge_color,
        show_video_badges=show_video.lower() not in ("false", "0"),
        show_audio_badges=show_audio.lower() not in ("false", "0"),
        show_sub_badges=show_subs.lower() not in ("false", "0"),
        show_rating_badge=show_rating.lower() not in ("false", "0"),
        prefer_languages=prefer_languages,
    )


@router.get("/api/badge-contrast")
async def badge_contrast_check(
    request: Request,
    opacity: float = 1.0,
    text_color: str = "#ffffff",
    video_color: str = "",
    audio_color: str = "",
    sub_color: str = "",
    rating_color: str = "",
    show_video: str = "true",
    show_audio: str = "true",
    show_subs: str = "true",
    show_rating: str = "true",
):
    """Rendered contrast of the badge colours being chosen (roadmap B2).

    Informational only -- nothing refuses a colour on the strength of this.
    Computed here rather than in the browser because the figure has to be the
    ratio of what RENDERS, and only the renderer knows that; a JavaScript copy
    of the compositing would be a second implementation free to drift, which
    is how B1 happened. Same code as `scripts/measure_badge_contrast.py`.
    """
    _require_user(request)
    cfg_img = _image_config_from_params(
        opacity=opacity,
        text_color=text_color,
        video_color=video_color,
        audio_color=audio_color,
        sub_color=sub_color,
        rating_color=rating_color,
        show_video=show_video,
        show_audio=show_audio,
        show_subs=show_subs,
        show_rating=show_rating,
    )
    return badge_contrast(cfg_img)


_language_cache: dict[str, object] = {"key": None, "value": None}


@router.get("/api/languages")
async def index_languages(request: Request):
    """Audio/subtitle language codes present in the index, most items first (roadmap P7).

    The pick-list for ``image.prefer_languages``. Read-only. The count walks
    every row's track JSON, so it is cached until the index changes: keyed on
    the latest scan and the row count, which a scan or a deletion moves.
    """
    _require_user(request)
    session = get_session()
    try:
        last = get_last_scan(session)
        key = (
            last.id if last else None,
            last.completed_at if last else None,
            session.query(MediaState).count(),
        )
        if _language_cache["key"] != key:
            counts = get_language_counts(session)
            _language_cache["value"] = [
                {"code": code, "items": n} for code, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
            ]
            _language_cache["key"] = key
    finally:
        session.close()
    return {"languages": _language_cache["value"], "preferred": get_config().image.prefer_languages}


# ---------------------------------------------------------------------------
# Sonarr/Radarr tag sync report (roadmap B5)
# ---------------------------------------------------------------------------


@router.get("/api/arr-sync/report")
async def arr_sync_report(request: Request):
    """The last *arr sync report -- from a scan, or from a dry run started below."""
    _require_user(request)
    cfg = get_config()
    return {
        "mode": cfg.arr_sync.mode,
        "certification_fallback": cfg.arr_sync.certification_fallback,
        "running": arr_dry_run_state["running"],
        "error": arr_dry_run_state["error"],
        "report": load_report(),
    }


@router.post("/api/arr-sync/dry-run")
async def arr_sync_dry_run(request: Request):
    """Match every item and count what a live sync would write. Sends nothing but GETs."""
    _require_user(request)
    if arr_dry_run_state["running"]:
        raise HTTPException(status_code=409, detail="A dry run is already running")
    arr_dry_run_state["running"] = True
    threading.Thread(target=run_arr_dry_run_background, args=(get_config(),), daemon=True).start()
    return {"status": "started"}


# ---------------------------------------------------------------------------
# Deleted items (roadmap U2)
# ---------------------------------------------------------------------------


@router.get("/api/deleted-items/report")
async def deleted_items_report(request: Request):
    """The last deleted-items report -- from a scan, or from a report started below."""
    _require_user(request)
    cfg = get_config()
    return {
        "mode": cfg.deleted_items.mode,
        "max_fraction": cfg.deleted_items.max_fraction,
        "arr_writes_live": cfg.arr_sync.mode == "live",
        "running": _deleted_items.state["running"],
        "error": _deleted_items.state["error"],
        "report": _deleted_items.load_report(),
    }


@router.post("/api/deleted-items/report")
async def deleted_items_run_report(request: Request):
    """Work out what the pass would remove. Always report-only, whatever deleted_items.mode says."""
    _require_user(request)
    if _deleted_items.state["running"]:
        raise HTTPException(status_code=409, detail="A deleted-items report is already running")
    _deleted_items.state["running"] = True
    threading.Thread(target=_deleted_items.run_report_background, args=(get_config(),), daemon=True).start()
    return {"status": "started"}
