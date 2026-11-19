import sqlite3

from app import db


def test_migration_adds_new_columns(tmp_path, settings):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE cameras (id VARCHAR PRIMARY KEY, name VARCHAR, enabled BOOLEAN)")
    con.execute("INSERT INTO cameras (id, name, enabled) VALUES ('C', 'Gammal', 1)")
    con.commit()
    con.close()

    db.init_db(f"sqlite:///{path}")
    with db.session_scope() as s:
        cam = s.get(db.Camera, "C")
        assert cam.name == "Gammal" and cam.temp_overlay_mode == "default" and cam.station_id is None
        assert cam.resolution_mode == "default" and cam.fullsize_width is None


def test_migration_keeps_temperature_choice_from_0_1_3(tmp_path, settings):
    path = tmp_path / "v013.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE cameras (id VARCHAR PRIMARY KEY, temp_overlay BOOLEAN NOT NULL DEFAULT '1')")
    con.executemany("INSERT INTO cameras (id, temp_overlay) VALUES (?, ?)", [("ON", 1), ("OFF", 0)])
    con.commit()
    con.close()

    db.init_db(f"sqlite:///{path}")
    with db.session_scope() as s:
        assert s.get(db.Camera, "ON").temp_overlay_mode == "default"
        assert s.get(db.Camera, "OFF").temp_overlay_mode == "off"
        s.add(db.Camera(id="NEW"))  # old NOT NULL column still gets its default


def test_migration_turns_period_timelapses_into_clips(tmp_path, settings):
    from datetime import datetime

    db.init_db(f"sqlite:///{tmp_path / 'v015.db'}")
    with db.session_scope() as s:
        s.add(db.Camera(id="C"))
        for kind in ("mp4", "gif"):
            s.add(
                db.Timelapse(camera_id="C", period="7d", kind=kind, path=f"/data/timelapses/C/7d.{kind}",
                             frames=900 if kind == "mp4" else 150, size_bytes=1000, encoder="h264_nvenc",
                             range_start=datetime(2026, 11, 12), range_end=datetime(2026, 11, 19),
                             created_at=datetime(2026, 11, 19, 2), temp_station="Stocksund")
            )  # fmt: skip
    db.init_db(f"sqlite:///{tmp_path / 'v015.db'}")  # restart runs the migration
    with db.session_scope() as s:
        clip = s.query(db.Clip).one()
        assert clip.mp4_path.endswith("7d.mp4") and clip.gif_path.endswith("7d.gif")
        assert clip.frames == 900 and clip.duration_s == 30 and clip.temp_station == "Stocksund"
        assert clip.range_start == datetime(2026, 11, 12) and s.query(db.Timelapse).count() == 0
