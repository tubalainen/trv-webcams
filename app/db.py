from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    create_engine,
    event,
    inspect,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


class Camera(Base):
    __tablename__ = "cameras"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, default="")
    type: Mapped[str] = mapped_column(String, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    direction: Mapped[str] = mapped_column(String, default="")
    county_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    photo_url: Mapped[str] = mapped_column(String, default="")
    has_fullsize: Mapped[bool] = mapped_column(Boolean, default=False)
    trv_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Selected by the user for collection + timelapse generation
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    last_photo_time: Mapped[str | None] = mapped_column(String, nullable=True)  # ISO 8601
    last_snapshot_path: Mapped[str | None] = mapped_column(String, nullable=True)
    snapshot_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    # Road temperature graph in timelapses (user can switch it off per camera)
    temp_overlay: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    # Nearest road weather station within TEMP_STATION_MAX_KM, set by weather.assign_stations
    station_id: Mapped[str | None] = mapped_column(String, nullable=True)
    station_distance_km: Mapped[float | None] = mapped_column(Float, nullable=True)


class Timelapse(Base):
    __tablename__ = "timelapses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"))
    period: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)  # mp4 | gif
    path: Mapped[str] = mapped_column(String)
    frames: Mapped[int] = mapped_column(Integer, default=0)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    encoder: Mapped[str] = mapped_column(String, default="")
    range_start: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    range_end: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime())
    # Name of the weather station whose road temperature is drawn, empty when no graph
    temp_station: Mapped[str] = mapped_column(String, default="", server_default="")

    __table_args__ = (Index("ix_timelapse_cam_period_kind", "camera_id", "period", "kind", unique=True),)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    camera_id: Mapped[str] = mapped_column(String, index=True)
    periods: Mapped[str] = mapped_column(String)  # comma separated
    trigger: Mapped[str] = mapped_column(String, default="manual")  # manual | nightly
    status: Mapped[str] = mapped_column(String, default="queued")  # queued|running|done|failed
    message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)


class WeatherStation(Base):
    """Trafikverket road weather station (WeatherMeasurepoint)."""

    __tablename__ = "weather_stations"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, default="")
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Reports road surface temperature (some stations only measure the air)
    has_road_temp: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)


class RoadTemperature(Base):
    """Road surface temperature samples (naive UTC), kept archive_retention_days."""

    __tablename__ = "road_temperatures"

    station_id: Mapped[str] = mapped_column(String, primary_key=True)
    sample: Mapped[datetime] = mapped_column(DateTime(), primary_key=True)
    value: Mapped[float] = mapped_column(Float)


def utcnow() -> datetime:
    """Naive UTC timestamp (SQLite has no timezone support)."""
    return datetime.now(UTC).replace(tzinfo=None)


_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def init_db(url: str | None = None) -> None:
    global _engine, _SessionLocal
    settings = get_settings()
    if url is None:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{settings.db_path}"
    _engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(_engine, "connect")
    def _pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(_engine)
    _add_missing_columns(_engine)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)


def _add_missing_columns(engine) -> None:
    """Minimal migration: add columns that exist in the models but not in an older database.

    create_all() only creates missing tables. New columns must be nullable or have a
    server_default so that existing rows stay valid.
    """
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing:
                    continue
                ddl = f"ALTER TABLE {table.name} ADD COLUMN {col.name} {col.type.compile(engine.dialect)}"
                if col.server_default is not None:
                    ddl += f" DEFAULT '{col.server_default.arg}'"
                conn.execute(text(ddl))


@contextmanager
def session_scope() -> Iterator[Session]:
    if _SessionLocal is None:
        init_db()
    assert _SessionLocal is not None
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
