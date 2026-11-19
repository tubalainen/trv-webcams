"""Timelapse generation: frame selection, ffmpeg runs and the job queue."""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from app import ffmpeg, storage
from app.config import Settings
from app.db import Camera, Job, Timelapse, session_scope, utcnow

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Period:
    key: str
    label: str
    days: int
    target_seconds: int
    source: str  # snapshots | archive


PERIODS: dict[str, Period] = {
    p.key: p
    for p in [
        Period("1d", "1 dygn", 1, 12, "snapshots"),
        Period("7d", "7 dygn", 7, 30, "snapshots"),
        Period("14d", "14 dygn", 14, 45, "snapshots"),
        Period("31d", "31 dygn", 31, 60, "snapshots"),
        Period("90d", "Kvartal", 90, 75, "archive"),
        Period("365d", "År", 365, 90, "archive"),
    ]
}

MIN_FRAMES = 2


def subsample(frames: list[Path], max_frames: int) -> list[Path]:
    """Evenly pick at most max_frames frames, keeping the first and last one."""
    n = len(frames)
    if max_frames <= 0 or n <= max_frames:
        return list(frames)
    if max_frames == 1:
        return [frames[-1]]
    return [frames[round(i * (n - 1) / (max_frames - 1))] for i in range(max_frames)]


def select_frames(
    settings: Settings, camera_id: str, period: Period, now: datetime | None = None
) -> tuple[list[Path], datetime, datetime]:
    end = now or datetime.now(UTC)
    start = end - timedelta(days=period.days)
    root = settings.snapshot_dir if period.source == "snapshots" else settings.archive_dir
    frames = storage.list_frames(root, camera_id, start, end, settings.timezone)
    return frames, start, end


class Encoder:
    """Lazily detected encoder, shared by all jobs."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._choice: ffmpeg.EncoderChoice | None = None
        self.gpus: list[str] | None = None

    @property
    def choice(self) -> ffmpeg.EncoderChoice:
        if self._choice is None:
            self.gpus = ffmpeg.nvidia_gpus()
            self._choice = ffmpeg.detect_encoder(
                self.settings.hwaccel, self.settings.vaapi_device, gpus=self.gpus
            )
        return self._choice

    def downgrade(self) -> None:
        self._choice = ffmpeg.EncoderChoice("cpu", "libx264")


async def generate(
    settings: Settings, encoder: Encoder, camera_id: str, period_key: str, now: datetime | None = None
) -> str:
    """Generate MP4 + GIF for one camera/period. Returns a short status message."""
    period = PERIODS[period_key]
    frames, start, end = await asyncio.to_thread(select_frames, settings, camera_id, period, now)
    if len(frames) < MIN_FRAMES:
        return f"{period.label}: för få bilder ({len(frames)})"

    mp4_frames = subsample(frames, period.target_seconds * settings.output_fps)
    gif_frames = subsample(frames, settings.gif_max_frames)
    size = await asyncio.to_thread(ffmpeg.probe_size, frames[-1]) or (1280, 720)

    mp4_out = storage.timelapse_path(settings, camera_id, period.key, "mp4")
    gif_out = storage.timelapse_path(settings, camera_id, period.key, "gif")
    mp4_out.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(dir=mp4_out.parent) as tmp:
        tmpd = Path(tmp)
        mp4_list, gif_list = tmpd / "mp4.ffconcat", tmpd / "gif.ffconcat"
        ffmpeg.write_concat_file(mp4_frames, settings.output_fps, mp4_list)
        ffmpeg.write_concat_file(gif_frames, settings.gif_fps, gif_list)
        mp4_tmp, gif_tmp = tmpd / "out.mp4", tmpd / "out.gif"

        enc = encoder.choice
        try:
            await ffmpeg.run(
                ffmpeg.mp4_command(
                    mp4_list, mp4_tmp, enc, settings.output_fps, size[0], size[1], settings.vaapi_device
                )
            )
        except RuntimeError as e:
            if enc.mode == "cpu":
                raise
            log.warning("%s encoding failed (%s), retrying on CPU", enc.codec, e)
            encoder.downgrade()
            enc = encoder.choice
            await ffmpeg.run(
                ffmpeg.mp4_command(mp4_list, mp4_tmp, enc, settings.output_fps, size[0], size[1])
            )
        await ffmpeg.run(ffmpeg.gif_command(gif_list, gif_tmp, settings.gif_width, settings.gif_fps))
        os.replace(mp4_tmp, mp4_out)
        os.replace(gif_tmp, gif_out)

    with session_scope() as s:
        for kind, path, n, codec in (
            ("mp4", mp4_out, len(mp4_frames), enc.codec),
            ("gif", gif_out, len(gif_frames), "gif"),
        ):
            row = s.scalars(
                select(Timelapse).where(
                    Timelapse.camera_id == camera_id, Timelapse.period == period.key, Timelapse.kind == kind
                )
            ).first()
            if row is None:
                row = Timelapse(camera_id=camera_id, period=period.key, kind=kind)
                s.add(row)
            row.path = str(path)
            row.frames = n
            row.size_bytes = path.stat().st_size
            row.encoder = codec
            row.range_start = start.astimezone(UTC).replace(tzinfo=None)
            row.range_end = end.astimezone(UTC).replace(tzinfo=None)
            row.created_at = utcnow()
    return f"{period.label}: {len(mp4_frames)} bilder ({enc.codec})"


class JobQueue:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.encoder = Encoder(settings)
        self.queue: asyncio.Queue[int] = asyncio.Queue()
        self._workers: list[asyncio.Task] = []

    async def start(self) -> None:
        with session_scope() as s:
            for job in s.scalars(select(Job).where(Job.status.in_(["queued", "running"]))):
                job.status = "queued"
                self.queue.put_nowait(job.id)
        for i in range(max(1, self.settings.max_concurrent_jobs)):
            self._workers.append(asyncio.create_task(self._worker(), name=f"timelapse-worker-{i}"))

    async def stop(self) -> None:
        for w in self._workers:
            w.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

    def enqueue(self, camera_id: str, periods: list[str], trigger: str = "manual") -> Job:
        periods = [p for p in periods if p in PERIODS] or list(PERIODS)
        with session_scope() as s:
            existing = s.scalars(
                select(Job).where(Job.camera_id == camera_id, Job.status.in_(["queued", "running"]))
            ).first()
            if existing is not None and existing.status == "queued":
                merged = sorted(set(existing.periods.split(",")) | set(periods), key=list(PERIODS).index)
                existing.periods = ",".join(merged)
                return existing
            job = Job(
                camera_id=camera_id,
                periods=",".join(periods),
                trigger=trigger,
                status="queued",
                created_at=utcnow(),
            )
            s.add(job)
            s.flush()
            job_id = job.id
        self.queue.put_nowait(job_id)
        with session_scope() as s:
            return s.get(Job, job_id)

    def enqueue_nightly(self) -> int:
        with session_scope() as s:
            ids = [c.id for c in s.scalars(select(Camera).where(Camera.enabled))]
        for cid in ids:
            self.enqueue(cid, list(PERIODS), trigger="nightly")
        log.info("Queued nightly timelapses for %d cameras", len(ids))
        return len(ids)

    async def _worker(self) -> None:
        while True:
            job_id = await self.queue.get()
            try:
                await self.run_job(job_id)
            except Exception:  # noqa: BLE001
                log.exception("Job %s crashed", job_id)
            finally:
                self.queue.task_done()

    async def run_job(self, job_id: int) -> None:
        with session_scope() as s:
            job = s.get(Job, job_id)
            if job is None or job.status != "queued":
                return
            job.status, job.started_at = "running", utcnow()
            camera_id, periods = job.camera_id, job.periods.split(",")
        messages, failed = [], False
        for p in periods:
            try:
                messages.append(await generate(self.settings, self.encoder, camera_id, p))
            except Exception as e:  # noqa: BLE001
                failed = True
                log.exception("Timelapse %s/%s failed", camera_id, p)
                messages.append(f"{PERIODS[p].label}: fel – {e}")
        with session_scope() as s:
            job = s.get(Job, job_id)
            job.status = "failed" if failed else "done"
            job.message = "\n".join(messages)
            job.finished_at = utcnow()
        log.info("Job %s for %s finished: %s", job_id, camera_id, "; ".join(messages))
