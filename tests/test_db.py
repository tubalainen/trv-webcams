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
        assert cam.name == "Gammal" and cam.temp_overlay is True and cam.station_id is None
