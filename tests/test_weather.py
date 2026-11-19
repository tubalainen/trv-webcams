from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from sqlalchemy import select

from app import collector, weather
from app.db import Camera, RoadTemperature, WeatherStation, session_scope
from app.trv_client import TrvClient, build_road_temp_query

NOW = datetime(2026, 11, 19, 8, 0, tzinfo=UTC)


def test_haversine_and_nearest():
    # Göteborg -> Stockholm is about 400 km
    assert weather.haversine_km(57.7089, 11.9746, 59.3293, 18.0686) == pytest.approx(398, abs=5)
    stations = [("far", 59.33, 18.07), ("near", 57.72, 11.99), ("nearer", 57.71, 11.975)]
    assert weather.nearest_station(57.7089, 11.9746, stations, 20)[0] == "nearer"
    assert weather.nearest_station(57.7089, 11.9746, [("far", 59.33, 18.07)], 20) is None


def test_road_temp_query():
    q = build_road_temp_query("key", ["1", "2"], NOW)
    assert '<IN name="Measurepoint.Id" value="1,2" />' in q
    assert '<GT name="Sample" value="2026-11-19T08:00:00+00:00" />' in q
    assert "Surface.Temperature.Value" in q


def _stations_response():
    return {
        "RESPONSE": {
            "RESULT": [
                {
                    "WeatherMeasurepoint": [
                        {
                            "Id": "222",
                            "Name": "Nära",
                            "Geometry": {"WGS84": "POINT (11.99 57.72)"},
                            "Observation": {"Surface": {"Temperature": {"Value": 0.5}}},
                        },
                        {  # closest, but only measures air temperature
                            "Id": "111",
                            "Name": "Bara luft",
                            "Geometry": {"WGS84": "POINT (11.9746 57.7089)"},
                            "Observation": {"Air": {"Temperature": {"Value": 0.5}}},
                        },
                        {
                            "Id": "333",
                            "Name": "Långt bort",
                            "Geometry": {"WGS84": "POINT (18.07 59.33)"},
                            "Observation": {"Surface": {"Temperature": {"Value": 0.5}}},
                        },
                    ]
                }
            ]
        }
    }


def _obs(station, minutes_ago, value):
    t = (NOW - timedelta(minutes=minutes_ago)).isoformat()
    return {"Measurepoint": {"Id": int(station)}, "Sample": t, "Surface": {"Temperature": {"Value": value}}}


@respx.mock
async def test_sync_assign_collect_series_prune(settings):
    with session_scope() as s:
        s.add(Camera(id="CAM_1", lat=57.7089, lon=11.9746, enabled=True))
        s.add(Camera(id="CAM_FAR", lat=67.85, lon=20.22, enabled=True))  # Kiruna, no station nearby
    api = respx.post(settings.trv_api_url)
    api.mock(return_value=httpx.Response(200, json=_stations_response()))
    async with TrvClient("k", settings.trv_api_url) as client:
        assert await weather.sync_stations(settings, client) == 3
    with session_scope() as s:
        cam, far = s.get(Camera, "CAM_1"), s.get(Camera, "CAM_FAR")
        assert cam.station_id == "222" and cam.station_distance_km < 2
        assert far.station_id is None
        assert s.get(WeatherStation, "222").name == "Nära"
        assert s.get(WeatherStation, "111").has_road_temp is False

    api.mock(
        return_value=httpx.Response(
            200,
            json={
                "RESPONSE": {
                    "RESULT": [
                        {
                            "WeatherObservation": [
                                _obs(222, 10, -1.5),
                                _obs(222, 5, -0.5),
                                _obs(222, 5, -0.5),
                                {"Measurepoint": {"Id": 222}, "Sample": NOW.isoformat()},  # no surface value
                            ]
                        }
                    ]
                }
            },
        )
    )
    async with TrvClient("k", settings.trv_api_url) as client:
        assert await weather.collect_temps(settings, client, now=NOW) == 3
        # Only the station of enabled cameras is queried, 7 days back for a new station
        body = api.calls[-1].request.content.decode()
        assert 'value="222"' in body and "2026-11-12T08:00:00+00:00" in body
        # Second run starts from the newest stored sample; duplicates are ignored
        await weather.collect_temps(settings, client, now=NOW)
        assert "2026-11-19T07:00:00+00:00" in api.calls[-1].request.content.decode()
    with session_scope() as s:
        assert len(s.scalars(select(RoadTemperature)).all()) == 2

    pts = weather.series("222", NOW - timedelta(hours=1), NOW)
    assert [v for _, v in pts] == [-1.5, -0.5] and pts[0][0].tzinfo is not None
    assert weather.prune(settings, now=NOW + timedelta(days=366)) == 2


async def test_collect_without_stations(settings):
    async with TrvClient("k", settings.trv_api_url) as client:
        assert await weather.collect_temps(settings, client, now=NOW) == 0


@respx.mock
async def test_collector_wrappers(settings):
    respx.post(settings.trv_api_url).mock(return_value=httpx.Response(200, json=_stations_response()))
    assert await collector.sync_weather_stations(settings) == 3
    assert await collector.collect_road_temps(settings) == 0
