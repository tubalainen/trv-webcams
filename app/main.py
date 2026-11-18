from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select

from app import __version__, collector
from app.api.routes import router
from app.config import get_settings
from app.db import Camera, init_db, session_scope
from app.scheduler import build_scheduler
from app.timelapse import JobQueue

STATIC_DIR = Path(__file__).parent / "static"

log = logging.getLogger("trv_webcams")


async def _initial_sync(settings) -> None:
    with session_scope() as s:
        count = s.scalar(select(func.count()).select_from(Camera)) or 0
    if count == 0:
        try:
            await collector.sync_metadata(settings)
        except Exception:  # noqa: BLE001
            log.exception("Initial camera sync failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    for d in (settings.snapshot_dir, settings.archive_dir, settings.timelapse_dir):
        d.mkdir(parents=True, exist_ok=True)
    init_db()
    jobs = JobQueue(settings)
    app.state.jobs = jobs
    await jobs.start()
    # Detect the encoder in the background so the status page can show it early
    asyncio.get_running_loop().run_in_executor(None, lambda: jobs.encoder.choice)
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


def create_app() -> FastAPI:
    app = FastAPI(title="TRV Webcams", version=__version__, lifespan=lifespan)
    app.include_router(router)
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


app = create_app()


def run() -> None:  # pragma: no cover
    import uvicorn

    s = get_settings()
    uvicorn.run("app.main:app", host=s.host, port=s.port, proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":  # pragma: no cover
    run()
