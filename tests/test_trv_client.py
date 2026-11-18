import httpx
import pytest
import respx

from app.trv_client import TrvClient, build_query, parse_camera, parse_point
from tests.conftest import api_response, camera_record

URL = "https://api.example/v2/data.json"


def test_parse_point():
    assert parse_point("POINT (11.984739 57.717617)") == (57.717617, 11.984739)
    assert parse_point(None) == (None, None)
    assert parse_point("garbage") == (None, None)


def test_parse_camera():
    c = parse_camera(camera_record())
    assert c.id == "CAM_1"
    assert c.county_no == 14
    assert c.lat == pytest.approx(57.717617)
    assert c.photo_time.isoformat() == "2026-11-18T12:00:00+01:00"


def test_build_query_escapes_and_filters():
    q = build_query('k"ey', ["A", "B"])
    assert 'authenticationkey="k&quot;ey"' in q
    assert '<IN name="Id" value="A,B" />' in q
    assert "<FILTER>" not in build_query("k")


@respx.mock
async def test_fetch_cameras():
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=api_response(camera_record())))
    async with TrvClient("key", URL) as c:
        cams = await c.fetch_cameras()
    assert [x.id for x in cams] == ["CAM_1"]
    assert b"authenticationkey" in route.calls[0].request.content


@respx.mock
async def test_fetch_cameras_error():
    respx.post(URL).mock(
        return_value=httpx.Response(200, json={"RESPONSE": {"RESULT": [{"ERROR": {"MESSAGE": "bad"}}]}})
    )
    async with TrvClient("key", URL) as c:
        with pytest.raises(RuntimeError):
            await c.fetch_cameras()


def test_requires_key():
    with pytest.raises(ValueError):
        TrvClient("", URL)
