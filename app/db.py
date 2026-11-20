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
    # Per camera choices: "default" follows the admin setting, otherwise "on"/"off"
    # (temperature graph) and "high"/"low" (image resolution).
    temp_overlay_mode: Mapped[str] = mapped_column(String, default="default", server_default="default")
    resolution_mode: Mapped[str] = mapped_column(String, default="default", server_default="default")
    # Size of the camera's full size image (?type=fullsize), read from its JPEG header
    fullsize_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fullsize_height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fullsize_checked_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    # Nearest road weather station within TEMP_STATION_MAX_KM, set by weather.assign_stations
    station_id: Mapped[str | None] = mapped_column(String, nullable=True)
    station_distance_km: Mapped[float | None] = mapped_column(Float, nullable=True)


class Timelapse(Base):
    """Per period clips from 0.1.0–0.1.5 (nightly generation). Only read once, to migrate
    the rows into `clips`; kept so the table can still be mapped in older databases."""

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


class Clip(Base):
    """A generated timelapse (MP4 and/or GIF) for one camera and time range."""

    __tablename__ = "clips"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"), index=True)
    batch_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    range_start: Mapped[datetime] = mapped_column(DateTime())
    range_end: Mapped[datetime] = mapped_column(DateTime())
    duration_s: Mapped[int] = mapped_column(Integer, default=0)
    frames: Mapped[int] = mapped_column(Integer, default=0)
    mp4_path: Mapped[str | None] = mapped_column(String, nullable=True)
    gif_path: Mapped[str | None] = mapped_column(String, nullable=True)
    mp4_bytes: Mapped[int] = mapped_column(Integer, default=0)
    gif_bytes: Mapped[int] = mapped_column(Integer, default=0)
    encoder: Mapped[str] = mapped_column(String, default="")
    temp_station: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime())


class Batch(Base):
    """Generation for many cameras at once (a county or all of Sweden): one job per camera."""

    __tablename__ = "batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(String)  # county | all
    county_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    range_start: Mapped[datetime] = mapped_column(DateTime())
    range_end: Mapped[datetime] = mapped_column(DateTime())
    formats: Mapped[str] = mapped_column(String)
    duration_s: Mapped[int] = mapped_column(Integer)
    temp_overlay: Mapped[str] = mapped_column(String, default="default")
    total: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime())


class Job(Base):
    """One clip to generate. Rows from before 0.1.6 used `periods` instead of a range."""

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    camera_id: Mapped[str] = mapped_column(String, index=True)
    periods: Mapped[str] = mapped_column(String, default="")  # legacy (nightly per period jobs)
    trigger: Mapped[str] = mapped_column(String, default="manual")  # manual | batch | nightly (legacy)
    batch_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    range_start: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    range_end: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    formats: Mapped[str] = mapped_column(String, default="mp4,gif", server_default="mp4,gif")
    duration_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    temp_overlay: Mapped[str] = mapped_column(String, default="default", server_default="default")
    clip_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String, default="queued")  # queued|running|done|failed|skipped
    message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)


class AppSetting(Base):
    """Settings saved on the admin page (JSON values), see app.settings_store."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text)


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
    added = _add_missing_columns(_engine)
    _migrate_data(_engine, added)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)


def _add_missing_columns(engine) -> set[str]:
    """Minimal migration: add columns that exist in the models but not in an older database.

    create_all() only creates missing tables. New columns must be nullable or have a
    server_default so that existing rows stay valid.
    """
    insp = inspect(engine)
    added: set[str] = set()
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
                added.add(f"{table.name}.{col.name}")
    return added


def _migrate_data(engine, added: set[str]) -> None:
    """One-off data conversions for columns that were just added (and old tables)."""
    _migrate_timelapses_to_clips(engine)
    # Before 0.1.10 clips from a county/all generation did not get the batch id (only the job had it)
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE clips SET batch_id = (SELECT jobs.batch_id FROM jobs WHERE jobs.clip_id = clips.id) "
                "WHERE batch_id IS NULL AND EXISTS "
                "(SELECT 1 FROM jobs WHERE jobs.clip_id = clips.id AND jobs.batch_id IS NOT NULL)"
            )
        )
    if "cameras.temp_overlay_mode" in added:
        cols = {c["name"] for c in inspect(engine).get_columns("cameras")}
        if "temp_overlay" in cols:  # 0.1.3 stored an on/off flag per camera
            with engine.begin() as conn:
                conn.execute(text("UPDATE cameras SET temp_overlay_mode = 'off' WHERE temp_overlay = 0"))


def _migrate_timelapses_to_clips(engine) -> None:
    """0.1.6: turn the per period clips of the nightly generation into library clips.

    The files stay where they are ({period}.mp4/.gif); the clip rows point at them.
    """
    with Session(engine) as s:
        rows = list(s.query(Timelapse).all())
        if not rows:
            return
        groups: dict[tuple[str, str], list[Timelapse]] = {}
        for r in rows:
            groups.setdefault((r.camera_id, r.period), []).append(r)
        for (_cam, _period), parts in groups.items():
            by_kind = {p.kind: p for p in parts}
            first = parts[0]
            mp4, gif = by_kind.get("mp4"), by_kind.get("gif")
            s.add(
                Clip(
                    camera_id=first.camera_id,
                    range_start=first.range_start or first.created_at,
                    range_end=first.range_end or first.created_at,
                    duration_s=round((mp4.frames if mp4 else 0) / 30),
                    frames=mp4.frames if mp4 else (gif.frames if gif else 0),
                    mp4_path=mp4.path if mp4 else None,
                    gif_path=gif.path if gif else None,
                    mp4_bytes=mp4.size_bytes if mp4 else 0,
                    gif_bytes=gif.size_bytes if gif else 0,
                    encoder=mp4.encoder if mp4 else "",
                    temp_station=first.temp_station or "",
                    created_at=max(p.created_at for p in parts),
                )
            )
            for p in parts:
                s.delete(p)
        s.commit()


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
