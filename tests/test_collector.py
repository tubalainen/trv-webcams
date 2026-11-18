import httpx
import respx
from sqlalchemy import select

from app import collector
from app.db import Camera, session_scope
from tests.conftest import api_response, camera_record

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 100


@respx.mock
async def test_sync_and_collect(settings, monkeypatch):
    monkeypatch.setattr(settings, "default_enabled_cameras", "CAM_1")
    api = respx.post(settings.trv_api_url)
    api.mock(
        return_value=httpx.Response(
            200,
            json=api_response(
                camera_record("CAM_1"), camera_record("CAM_2", url="https://img.example/cam2.jpg")
            ),
        )
    )
    assert await collector.sync_metadata(settings) == 2
    with session_scope() as s:
        enabled = {c.id: c.enabled for c in s.scalars(select(Camera))}
    assert enabled == {"CAM_1": True, "CAM_2": False}

    img = respx.get("https://img.example/cam1.jpg").mock(return_value=httpx.Response(200, content=JPEG))
    api.mock(return_value=httpx.Response(200, json=api_response(camera_record("CAM_1"))))
    assert await collector.collect_once(settings) == 1
    snap = settings.snapshot_dir / "CAM_1" / "2026-11-18" / "120000.jpg"
    assert snap.read_bytes() == JPEG
    assert (settings.archive_dir / "CAM_1" / "2026-11-18" / "12.jpg").exists()
    # only the enabled camera is requested
    assert b'value="CAM_1"' in api.calls[-1].request.content

    # same PhotoTime -> no new download
    assert await collector.collect_once(settings) == 0
    assert img.call_count == 1

    # new photo within the same hour -> snapshot saved, archive keeps the first one
    api.mock(
        return_value=httpx.Response(
            200, json=api_response(camera_record("CAM_1", photo_time="2026-11-18T12:05:00+01:00"))
        )
    )
    assert await collector.collect_once(settings) == 1
    assert len(list((settings.archive_dir / "CAM_1" / "2026-11-18").iterdir())) == 1
    with session_scope() as s:
        assert s.get(Camera, "CAM_1").snapshot_count == 2


@respx.mock
async def test_collect_rejects_non_jpeg(settings):
    with session_scope() as s:
        s.add(Camera(id="CAM_1", enabled=True))
    respx.post(settings.trv_api_url).mock(
        return_value=httpx.Response(200, json=api_response(camera_record("CAM_1")))
    )
    respx.get("https://img.example/cam1.jpg").mock(return_value=httpx.Response(200, content=b"<html>"))
    assert await collector.collect_once(settings) == 0


async def test_collect_nothing_enabled(settings):
    assert await collector.collect_once(settings) == 0
