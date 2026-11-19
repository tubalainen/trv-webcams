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
