from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app.db import Camera, session_scope
from app.main import create_app

TZ = ZoneInfo("Europe/Stockholm")


def _client(settings):
    with session_scope() as s:
        s.add(
            Camera(
                id="CAM_1",
                name="Göteborg E6",
                county_no=14,
                lat=57.7,
                lon=11.9,
                photo_url="https://img.example/1.jpg",
            )
        )
        s.add(Camera(id="CAM_2", name="Kiruna", county_no=25, lat=67.8, lon=20.2))
    return TestClient(create_app())


def test_api_flow(settings):
    with _client(settings) as c:
        assert c.get("/api/health").json()["status"] == "ok"
        cams = c.get("/api/cameras").json()
        assert [x["name"] for x in cams] == ["Göteborg E6", "Kiruna"]
        assert cams[0]["county"] == "Västra Götaland"
        assert c.get("/api/cameras", params={"q": "kir"}).json()[0]["id"] == "CAM_2"

        assert c.patch("/api/cameras/CAM_1", json={"enabled": True}).json()["enabled"] is True
        r = c.patch("/api/cameras/CAM_1", json={"temp_overlay_mode": "off"}).json()
        assert r["temp_overlay"] is False and r["enabled"] is True
        d = c.get("/api/cameras/CAM_1").json()
        assert d["temp_overlay_mode"] == "off" and d["temp_overlay"] is False and d["temp_station"] is None
        assert c.patch("/api/cameras/CAM_1", json={"temp_overlay_mode": "maybe"}).status_code == 400
        r = c.patch("/api/cameras/CAM_1", json={"resolution_mode": "high"}).json()
        assert r["resolution_mode"] == "high" and r["saves_fullsize"] is False  # no full size image
        assert c.patch("/api/cameras/CAM_1", json={"resolution_mode": "4k"}).status_code == 400
        assert [x["id"] for x in c.get("/api/cameras", params={"enabled": True}).json()] == ["CAM_1"]
        assert c.post("/api/cameras/bulk", json={"county": 25, "enabled": True}).json() == {"updated": 1}

        detail = c.get("/api/cameras/CAM_1").json()
        assert detail["clip_count"] == 0 and detail["enabled"] is True
        assert c.get("/api/cameras/NOPE").status_code == 404

        r = c.get("/api/cameras/CAM_1/latest.jpg", follow_redirects=False)
        assert r.status_code == 307

        opts = c.get("/api/generate/options").json()
        assert [p["key"] for p in opts["presets"]][:2] == ["1d", "7d"] and opts["durations"] == [
            10,
            30,
            60,
            120,
        ]

        now = datetime.now(UTC)
        body = {"start": (now - timedelta(days=1)).isoformat(), "end": now.isoformat(), "formats": ["mp4"]}
        r = c.post("/api/cameras/CAM_1/clips", json=body)
        assert r.status_code == 400 and "inga sparade bilder" in r.json()["detail"]
        snap = settings.snapshot_dir / "CAM_1" / f"{now.astimezone(TZ):%Y-%m-%d}" / "000000.jpg"
        snap.parent.mkdir(parents=True)
        snap.write_bytes(b"x")
        job = c.post("/api/cameras/CAM_1/clips", json=body)
        assert job.status_code == 202 and job.json()["formats"] == ["mp4"]
        assert c.get(f"/api/jobs/{job.json()['id']}").status_code == 200
        assert c.post("/api/cameras/CAM_1/clips", json={**body, "duration_s": 7}).status_code == 400
        assert c.post("/api/cameras/CAM_1/clips", json={**body, "formats": []}).status_code == 400
        assert c.get("/api/cameras/CAM_1/clips").json() == []
        assert c.get("/api/cameras/CAM_1/availability").json()["snapshots"] == [snap.parent.name]
        assert c.get("/api/availability", params={"county": 14}).json()["cameras"] == 1
        # County / all of Sweden needs the admin page (disabled without ADMIN_PASSWORD)
        r = c.post("/api/admin/batches", json={**body, "scope": "all"})
        assert r.status_code == 403

        st = c.get("/api/status").json()
        assert st["cameras_enabled"] == 2 and st["cameras_total"] == 2
        assert c.get("/media/clips/1.mp4").status_code == 404
        assert c.get("/media/clips/..%2Fapp.db").status_code == 404
        assert "TRV Webbkameror" in c.get("/").text


def test_snapshot_browsing(settings):
    base = settings.snapshot_dir / "CAM_1"
    for day, names in {"2026-11-18": ["235500", "000500"], "2026-11-19": ["084145", "083645"]}.items():
        (base / day).mkdir(parents=True)
        for n in names:
            (base / day / f"{n}.jpg").write_bytes(b"jpg-" + n.encode())
    (base / "2026-11-19" / "x.tmp").write_bytes(b"")
    with _client(settings) as c:
        r = c.get("/api/cameras/CAM_1/snapshots").json()
        assert r["days"] == ["2026-11-18", "2026-11-19"] and r["day"] == "2026-11-19"
        assert r["frames"] == ["083645", "084145"]
        r = c.get("/api/cameras/CAM_1/snapshots", params={"day": "2026-11-18"}).json()
        assert r["frames"] == ["000500", "235500"]
        assert c.get("/api/cameras/CAM_1/snapshots", params={"day": "2026-11-01"}).json()["frames"] == []
        assert c.get("/api/cameras/CAM_1/snapshots", params={"day": "../x"}).status_code == 400
        assert c.get("/api/cameras/CAM_2/snapshots").json() == {"days": [], "day": None, "frames": []}
        assert c.get("/api/cameras/NOPE/snapshots").status_code == 404

        img = c.get("/api/cameras/CAM_1/snapshots/2026-11-19/084145.jpg")
        assert img.status_code == 200 and img.content == b"jpg-084145"
        assert img.headers["content-type"] == "image/jpeg"
        assert c.get("/api/cameras/CAM_1/snapshots/2026-11-19/090000.jpg").status_code == 404
        assert c.get("/api/cameras/CAM_1/snapshots/2026-11-19/..%2F..%2Fapp.db").status_code == 404
        assert c.get("/api/cameras/CAM_1/snapshots/..%2F..%2F/084145.jpg").status_code == 404


def test_clip_media_uses_current_data_dir(settings):
    from app.db import Clip

    now = datetime(2026, 11, 19, 8)
    with _client(settings) as c:
        with session_scope() as s:
            # Migrated clip whose stored path points at an older DATA_DIR
            s.add(Clip(camera_id="CAM_1", range_start=now, range_end=now, created_at=now,
                       mp4_path="/old/data/timelapses/CAM_1/7d.mp4", gif_path="../../app.db"))  # fmt: skip
        f = settings.timelapse_dir / "CAM_1" / "7d.mp4"
        f.parent.mkdir(parents=True)
        f.write_bytes(b"mp4")
        clip = c.get("/api/cameras/CAM_1/clips").json()[0]
        assert c.get("/" + clip["mp4"]["url"]).content == b"mp4"
        assert c.get(f"/media/clips/{clip['id']}.gif").status_code == 404  # never outside timelapses/


def test_clip_parts_can_be_deleted_by_anyone(settings):
    from app.db import Clip

    now = datetime(2026, 11, 19, 8)
    with _client(settings) as c:
        with session_scope() as s:
            clip = Clip(camera_id="CAM_1", range_start=now, range_end=now, created_at=now)
            s.add(clip)
            s.flush()
            for kind in ("mp4", "gif"):
                f = settings.timelapse_dir / "CAM_1" / f"{clip.id}.{kind}"
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_bytes(b"x")
                setattr(clip, f"{kind}_path", str(f))
            cid = clip.id
        r = c.delete(f"/api/cameras/CAM_1/clips/{cid}", params={"kind": "mp4"})
        assert r.status_code == 200 and r.json()["timelapses"] == 0
        assert c.get("/api/cameras/CAM_1/clips").json()[0]["mp4"] is None
        assert c.delete(f"/api/cameras/CAM_2/clips/{cid}").status_code == 404
        assert c.delete(f"/api/cameras/CAM_1/clips/{cid}", params={"kind": "webm"}).status_code == 400
        assert c.delete(f"/api/cameras/CAM_1/clips/{cid}", params={"kind": "gif"}).json()["timelapses"] == 1
        assert c.get("/api/cameras/CAM_1/clips").json() == []
        assert c.delete(f"/api/cameras/CAM_1/clips/{cid}").status_code == 404


def test_ui_files_are_revalidated(settings):
    with _client(settings) as c:
        assert c.get("/generate.js").headers["cache-control"] == "no-cache"
        assert c.get("/").headers["cache-control"] == "no-cache"
        assert "no-cache" not in c.get("/vendor/leaflet/leaflet.js").headers.get("cache-control", "")
