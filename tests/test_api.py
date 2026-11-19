from fastapi.testclient import TestClient

from app.db import Camera, session_scope
from app.main import create_app


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
        assert detail["timelapses"] == {} and detail["enabled"] is True
        assert c.get("/api/cameras/NOPE").status_code == 404

        r = c.get("/api/cameras/CAM_1/latest.jpg", follow_redirects=False)
        assert r.status_code == 307

        job = c.post("/api/cameras/CAM_1/timelapses", json={"periods": ["1d"]})
        assert job.status_code == 202
        assert c.get(f"/api/jobs/{job.json()['id']}").status_code == 200
        assert c.post("/api/cameras/CAM_1/timelapses", json={"periods": ["2d"]}).status_code == 400

        st = c.get("/api/status").json()
        assert st["cameras_enabled"] == 2 and st["cameras_total"] == 2
        assert c.get("/media/timelapses/CAM_1/1d.mp4").status_code == 404
        assert c.get("/media/timelapses/..%2F/1d.mp4").status_code == 404
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
