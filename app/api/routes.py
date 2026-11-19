from __future__ import annotations

import asyncio
import re
import shutil
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import func, select

from app import __version__, collector, storage
from app.config import get_settings
from app.db import Camera, Job, RoadTemperature, Timelapse, WeatherStation, session_scope
from app.timelapse import PERIODS

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

# Approximate storage per enabled camera per day (normal size, every 5 min)
EST_BYTES_PER_IMAGE = 27_000


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
    }


def _job_dict(j: Job) -> dict:
    return {
        "id": j.id,
        "camera_id": j.camera_id,
        "periods": j.periods.split(","),
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


@router.get("/api/periods")
def periods() -> list[dict]:
    return [{"key": p.key, "label": p.label, "days": p.days} for p in PERIODS.values()]


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
            temp_overlay=cam.temp_overlay,
        )
        data["temp_station"] = _station_info(s, cam)
        data["temp_station_max_km"] = settings.temp_station_max_km
        tls = s.scalars(select(Timelapse).where(Timelapse.camera_id == camera_id)).all()
        data["timelapses"] = {}
        for t in tls:
            if not Path(t.path).exists():
                continue
            entry = data["timelapses"].setdefault(t.period, {})
            entry[t.kind] = {
                "url": f"/media/timelapses/{storage.safe_id(camera_id)}/{t.period}.{t.kind}"
                f"?v={int(t.created_at.timestamp())}",
                "frames": t.frames,
                "size_bytes": t.size_bytes,
                "encoder": t.encoder,
                "created_at": _iso(t.created_at),
                "range_start": _iso(t.range_start),
                "range_end": _iso(t.range_end),
                "temp_station": t.temp_station,
            }
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
    temp_overlay: bool | None = None


@router.patch("/api/cameras/{camera_id}")
def update_camera(camera_id: str, body: CameraUpdate, background: BackgroundTasks) -> dict:
    with session_scope() as s:
        cam = _get_camera(s, camera_id)
        if body.enabled is not None:
            cam.enabled = body.enabled
        if body.temp_overlay is not None:
            cam.temp_overlay = body.temp_overlay
        data = _camera_summary(cam)
        data["temp_overlay"] = cam.temp_overlay
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


_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HMS_RE = re.compile(r"^\d{6}$")


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


class GenerateRequest(BaseModel):
    periods: list[str] = []


@router.post("/api/cameras/{camera_id}/timelapses", status_code=202)
def generate(camera_id: str, body: GenerateRequest, request: Request) -> dict:
    with session_scope() as s:
        _get_camera(s, camera_id)
    bad = [p for p in body.periods if p not in PERIODS]
    if bad:
        raise HTTPException(400, f"Okänd period: {', '.join(bad)}")
    job = request.app.state.jobs.enqueue(camera_id, body.periods or list(PERIODS))
    return _job_dict(job)


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
        enabled = s.scalar(select(func.count()).select_from(Camera).where(Camera.enabled)) or 0
        queued = (
            s.scalar(select(func.count()).select_from(Job).where(Job.status.in_(["queued", "running"]))) or 0
        )
    usage = await asyncio.to_thread(_usage)
    disk = shutil.disk_usage(settings.data_dir)
    per_day = EST_BYTES_PER_IMAGE * (1440 // max(1, settings.collect_interval_minutes))
    est_total = enabled * (
        per_day * settings.snapshot_retention_days
        + EST_BYTES_PER_IMAGE * 24 * settings.archive_retention_days
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
    }


@router.get("/media/timelapses/{cam}/{name}")
def media(cam: str, name: str):
    settings = get_settings()
    period, _, kind = name.partition(".")
    if period not in PERIODS or kind not in ("mp4", "gif") or cam != storage.safe_id(cam):
        raise HTTPException(404)
    path = settings.timelapse_dir / cam / f"{period}.{kind}"
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="video/mp4" if kind == "mp4" else "image/gif")
