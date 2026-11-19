from datetime import date

from app import retention
from app.db import Camera, session_scope


def test_retention(settings):
    for root, day in [
        (settings.snapshot_dir, "2026-08-01"),
        (settings.snapshot_dir, "2026-11-01"),
        (settings.archive_dir, "2025-10-01"),
        (settings.archive_dir, "2026-08-01"),
    ]:
        d = root / "CAM" / day
        d.mkdir(parents=True)
        (d / "120000.jpg").write_bytes(b"x")
    with session_scope() as s:
        s.add(Camera(id="CAM", snapshot_count=99))
    res = retention.run_retention(settings, today=date(2026, 11, 18))
    assert res == {"snapshot_days_removed": 1, "archive_days_removed": 1, "road_temps_removed": 0}
    assert not (settings.snapshot_dir / "CAM" / "2026-08-01").exists()
    assert (settings.snapshot_dir / "CAM" / "2026-11-01").exists()
    assert (settings.archive_dir / "CAM" / "2026-08-01").exists()
    with session_scope() as s:
        assert s.get(Camera, "CAM").snapshot_count == 1
