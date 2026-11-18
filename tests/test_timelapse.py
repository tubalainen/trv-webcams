import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app import storage
from app.db import Camera, Job, Timelapse, session_scope
from app.timelapse import PERIODS, Encoder, JobQueue, generate, subsample

TZ = ZoneInfo("Europe/Stockholm")
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg missing")


def test_subsample():
    frames = [Path(str(i)) for i in range(100)]
    s = subsample(frames, 10)
    assert len(s) == 10 and s[0] == frames[0] and s[-1] == frames[-1]
    assert subsample(frames[:5], 10) == frames[:5]
    assert subsample(frames, 1) == [frames[-1]]


def _make_frames(settings, cam, now, count, step_min=5):
    src = settings.data_dir / "src.jpg"
    if not src.exists():
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc=size=320x240:rate=1",
                "-frames:v",
                "1",
                str(src),
            ],
            check=True,
        )
    for i in range(count):
        t = now - timedelta(minutes=step_min * i)
        p = storage.snapshot_path(settings, cam, t)
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, p)
        storage.archive_snapshot(settings, cam, t, p)


def test_list_frames_range(settings):
    now = datetime(2026, 11, 18, 0, 10, tzinfo=TZ)
    for t in [now - timedelta(days=2), now - timedelta(minutes=20), now]:
        p = storage.snapshot_path(settings, "C", t)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    frames = storage.list_frames(settings.snapshot_dir, "C", now - timedelta(days=1), now, settings.timezone)
    assert [f.name for f in frames] == ["235000.jpg", "001000.jpg"]


@needs_ffmpeg
async def test_generate_cpu(settings):
    now = datetime.now(TZ).replace(microsecond=0)
    with session_scope() as s:
        s.add(Camera(id="CAM_1", enabled=True))
    _make_frames(settings, "CAM_1", now, 30)
    msg = await generate(settings, Encoder(settings), "CAM_1", "1d", now=now)
    assert "30 bilder" in msg
    mp4 = storage.timelapse_path(settings, "CAM_1", "1d", "mp4")
    gif = storage.timelapse_path(settings, "CAM_1", "1d", "gif")
    assert mp4.stat().st_size > 0 and gif.read_bytes()[:3] == b"GIF"
    with session_scope() as s:
        kinds = {t.kind: t.encoder for t in s.scalars(select(Timelapse))}
    assert kinds == {"mp4": "libx264", "gif": "gif"}


async def test_generate_too_few(settings):
    msg = await generate(settings, Encoder(settings), "NONE", "7d")
    assert "för få bilder" in msg


async def test_job_queue(settings):
    with session_scope() as s:
        s.add(Camera(id="CAM_1", enabled=True))
        s.add(Camera(id="CAM_2", enabled=False))
    q = JobQueue(settings)
    job = q.enqueue("CAM_1", ["1d"])
    again = q.enqueue("CAM_1", ["7d"])
    assert again.id == job.id and again.periods == "1d,7d"
    assert q.enqueue_nightly() == 1
    with session_scope() as s:
        assert s.get(Job, job.id).periods == ",".join(PERIODS)
    await q.run_job(job.id)
    with session_scope() as s:
        j = s.get(Job, job.id)
        assert j.status == "done" and "för få bilder" in j.message
