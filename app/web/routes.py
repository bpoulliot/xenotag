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
from ..clients.jellyfin import JellyfinClient
from ..clients.radarr import RadarrClient
from ..clients.sonarr import SonarrClient
from ..config import (
    config_as_dict_safe,
    config_as_yaml,
    get_config,
    save_auth,
    save_config,
    save_config_from_dict,
)
from ..overlay import BadgeGroup, clear_pill_cache, generate_preview_bytes
from ..pipeline import handle_webhook, progress, run_full_scan, run_incremental_scan
from ..preview_samples import ensure_sample_posters
from ..scheduler import next_run_time, reschedule
from ..state import (
    MediaState,
    clear_scan_errors,
    get_media_filtered,
    get_recent_scans,
    get_scan_errors,
    get_session,
    get_stats,
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
    return templates.TemplateResponse(request, "index.html", context={"v": _APP_VERSION})


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


@router.post("/api/jellyfin/test")
async def jellyfin_test(request: Request, body: _ConnTestReq):
    """Test Jellyfin connectivity with provided credentials — does not save config."""
    _require_user(request)
    libraries: list[dict] = []
    with JellyfinClient(body.url.rstrip("/"), body.api_key) as jf:
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
    if body.arr_type == "sonarr":
        client: SonarrClient | RadarrClient = SonarrClient(body.url, body.api_key, "test")
    elif body.arr_type == "radarr":
        client = RadarrClient(body.url, body.api_key, "test")
    else:
        raise HTTPException(status_code=400, detail="arr_type must be sonarr or radarr")
    with client:
        return client.health()


@router.post("/api/arr/rootfolders")
async def arr_rootfolders_test(request: Request, body: _ArrTestReq):
    """Fetch root folders from an arr instance with provided credentials — does not save config."""
    _require_user(request)
    if body.arr_type == "sonarr":
        client: SonarrClient | RadarrClient = SonarrClient(body.url, body.api_key, "test")
    elif body.arr_type == "radarr":
        client = RadarrClient(body.url, body.api_key, "test")
    else:
        raise HTTPException(status_code=400, detail="arr_type must be sonarr or radarr")
    with client:
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
    opacity: float = 1.0,
    badge_size: str = "tv",
    text_color: str = "#ffffff",
    video_color: str = "#134e4a",
    audio_color: str = "#1e3a8a",
    sub_color: str = "#7c2d12",
    rating_color: str = "#4c1d95",
    show_video: str = "true",
    show_audio: str = "true",
    show_subs: str = "true",
    show_rating: str = "true",
    item_id: str = "",
    sample: str = "",
):
    _require_user(request)
    from ..config import ImageConfig

    cfg_img = ImageConfig(
        badge_position=position,
        badge_opacity=max(0.1, min(1.0, opacity)),
        badge_size=badge_size if badge_size in ("desktop", "tv", "tv_plus") else "tv",
        badge_text_color=text_color or "#ffffff",
        video_badge_color=video_color or "#134e4a",
        audio_badge_color=audio_color or "#1e3a8a",
        sub_badge_color=sub_color or "#7c2d12",
        rating_badge_color=rating_color or "#4c1d95",
        show_video_badges=show_video.lower() not in ("false", "0"),
        show_audio_badges=show_audio.lower() not in ("false", "0"),
        show_sub_badges=show_subs.lower() not in ("false", "0"),
        show_rating_badge=show_rating.lower() not in ("false", "0"),
    )

    # Build badge groups from preview params
    groups: list[BadgeGroup] = []

    if cfg_img.show_video_badges:
        video_labels = [x for x in [resolution, video_codec, hdr_type] if x]
        if video_labels:
            groups.append(BadgeGroup(video_labels, cfg_img.video_badge_color, cfg_img.badge_text_color))

    if cfg_img.show_audio_badges:
        audio_labels = [a.strip() for a in audio.split(",") if a.strip()]
        if audio_labels:
            groups.append(BadgeGroup(audio_labels, cfg_img.audio_badge_color, cfg_img.badge_text_color))

    if cfg_img.show_sub_badges:
        sub_labels = [s.strip() for s in subtitles.split(",") if s.strip()]
        if sub_labels:
            groups.append(BadgeGroup(sub_labels, cfg_img.sub_badge_color, cfg_img.badge_text_color))

    rating_group = None
    if cfg_img.show_rating_badge and rating:
        rating_group = BadgeGroup([rating], cfg_img.rating_badge_color, cfg_img.badge_text_color)

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
