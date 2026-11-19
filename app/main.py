from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select

from app import __version__, collector, settings_store
from app.api.admin import router as admin_router
from app.api.routes import router
from app.config import get_settings
from app.db import Camera, WeatherStation, init_db, session_scope
from app.scheduler import build_scheduler
from app.timelapse import JobQueue

STATIC_DIR = Path(__file__).parent / "static"

log = logging.getLogger("trv_webcams")


async def _initial_sync(settings) -> None:
    with session_scope() as s:
        count = s.scalar(select(func.count()).select_from(Camera)) or 0
        stations = s.scalar(select(func.count()).select_from(WeatherStation)) or 0
    if count == 0:
        try:
            await collector.sync_metadata(settings)
        except Exception:  # noqa: BLE001
            log.exception("Initial camera sync failed")
    if stations == 0 or count == 0:
        try:
            await collector.sync_weather_stations(settings)
        except Exception:  # noqa: BLE001
            log.exception("Initial road weather station sync failed")
    try:
        await collector.collect_road_temps(settings)
    except Exception:  # noqa: BLE001
        log.exception("Initial road temperature collection failed")
    try:
        await collector.probe_fullsize(settings)
    except Exception:  # noqa: BLE001
        log.exception("Initial full size image check failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    for d in (settings.snapshot_dir, settings.archive_dir, settings.timelapse_dir):
        d.mkdir(parents=True, exist_ok=True)
    init_db()
    # Environment values are the start values; settings saved on the admin page win.
    app.state.env_defaults = settings_store.env_defaults_of(settings)
    settings_store.apply_stored(settings, app.state.env_defaults)
    if not settings.admin_password:
        log.info("ADMIN_PASSWORD is not set – the admin page is disabled")
    jobs = JobQueue(settings)
    app.state.jobs = jobs
    await jobs.start()
    # Check the GPU / hardware encoder at startup so the result shows up in the container log
    await asyncio.to_thread(lambda: jobs.encoder.choice)
    sched = None
    if settings.scheduler_enabled:
        if not settings.trv_api_key:
            log.error("TRV_API_KEY is not set – collection is disabled")
        else:
            asyncio.create_task(_initial_sync(settings))
            sched = build_scheduler(settings, jobs)
            sched.start()
    app.state.scheduler = sched
    log.info("trv-webcams %s started on port %s", __version__, settings.port)
    try:
        yield
    finally:
        if sched is not None:
            sched.shutdown(wait=False)
        await jobs.stop()


class UIStaticFiles(StaticFiles):
    """The UI files: browsers must revalidate them (ETag), so an upgraded container is
    picked up at once instead of running old JavaScript from the cache. Vendor libraries
    (Leaflet) never change between releases and may be cached."""

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if not path.startswith("vendor/"):
            response.headers["Cache-Control"] = "no-cache"
        return response


def create_app() -> FastAPI:
    app = FastAPI(title="TRV Webcams", version=__version__, lifespan=lifespan)
    app.include_router(router)
    app.include_router(admin_router)
    app.mount("/", UIStaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


app = create_app()


def run() -> None:  # pragma: no cover
    import uvicorn

    s = get_settings()
    uvicorn.run("app.main:app", host=s.host, port=s.port, proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":  # pragma: no cover
    run()
