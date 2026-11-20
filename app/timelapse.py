"""Timelapse generation on request: frame selection, ffmpeg runs, clips and the job queue.

There is no automatic (nightly) generation. A user picks a camera, a county or all of
Sweden, a time range, the formats (MP4 and/or GIF) and the clip length; each camera
gets its own clip in the library (table `clips`).
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from app import cameras, ffmpeg, overlay, storage, weather
from app.config import Settings
from app.db import Batch, Camera, Clip, Job, session_scope, utcnow

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    days: int


# Quick picks in the generate dialog
PRESETS: dict[str, Preset] = {
    p.key: p
    for p in [
        Preset("1d", "Senaste dygnet", 1),
        Preset("7d", "7 dygn", 7),
        Preset("14d", "14 dygn", 14),
        Preset("31d", "31 dygn", 31),
        Preset("90d", "Kvartal", 90),
        Preset("365d", "År", 365),
    ]
}

DURATIONS = (10, 30, 60, 120)
DEFAULT_DURATION = 30
FORMATS = ("mp4", "gif")
TEMP_CHOICES = ("default", "on", "off")
MIN_FRAMES = 2
MAX_BATCH_CAMERAS = 500


class RequestError(ValueError):
    """Invalid generate request (Swedish message for the user)."""


@dataclass(frozen=True)
class ClipRequest:
    start: datetime  # aware UTC
    end: datetime
    formats: tuple[str, ...]
    duration_s: int
    temp_overlay: str = "default"

    @property
    def formats_str(self) -> str:
        return ",".join(self.formats)


def _aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _naive_utc(dt: datetime) -> datetime:
    return _aware(dt).replace(tzinfo=None)


def make_request(
    settings: Settings,
    start: datetime,
    end: datetime,
    formats: list[str] | tuple[str, ...],
    duration_s: int = DEFAULT_DURATION,
    temp_overlay: str = "default",
    now: datetime | None = None,
) -> ClipRequest:
    """Validate a generate request."""
    now = now or datetime.now(UTC)
    start, end = _aware(start), _aware(end)
    if end > now:
        end = now  # "to now" or an end later today: stop at the newest image
    if start >= end:
        raise RequestError("Starten måste ligga före slutet")
    if end - start > timedelta(days=settings.archive_retention_days):
        raise RequestError(f"Tidsspannet får vara högst {settings.archive_retention_days} dagar")
    wanted = set(formats or ())
    fmts = tuple(f for f in FORMATS if f in wanted)
    if not fmts or len(fmts) != len(wanted):
        raise RequestError("Välj MP4, GIF eller båda")
    if duration_s not in DURATIONS:
        raise RequestError(f"Längden ska vara en av {', '.join(map(str, DURATIONS))} sekunder")
    if temp_overlay not in TEMP_CHOICES:
        raise RequestError("Ogiltigt val för temperaturgraf")
    return ClipRequest(start, end, fmts, duration_s, temp_overlay)


def subsample(frames: list[Path], max_frames: int) -> list[Path]:
    """Evenly pick at most max_frames frames, keeping the first and last one."""
    n = len(frames)
    if max_frames <= 0 or n <= max_frames:
        return list(frames)
    if max_frames == 1:
        return [frames[-1]]
    return [frames[round(i * (n - 1) / (max_frames - 1))] for i in range(max_frames)]


def select_frames(settings: Settings, camera_id: str, start: datetime, end: datetime) -> list[Path]:
    """All snapshots in the range, plus the hourly archive images for days without snapshots
    (older than the snapshot retention), oldest first."""
    tz = settings.timezone
    snaps = storage.list_frames(settings.snapshot_dir, camera_id, start, end, tz)
    snap_days = {f.parent.name for f in snaps}
    arch = [
        f
        for f in storage.list_frames(settings.archive_dir, camera_id, start, end, tz)
        if f.parent.name not in snap_days
    ]
    if not arch:
        return snaps
    frames = snaps + arch
    frames.sort(key=lambda f: (f.parent.name, f.stem.ljust(6, "0")))
    return frames


def available_days(settings: Settings, camera_ids: list[str]) -> dict[str, list[str]]:
    """Days with snapshots and days that only have hourly archive images."""
    snap: set[str] = set()
    arch: set[str] = set()
    for cid in camera_ids:
        for root, target in ((settings.snapshot_dir, snap), (settings.archive_dir, arch)):
            base = root / storage.safe_id(cid)
            if base.is_dir():
                target.update(d.name for d in base.iterdir() if d.is_dir() and storage.DAY_RE.match(d.name))
    return {"snapshots": sorted(snap), "archive_only": sorted(arch - snap)}


def has_images_in_range(settings: Settings, camera_id: str, start: datetime, end: datetime) -> bool:
    """Cheap check (by day directory) used to skip cameras when queueing a batch."""
    tz = settings.timezone
    first = storage.local_time(_aware(start), tz).date().isoformat()
    last = storage.local_time(_aware(end), tz).date().isoformat()
    for root in (settings.snapshot_dir, settings.archive_dir):
        base = root / storage.safe_id(camera_id)
        if base.is_dir() and any(first <= d.name <= last for d in base.iterdir() if d.is_dir()):
            return True
    return False


class Slots:
    """Async limiter whose size can change while it runs (read from `limit` on every wait)."""

    def __init__(self, limit: Callable[[], int]) -> None:
        self._limit = limit
        self._used = 0
        self._cond = asyncio.Condition()

    @property
    def used(self) -> int:
        return self._used

    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[None]:
        async with self._cond:
            await self._cond.wait_for(lambda: self._used < max(1, self._limit()))
            self._used += 1
        try:
            yield
        finally:
            async with self._cond:
                self._used -= 1
                self._cond.notify_all()


async def encode_mp4(
    settings: Settings,
    encoder: Encoder,
    enc: ffmpeg.EncoderChoice,
    cmd: Callable[[ffmpeg.EncoderChoice], list[str]],
) -> ffmpeg.EncoderChoice:
    """Run the MP4 encode and return the encoder that was used.

    Hardware encodes wait for a GPU slot. When one fails, this clip is encoded on the CPU, and
    the shared encoder only switches to the CPU for good when a fresh test encode fails too.
    A single failure can be a busy GPU (too many sessions), not a broken one.
    """
    if enc.mode == "cpu":
        await ffmpeg.run(cmd(enc))
        return enc
    try:
        async with encoder.gpu_slot():
            await ffmpeg.run(cmd(enc))
        return enc
    except RuntimeError as e:
        log.warning("%s encoding failed (%s), encoding this clip on the CPU", enc.codec, e)
        async with encoder.gpu_slot():
            works = await asyncio.to_thread(ffmpeg.encoder_works, enc.mode, settings.vaapi_device)
        if not works:
            log.warning("%s test encode failed too, using the CPU from now on", enc.codec)
            encoder.downgrade()
    cpu = ffmpeg.EncoderChoice("cpu", "libx264")
    await ffmpeg.run(cmd(cpu))
    return cpu


class Encoder:
    """Lazily detected encoder, shared by all jobs."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._choice: ffmpeg.EncoderChoice | None = None
        self.gpus: list[str] | None = None
        # GeForce cards allow only a few NVENC sessions at a time, so hardware encodes are
        # limited separately from the job workers (frame selection, graphs and GIFs run on CPU).
        self.gpu_slot = Slots(lambda: settings.max_gpu_encodes)

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

    def reset(self) -> None:
        """Forget the detected encoder so the next use checks the hardware again."""
        self._choice = None


# Trafikverket's normal images are only 384 px wide. Smaller sources are scaled up by
# a whole factor so the temperature graph text stays readable in the video.
MIN_VIDEO_WIDTH = 768

# Share of the frame width used by the temperature graph
MP4_GRAPH_FRACTION = 0.30
GIF_GRAPH_FRACTION = 0.45  # GIFs are small, the graph needs a larger share to stay readable


@dataclass
class TempGraph:
    station: str
    samples: list[tuple[datetime, float]]


def temp_graph_for(
    settings: Settings, camera_id: str, start: datetime, end: datetime, enabled: bool | None = None
) -> tuple[TempGraph | None, str]:
    """Road temperature data for the clip, or None plus a short reason (empty when switched off)."""
    with session_scope() as s:
        cam = s.get(Camera, camera_id)
        if cam is None:
            return None, ""
        use = cameras.wants_temp_overlay(cam, settings) if enabled is None else enabled
        station_id = cam.station_id
    if not use:
        return None, ""
    if not station_id:
        return None, f"ingen vägväderstation inom {settings.temp_station_max_km:g} km"
    samples = weather.series(station_id, start, end)
    if not samples:
        return None, "ingen vägtemperatur sparad för perioden"
    return TempGraph(weather.station_name(station_id) or station_id, samples), ""


def _render_overlay(
    settings: Settings,
    graph: TempGraph,
    frames: list[Path],
    start: datetime,
    end: datetime,
    width: int,
    fraction: float,
    fps: int,
    out_dir: Path,
    prefix: str,
) -> tuple[Path, int]:
    """Render the graph for each frame and write an ffconcat list. Returns (list, margin)."""
    info = overlay.overlay_size(width, fraction)
    renderer = overlay.GraphRenderer(graph.samples, start, end, info.width, info.height, settings.timezone)
    times = [storage.frame_time(f, settings.timezone) or end for f in frames]
    pngs = overlay.render_sequence(renderer, times, out_dir, prefix)
    concat = out_dir / f"{prefix}overlay.ffconcat"
    ffmpeg.write_concat_file(pngs, fps, concat)
    return concat, info.margin


class Skipped(Exception):  # noqa: N818 - not an error: the job is marked "skipped"
    """Nothing to generate (too few images in the range)."""


@dataclass
class ClipResult:
    clip_id: int
    message: str


def _temp_choice(value: str) -> bool | None:
    return {"on": True, "off": False}.get(value)


async def generate_clip(settings: Settings, encoder: Encoder, camera_id: str, req: ClipRequest) -> ClipResult:
    """Generate the requested formats for one camera and store them as a library clip."""
    start, end = req.start, req.end
    frames = await asyncio.to_thread(select_frames, settings, camera_id, start, end)
    if len(frames) < MIN_FRAMES:
        raise Skipped(f"för få bilder i tidsspannet ({len(frames)})")

    want_mp4, want_gif = "mp4" in req.formats, "gif" in req.formats
    mp4_frames = subsample(frames, req.duration_s * settings.output_fps)
    gif_frames = subsample(frames, min(req.duration_s * settings.gif_fps, settings.gif_max_frames))
    size = await asyncio.to_thread(ffmpeg.probe_size, frames[-1]) or (1280, 720)
    if size[0] < MIN_VIDEO_WIDTH:
        factor = -(-MIN_VIDEO_WIDTH // size[0])
        size = (size[0] * factor, size[1] * factor)
    graph, graph_note = await asyncio.to_thread(
        temp_graph_for, settings, camera_id, start, end, _temp_choice(req.temp_overlay)
    )

    cam_dir = settings.timelapse_dir / storage.safe_id(camera_id)
    cam_dir.mkdir(parents=True, exist_ok=True)
    enc = encoder.choice
    with tempfile.TemporaryDirectory(dir=cam_dir) as tmp:
        tmpd = Path(tmp)
        mp4_tmp, gif_tmp = tmpd / "out.mp4", tmpd / "out.gif"

        if want_mp4:
            mp4_list = tmpd / "mp4.ffconcat"
            ffmpeg.write_concat_file(mp4_frames, settings.output_fps, mp4_list)
            mp4_ov, mp4_margin = None, 12
            if graph is not None:
                mp4_ov, mp4_margin = await asyncio.to_thread(
                    _render_overlay, settings, graph, mp4_frames, start, end, size[0],
                    MP4_GRAPH_FRACTION, settings.output_fps, tmpd, "mp4_",
                )  # fmt: skip

            def mp4_cmd(e: ffmpeg.EncoderChoice) -> list[str]:
                return ffmpeg.mp4_command(
                    mp4_list, mp4_tmp, e, settings.output_fps, size[0], size[1], settings.vaapi_device,
                    overlay=mp4_ov, overlay_margin=mp4_margin,
                )  # fmt: skip

            enc = await encode_mp4(settings, encoder, enc, mp4_cmd)

        if want_gif:
            gif_list = tmpd / "gif.ffconcat"
            ffmpeg.write_concat_file(gif_frames, settings.gif_fps, gif_list)
            gif_ov, gif_margin = None, 6
            if graph is not None:
                gif_ov, gif_margin = await asyncio.to_thread(
                    _render_overlay, settings, graph, gif_frames, start, end, settings.gif_width,
                    GIF_GRAPH_FRACTION, settings.gif_fps, tmpd, "gif_",
                )  # fmt: skip
            await ffmpeg.run(
                ffmpeg.gif_command(
                    gif_list,
                    gif_tmp,
                    settings.gif_width,
                    settings.gif_fps,
                    overlay=gif_ov,
                    overlay_margin=gif_margin,
                )  # fmt: skip
            )

        with session_scope() as s:
            clip = Clip(
                camera_id=camera_id,
                range_start=_naive_utc(start),
                range_end=_naive_utc(end),
                duration_s=req.duration_s,
                frames=len(mp4_frames) if want_mp4 else len(gif_frames),
                encoder=enc.codec if want_mp4 else "gif",
                temp_station=graph.station if graph else "",
                created_at=utcnow(),
            )
            s.add(clip)
            s.flush()
            if want_mp4:
                clip.mp4_path = str(storage.clip_path(settings, camera_id, clip.id, "mp4"))
                os.replace(mp4_tmp, clip.mp4_path)
                clip.mp4_bytes = os.path.getsize(clip.mp4_path)
            if want_gif:
                clip.gif_path = str(storage.clip_path(settings, camera_id, clip.id, "gif"))
                os.replace(gif_tmp, clip.gif_path)
                clip.gif_bytes = os.path.getsize(clip.gif_path)
            return ClipResult(clip.id, describe(clip, graph_note))


def describe(clip: Clip, note: str = "") -> str:
    kinds = " + ".join(k for k, p in (("MP4", clip.mp4_path), ("GIF", clip.gif_path)) if p)
    if clip.temp_station:
        temp = f", vägtemp. {clip.temp_station}"
    else:
        temp = f", utan temperaturgraf: {note}" if note else ""
    return f"{kinds}: {clip.frames} bilder, {clip.duration_s} s ({clip.encoder}{temp})"


class JobQueue:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.encoder = Encoder(settings)
        self.queue: asyncio.Queue[int] = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._target = 1

    async def start(self) -> None:
        with session_scope() as s:
            for job in s.scalars(select(Job).where(Job.status.in_(["queued", "running"]))):
                if job.range_start is None:  # pre 0.1.6 nightly/period job: no longer supported
                    job.status, job.finished_at = "failed", utcnow()
                    job.message = "Ersatt av generering på begäran"
                    continue
                job.status = "queued"
                self.queue.put_nowait(job.id)
        self.resize(self.settings.max_concurrent_jobs)

    def resize(self, count: int) -> None:
        """Run `count` workers. Surplus workers stop after their current job."""
        self._target = max(1, count)
        self._workers = [w for w in self._workers if not w.done()]
        while len(self._workers) < self._target:
            n = len(self._workers)
            self._workers.append(asyncio.create_task(self._worker(n), name=f"timelapse-worker-{n}"))

    async def stop(self) -> None:
        for w in self._workers:
            w.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

    def enqueue_clip(
        self, camera_id: str, req: ClipRequest, *, batch_id: int | None = None, trigger: str = "manual"
    ) -> Job:
        """Queue one clip. An identical request that is still queued is returned instead."""
        with session_scope() as s:
            if batch_id is None:
                dup = s.scalars(
                    select(Job).where(
                        Job.camera_id == camera_id,
                        Job.status == "queued",
                        Job.range_start == _naive_utc(req.start),
                        Job.range_end == _naive_utc(req.end),
                        Job.formats == req.formats_str,
                        Job.duration_s == req.duration_s,
                        Job.temp_overlay == req.temp_overlay,
                    )
                ).first()
                if dup is not None:
                    return dup
            job = Job(
                camera_id=camera_id,
                trigger=trigger,
                batch_id=batch_id,
                range_start=_naive_utc(req.start),
                range_end=_naive_utc(req.end),
                formats=req.formats_str,
                duration_s=req.duration_s,
                temp_overlay=req.temp_overlay,
                status="queued",
                created_at=utcnow(),
            )
            s.add(job)
            s.flush()
            job_id = job.id
        self.queue.put_nowait(job_id)
        with session_scope() as s:
            return s.get(Job, job_id)

    def enqueue_batch(self, scope: str, county_no: int | None, req: ClipRequest) -> tuple[Batch, list[str]]:
        """One clip per enabled camera in the county (or all of Sweden) that has images in
        the range. Returns the batch and the names of the cameras that were skipped."""
        if scope not in ("county", "all"):
            raise RequestError("Välj ett län eller alla aktiva kameror")
        if scope == "county" and county_no is None:
            raise RequestError("Välj ett län")
        with session_scope() as s:
            stmt = select(Camera).where(Camera.enabled)
            if scope == "county":
                stmt = stmt.where(Camera.county_no == county_no)
            cams = [(c.id, c.name) for c in s.scalars(stmt.order_by(Camera.name))]
        if not cams:
            raise RequestError("Inga aktiva kameror i urvalet")
        with_images = [c for c in cams if has_images_in_range(self.settings, c[0], req.start, req.end)]
        skipped = [name for cid, name in cams if (cid, name) not in with_images]
        if not with_images:
            raise RequestError("Ingen av kamerorna har bilder i tidsspannet")
        if len(with_images) > MAX_BATCH_CAMERAS:
            raise RequestError(f"Högst {MAX_BATCH_CAMERAS} kameror åt gången ({len(with_images)} valda)")
        with session_scope() as s:
            batch = Batch(
                scope=scope,
                county_no=county_no if scope == "county" else None,
                range_start=_naive_utc(req.start),
                range_end=_naive_utc(req.end),
                formats=req.formats_str,
                duration_s=req.duration_s,
                temp_overlay=req.temp_overlay,
                total=len(with_images),
                skipped=len(skipped),
                created_at=utcnow(),
            )
            s.add(batch)
            s.flush()
            batch_id = batch.id
        for cid, _name in with_images:
            self.enqueue_clip(cid, req, batch_id=batch_id, trigger="batch")
        with session_scope() as s:
            batch = s.get(Batch, batch_id)
        log.info("Queued batch %s: %d cameras, %d skipped", batch_id, len(with_images), len(skipped))
        return batch, skipped

    async def _worker(self, index: int = 0) -> None:
        while True:
            if index >= self._target:  # pool was made smaller
                return
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
            if job is None or job.status != "queued" or job.range_start is None:
                return
            job.status, job.started_at = "running", utcnow()
            camera_id = job.camera_id
            req = ClipRequest(
                _aware(job.range_start),
                _aware(job.range_end),
                tuple(job.formats.split(",")),
                job.duration_s or DEFAULT_DURATION,
                job.temp_overlay or "default",
            )
        status, message, clip_id = "done", "", None
        try:
            result = await generate_clip(self.settings, self.encoder, camera_id, req)
            clip_id, message = result.clip_id, result.message
        except Skipped as e:
            status, message = "skipped", f"Hoppades över: {e}"
        except Exception as e:  # noqa: BLE001
            log.exception("Clip for %s failed", camera_id)
            status, message = "failed", f"Fel: {e}"
        with session_scope() as s:
            job = s.get(Job, job_id)
            if job is None:  # removed meanwhile ("start over")
                return
            job.status, job.message, job.clip_id, job.finished_at = status, message, clip_id, utcnow()
            if clip_id is not None and job.batch_id is not None:
                clip = s.get(Clip, clip_id)
                if clip is not None:
                    clip.batch_id = job.batch_id  # so the clip library can filter on the run
        log.info("Job %s for %s: %s %s", job_id, camera_id, status, message)
