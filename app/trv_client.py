"""Client for Trafikverket's open API (api.trafikinfo.trafikverket.se)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from xml.sax.saxutils import quoteattr

import httpx

log = logging.getLogger(__name__)

USER_AGENT = "trv-webcams/1.0 (+https://github.com/tubalainen/trv-webcams)"

_POINT_RE = re.compile(r"POINT\s*\(\s*([-0-9.]+)\s+([-0-9.]+)\s*\)")

_FIELDS = [
    "Id",
    "Name",
    "Type",
    "Description",
    "Direction",
    "CountyNo",
    "PhotoUrl",
    "PhotoTime",
    "Active",
    "HasFullSizePhoto",
    "Geometry.WGS84",
]


def _attr(value: str) -> str:
    """XML attribute value, always double quoted and escaped."""
    return quoteattr(value, {'"': "&quot;"})


@dataclass
class CameraInfo:
    id: str
    name: str = ""
    type: str = ""
    description: str = ""
    direction: str = ""
    county_no: int | None = None
    lat: float | None = None
    lon: float | None = None
    photo_url: str = ""
    photo_time: datetime | None = None
    active: bool = True
    has_fullsize: bool = False


def parse_point(wkt: str | None) -> tuple[float | None, float | None]:
    """Parse 'POINT (lon lat)' into (lat, lon)."""
    if not wkt:
        return None, None
    m = _POINT_RE.search(wkt)
    if not m:
        return None, None
    lon, lat = float(m.group(1)), float(m.group(2))
    return lat, lon


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def parse_camera(raw: dict) -> CameraInfo:
    lat, lon = parse_point((raw.get("Geometry") or {}).get("WGS84"))
    county = raw.get("CountyNo")
    if isinstance(county, list):
        county = county[0] if county else None
    return CameraInfo(
        id=raw["Id"],
        name=raw.get("Name") or "",
        type=raw.get("Type") or "",
        description=raw.get("Description") or "",
        direction=str(raw.get("Direction") or ""),
        county_no=int(county) if county is not None else None,
        lat=lat,
        lon=lon,
        photo_url=raw.get("PhotoUrl") or "",
        photo_time=parse_time(raw.get("PhotoTime")),
        active=bool(raw.get("Active", True)),
        has_fullsize=bool(raw.get("HasFullSizePhoto", False)),
    )


def build_query(api_key: str, ids: list[str] | None = None) -> str:
    includes = "".join(f"<INCLUDE>{f}</INCLUDE>" for f in _FIELDS)
    filt = ""
    if ids is not None:
        values = ",".join(ids)
        filt = f'<FILTER><IN name="Id" value={_attr(values)} /></FILTER>'
    return (
        f"<REQUEST><LOGIN authenticationkey={_attr(api_key)} />"
        f'<QUERY objecttype="Camera" schemaversion="1">{filt}{includes}</QUERY></REQUEST>'
    )


@dataclass
class StationInfo:
    id: str
    name: str = ""
    lat: float | None = None
    lon: float | None = None
    has_road_temp: bool = False  # some stations only measure air temperature


@dataclass
class RoadTempSample:
    station_id: str
    sample: datetime
    value: float


def build_station_query(api_key: str) -> str:
    return (
        f"<REQUEST><LOGIN authenticationkey={_attr(api_key)} />"
        '<QUERY objecttype="WeatherMeasurepoint" schemaversion="2.1">'
        "<INCLUDE>Id</INCLUDE><INCLUDE>Name</INCLUDE><INCLUDE>Geometry.WGS84</INCLUDE>"
        "<INCLUDE>Observation.Surface.Temperature.Value</INCLUDE>"
        "</QUERY></REQUEST>"
    )


def build_road_temp_query(api_key: str, station_ids: list[str], since: datetime) -> str:
    return (
        f"<REQUEST><LOGIN authenticationkey={_attr(api_key)} />"
        '<QUERY objecttype="WeatherObservation" schemaversion="2.1"><FILTER>'
        f'<IN name="Measurepoint.Id" value={_attr(",".join(station_ids))} />'
        f'<GT name="Sample" value={_attr(since.isoformat())} />'
        "</FILTER><INCLUDE>Measurepoint.Id</INCLUDE><INCLUDE>Sample</INCLUDE>"
        "<INCLUDE>Surface.Temperature.Value</INCLUDE></QUERY></REQUEST>"
    )


class TrvClient:
    def __init__(
        self, api_key: str, api_url: str, timeout: float = 30.0, client: httpx.AsyncClient | None = None
    ) -> None:
        if not api_key:
            raise ValueError("TRV_API_KEY is not configured")
        self._api_key = api_key
        self._api_url = api_url
        self._client = client or httpx.AsyncClient(
            timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True
        )
        self._owns_client = client is None

    async def __aenter__(self) -> TrvClient:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @property
    def http(self) -> httpx.AsyncClient:
        return self._client

    async def fetch_cameras(self, ids: list[str] | None = None) -> list[CameraInfo]:
        """Fetch camera metadata. ids=None fetches all cameras."""
        if ids is not None and not ids:
            return []
        result: list[CameraInfo] = []
        chunks = [None] if ids is None else [ids[i : i + 200] for i in range(0, len(ids), 200)]
        for chunk in chunks:
            body = build_query(self._api_key, chunk)
            resp = await self._client.post(
                self._api_url, content=body.encode(), headers={"Content-Type": "text/xml"}
            )
            resp.raise_for_status()
            data = resp.json()
            for res in data.get("RESPONSE", {}).get("RESULT", []):
                if "ERROR" in res:
                    raise RuntimeError(f"Trafikverket API error: {res['ERROR']}")
                for raw in res.get("Camera", []):
                    try:
                        result.append(parse_camera(raw))
                    except (KeyError, ValueError) as e:  # pragma: no cover - defensive
                        log.warning("Skipping malformed camera record: %s", e)
        return result

    async def _query(self, body: str, objecttype: str) -> list[dict]:
        resp = await self._client.post(
            self._api_url, content=body.encode(), headers={"Content-Type": "text/xml"}
        )
        resp.raise_for_status()
        out: list[dict] = []
        for res in resp.json().get("RESPONSE", {}).get("RESULT", []):
            if "ERROR" in res:
                raise RuntimeError(f"Trafikverket API error: {res['ERROR']}")
            out.extend(res.get(objecttype, []))
        return out

    async def fetch_stations(self) -> list[StationInfo]:
        """All road weather stations (WeatherMeasurepoint)."""
        result = []
        for raw in await self._query(build_station_query(self._api_key), "WeatherMeasurepoint"):
            lat, lon = parse_point((raw.get("Geometry") or {}).get("WGS84"))
            surface = ((raw.get("Observation") or {}).get("Surface") or {}).get("Temperature") or {}
            result.append(
                StationInfo(
                    id=str(raw["Id"]),
                    name=raw.get("Name") or "",
                    lat=lat,
                    lon=lon,
                    has_road_temp=surface.get("Value") is not None,
                )
            )
        return result

    async def fetch_road_temps(
        self, station_ids: list[str], since: datetime, chunk: int = 10
    ) -> list[RoadTempSample]:
        """Road surface temperatures after `since` (Trafikverket keeps about 7 days)."""
        result: list[RoadTempSample] = []
        for i in range(0, len(station_ids), chunk):
            body = build_road_temp_query(self._api_key, station_ids[i : i + chunk], since)
            for raw in await self._query(body, "WeatherObservation"):
                value = ((raw.get("Surface") or {}).get("Temperature") or {}).get("Value")
                sample = parse_time(raw.get("Sample"))
                mp = (raw.get("Measurepoint") or {}).get("Id")
                if value is None or sample is None or mp is None:
                    continue
                result.append(RoadTempSample(str(mp), sample, float(value)))
        return result

    async def download(self, url: str) -> bytes:
        resp = await self._client.get(url)
        resp.raise_for_status()
        return resp.content
