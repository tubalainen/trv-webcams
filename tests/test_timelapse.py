import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app import storage, timelapse
from app.db import Batch, Camera, Clip, Job, RoadTemperature, WeatherStation, session_scope
from app.timelapse import Encoder, JobQueue, RequestError, Skipped, generate_clip, make_request, subsample


def _req(settings, now, days=1, formats=("mp4", "gif"), duration=10, temp="default"):
    return make_request(settings, now - timedelta(days=days), now, list(formats), duration, temp, now=now)


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
async def test_generate_clip_cpu(settings):
    now = datetime.now(TZ).replace(microsecond=0)
    with session_scope() as s:
        s.add(Camera(id="CAM_1", enabled=True))
    _make_frames(settings, "CAM_1", now, 30)
    res = await generate_clip(settings, Encoder(settings), "CAM_1", _req(settings, now))
    assert "MP4 + GIF: 30 bilder, 10 s" in res.message
    with session_scope() as s:
        clip = s.get(Clip, res.clip_id)
        assert clip.encoder == "libx264" and clip.duration_s == 10
        assert Path(clip.mp4_path).name == f"{clip.id}.mp4" and clip.mp4_bytes > 0
        assert Path(clip.gif_path).read_bytes()[:3] == b"GIF"

    # Only a GIF, limited to duration x gif_fps frames
    settings.gif_fps = 2
    res = await generate_clip(settings, Encoder(settings), "CAM_1", _req(settings, now, formats=["gif"]))
    with session_scope() as s:
        clip = s.get(Clip, res.clip_id)
        assert clip.mp4_path is None and clip.gif_path and clip.frames == 20 and clip.encoder == "gif"


@needs_ffmpeg
async def test_generate_with_temperature_overlay(settings):
    now = datetime.now(TZ).replace(microsecond=0)
    with session_scope() as s:
        s.add(WeatherStation(id="222", name="Stocksund"))
        s.add(Camera(id="CAM_T", enabled=True, station_id="222", station_distance_km=1.2))
        for i in range(0, 24 * 12):
            t = (now - timedelta(minutes=5 * i)).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
            s.add(RoadTemperature(station_id="222", sample=t, value=-2 + i * 0.01))
    _make_frames(settings, "CAM_T", now, 30)

    res = await generate_clip(settings, Encoder(settings), "CAM_T", _req(settings, now))
    assert "vägtemp. Stocksund" in res.message
    with session_scope() as s:
        with_graph_clip = s.get(Clip, res.clip_id)
        assert with_graph_clip.temp_station == "Stocksund"

    def corner(path):
        out = subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-i", str(path), "-frames:v", "1", "-vf",
             "crop=40:20:iw-60:ih-30", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
            capture_output=True, check=True,
        ).stdout  # fmt: skip
        return sum(out) / len(out)

    # The generate dialog can switch the graph off for one clip
    res2 = await generate_clip(settings, Encoder(settings), "CAM_T", _req(settings, now, temp="off"))
    assert "vägtemp" not in res2.message
    with session_scope() as s:
        a, b = s.get(Clip, res.clip_id), s.get(Clip, res2.clip_id)
        assert abs(corner(a.mp4_path) - corner(b.mp4_path)) > 3


async def test_generate_overlay_notes(settings):
    now = datetime.now(TZ)
    with session_scope() as s:
        s.add(Camera(id="CAM_N", enabled=True))
    from app.timelapse import temp_graph_for

    graph, note = temp_graph_for(settings, "CAM_N", now - timedelta(days=1), now)
    assert graph is None and "inom 20 km" in note
    with session_scope() as s:
        s.get(Camera, "CAM_N").station_id = "999"
    graph, note = temp_graph_for(settings, "CAM_N", now - timedelta(days=1), now)
    assert graph is None and "ingen vägtemperatur" in note
    graph, note = temp_graph_for(settings, "CAM_N", now - timedelta(days=1), now, enabled=False)
    assert graph is None and note == ""


async def test_generate_too_few(settings):
    now = datetime.now(TZ)
    with pytest.raises(Skipped):
        await generate_clip(settings, Encoder(settings), "NONE", _req(settings, now, days=7))


def test_make_request_validation(settings):
    now = datetime(2026, 11, 19, 12, tzinfo=ZoneInfo("UTC"))
    r = make_request(
        settings, now - timedelta(days=2), now + timedelta(hours=3), ["gif", "mp4"], 60, "on", now=now
    )
    assert r.end == now and r.formats == ("mp4", "gif") and r.formats_str == "mp4,gif"
    bad = [
        dict(start=now, end=now - timedelta(hours=1), formats=["mp4"]),
        dict(start=now - timedelta(days=400), end=now, formats=["mp4"]),
        dict(start=now - timedelta(days=1), end=now, formats=[]),
        dict(start=now - timedelta(days=1), end=now, formats=["avi"]),
        dict(start=now - timedelta(days=1), end=now, formats=["mp4"], duration_s=45),
        dict(start=now - timedelta(days=1), end=now, formats=["mp4"], temp_overlay="kanske"),
    ]
    for kw in bad:
        with pytest.raises(RequestError):
            make_request(settings, now=now, **kw)


def test_select_frames_uses_archive_for_old_days(settings):
    now = datetime(2026, 11, 19, 12, tzinfo=TZ)
    # Recent day: snapshots (+ archive link); old day: only the hourly archive is left
    for t in [now - timedelta(hours=1), now - timedelta(minutes=55)]:
        p = storage.snapshot_path(settings, "C", t)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
        storage.archive_snapshot(settings, "C", t, p)
    old = now - timedelta(days=100)
    for h in (8, 9):
        a = storage.archive_path(settings, "C", old.replace(hour=h))
        a.parent.mkdir(parents=True, exist_ok=True)
        a.write_bytes(b"x")
    frames = timelapse.select_frames(settings, "C", now - timedelta(days=120), now)
    assert [f"{f.parent.name}/{f.name}" for f in frames] == [
        f"{old:%Y-%m-%d}/08.jpg",
        f"{old:%Y-%m-%d}/09.jpg",
        "2026-11-19/110000.jpg",
        "2026-11-19/110500.jpg",
    ]
    days = timelapse.available_days(settings, ["C"])
    assert days == {"snapshots": ["2026-11-19"], "archive_only": [f"{old:%Y-%m-%d}"]}
    assert timelapse.has_images_in_range(settings, "C", old - timedelta(days=1), old + timedelta(days=1))
    assert not timelapse.has_images_in_range(
        settings, "C", now - timedelta(days=50), now - timedelta(days=40)
    )


async def test_job_queue_clip_and_batch(settings):
    now = datetime.now(TZ).replace(microsecond=0)
    with session_scope() as s:
        s.add(Camera(id="CAM_1", name="Ett", enabled=True, county_no=14))
        s.add(Camera(id="CAM_2", name="Två", enabled=True, county_no=14))  # no images
        s.add(Camera(id="CAM_3", name="Tre", enabled=False, county_no=14))
        s.add(Camera(id="CAM_4", name="Fyra", enabled=True, county_no=1))
    for cid in ("CAM_1", "CAM_4"):
        p = storage.snapshot_path(settings, cid, now - timedelta(hours=1))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    q = JobQueue(settings)
    req = _req(settings, now)
    job = q.enqueue_clip("CAM_1", req)
    assert q.enqueue_clip("CAM_1", req).id == job.id  # identical queued request is reused
    assert q.enqueue_clip("CAM_1", _req(settings, now, formats=["gif"])).id != job.id

    batch, skipped = q.enqueue_batch("county", 14, req)
    assert batch.total == 1 and skipped == ["Två"]
    batch_all, skipped_all = q.enqueue_batch("all", None, req)
    assert batch_all.total == 2 and skipped_all == ["Två"]
    with pytest.raises(RequestError):
        q.enqueue_batch("county", 25, req)  # no enabled cameras
    with pytest.raises(RequestError):
        q.enqueue_batch("county", None, req)
    with session_scope() as s:
        assert {j.camera_id for j in s.scalars(select(Job).where(Job.batch_id == batch_all.id))} == {
            "CAM_1",
            "CAM_4",
        }
        assert s.get(Batch, batch.id).skipped == 1

    # One image is too few: the job is marked skipped, not failed
    await q.run_job(job.id)
    with session_scope() as s:
        j = s.get(Job, job.id)
        assert j.status == "skipped" and "för få bilder" in j.message and j.clip_id is None


async def test_old_period_jobs_are_retired_on_start(settings):
    with session_scope() as s:
        s.add(
            Job(camera_id="X", periods="1d,7d", trigger="nightly", status="queued", created_at=datetime.now())
        )
    q = JobQueue(settings)
    await q.start()
    with session_scope() as s:
        j = s.scalars(select(Job)).one()
        assert j.status == "failed" and "begäran" in j.message
    assert q.queue.empty()
    await q.stop()
