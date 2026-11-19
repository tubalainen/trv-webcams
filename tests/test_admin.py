import time

import pytest
from fastapi.testclient import TestClient

from app import settings_store
from app.api import admin
from app.db import AppSetting, session_scope
from app.main import create_app


@pytest.fixture
def admin_settings(settings, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "hemligt")
    return settings


def test_coerce_validates():
    f = settings_store.FIELDS_BY_KEY
    assert settings_store.coerce(f["collect_interval_minutes"], "10") == 10
    assert settings_store.coerce(f["trv_fullsize"], "true") is True
    assert settings_store.coerce(f["hwaccel"], "nvidia") == "nvidia"
    for key, bad in [
        ("collect_interval_minutes", 0),
        ("collect_interval_minutes", "x"),
        ("hwaccel", "amiga"),
        ("trv_fullsize", "kanske"),
        ("output_fps", 2.5),
    ]:
        with pytest.raises(settings_store.SettingsError):
            settings_store.coerce(f[key], bad)


def test_save_apply_and_reset(settings):
    env = settings_store.env_defaults_of(settings)
    changed = settings_store.save(settings, {"collect_interval_minutes": 3, "trv_fullsize": True})
    assert set(changed) == {"collect_interval_minutes", "trv_fullsize"}
    assert settings.collect_interval_minutes == 3
    # A restart applies the stored values on top of the environment
    settings.collect_interval_minutes = 5
    settings_store.apply_stored(settings, env)
    assert settings.collect_interval_minutes == 3 and settings.trv_fullsize is True
    settings_store.reset(settings, ["collect_interval_minutes"], env)
    assert settings.collect_interval_minutes == env["collect_interval_minutes"]
    with pytest.raises(settings_store.SettingsError):
        settings_store.save(settings, {"trv_api_key": "x"})  # secrets are not admin settings


def test_tokens(admin_settings):
    tok = admin.make_token()
    assert admin.valid_token(tok)
    assert not admin.valid_token(tok + "0")
    assert not admin.valid_token(tok, now=time.time() + admin.SESSION_SECONDS + 10)
    admin_settings.admin_password = "nytt"
    assert not admin.valid_token(tok)  # changing the password ends sessions


def test_admin_disabled_without_password(settings):
    with TestClient(create_app()) as c:
        assert c.get("/api/admin/session").json() == {"enabled": False, "logged_in": False}
        assert c.get("/api/admin/settings").status_code == 403
        assert c.post("/api/admin/login", json={"password": ""}).status_code == 403


def test_admin_flow(admin_settings, monkeypatch):
    monkeypatch.setattr(admin, "FAILED_LOGIN_DELAY", 0)
    with TestClient(create_app()) as c:
        assert c.get("/api/admin/settings").status_code == 401
        assert c.put("/api/admin/settings", json={"values": {"gif_fps": 5}}).status_code == 401
        assert c.post("/api/admin/login", json={"password": "fel"}).status_code == 401
        r = c.post("/api/admin/login", json={"password": "hemligt"})
        assert r.status_code == 200 and "httponly" in r.headers["set-cookie"].lower()
        assert c.get("/api/admin/session").json()["logged_in"] is True

        data = c.get("/api/admin/settings").json()
        fields = {f["key"]: f for f in data["fields"]}
        assert fields["trv_fullsize"]["value"] is False and "trv_api_key" not in fields
        assert {g["key"] for g in data["groups"]} >= {"cameras", "collect", "encoding"}

        r = c.put(
            "/api/admin/settings", json={"values": {"max_concurrent_jobs": 3, "temp_station_max_km": 15}}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["changed"] == ["max_concurrent_jobs", "temp_station_max_km"]
        assert any("3 jobb" in n for n in body["applied"])
        assert admin_settings.max_concurrent_jobs == 3
        with session_scope() as s:
            assert s.get(AppSetting, "max_concurrent_jobs").value == "3"

        assert c.put("/api/admin/settings", json={"values": {"gif_width": 5}}).status_code == 400
        r = c.put("/api/admin/settings", json={"reset": ["max_concurrent_jobs"]}).json()
        assert {f["key"]: f for f in r["fields"]}["max_concurrent_jobs"]["saved"] is False

        c.post("/api/admin/logout")
        assert c.get("/api/admin/settings").status_code == 401


async def test_job_pool_resize(settings):
    from app.timelapse import JobQueue

    q = JobQueue(settings)
    q.resize(3)
    assert len(q._workers) == 3
    q.resize(1)
    q.queue.put_nowait(999_999)  # unknown job: wakes a worker, which then exits
    q.queue.put_nowait(999_998)
    import asyncio

    await asyncio.sleep(0.05)
    assert sum(not w.done() for w in q._workers) == 1
    await q.stop()


def test_reschedule(settings):
    from app.scheduler import build_scheduler, reschedule
    from app.timelapse import JobQueue

    sched = build_scheduler(settings, JobQueue(settings))
    settings.collect_interval_minutes, settings.nightly_hour = 2, 4
    reschedule(sched, settings)
    assert sched.get_job("collect").trigger.interval.total_seconds() == 120
    assert "hour='4'" in str(sched.get_job("retention").trigger)
    # No automatic timelapse generation any more (since 0.1.6)
    assert {j.id for j in sched.get_jobs()} == {"collect", "temps", "sync", "retention"}


def test_batch_generation_requires_admin(admin_settings, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from app.db import Camera

    monkeypatch.setattr(admin, "FAILED_LOGIN_DELAY", 0)
    now = datetime.now(UTC)
    with session_scope() as s:
        s.add(Camera(id="A", name="A", enabled=True, county_no=14))
        s.add(Camera(id="B", name="B", enabled=True, county_no=14))
    day = admin_settings.snapshot_dir / "A" / now.astimezone().strftime("%Y-%m-%d")
    day.mkdir(parents=True)
    (day / "000000.jpg").write_bytes(b"x")
    body = {"scope": "county", "county_no": 14, "start": (now - timedelta(days=1)).isoformat(),
            "end": now.isoformat(), "formats": ["gif"], "duration_s": 10}  # fmt: skip
    with TestClient(create_app()) as c:
        assert c.post("/api/admin/batches", json=body).status_code == 401
        c.post("/api/admin/login", json={"password": "hemligt"})
        r = c.post("/api/admin/batches", json=body)
        assert r.status_code == 202
        b = r.json()
        assert b["total"] == 1 and b["skipped_names"] == ["B"] and b["county"] == "Västra Götaland"
        progress = c.get(f"/api/batches/{b['id']}").json()
        assert sum(progress["status"].values()) == 1
        assert (
            c.post("/api/admin/batches", json={**body, "scope": "county", "county_no": 1}).status_code == 400
        )
        assert c.post("/api/admin/batches", json={**body, "scope": "planet"}).status_code == 400
        assert c.get("/api/batches/999").status_code == 404


def test_archive_images_api(admin_settings, monkeypatch):
    from app.db import Camera

    monkeypatch.setattr(admin, "FAILED_LOGIN_DELAY", 0)
    with session_scope() as s:
        s.add(Camera(id="A", name="A"))
    day = admin_settings.archive_dir / "A" / "2025-12-01"
    day.mkdir(parents=True)
    (day / "08.jpg").write_bytes(b"jpg")
    with TestClient(create_app()) as c:
        assert c.get("/api/admin/cameras/A/archive", params={"day": "2025-12-01"}).status_code == 401
        c.post("/api/admin/login", json={"password": "hemligt"})
        assert c.get("/api/admin/cameras/A/archive", params={"day": "2025-12-01"}).json()["frames"] == ["08"]
        assert c.get("/api/admin/cameras/A/archive/2025-12-01/08.jpg").content == b"jpg"
        assert c.get("/api/admin/cameras/A/archive", params={"day": ".."}).status_code == 400
        assert c.get("/api/admin/cameras/A/archive/2025-12-01/app.db").status_code == 404
        r = c.post("/api/admin/cameras/A/delete-images", json={"days": ["2025-12-01"]})
        assert r.status_code == 200 and r.json()["files"] == 1
