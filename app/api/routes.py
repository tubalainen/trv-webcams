from __future__ import annotations

import asyncio
import shutil
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import func, select

from app import __version__, cameras, collector, storage, timelapse
from app.config import get_settings
from app.db import Batch, Camera, Clip, Job, RoadTemperature, WeatherStation, session_scope

router = APIRouter()

COUNTIES = {
    1: "Stockholm",
    3: "Uppsala",
    4: "Södermanland",
    5: "Östergötland",
    6: "Jönköping",
    7: "Kronoberg",
    8: "Kalmar",
    9: "Gotland",
    10: "Blekinge",
    12: "Skåne",
    13: "Halland",
    14: "Västra Götaland",
    17: "Värmland",
    18: "Örebro",
    19: "Västmanland",
    20: "Dalarna",
    21: "Gävleborg",
    22: "Västernorrland",
    23: "Jämtland",
    24: "Västerbotten",
    25: "Norrbotten",
}


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() + "Z" if dt else None


def _camera_summary(c: Camera) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "type": c.type,
        "lat": c.lat,
        "lon": c.lon,
        "county_no": c.county_no,
        "county": COUNTIES.get(c.county_no or 0, ""),
        "enabled": c.enabled,
        "trv_active": c.trv_active,
        "hd": cameras.hd_label(c),
        "saves_hd": c.enabled and cameras.wants_fullsize(c, get_settings()),
    }


def _job_dict(j: Job) -> dict:
    return {
        "id": j.id,
        "camera_id": j.camera_id,
        "batch_id": j.batch_id,
        "range_start": _iso(j.range_start),
        "range_end": _iso(j.range_end),
        "formats": j.formats.split(",") if j.formats else [],
        "duration_s": j.duration_s,
        "temp_overlay": j.temp_overlay,
        "clip_id": j.clip_id,
        "trigger": j.trigger,
        "status": j.status,
        "message": j.message,
        "created_at": _iso(j.created_at),
        "started_at": _iso(j.started_at),
        "finished_at": _iso(j.finished_at),
    }


@router.get("/api/health")
def health() -> dict:
    return {"status": "ok", "version": __version__}


@router.get("/api/generate/options")
def generate_options() -> dict:
    """Choices for the generate dialog."""
    settings = get_settings()
    return {
        "presets": [{"key": p.key, "label": p.label, "days": p.days} for p in timelapse.PRESETS.values()],
        "durations": list(timelapse.DURATIONS),
        "default_duration": timelapse.DEFAULT_DURATION,
        "max_days": settings.archive_retention_days,
        "snapshot_days": settings.snapshot_retention_days,
        "max_batch_cameras": timelapse.MAX_BATCH_CAMERAS,
        "output_fps": settings.output_fps,
        "gif_fps": settings.gif_fps,
        "gif_max_frames": settings.gif_max_frames,
    }


@router.get("/api/counties")
def counties() -> dict:
    return {str(k): v for k, v in COUNTIES.items()}


@router.get("/api/cameras")
def list_cameras(q: str | None = None, county: int | None = None, enabled: bool | None = None) -> list[dict]:
    with session_scope() as s:
        stmt = select(Camera).order_by(Camera.name)
        if q:
            stmt = stmt.where(Camera.name.ilike(f"%{q}%"))
        if county is not None:
            stmt = stmt.where(Camera.county_no == county)
        if enabled is not None:
            stmt = stmt.where(Camera.enabled == enabled)
        return [_camera_summary(c) for c in s.scalars(stmt)]


def _get_camera(s, camera_id: str) -> Camera:
    cam = s.get(Camera, camera_id)
    if cam is None:
        raise HTTPException(404, "Kameran finns inte")
    return cam


@router.get("/api/cameras/{camera_id}")
def get_camera(camera_id: str) -> dict:
    settings = get_settings()
    with session_scope() as s:
        cam = _get_camera(s, camera_id)
        data = _camera_summary(cam)
        data.update(
            description=cam.description,
            direction=cam.direction,
            photo_url=cam.photo_url,
            last_photo_time=cam.last_photo_time,
            snapshot_count=cam.snapshot_count,
            temp_overlay_mode=cam.temp_overlay_mode,
            temp_overlay=cameras.wants_temp_overlay(cam, settings),
            temp_overlay_default=settings.temp_overlay_default,
            resolution_mode=cam.resolution_mode,
            resolution_default="high" if settings.trv_fullsize else "low",
            fullsize={
                "available": cameras.fullsize_available(cam),
                "width": cam.fullsize_width,
                "height": cam.fullsize_height,
                "checked_at": _iso(cam.fullsize_checked_at),
            },
            saves_fullsize=cameras.wants_fullsize(cam, settings),
        )
        data["temp_station"] = _station_info(s, cam)
        data["temp_station_max_km"] = settings.temp_station_max_km
        data["clip_count"] = s.scalar(
            select(func.count()).select_from(Clip).where(Clip.camera_id == camera_id)
        )
        job = s.scalars(select(Job).where(Job.camera_id == camera_id).order_by(Job.id.desc())).first()
        data["last_job"] = _job_dict(job) if job else None
    archive = settings.archive_dir / storage.safe_id(camera_id)
    data["archive_days"] = sum(1 for _ in archive.iterdir()) if archive.is_dir() else 0
    return data


def _station_info(s, cam: Camera) -> dict | None:
    """Linked road weather station and its latest stored road temperature."""
    if not cam.station_id:
        return None
    st = s.get(WeatherStation, cam.station_id)
    last = s.scalars(
        select(RoadTemperature)
        .where(RoadTemperature.station_id == cam.station_id)
        .order_by(RoadTemperature.sample.desc())
        .limit(1)
    ).first()
    return {
        "id": cam.station_id,
        "name": st.name if st else cam.station_id,
        "distance_km": cam.station_distance_km,
        "last_value": last.value if last else None,
        "last_sample": _iso(last.sample) if last else None,
    }


def _backfill_temps(background: BackgroundTasks) -> None:
    """Fetch road temperatures (7 days back) for newly enabled cameras after the response."""
    settings = get_settings()
    if settings.scheduler_enabled and settings.trv_api_key:
        background.add_task(collector.collect_road_temps, settings)


class CameraUpdate(BaseModel):
    enabled: bool | None = None
    temp_overlay_mode: str | None = None  # default | on | off
    resolution_mode: str | None = None  # default | high | low


@router.patch("/api/cameras/{camera_id}")
def update_camera(camera_id: str, body: CameraUpdate, background: BackgroundTasks) -> dict:
    with session_scope() as s:
        cam = _get_camera(s, camera_id)
        if body.enabled is not None:
            cam.enabled = body.enabled
        if body.temp_overlay_mode is not None:
            if body.temp_overlay_mode not in cameras.TEMP_MODES:
                raise HTTPException(400, "temp_overlay_mode ska vara default, on eller off")
            cam.temp_overlay_mode = body.temp_overlay_mode
        if body.resolution_mode is not None:
            if body.resolution_mode not in cameras.RESOLUTION_MODES:
                raise HTTPException(400, "resolution_mode ska vara default, high eller low")
            cam.resolution_mode = body.resolution_mode
        data = _camera_summary(cam)
        settings = get_settings()
        data.update(
            temp_overlay_mode=cam.temp_overlay_mode,
            temp_overlay=cameras.wants_temp_overlay(cam, settings),
            resolution_mode=cam.resolution_mode,
            saves_fullsize=cameras.wants_fullsize(cam, settings),
        )
    if body.enabled:
        _backfill_temps(background)
    return data


class BulkUpdate(BaseModel):
    ids: list[str] = []
    county: int | None = None
    enabled: bool


@router.post("/api/cameras/bulk")
def bulk_update(body: BulkUpdate, background: BackgroundTasks) -> dict:
    with session_scope() as s:
        stmt = select(Camera)
        if body.county is not None:
            stmt = stmt.where(Camera.county_no == body.county)
        elif body.ids:
            stmt = stmt.where(Camera.id.in_(body.ids))
        else:
            raise HTTPException(400, "Ange ids eller county")
        n = 0
        for cam in s.scalars(stmt):
            cam.enabled = body.enabled
            n += 1
    if body.enabled:
        _backfill_temps(background)
    return {"updated": n}


@router.get("/api/cameras/{camera_id}/latest.jpg")
def latest_image(camera_id: str):
    with session_scope() as s:
        cam = _get_camera(s, camera_id)
        path, url = cam.last_snapshot_path, cam.photo_url
    if path and Path(path).is_file():
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})
    if url:
        return RedirectResponse(url)
    raise HTTPException(404, "Ingen bild")


_DAY_RE = storage.DAY_RE
_HMS_RE = storage.HMS_RE


def _snapshot_days(base: Path) -> list[str]:
    if not base.is_dir():
        return []
    return sorted(d.name for d in base.iterdir() if d.is_dir() and _DAY_RE.match(d.name))


@router.get("/api/cameras/{camera_id}/snapshots")
def list_snapshots(camera_id: str, day: str | None = None) -> dict:
    """Saved images for one day (latest day by default), plus all days that have images.

    Times are local wall-clock time (settings.timezone) as HHMMSS, oldest first.
    """
    with session_scope() as s:
        _get_camera(s, camera_id)
    if day is not None and not _DAY_RE.match(day):
        raise HTTPException(400, "Ogiltigt datum, använd ÅÅÅÅ-MM-DD")
    base = get_settings().snapshot_dir / storage.safe_id(camera_id)
    days = _snapshot_days(base)
    if day is None:
        day = days[-1] if days else None
    frames: list[str] = []
    if day and (base / day).is_dir():
        frames = sorted(
            f.stem for f in (base / day).iterdir() if f.suffix == ".jpg" and _HMS_RE.match(f.stem)
        )
    return {"days": days, "day": day, "frames": frames}


@router.get("/api/cameras/{camera_id}/snapshots/{day}/{name}")
def snapshot_image(camera_id: str, day: str, name: str):
    hms, _, ext = name.partition(".")
    if not _DAY_RE.match(day) or not _HMS_RE.match(hms) or ext != "jpg":
        raise HTTPException(404)
    path = get_settings().snapshot_dir / storage.safe_id(camera_id) / day / f"{hms}.jpg"
    if not path.is_file():
        raise HTTPException(404, "Ingen bild")
    # Saved snapshots never change, so the browser may cache them
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


def clip_dict(c: Clip) -> dict:
    v = int(c.created_at.timestamp())
    return {
        "id": c.id,
        "camera_id": c.camera_id,
        "batch_id": c.batch_id,
        "range_start": _iso(c.range_start),
        "range_end": _iso(c.range_end),
        "duration_s": c.duration_s,
        "frames": c.frames,
        "encoder": c.encoder,
        "temp_station": c.temp_station,
        "created_at": _iso(c.created_at),
        "mp4": {"url": f"media/clips/{c.id}.mp4?v={v}", "bytes": c.mp4_bytes} if c.mp4_path else None,
        "gif": {"url": f"media/clips/{c.id}.gif?v={v}", "bytes": c.gif_bytes} if c.gif_path else None,
    }


class GenerateRequest(BaseModel):
    start: datetime
    end: datetime
    formats: list[str] = ["mp4", "gif"]
    duration_s: int = timelapse.DEFAULT_DURATION
    temp_overlay: str = "default"

    def to_request(self) -> timelapse.ClipRequest:
        try:
            return timelapse.make_request(
                get_settings(), self.start, self.end, self.formats, self.duration_s, self.temp_overlay
            )
        except timelapse.RequestError as e:
            raise HTTPException(400, str(e)) from None


@router.post("/api/cameras/{camera_id}/clips", status_code=202)
def generate_clip(camera_id: str, body: GenerateRequest, request: Request) -> dict:
    """Generate a timelapse for one camera (open to everyone)."""
    with session_scope() as s:
        _get_camera(s, camera_id)
    req = body.to_request()
    if not timelapse.has_images_in_range(get_settings(), camera_id, req.start, req.end):
        raise HTTPException(400, "Kameran har inga sparade bilder i tidsspannet")
    return _job_dict(request.app.state.jobs.enqueue_clip(camera_id, req))


@router.get("/api/cameras/{camera_id}/clips")
def list_clips(camera_id: str) -> list[dict]:
    with session_scope() as s:
        _get_camera(s, camera_id)
        rows = s.scalars(select(Clip).where(Clip.camera_id == camera_id).order_by(Clip.id.desc()))
        return [clip_dict(c) for c in rows]


@router.get("/api/cameras/{camera_id}/availability")
def camera_availability(camera_id: str) -> dict:
    """Days with images, for the calendar in the generate dialog."""
    with session_scope() as s:
        _get_camera(s, camera_id)
    return timelapse.available_days(get_settings(), [camera_id])


@router.get("/api/availability")
def availability(county: int | None = None) -> dict:
    """Days with images for the enabled cameras in a county (or all of Sweden)."""
    with session_scope() as s:
        stmt = select(Camera.id).where(Camera.enabled)
        if county is not None:
            stmt = stmt.where(Camera.county_no == county)
        ids = list(s.scalars(stmt))
    data = timelapse.available_days(get_settings(), ids)
    data["cameras"] = len(ids)
    return data


def batch_dict(b: Batch) -> dict:
    with session_scope() as s:
        counts = dict(
            s.execute(select(Job.status, func.count()).where(Job.batch_id == b.id).group_by(Job.status)).all()
        )
    return {
        "id": b.id,
        "scope": b.scope,
        "county_no": b.county_no,
        "county": COUNTIES.get(b.county_no or 0, "") if b.scope == "county" else "Hela Sverige",
        "range_start": _iso(b.range_start),
        "range_end": _iso(b.range_end),
        "formats": b.formats.split(","),
        "duration_s": b.duration_s,
        "total": b.total,
        "skipped_cameras": b.skipped,
        "status": {k: counts.get(k, 0) for k in ("queued", "running", "done", "failed", "skipped")},
        "finished": counts.get("queued", 0) + counts.get("running", 0) == 0,
        "created_at": _iso(b.created_at),
    }


@router.get("/api/batches/{batch_id}")
def get_batch(batch_id: int) -> dict:
    with session_scope() as s:
        b = s.get(Batch, batch_id)
        if b is None:
            raise HTTPException(404, "Körningen finns inte")
    return batch_dict(b)


@router.get("/media/clips/{name}")
def clip_media(name: str):
    clip_id, _, kind = name.partition(".")
    if not clip_id.isdigit() or kind not in ("mp4", "gif"):
        raise HTTPException(404)
    with session_scope() as s:
        c = s.get(Clip, int(clip_id))
        path = (
            storage.clip_file(get_settings(), c.camera_id, c.mp4_path if kind == "mp4" else c.gif_path)
            if c
            else None
        )
    if path is None or not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="video/mp4" if kind == "mp4" else "image/gif")


@router.get("/api/jobs/{job_id}")
def get_job(job_id: int) -> dict:
    with session_scope() as s:
        job = s.get(Job, job_id)
        if job is None:
            raise HTTPException(404, "Jobbet finns inte")
        return _job_dict(job)


@router.get("/api/jobs")
def list_jobs(limit: int = 50) -> list[dict]:
    with session_scope() as s:
        return [_job_dict(j) for j in s.scalars(select(Job).order_by(Job.id.desc()).limit(limit))]


@router.post("/api/collect", status_code=202)
async def trigger_collect() -> dict:
    asyncio.create_task(collector.collect_once(get_settings()))
    return {"status": "started"}


@router.post("/api/sync", status_code=202)
async def trigger_sync() -> dict:
    asyncio.create_task(collector.sync_metadata(get_settings()))
    return {"status": "started"}


_usage_cache: dict = {"t": 0.0, "data": None}


def invalidate_usage() -> None:
    """Recompute disk usage on the next status request (after deleting data)."""
    _usage_cache["data"] = None


def _usage() -> dict:
    settings = get_settings()
    if _usage_cache["data"] is None or time.time() - _usage_cache["t"] > 600:
        _usage_cache["data"] = {
            "snapshots_bytes": storage.dir_size(settings.snapshot_dir),
            "timelapses_bytes": storage.dir_size(settings.timelapse_dir),
        }
        _usage_cache["t"] = time.time()
    return _usage_cache["data"]


@router.get("/api/status")
async def status(request: Request) -> dict:
    settings = get_settings()
    with session_scope() as s:
        total = s.scalar(select(func.count()).select_from(Camera)) or 0
        enabled_cams = list(s.scalars(select(Camera).where(Camera.enabled)))
        enabled = len(enabled_cams)
        image_bytes = [
            cameras.BYTES_HIGH if cameras.wants_fullsize(c, settings) else cameras.BYTES_LOW
            for c in enabled_cams
        ]
        queued = (
            s.scalar(select(func.count()).select_from(Job).where(Job.status.in_(["queued", "running"]))) or 0
        )
    usage = await asyncio.to_thread(_usage)
    disk = shutil.disk_usage(settings.data_dir)
    per_day_images = 1440 // max(1, settings.collect_interval_minutes)
    est_total = sum(
        b * (per_day_images * settings.snapshot_retention_days + 24 * settings.archive_retention_days)
        for b in image_bytes
    )
    sched = getattr(request.app.state, "scheduler", None)
    next_runs = {}
    if sched is not None:
        for job in sched.get_jobs():
            nrt = getattr(job, "next_run_time", None)
            next_runs[job.id] = nrt.isoformat() if nrt else None
    encoder = request.app.state.jobs.encoder
    enc = encoder._choice
    return {
        "version": __version__,
        "cameras_total": total,
        "cameras_enabled": enabled,
        "jobs_pending": queued,
        "encoder": {"mode": enc.mode, "codec": enc.codec} if enc else None,
        "gpus": encoder.gpus or [],
        "usage": usage,
        "disk_free_bytes": disk.free,
        "disk_total_bytes": disk.total,
        "estimated_bytes_at_retention": est_total,
        "retention_days": settings.snapshot_retention_days,
        "archive_retention_days": settings.archive_retention_days,
        "next_runs": next_runs,
        "api_key_configured": bool(settings.trv_api_key),
        "admin_enabled": bool(settings.admin_password),
    }
