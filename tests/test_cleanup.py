import os
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import cleanup, storage
from app.api import admin
from app.db import AppSetting, Batch, Camera, Clip, Job, RoadTemperature, session_scope, utcnow
from app.main import create_app

TODAY = date.today()


def _day(n: int) -> str:
    return (TODAY - timedelta(days=n)).isoformat()


def _add_images(settings, cam: str, day: str, times=("120000", "120500", "130000")):
    """Snapshots plus hard-linked hourly archive images, like the collector writes them."""
    for hms in times:
        snap = settings.snapshot_dir / cam / day / f"{hms}.jpg"
        snap.parent.mkdir(parents=True, exist_ok=True)
        snap.write_bytes(b"\xff\xd8" + b"x" * 1000)
        arch = settings.archive_dir / cam / day / f"{hms[:2]}.jpg"
        arch.parent.mkdir(parents=True, exist_ok=True)
        if not arch.exists():
            os.link(snap, arch)


def _add_clip(settings, cam: str, created: datetime) -> int:
    with session_scope() as s:
        c = Clip(camera_id=cam, range_start=created, range_end=created, duration_s=30, created_at=created)
        s.add(c)
        s.flush()
        for kind in ("mp4", "gif"):
            p = storage.clip_path(settings, cam, c.id, kind)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"v" * 500)
            setattr(c, f"{kind}_path", str(p))
            setattr(c, f"{kind}_bytes", 500)
        return c.id


@pytest.fixture
def data(settings):
    with session_scope() as s:
        s.add(Camera(id="CAM_A", name="A", enabled=True, temp_overlay_mode="off", resolution_mode="high"))
        s.add(Camera(id="CAM_B", name="B", enabled=False))
    for cam in ("CAM_A", "CAM_B"):
        for n in (40, 10, 0):
            _add_images(settings, cam, _day(n))
    with session_scope() as s:
        s.get(Camera, "CAM_A").last_snapshot_path = str(
            settings.snapshot_dir / "CAM_A" / _day(0) / "130000.jpg"
        )
        s.get(Camera, "CAM_A").snapshot_count = 9
    settings._clips = {
        "new_a": _add_clip(settings, "CAM_A", utcnow()),
        "old_a": _add_clip(settings, "CAM_A", utcnow() - timedelta(days=20)),
        "old_b": _add_clip(settings, "CAM_B", utcnow() - timedelta(days=20)),
    }
    return settings


def _days(settings, root, cam):
    base = getattr(settings, root) / cam
    return sorted(d.name for d in base.iterdir()) if base.is_dir() else []


def test_delete_day_frees_snapshot_and_archive(data):
    r = cleanup.delete_images(data, "CAM_A", day=_day(10))
    assert r.files == 3 and r.bytes == 3 * 1002  # archive links are not counted twice
    assert _day(10) not in _days(data, "snapshot_dir", "CAM_A")
    assert _day(10) not in _days(data, "archive_dir", "CAM_A")
    with session_scope() as s:
        assert s.get(Camera, "CAM_A").snapshot_count == 6


def test_delete_older_than(data):
    r = cleanup.delete_images(data, "CAM_A", before=_day(5))
    assert r.files == 6
    assert _days(data, "snapshot_dir", "CAM_A") == [_day(0)]
    assert _days(data, "archive_dir", "CAM_A") == [_day(0)]
    assert _days(data, "snapshot_dir", "CAM_B") == [_day(40), _day(10), _day(0)]  # other camera untouched


def test_delete_single_image_and_its_archive_link(data):
    day = _day(0)
    cleanup.delete_images(data, "CAM_A", day=day, name="130000.jpg")
    assert not (data.snapshot_dir / "CAM_A" / day / "130000.jpg").exists()
    assert not (data.archive_dir / "CAM_A" / day / "13.jpg").exists()
    # 12.jpg in the archive is linked to 120000.jpg, not to 120500.jpg: keep it
    cleanup.delete_images(data, "CAM_A", day=day, name="120500.jpg")
    assert (data.archive_dir / "CAM_A" / day / "12.jpg").exists()
    with session_scope() as s:
        cam = s.get(Camera, "CAM_A")
        assert cam.last_snapshot_path.endswith(f"{day}/120000.jpg")  # moved to the newest image left


def test_delete_everything_for_camera(data):
    cleanup.delete_images(data, "CAM_A", everything=True)
    assert not (data.snapshot_dir / "CAM_A").exists() and not (data.archive_dir / "CAM_A").exists()
    with session_scope() as s:
        cam = s.get(Camera, "CAM_A")
        assert cam.snapshot_count == 0 and cam.last_snapshot_path is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"day": "2026-13-01"},
        {"before": "igår"},
        {"before": (TODAY + timedelta(days=2)).isoformat()},
        {"day": _day(0), "name": "../../x.jpg"},
        {"day": "..", "name": "120000.jpg"},
        {"before": _day(1), "everything": True},
    ],
)
def test_invalid_requests(data, kwargs):
    with pytest.raises(cleanup.CleanupError):
        cleanup.delete_images(data, "CAM_A", **kwargs)


def test_unknown_camera(data):
    with pytest.raises(cleanup.CleanupError):
        cleanup.delete_images(data, "NOPE", everything=True)


def test_delete_clips(data):
    old = data._clips["old_a"]
    r = cleanup.delete_timelapses(data, clip_id=old)
    assert r.timelapses == 1 and r.files == 2
    assert not storage.clip_path(data, "CAM_A", old, "mp4").exists()
    assert storage.clip_path(data, "CAM_A", data._clips["new_a"], "mp4").exists()
    with pytest.raises(cleanup.CleanupError):
        cleanup.delete_timelapses(data, clip_id=old)  # already gone
    assert cleanup.delete_timelapses(data, "CAM_A").timelapses == 1


def test_purge_older_than_all_cameras(data):
    r = cleanup.purge_older_than(data, _day(5), "all")
    assert r.timelapses == 2  # the two clips created 20 days ago
    assert r.cameras == {"CAM_A", "CAM_B"}
    for cam in ("CAM_A", "CAM_B"):
        assert _days(data, "snapshot_dir", cam) == [_day(0)]
    with session_scope() as s:
        assert [c.id for c in s.scalars(select(Clip))] == [data._clips["new_a"]]
    with pytest.raises(cleanup.CleanupError):
        cleanup.purge_older_than(data, _day(5), "everything")


def test_running_job_blocks_deletion(data):
    with session_scope() as s:
        s.add(Job(camera_id="CAM_A", status="running", created_at=utcnow()))
    with pytest.raises(cleanup.JobRunning):
        cleanup.delete_images(data, "CAM_A", everything=True)
    cleanup.delete_images(data, "CAM_B", everything=True)  # other cameras are fine


async def test_wipe_all_keeps_cameras_and_settings(data):
    from app.timelapse import JobQueue

    with session_scope() as s:
        s.add(RoadTemperature(station_id="1", sample=utcnow(), value=1.0))
        s.add(AppSetting(key="gif_fps", value="5"))
        s.add(Job(camera_id="CAM_A", status="done", created_at=utcnow()))
        s.add(
            Batch(
                scope="all",
                range_start=utcnow(),
                range_end=utcnow(),
                formats="mp4",
                duration_s=30,
                created_at=utcnow(),
            )
        )
    q = JobQueue(data)
    q.queue.put_nowait(123)
    r = await cleanup.wipe_all(data, q)
    assert r.files == 18 + 6 and r.timelapses == 3  # 18 snapshots, 6 clip files (archive = links)
    assert q.queue.empty()
    for root in (data.snapshot_dir, data.archive_dir, data.timelapse_dir):
        assert root.is_dir() and not any(root.iterdir())
    with session_scope() as s:
        assert not s.scalars(select(Clip)).all() and not s.scalars(select(Job)).all()
        assert not s.scalars(select(Batch)).all()
        assert not s.scalars(select(RoadTemperature)).all()
        a = s.get(Camera, "CAM_A")
        assert a.enabled and a.temp_overlay_mode == "off" and a.resolution_mode == "high"
        assert a.snapshot_count == 0 and a.last_snapshot_path is None and a.last_photo_time is None
        assert s.get(AppSetting, "gif_fps").value == "5"


def test_admin_api(data, monkeypatch):
    monkeypatch.setattr(data, "admin_password", "pw")
    monkeypatch.setattr(admin, "FAILED_LOGIN_DELAY", 0)
    with TestClient(create_app()) as c:
        assert c.get("/api/admin/storage").status_code == 401
        assert c.post("/api/admin/wipe", json={"confirm": "RENSA ALLT"}).status_code == 401
        c.post("/api/admin/login", json={"password": "pw"})

        st = c.get("/api/admin/storage").json()
        a = next(x for x in st["cameras"] if x["id"] == "CAM_A")
        assert [d[0] for d in a["days"]] == [_day(40), _day(10), _day(0)]
        assert a["days"][0][1:3] == [3, 3006] and a["days"][0][3:] == [2, 0]  # archive bytes are links
        assert len(a["timelapses"]) == 2 and a["timelapses"][0]["bytes"] == 1000 and st["disk_free_bytes"] > 0

        r = c.post("/api/admin/purge", json={"before": _day(5), "what": "images", "camera_id": "CAM_B"})
        assert r.status_code == 200 and r.json()["files"] == 6
        assert c.post("/api/admin/purge", json={"before": "x", "what": "images"}).status_code == 400

        r = c.post("/api/admin/cameras/CAM_A/delete-images", json={"day": _day(40)})
        assert r.json()["files"] == 3
        r = c.delete(f"/api/admin/cameras/CAM_A/snapshots/{_day(0)}/120500.jpg")
        assert r.status_code == 200 and r.json()["files"] == 1
        # Path tricks never reach the delete code (no route match or rejected name)
        r = c.delete(f"/api/admin/cameras/CAM_A/snapshots/{_day(0)}/..%2F..%2Fapp.db")
        assert r.status_code in (400, 404, 405) and Path(data.db_path).exists()
        assert c.delete(f"/api/admin/cameras/CAM_A/snapshots/{_day(0)}/app.db").status_code == 400
        assert c.delete(f"/api/admin/clips/{data._clips['old_a']}").json()["timelapses"] == 1
        assert c.delete(f"/api/admin/clips/{data._clips['old_a']}").status_code == 400
        assert c.delete("/api/admin/cameras/CAM_A/timelapses").json()["timelapses"] == 1

        assert c.post("/api/admin/wipe", json={"confirm": "rensa"}).status_code == 400
        r = c.post("/api/admin/wipe", json={"confirm": "RENSA ALLT"})
        assert r.status_code == 200
        assert c.get("/api/admin/storage").json()["cameras"] == []
        assert Path(data.db_path).exists()
