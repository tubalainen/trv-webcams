"""Filesystem layout for snapshots, the sparse hourly archive and timelapses.

snapshots/{cam}/{YYYY-MM-DD}/{HHMMSS}.jpg   all images, kept snapshot_retention_days
archive/{cam}/{YYYY-MM-DD}/{HH}.jpg         first image of each hour, kept archive_retention_days
timelapses/{cam}/{clip_id}.mp4|.gif         generated clips (library)
"""

from __future__ import annotations

import os
import re
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import Settings

_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]")
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HMS_RE = re.compile(r"^\d{6}$")
# Stored image file names: HHMMSS.jpg (snapshots) or HH.jpg (archive)
IMAGE_NAME_RE = re.compile(r"^\d{2}(\d{4})?\.jpg$")


def safe_id(camera_id: str) -> str:
    return _SAFE_RE.sub("_", camera_id)


def local_time(dt: datetime, tz: str) -> datetime:
    return dt.astimezone(ZoneInfo(tz)) if dt.tzinfo else dt


def snapshot_path(settings: Settings, camera_id: str, photo_time: datetime) -> Path:
    t = local_time(photo_time, settings.timezone)
    return settings.snapshot_dir / safe_id(camera_id) / t.strftime("%Y-%m-%d") / f"{t:%H%M%S}.jpg"


def archive_path(settings: Settings, camera_id: str, photo_time: datetime) -> Path:
    t = local_time(photo_time, settings.timezone)
    return settings.archive_dir / safe_id(camera_id) / t.strftime("%Y-%m-%d") / f"{t:%H}.jpg"


def clip_path(settings: Settings, camera_id: str, clip_id: int, kind: str) -> Path:
    return settings.timelapse_dir / safe_id(camera_id) / f"{clip_id}.{kind}"


def clip_file(settings: Settings, camera_id: str, stored: str | None) -> Path | None:
    """Location of a stored clip file below the current timelapse directory.

    Only the file name of the stored path is used, so clips keep working when DATA_DIR
    moves, and a stored path can never point outside the timelapse directory.
    """
    if not stored:
        return None
    return settings.timelapse_dir / safe_id(camera_id) / Path(stored).name


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def archive_snapshot(settings: Settings, camera_id: str, photo_time: datetime, src: Path) -> bool:
    """Keep the first snapshot of each hour in the sparse archive (hard link, copy as fallback)."""
    dst = archive_path(settings, camera_id, photo_time)
    if dst.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)
    return True


def frame_time(path: Path, tz: str) -> datetime | None:
    """Local capture time of a stored image (.../{YYYY-MM-DD}/{HHMMSS|HH}.jpg), timezone aware."""
    stem = path.stem.ljust(6, "0")
    try:
        naive = datetime.strptime(f"{path.parent.name} {stem}", "%Y-%m-%d %H%M%S")
    except ValueError:
        return None
    return naive.replace(tzinfo=ZoneInfo(tz))


def _days_in_range(start: date, end: date) -> list[str]:
    n = (end - start).days
    return [(start + timedelta(days=i)).isoformat() for i in range(n + 1)]


def list_frames(root: Path, camera_id: str, start: datetime, end: datetime, tz: str) -> list[Path]:
    """Sorted list of image files for a camera between start and end (local time)."""
    base = root / safe_id(camera_id)
    if not base.is_dir():
        return []
    start_l, end_l = local_time(start, tz), local_time(end, tz)
    s_key = start_l.strftime("%Y-%m-%d/%H%M%S")
    e_key = end_l.strftime("%Y-%m-%d/%H%M%S")
    frames: list[Path] = []
    for day in _days_in_range(start_l.date(), end_l.date()):
        d = base / day
        if not d.is_dir():
            continue
        for f in sorted(d.iterdir()):
            if f.suffix != ".jpg":
                continue
            stem = f.stem.ljust(6, "0")  # archive files are HH only
            key = f"{day}/{stem}"
            if s_key <= key <= e_key:
                frames.append(f)
    return frames


def day_dirs(root: Path) -> list[tuple[Path, date]]:
    """All (dir, date) pairs below root/{cam}/{YYYY-MM-DD}."""
    out: list[tuple[Path, date]] = []
    if not root.is_dir():
        return out
    for cam in root.iterdir():
        if not cam.is_dir():
            continue
        for d in cam.iterdir():
            if d.is_dir() and DAY_RE.match(d.name):
                out.append((d, date.fromisoformat(d.name)))
    return out


def dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for dirpath, _dirs, files in os.walk(path):
        for f in files:
            try:
                st = os.stat(os.path.join(dirpath, f))
            except OSError:
                continue
            total += st.st_size
    return total
