import pytest

from app import db
from app.config import get_settings


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TRV_API_KEY", "test-key")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("HWACCEL", "cpu")
    get_settings.cache_clear()
    s = get_settings()
    s.data_dir.mkdir(parents=True)
    db.init_db()
    yield s
    get_settings.cache_clear()


def camera_record(
    cid="CAM_1",
    name="Testkamera",
    photo_time="2026-11-18T12:00:00.000+01:00",
    url="https://img.example/cam1.jpg",
):
    return {
        "Id": cid,
        "Name": name,
        "Type": "Trafikflödeskamera",
        "CountyNo": [14],
        "PhotoUrl": url,
        "PhotoTime": photo_time,
        "Active": True,
        "HasFullSizePhoto": True,
        "Geometry": {"WGS84": "POINT (11.984739 57.717617)"},
    }


def api_response(*records):
    return {"RESPONSE": {"RESULT": [{"Camera": list(records)}]}}
