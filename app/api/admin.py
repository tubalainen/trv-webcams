"""Admin page API: password login, global settings and deleting stored data."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, select

from app import cameras, cleanup, settings_store, storage, timelapse, weather
from app.api.routes import batch_dict, invalidate_usage
from app.config import get_settings
from app.db import Camera, Clip, session_scope

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin")

COOKIE = "trv_admin"
SESSION_SECONDS = 7 * 86400
FAILED_LOGIN_DELAY = 1.0


def _sign(expires: int) -> str:
    password = get_settings().admin_password
    key = hashlib.sha256((settings_store.admin_secret() + password).encode()).digest()
    return hmac.new(key, str(expires).encode(), hashlib.sha256).hexdigest()


def make_token(now: float | None = None) -> str:
    expires = int((now or time.time()) + SESSION_SECONDS)
    return f"{expires}.{_sign(expires)}"


def valid_token(token: str | None, now: float | None = None) -> bool:
    if not token or "." not in token or not get_settings().admin_password:
        return False
    exp_s, sig = token.split(".", 1)
    if not exp_s.isdigit() or int(exp_s) < (now or time.time()):
        return False
    return hmac.compare_digest(sig, _sign(int(exp_s)))


def require_admin(request: Request) -> None:
    if not get_settings().admin_password:
        raise HTTPException(403, "Admin är avstängt. Sätt ADMIN_PASSWORD i .env och starta om.")
    if not valid_token(request.cookies.get(COOKIE)):
        raise HTTPException(401, "Logga in som admin")


@router.get("/session")
def session(request: Request) -> dict:
    enabled = bool(get_settings().admin_password)
    return {"enabled": enabled, "logged_in": enabled and valid_token(request.cookies.get(COOKIE))}


class Login(BaseModel):
    password: str


@router.post("/login")
async def login(body: Login, request: Request, response: Response) -> dict:
    password = get_settings().admin_password
    if not password:
        raise HTTPException(403, "Admin är avstängt. Sätt ADMIN_PASSWORD i .env och starta om.")
    if not hmac.compare_digest(body.password.encode(), password.encode()):
        await asyncio.sleep(FAILED_LOGIN_DELAY)  # slows down guessing
        log.warning("Failed admin login from %s", request.client.host if request.client else "?")
        raise HTTPException(401, "Fel lösenord")
    response.set_cookie(
        COOKIE,
        make_token(),
        max_age=SESSION_SECONDS,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        path="/",
    )
    return {"logged_in": True}


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE, path="/")
    return {"logged_in": False}


def _settings_payload(request: Request) -> dict:
    settings = get_settings()
    env = request.app.state.env_defaults
    saved = settings_store.stored()
    with session_scope() as s:
        cams = list(s.scalars(select(Camera).where(Camera.enabled)))
        hd_enabled = sum(1 for c in cams if cameras.wants_fullsize(c, settings))
        checked = s.scalar(
            select(func.count()).select_from(Camera).where(Camera.fullsize_checked_at.is_not(None))
        )
        with_hd = s.scalar(select(func.count()).select_from(Camera).where(Camera.fullsize_width.is_not(None)))
    return {
        "groups": [{"key": k, "label": label} for k, label in settings_store.GROUPS],
        "fields": [
            {
                "key": f.key,
                "label": f.label,
                "group": f.group,
                "kind": f.kind,
                "help": f.help,
                "min": f.min,
                "max": f.max,
                "unit": f.unit,
                "choices": [{"value": v, "label": label} for v, label in f.choices],
                "value": getattr(settings, f.key),
                "env_default": env[f.key],
                "saved": f.key in saved,
            }
            for f in settings_store.FIELDS
        ],
        "info": {
            "cameras_enabled": len(cams),
            "cameras_enabled_hd": hd_enabled,
            "cameras_checked": checked or 0,
            "cameras_with_hd": with_hd or 0,
        },
    }


@router.get("/settings", dependencies=[Depends(require_admin)])
def get_admin_settings(request: Request) -> dict:
    return _settings_payload(request)


class SettingsUpdate(BaseModel):
    values: dict[str, Any] = {}
    reset: list[str] = []


@router.put("/settings", dependencies=[Depends(require_admin)])
async def put_admin_settings(body: SettingsUpdate, request: Request) -> dict:
    settings = get_settings()
    try:
        changed = settings_store.save(settings, body.values)
        changed |= settings_store.reset(settings, body.reset, request.app.state.env_defaults)
    except settings_store.SettingsError as e:
        raise HTTPException(400, str(e)) from None
    applied = await apply_changes(request.app, changed)
    payload = _settings_payload(request)
    payload["changed"] = sorted(changed)
    payload["applied"] = applied
    return payload


async def apply_changes(app, changed: dict[str, tuple[Any, Any]]) -> list[str]:
    """Make changed settings take effect without a restart. Returns short Swedish notes."""
    settings = get_settings()
    notes: list[str] = []
    sched = getattr(app.state, "scheduler", None)
    timing = {"collect_interval_minutes", "temp_collect_interval_minutes", "nightly_hour", "nightly_minute"}
    if sched is not None and timing & changed.keys():
        from app.scheduler import reschedule

        reschedule(sched, settings)
        notes.append("Schemat är uppdaterat.")
    jobs = app.state.jobs
    if "hwaccel" in changed:
        jobs.encoder.reset()
        choice = await asyncio.to_thread(lambda: jobs.encoder.choice)
        notes.append(f"Ny GPU-kontroll: {choice.codec} ({'CPU' if choice.mode == 'cpu' else 'GPU'}).")
    if "max_concurrent_jobs" in changed:
        jobs.resize(settings.max_concurrent_jobs)
        notes.append(f"{settings.max_concurrent_jobs} jobb kan köras samtidigt.")
    if "max_gpu_encodes" in changed:
        notes.append(f"Högst {settings.max_gpu_encodes} GPU-kodningar åt gången.")
    if "temp_station_max_km" in changed:
        linked = await asyncio.to_thread(weather.assign_stations, settings)
        notes.append(f"{linked} kameror har en vägväderstation inom {settings.temp_station_max_km:g} km.")
    if "trv_fullsize" in changed:
        notes.append("Gäller nya bilder från nästa hämtning.")
    return notes


# --- Storage and deletion -------------------------------------------------------------

WIPE_CONFIRMATION = "RENSA ALLT"


def _day_stats(root) -> dict[str, list[int]]:
    """{day: [files, bytes]} below one camera directory."""
    out: dict[str, list[int]] = {}
    if not root.is_dir():
        return out
    for d in root.iterdir():
        if not d.is_dir() or not storage.DAY_RE.match(d.name):
            continue
        files = size = 0
        for f in d.iterdir():
            try:
                st = f.stat()
            except OSError:
                continue
            files += 1
            if st.st_nlink <= 1 or root.parent.name == "snapshots":
                size += st.st_size
        out[d.name] = [files, size]
    return out


def storage_overview() -> dict:
    """Per camera: images per day (snapshots + archive-only bytes) and timelapses."""
    settings = get_settings()
    ids = cleanup.cameras_with_data(settings)
    with session_scope() as s:
        cams = {c.id: c for c in s.scalars(select(Camera).where(Camera.id.in_(ids)))}
        clips: dict[str, list[dict]] = {}
        for c in s.scalars(select(Clip)):
            clips.setdefault(c.camera_id, []).append(
                {"id": c.id, "bytes": c.mp4_bytes + c.gif_bytes, "created_at": c.created_at.isoformat() + "Z"}
            )
    rows = []
    for cid in ids:
        cam = cams.get(cid)
        snaps = _day_stats(settings.snapshot_dir / storage.safe_id(cid))
        arch = _day_stats(settings.archive_dir / storage.safe_id(cid))
        days = sorted(set(snaps) | set(arch))
        rows.append(
            {
                "id": cid,
                "name": cam.name if cam else cid,
                "enabled": bool(cam and cam.enabled),
                # [day, snapshot images, snapshot bytes, archive images, archive-only bytes]
                "days": [[d, *snaps.get(d, [0, 0]), *arch.get(d, [0, 0])] for d in days],
                "timelapses": clips.get(cid, []),
            }
        )
    rows.sort(key=lambda r: r["name"].lower())
    disk = os.statvfs(settings.data_dir)
    return {"cameras": rows, "disk_free_bytes": disk.f_bavail * disk.f_frsize}


@router.get("/storage", dependencies=[Depends(require_admin)])
async def get_storage() -> dict:
    return await asyncio.to_thread(storage_overview)


def _run(fn, *args, **kwargs) -> dict:
    try:
        result = fn(get_settings(), *args, **kwargs)
    except cleanup.CleanupError as e:
        raise HTTPException(400, str(e)) from None
    except cleanup.JobRunning as e:
        raise HTTPException(409, str(e)) from None
    invalidate_usage()
    return result.as_dict()


class Purge(BaseModel):
    before: str
    what: str = "all"  # images | timelapses | all
    camera_id: str | None = None


@router.post("/purge", dependencies=[Depends(require_admin)])
async def purge(body: Purge) -> dict:
    """Delete images and/or timelapses older than a date."""
    return await asyncio.to_thread(
        _run, cleanup.purge_older_than, body.before, body.what, camera_id=body.camera_id
    )


class DeleteImages(BaseModel):
    day: str | None = None
    days: list[str] | None = None
    before: str | None = None
    all: bool = False


@router.post("/cameras/{camera_id}/delete-images", dependencies=[Depends(require_admin)])
async def delete_camera_images(camera_id: str, body: DeleteImages) -> dict:
    return await asyncio.to_thread(
        _run,
        cleanup.delete_images,
        camera_id,
        day=body.day,
        days=body.days,
        before=body.before,
        everything=body.all,
    )


@router.get("/cameras/{camera_id}/archive", dependencies=[Depends(require_admin)])
def list_archive(camera_id: str, day: str) -> dict:
    """Hourly archive images of one day (for days without snapshots in the admin view)."""
    if not storage.DAY_RE.match(day):
        raise HTTPException(400, "Ogiltigt datum, använd ÅÅÅÅ-MM-DD")
    base = get_settings().archive_dir / storage.safe_id(camera_id) / day
    frames = (
        sorted(f.stem for f in base.iterdir() if f.suffix == ".jpg" and storage.IMAGE_NAME_RE.match(f.name))
        if base.is_dir()
        else []
    )
    return {"day": day, "frames": frames}


@router.get("/cameras/{camera_id}/archive/{day}/{name}", dependencies=[Depends(require_admin)])
def archive_image(camera_id: str, day: str, name: str):
    if not storage.DAY_RE.match(day) or not storage.IMAGE_NAME_RE.match(name):
        raise HTTPException(404)
    path = get_settings().archive_dir / storage.safe_id(camera_id) / day / name
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@router.delete("/cameras/{camera_id}/snapshots/{day}/{name}", dependencies=[Depends(require_admin)])
async def delete_snapshot(camera_id: str, day: str, name: str) -> dict:
    return await asyncio.to_thread(_run, cleanup.delete_images, camera_id, day=day, name=name)


@router.delete("/cameras/{camera_id}/timelapses", dependencies=[Depends(require_admin)])
async def delete_camera_timelapses(camera_id: str) -> dict:
    """Delete all library clips of a camera."""
    return await asyncio.to_thread(_run, cleanup.delete_timelapses, camera_id)


@router.delete("/clips/{clip_id}", dependencies=[Depends(require_admin)])
async def delete_clip(clip_id: int) -> dict:
    return await asyncio.to_thread(_run, cleanup.delete_timelapses, clip_id=clip_id)


class BatchRequest(BaseModel):
    scope: str  # county | all
    county_no: int | None = None
    start: datetime
    end: datetime
    formats: list[str] = ["mp4", "gif"]
    duration_s: int = timelapse.DEFAULT_DURATION
    temp_overlay: str = "default"


@router.post("/batches", status_code=202, dependencies=[Depends(require_admin)])
def create_batch(body: BatchRequest, request: Request) -> dict:
    """Generate one clip per enabled camera in a county or all of Sweden (admin only)."""
    try:
        req = timelapse.make_request(
            get_settings(), body.start, body.end, body.formats, body.duration_s, body.temp_overlay
        )
        batch, skipped = request.app.state.jobs.enqueue_batch(body.scope, body.county_no, req)
    except timelapse.RequestError as e:
        raise HTTPException(400, str(e)) from None
    data = batch_dict(batch)
    data["skipped_names"] = skipped[:50]
    return data


class Wipe(BaseModel):
    confirm: str


@router.post("/wipe", dependencies=[Depends(require_admin)])
async def wipe(body: Wipe, request: Request) -> dict:
    """Delete all images, timelapses, road temperatures and job history ("start over")."""
    if body.confirm.strip() != WIPE_CONFIRMATION:
        raise HTTPException(400, f'Skriv "{WIPE_CONFIRMATION}" för att bekräfta')
    try:
        result = await cleanup.wipe_all(get_settings(), request.app.state.jobs)
    except cleanup.JobRunning as e:
        raise HTTPException(409, str(e)) from None
    invalidate_usage()
    log.warning("All data wiped from the admin page by %s", request.client.host if request.client else "?")
    return result.as_dict()
