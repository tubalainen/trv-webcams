"""Deleting stored data: images, timelapses, or everything ("start over").

Snapshot days are removed from both snapshots/ and archive/. The archive holds hard
links to snapshots, so space is only freed when both links are gone.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import delete, select, update

from app import storage
from app.config import Settings
from app.db import Batch, Camera, Clip, Job, RoadTemperature, session_scope, utcnow

log = logging.getLogger(__name__)


class CleanupError(ValueError):
    """Invalid request (bad date, unknown camera, ...)."""


class JobRunning(RuntimeError):
    """A timelapse job is running for the camera, so its files must not be touched."""


@dataclass
class Result:
    files: int = 0
    bytes: int = 0
    timelapses: int = 0
    cameras: set[str] = field(default_factory=set)
    # (device, inode) of removed snapshot files, so their archive hard link is not counted again
    seen: set[tuple[int, int]] = field(default_factory=set, repr=False)

    def add(self, other: Result) -> Result:
        self.files += other.files
        self.bytes += other.bytes
        self.timelapses += other.timelapses
        self.cameras |= other.cameras
        self.seen |= other.seen
        return self

    def as_dict(self) -> dict:
        return {
            "files": self.files,
            "bytes": self.bytes,
            "timelapses": self.timelapses,
            "cameras": len(self.cameras),
        }


def parse_day(value: str, *, allow_future: bool = False) -> date:
    if not isinstance(value, str) or not storage.DAY_RE.match(value):
        raise CleanupError("Ogiltigt datum, använd ÅÅÅÅ-MM-DD")
    try:
        d = date.fromisoformat(value)
    except ValueError:
        raise CleanupError("Ogiltigt datum") from None
    if not allow_future and d > date.today():
        raise CleanupError("Datumet ligger i framtiden")
    return d


def _ensure_idle(camera_id: str | None = None) -> None:
    with session_scope() as s:
        stmt = select(Job.camera_id).where(Job.status == "running")
        if camera_id is not None:
            stmt = stmt.where(Job.camera_id == camera_id)
        busy = s.scalars(stmt).first()
    if busy is not None:
        raise JobRunning("Ett timelapse-jobb pågår. Vänta tills det är klart och försök igen.")


def _camera_exists(camera_id: str) -> None:
    with session_scope() as s:
        if s.get(Camera, camera_id) is None:
            raise CleanupError("Kameran finns inte")


def _remove_tree(path: Path, result: Result) -> None:
    """Remove a directory, counting images and the bytes actually freed.

    Remove snapshots before the archive: an archive file that is a hard link to a
    snapshot removed earlier is the same image, so it is not counted twice, but its
    bytes are counted when its last link goes.
    """
    if not path.exists():
        return
    for dirpath, _dirs, files in os.walk(path):
        for f in files:
            try:
                st = os.stat(os.path.join(dirpath, f))
            except OSError:
                continue
            key = (st.st_dev, st.st_ino)
            if key not in result.seen:
                result.files += 1
                result.seen.add(key)
            if st.st_nlink <= 1:
                result.bytes += st.st_size
    shutil.rmtree(path, ignore_errors=True)


def _remove_file(path: Path, result: Result) -> None:
    try:
        st = path.stat()
        path.unlink()
    except FileNotFoundError:
        return
    result.files += 1
    result.seen.add((st.st_dev, st.st_ino))
    if st.st_nlink <= 1:
        result.bytes += st.st_size


def _drop_empty(path: Path) -> None:
    if path.is_dir() and not any(path.iterdir()):
        path.rmdir()


def _refresh_camera(settings: Settings, camera_id: str) -> None:
    """Recount images and point last_snapshot_path at the newest image that is left."""
    base = settings.snapshot_dir / storage.safe_id(camera_id)
    images = sorted(base.glob("*/*.jpg")) if base.is_dir() else []
    with session_scope() as s:
        cam = s.get(Camera, camera_id)
        if cam is None:
            return
        cam.snapshot_count = len(images)
        if cam.last_snapshot_path and not Path(cam.last_snapshot_path).is_file():
            cam.last_snapshot_path = str(images[-1]) if images else None


def delete_images(
    settings: Settings,
    camera_id: str,
    *,
    day: str | None = None,
    before: str | None = None,
    name: str | None = None,
    everything: bool = False,
) -> Result:
    """Delete one image (day+name), one day, all days before a date, or everything for a camera."""
    modes = [name is not None, before is not None, everything, day is not None and name is None]
    if sum(modes) != 1:
        raise CleanupError("Ange en bild, en dag, ett datum (äldre än) eller alla")
    _camera_exists(camera_id)
    _ensure_idle(camera_id)
    cam_dir = storage.safe_id(camera_id)
    snap_root, arch_root = settings.snapshot_dir / cam_dir, settings.archive_dir / cam_dir
    result = Result()

    if name is not None:
        if day is None or not storage.DAY_RE.match(day) or not storage.IMAGE_NAME_RE.match(name):
            raise CleanupError("Ogiltig bild")
        snap = snap_root / day / name
        if not snap.is_file():
            raise CleanupError("Bilden finns inte")
        # The archive keeps the first image of each hour as a hard link to the snapshot
        arch = arch_root / day / f"{name[:2]}.jpg"
        if arch.is_file() and os.path.samefile(arch, snap):
            arch.unlink()  # same image as the snapshot, counted below when its last link goes
            _drop_empty(arch_root / day)
        _remove_file(snap, result)
        _drop_empty(snap_root / day)
    else:
        cutoff = parse_day(before) if before is not None else None
        if day is not None:
            parse_day(day, allow_future=True)
        # Snapshots first: the archive then holds the last link and frees the space
        for root in (snap_root, arch_root):
            if not root.is_dir():
                continue
            for d in sorted(root.iterdir()):
                if not d.is_dir() or not storage.DAY_RE.match(d.name):
                    continue
                if everything or d.name == day or (cutoff and date.fromisoformat(d.name) < cutoff):
                    _remove_tree(d, result)
    for root in (snap_root, arch_root):
        _drop_empty(root)
    if result.files:
        result.cameras.add(camera_id)
    _refresh_camera(settings, camera_id)
    log.info("Deleted %d images (%d bytes) for %s", result.files, result.bytes, camera_id)
    return result


def delete_timelapses(
    settings: Settings,
    camera_id: str | None = None,
    *,
    clip_id: int | None = None,
    before: str | None = None,
) -> Result:
    """Delete library clips (MP4 and GIF): one clip, all clips of a camera or of all
    cameras, optionally only clips created before a date."""
    if camera_id is not None:
        _camera_exists(camera_id)
    _ensure_idle(camera_id)
    cutoff = datetime.combine(parse_day(before), datetime.min.time()) if before is not None else None
    result = Result()
    with session_scope() as s:
        stmt = select(Clip)
        if camera_id is not None:
            stmt = stmt.where(Clip.camera_id == camera_id)
        if clip_id is not None:
            stmt = stmt.where(Clip.id == clip_id)
        if cutoff is not None:
            stmt = stmt.where(Clip.created_at < cutoff)
        rows = list(s.scalars(stmt))
        if clip_id is not None and not rows:
            raise CleanupError("Klippet finns inte")
        for r in rows:
            for p in (r.mp4_path, r.gif_path):
                f = storage.clip_file(settings, r.camera_id, p)
                if f is not None:
                    _remove_file(f, result)
            s.delete(r)
            result.cameras.add(r.camera_id)
        result.timelapses = len(rows)
    for cam_id in result.cameras:
        _drop_empty(settings.timelapse_dir / storage.safe_id(cam_id))
    log.info("Deleted %d clips (%d bytes)", result.timelapses, result.bytes)
    return result


def cameras_with_data(settings: Settings) -> list[str]:
    """Camera ids that have image directories (on disk) or timelapses."""
    dirs = set()
    for root in (settings.snapshot_dir, settings.archive_dir):
        if root.is_dir():
            dirs |= {d.name for d in root.iterdir() if d.is_dir()}
    with session_scope() as s:
        ids = list(s.scalars(select(Camera.id)))
        with_clips = set(s.scalars(select(Clip.camera_id).distinct()))
    return [cid for cid in ids if storage.safe_id(cid) in dirs or cid in with_clips]


def purge_older_than(settings: Settings, before: str, what: str, camera_id: str | None = None) -> Result:
    """Delete images and/or timelapses older than a date, for one camera or all cameras."""
    if what not in ("images", "timelapses", "all"):
        raise CleanupError("Välj bilder, timelapses eller båda")
    parse_day(before)
    _ensure_idle(camera_id)
    result = Result()
    if what in ("images", "all"):
        ids = [camera_id] if camera_id else cameras_with_data(settings)
        for cid in ids:
            result.add(delete_images(settings, cid, before=before))
    if what in ("timelapses", "all"):
        result.add(delete_timelapses(settings, camera_id, before=before))
    return result


async def wipe_all(settings: Settings, jobs) -> Result:
    """Delete all images, timelapses, stored road temperatures and job history.

    Cameras (and which are enabled), per camera choices, weather stations and admin
    settings are kept, so collection carries on with the next run.
    """
    from app import collector, weather

    _ensure_idle()
    result = Result()
    async with collector._collect_lock, weather._collect_lock:
        _ensure_idle()
        while not jobs.queue.empty():  # drop queued jobs; workers skip unknown ids anyway
            jobs.queue.get_nowait()
            jobs.queue.task_done()
        for root in (settings.snapshot_dir, settings.archive_dir, settings.timelapse_dir):
            _remove_tree(root, result)
            root.mkdir(parents=True, exist_ok=True)
        with session_scope() as s:
            result.timelapses = len(s.scalars(select(Clip.id)).all())
            s.execute(delete(Clip))
            s.execute(delete(Batch))
            s.execute(delete(Job))
            s.execute(delete(RoadTemperature))
            s.execute(
                update(Camera).values(
                    snapshot_count=0, last_snapshot_path=None, last_photo_time=None, updated_at=utcnow()
                )
            )
    log.warning("Admin wiped all data: %d files, %d bytes", result.files, result.bytes)
    return result
