"""Retention: remove snapshots and archive images older than the configured limits."""

from __future__ import annotations

import logging
import shutil
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app import storage
from app.config import Settings
from app.db import Camera, session_scope

log = logging.getLogger(__name__)


def prune(root, keep_days: int, today: date) -> int:
    cutoff = today - timedelta(days=keep_days)
    removed = 0
    for d, day in storage.day_dirs(root):
        if day < cutoff:
            shutil.rmtree(d, ignore_errors=True)
            removed += 1
    # drop empty camera directories
    if root.is_dir():
        for cam in root.iterdir():
            if cam.is_dir() and not any(cam.iterdir()):
                cam.rmdir()
    return removed


def count_snapshots(settings: Settings, camera_id: str) -> int:
    base = settings.snapshot_dir / storage.safe_id(camera_id)
    if not base.is_dir():
        return 0
    return sum(1 for _ in base.glob("*/*.jpg"))


def run_retention(settings: Settings, today: date | None = None) -> dict[str, int]:
    today = today or datetime.now(ZoneInfo(settings.timezone)).date()
    snap = prune(settings.snapshot_dir, settings.snapshot_retention_days, today)
    arch = prune(settings.archive_dir, settings.archive_retention_days, today)
    with session_scope() as s:
        for cam in s.scalars(select(Camera).where(Camera.snapshot_count > 0)):
            cam.snapshot_count = count_snapshots(settings, cam.id)
    log.info("Retention removed %d snapshot day-dirs and %d archive day-dirs", snap, arch)
    return {"snapshot_days_removed": snap, "archive_days_removed": arch}
