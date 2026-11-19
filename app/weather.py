"""Road surface temperatures from Trafikverket's road weather stations.

Each camera is linked to the nearest station within TEMP_STATION_MAX_KM. Trafikverket
only keeps about 7 days of observations, so samples for the stations of enabled cameras
are stored locally (road_temperatures) and kept as long as the hourly archive.
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.config import Settings
from app.db import Camera, RoadTemperature, WeatherStation, session_scope, utcnow
from app.trv_client import TrvClient

log = logging.getLogger(__name__)

TRV_HISTORY_DAYS = 7
EARTH_RADIUS_KM = 6371.0

_collect_lock = asyncio.Lock()


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def nearest_station(
    lat: float, lon: float, stations: list[tuple[str, float, float]], max_km: float
) -> tuple[str, float] | None:
    best: tuple[str, float] | None = None
    for sid, slat, slon in stations:
        d = haversine_km(lat, lon, slat, slon)
        if d <= max_km and (best is None or d < best[1]):
            best = (sid, d)
    return best


def assign_stations(settings: Settings) -> int:
    """Link every camera to its nearest station with a road temperature sensor within the limit.

    Returns the number of cameras that have a station.
    """
    with session_scope() as s:
        stations = [
            (st.id, st.lat, st.lon)
            for st in s.scalars(select(WeatherStation).where(WeatherStation.has_road_temp))
            if st.lat is not None and st.lon is not None
        ]
        linked = 0
        for cam in s.scalars(select(Camera)):
            hit = None
            if cam.lat is not None and cam.lon is not None:
                hit = nearest_station(cam.lat, cam.lon, stations, settings.temp_station_max_km)
            cam.station_id = hit[0] if hit else None
            cam.station_distance_km = round(hit[1], 1) if hit else None
            linked += hit is not None
    return linked


async def sync_stations(settings: Settings, client: TrvClient) -> int:
    """Fetch all road weather stations, upsert them and relink cameras."""
    infos = await client.fetch_stations()
    with session_scope() as s:
        existing = {st.id: st for st in s.scalars(select(WeatherStation))}
        for info in infos:
            st = existing.get(info.id)
            if st is None:
                st = WeatherStation(id=info.id)
                s.add(st)
            st.name, st.lat, st.lon, st.updated_at = info.name, info.lat, info.lon, utcnow()
            st.has_road_temp = info.has_road_temp
    linked = await asyncio.to_thread(assign_stations, settings)
    log.info("Synced %d road weather stations, %d cameras have a station nearby", len(infos), linked)
    return len(infos)


def _to_naive_utc(dt: datetime) -> datetime:
    return dt.astimezone(UTC).replace(tzinfo=None) if dt.tzinfo else dt


async def collect_temps(settings: Settings, client: TrvClient, now: datetime | None = None) -> int:
    """Store new road temperatures for the stations of enabled cameras. Returns new samples."""
    if _collect_lock.locked():
        return 0
    async with _collect_lock:
        now = now or datetime.now(UTC)
        oldest = now - timedelta(days=TRV_HISTORY_DAYS)
        with session_scope() as s:
            ids = sorted(
                {
                    sid
                    for sid in s.scalars(
                        select(Camera.station_id).where(Camera.enabled, Camera.station_id.is_not(None))
                    )
                }
            )
            last = dict(
                s.execute(
                    select(RoadTemperature.station_id, func.max(RoadTemperature.sample))
                    .where(RoadTemperature.station_id.in_(ids))
                    .group_by(RoadTemperature.station_id)
                ).all()
            )
        if not ids:
            return 0
        # Group stations by how far back they need data, so a new station gets 7 days
        # of history without refetching a week for every other station.
        groups: dict[datetime, list[str]] = {}
        for sid in ids:
            since = last.get(sid)
            since = max(since.replace(tzinfo=UTC), oldest) if since else oldest
            key = since.replace(minute=0, second=0, microsecond=0)
            groups.setdefault(key, []).append(sid)
        rows = []
        for since, sids in sorted(groups.items()):
            for smp in await client.fetch_road_temps(sids, since):
                rows.append(
                    {"station_id": smp.station_id, "sample": _to_naive_utc(smp.sample), "value": smp.value}
                )
        if rows:
            with session_scope() as s:
                for i in range(0, len(rows), 2000):
                    stmt = sqlite_insert(RoadTemperature).values(rows[i : i + 2000])
                    s.execute(stmt.on_conflict_do_nothing(index_elements=["station_id", "sample"]))
        log.info("Road temperatures: %d samples fetched for %d stations", len(rows), len(ids))
        return len(rows)


def series(station_id: str, start: datetime, end: datetime) -> list[tuple[datetime, float]]:
    """Samples between start and end as (aware UTC datetime, value), oldest first."""
    with session_scope() as s:
        rows = s.execute(
            select(RoadTemperature.sample, RoadTemperature.value)
            .where(
                RoadTemperature.station_id == station_id,
                RoadTemperature.sample >= _to_naive_utc(start),
                RoadTemperature.sample <= _to_naive_utc(end),
            )
            .order_by(RoadTemperature.sample)
        ).all()
    return [(t.replace(tzinfo=UTC), v) for t, v in rows]


def station_name(station_id: str | None) -> str:
    if not station_id:
        return ""
    with session_scope() as s:
        st = s.get(WeatherStation, station_id)
        return st.name if st else ""


def prune(settings: Settings, now: datetime | None = None) -> int:
    cutoff = _to_naive_utc((now or datetime.now(UTC)) - timedelta(days=settings.archive_retention_days))
    with session_scope() as s:
        res = s.execute(delete(RoadTemperature).where(RoadTemperature.sample < cutoff))
        return res.rowcount or 0
