"""Snapshot collection and camera metadata sync."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import select

from app import cameras, storage, weather
from app.config import Settings
from app.db import Camera, session_scope, utcnow
from app.trv_client import CameraInfo, TrvClient

log = logging.getLogger(__name__)

_collect_lock = asyncio.Lock()


def make_client(settings: Settings) -> TrvClient:
    return TrvClient(settings.trv_api_key, settings.trv_api_url, settings.http_timeout_seconds)


def _apply_info(cam: Camera, info: CameraInfo) -> None:
    cam.name = info.name
    cam.type = info.type
    cam.description = info.description
    cam.direction = info.direction
    cam.county_no = info.county_no
    cam.lat = info.lat
    cam.lon = info.lon
    cam.photo_url = info.photo_url
    cam.has_fullsize = info.has_fullsize
    cam.trv_active = info.active
    cam.updated_at = utcnow()


async def sync_metadata(settings: Settings, client: TrvClient | None = None) -> int:
    """Fetch all cameras from Trafikverket and upsert them. Returns number of cameras."""
    own = client is None
    client = client or make_client(settings)
    try:
        infos = await client.fetch_cameras()
    finally:
        if own:
            await client.aclose()
    default_ids = set(settings.default_enabled_ids)
    with session_scope() as s:
        existing = {c.id: c for c in s.scalars(select(Camera))}
        first_run = not existing
        for info in infos:
            cam = existing.get(info.id)
            if cam is None:
                cam = Camera(id=info.id, enabled=info.id in default_ids if first_run else False)
                s.add(cam)
            _apply_info(cam, info)
    log.info("Synced metadata for %d cameras", len(infos))
    return len(infos)


def fullsize_url(photo_url: str) -> str:
    sep = "&" if "?" in photo_url else "?"
    return f"{photo_url}{sep}type=fullsize"


def _image_url(info: CameraInfo, fullsize: bool) -> str:
    return fullsize_url(info.photo_url) if fullsize and info.has_fullsize else info.photo_url


async def collect_once(settings: Settings, client: TrvClient | None = None) -> int:
    """Download new snapshots for all enabled cameras. Returns number of new images."""
    if _collect_lock.locked():
        log.info("Collection already running, skipping")
        return 0
    async with _collect_lock:
        with session_scope() as s:
            enabled = list(s.scalars(select(Camera).where(Camera.enabled)))
            known = {c.id: c.last_photo_time for c in enabled}
            fullsize = {c.id: cameras.wants_fullsize(c, settings) for c in enabled}
        if not known:
            return 0
        own = client is None
        client = client or make_client(settings)
        try:
            infos = await client.fetch_cameras(list(known))
            todo = [
                i
                for i in infos
                if i.photo_url and i.photo_time and i.photo_time.isoformat() != known.get(i.id)
            ]
            sem = asyncio.Semaphore(settings.download_concurrency)

            async def fetch(info: CameraInfo):
                async with sem:
                    path = storage.snapshot_path(settings, info.id, info.photo_time)
                    if path.exists():
                        return info, path, False
                    try:
                        data = await client.download(_image_url(info, fullsize.get(info.id, False)))
                    except Exception as e:  # noqa: BLE001
                        log.warning("Download failed for %s: %s", info.id, e)
                        return info, None, False
                    if not data.startswith(b"\xff\xd8"):
                        log.warning("Not a JPEG for %s, skipping", info.id)
                        return info, None, False
                    await asyncio.to_thread(storage.write_atomic, path, data)
                    await asyncio.to_thread(
                        storage.archive_snapshot, settings, info.id, info.photo_time, path
                    )
                    return info, path, True

            results = await asyncio.gather(*(fetch(i) for i in todo))
        finally:
            if own:
                await client.aclose()

        new = 0
        with session_scope() as s:
            for info, path, created in results:
                if path is None:
                    continue
                cam = s.get(Camera, info.id)
                if cam is None:
                    continue
                cam.last_photo_time = info.photo_time.isoformat()
                cam.last_snapshot_path = str(path)
                if created:
                    cam.snapshot_count = (cam.snapshot_count or 0) + 1
                    new += 1
        log.info("Collected %d new snapshots from %d enabled cameras", new, len(known))
        return new


async def sync_weather_stations(settings: Settings, client: TrvClient | None = None) -> int:
    """Fetch road weather stations and link each camera to the nearest one."""
    own = client is None
    client = client or make_client(settings)
    try:
        return await weather.sync_stations(settings, client)
    finally:
        if own:
            await client.aclose()


async def collect_road_temps(settings: Settings, client: TrvClient | None = None) -> int:
    """Store new road temperatures for the stations of enabled cameras (7 days back for new ones)."""
    own = client is None
    client = client or make_client(settings)
    try:
        return await weather.collect_temps(settings, client)
    finally:
        if own:
            await client.aclose()


FULLSIZE_RECHECK_DAYS = 7


async def probe_fullsize(settings: Settings, client: TrvClient | None = None, force: bool = False) -> int:
    """Read the full size image dimensions of cameras not checked in the last week.

    Only the JPEG header is read (the image server ignores Range, so the stream is cut
    after a few kB). Returns the number of cameras checked.
    """
    cutoff = utcnow() - timedelta(days=FULLSIZE_RECHECK_DAYS)
    with session_scope() as s:
        stmt = select(Camera.id, Camera.photo_url).where(Camera.has_fullsize, Camera.photo_url != "")
        if not force:
            stmt = stmt.where((Camera.fullsize_checked_at.is_(None)) | (Camera.fullsize_checked_at < cutoff))
        todo = s.execute(stmt).all()
    if not todo:
        return 0
    own = client is None
    client = client or make_client(settings)
    sem = asyncio.Semaphore(8)

    async def probe(cid: str, url: str):
        async with sem:
            try:
                head = await client.read_head(fullsize_url(url))
            except Exception as e:  # noqa: BLE001
                log.debug("Full size check failed for %s: %s", cid, e)
                return cid, None
            return cid, cameras.jpeg_size(head)

    try:
        results = await asyncio.gather(*(probe(cid, url) for cid, url in todo))
    finally:
        if own:
            await client.aclose()
    now = utcnow()
    with session_scope() as s:
        for cid, size in results:
            cam = s.get(Camera, cid)
            if cam is None:
                continue
            cam.fullsize_width, cam.fullsize_height = size if size else (None, None)
            cam.fullsize_checked_at = now
    found = sum(1 for _, size in results if size)
    log.info("Full size images: checked %d cameras, %d have one", len(results), found)
    return len(results)
