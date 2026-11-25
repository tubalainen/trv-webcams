import subprocess
from datetime import datetime

import httpx
import pytest
import respx

from app import cameras, collector
from app.db import Camera, session_scope
from tests.conftest import api_response, camera_record


def _jpeg(w=1920, h=1080) -> bytes:
    return subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=gray:s={w}x{h}",
            "-frames:v",
            "1",
            "-f",
            "mjpeg",
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout


def test_jpeg_size():
    data = _jpeg(1280, 720)
    assert cameras.jpeg_size(data) == (1280, 720)
    assert cameras.jpeg_size(data[:2000]) == (1280, 720)  # header only
    assert cameras.jpeg_size(b"<html>") is None
    assert cameras.jpeg_size(data[:10]) is None


@pytest.mark.parametrize(
    ("mode", "default", "has", "checked", "width", "expected"),
    [
        ("default", False, True, None, None, False),
        ("default", True, True, None, None, True),  # not checked yet: trust Trafikverket
        ("high", False, True, datetime(2026, 1, 1), 1920, True),
        ("high", False, True, datetime(2026, 1, 1), None, False),  # checked, no full size image
        ("high", True, False, None, None, False),
        ("low", True, True, None, 1920, False),
    ],
)
def test_wants_fullsize(settings, mode, default, has, checked, width, expected):
    settings.trv_fullsize = default
    cam = Camera(
        id="C",
        resolution_mode=mode,
        has_fullsize=has,
        fullsize_checked_at=checked,
        fullsize_width=width,
        fullsize_height=1080 if width else None,
    )
    assert cameras.wants_fullsize(cam, settings) is expected


def test_temp_overlay_and_labels(settings):
    cam = Camera(
        id="C",
        temp_overlay_mode="default",
        has_fullsize=True,
        fullsize_height=720,
        fullsize_width=1280,
        fullsize_checked_at=datetime(2026, 1, 1),
    )
    settings.temp_overlay_default = False
    assert cameras.wants_temp_overlay(cam, settings) is False
    cam.temp_overlay_mode = "on"
    assert cameras.wants_temp_overlay(cam, settings) is True
    assert cameras.hd_label(cam) == "720p"
    cam.has_fullsize = False
    assert cameras.hd_label(cam) == ""


@respx.mock
async def test_probe_fullsize_and_collect_in_full_size(settings):
    with session_scope() as s:
        s.add(
            Camera(
                id="CAM_1",
                enabled=True,
                has_fullsize=True,
                photo_url="https://img.example/cam1.jpg",
                resolution_mode="high",
            )
        )
        s.add(Camera(id="CAM_2", has_fullsize=True, photo_url="https://img.example/cam2.jpg"))
    respx.get("https://img.example/cam1.jpg?type=fullsize").mock(
        return_value=httpx.Response(200, content=_jpeg(1920, 1080))
    )
    respx.get("https://img.example/cam2.jpg?type=fullsize").mock(return_value=httpx.Response(404))
    assert await collector.probe_fullsize(settings) == 2
    assert await collector.probe_fullsize(settings) == 0  # checked within the last week
    with session_scope() as s:
        c1, c2 = s.get(Camera, "CAM_1"), s.get(Camera, "CAM_2")
        assert (c1.fullsize_width, c1.fullsize_height) == (1920, 1080)
        assert c2.fullsize_width is None and c2.fullsize_checked_at is not None
        assert cameras.hd_label(c1) == "1080p" and not cameras.fullsize_available(c2)

    respx.post(settings.trv_api_url).mock(
        return_value=httpx.Response(200, json=api_response(camera_record()))
    )
    assert await collector.collect_once(settings) == 1
    saved = list((settings.snapshot_dir / "CAM_1").glob("*/*.jpg"))
    assert cameras.jpeg_size(saved[0].read_bytes()) == (1920, 1080)


@pytest.mark.parametrize(
    "type_, expected",
    [
        ("Trafikflödeskamera", "flow"),
        ("Väglagskamera", "road"),
        ("VÄGLAGSKAMERA", "road"),
        ("", ""),
        (None, ""),
    ],
)
def test_camera_kind(type_, expected):
    assert cameras.camera_kind(type_) == expected
