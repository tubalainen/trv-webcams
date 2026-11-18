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
