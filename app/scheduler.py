"""Background jobs: collection, metadata sync, nightly timelapses and retention."""

from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app import collector, retention
from app.config import Settings
from app.timelapse import JobQueue

log = logging.getLogger(__name__)


def build_scheduler(settings: Settings, jobs: JobQueue) -> AsyncIOScheduler:
    sched = AsyncIOScheduler(timezone=settings.timezone)

    async def collect():
        try:
            await collector.collect_once(settings)
        except Exception:  # noqa: BLE001
            log.exception("Collection failed")

    async def sync():
        try:
            await collector.sync_metadata(settings)
        except Exception:  # noqa: BLE001
            log.exception("Metadata sync failed")

    async def nightly():
        try:
            await asyncio.to_thread(retention.run_retention, settings)
        except Exception:  # noqa: BLE001
            log.exception("Retention failed")
        jobs.enqueue_nightly()

    common = {"coalesce": True, "max_instances": 1, "misfire_grace_time": 600}
    sched.add_job(
        collect,
        IntervalTrigger(minutes=settings.collect_interval_minutes),
        id="collect",
        name="Hämta bilder",
        **common,
    )
    sched.add_job(
        sync,
        CronTrigger(hour=settings.metadata_sync_hour, minute=15),
        id="sync",
        name="Synka kameror",
        **common,
    )
    sched.add_job(
        nightly,
        CronTrigger(hour=settings.nightly_hour, minute=settings.nightly_minute),
        id="nightly",
        name="Nattlig timelapse",
        **common,
    )
    return sched
