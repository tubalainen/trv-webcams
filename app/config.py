"""Application configuration, read from environment variables (or a .env file).

Secrets such as TRV_API_KEY must never be committed – provide them via the
environment or an untracked .env file.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Trafikverket API
    trv_api_key: str = Field(default="", description="Trafikverket API key (secret)")
    trv_api_url: str = "https://api.trafikinfo.trafikverket.se/v2/data.json"
    trv_fullsize: bool = False  # fetch ?type=fullsize images instead of normal

    # Storage
    data_dir: Path = Path("/data")

    # Server
    host: str = "0.0.0.0"
    port: int = 8933
    timezone: str = "Europe/Stockholm"
    log_level: str = "INFO"

    # Scheduling
    collect_interval_minutes: int = 5
    nightly_hour: int = 2
    nightly_minute: int = 0
    metadata_sync_hour: int = 1
    scheduler_enabled: bool = True

    # Retention
    snapshot_retention_days: int = 90
    archive_retention_days: int = 365

    # Collection
    download_concurrency: int = 16
    http_timeout_seconds: float = 30.0

    # Encoding
    hwaccel: str = "auto"  # auto | nvidia | vaapi | qsv | cpu
    vaapi_device: str = "/dev/dri/renderD128"
    max_concurrent_jobs: int = 2
    output_fps: int = 30
    gif_width: int = 480
    gif_fps: int = 10
    gif_max_frames: int = 150

    # Road temperature graph (Trafikverket road weather stations)
    temp_station_max_km: float = 20.0  # no graph when the nearest station is further away
    temp_collect_interval_minutes: int = 15

    # Cameras enabled on first start (comma separated camera ids)
    default_enabled_cameras: str = ""

    @property
    def snapshot_dir(self) -> Path:
        return self.data_dir / "snapshots"

    @property
    def archive_dir(self) -> Path:
        return self.data_dir / "archive"

    @property
    def timelapse_dir(self) -> Path:
        return self.data_dir / "timelapses"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def default_enabled_ids(self) -> list[str]:
        return [c.strip() for c in self.default_enabled_cameras.split(",") if c.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
