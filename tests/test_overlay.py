from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app import overlay, storage

START = datetime(2026, 11, 18, 8, 0, tzinfo=UTC)
END = START + timedelta(days=1)


def _samples():
    return [(START + timedelta(minutes=5 * i), -3 + i * 0.02) for i in range(288)]


def test_fmt_temp():
    assert overlay.fmt_temp(-3.24) == "\u22123,2 °C"
    assert overlay.fmt_temp(4) == "4,0 °C"


@pytest.mark.skipif(not Path(overlay.FONT_FILES[0]).exists(), reason="fonts-dejavu-core missing")
def test_font_has_swedish_letters():
    font = overlay._font(20)
    assert getattr(font, "path", "").endswith("DejaVuSans.ttf")
    # Distinct glyphs (Pillow's ASCII-only fallback draws the same box for all of these)
    masks = {bytes(font.getmask(ch)) for ch in "äöå\u2212"}
    assert len(masks) == 4


def test_overlay_size_even():
    info = overlay.overlay_size(1281)
    assert info.width % 2 == 0 and info.height % 2 == 0 and info.width == 384


def test_axis_labels():
    r = overlay.GraphRenderer(_samples(), START, END, 300, 126, "Europe/Stockholm")
    assert r._axis_label(START) == "ons 09:00" and r._axis_label(END) == "tor 09:00"
    week = overlay.GraphRenderer(_samples(), START, START + timedelta(days=7), 300, 126, "Europe/Stockholm")
    assert week._axis_label(START) == "18/11"


def test_renderer_progresses_with_time():
    r = overlay.GraphRenderer(_samples(), START, END, 300, 126, "Europe/Stockholm")
    assert r.visible(START - timedelta(hours=1)) == 0
    assert r.visible(START + timedelta(hours=12)) < r.visible(END)
    assert r.current(START + timedelta(minutes=7)) == -2.98
    assert r.current(END + timedelta(hours=3)) is None  # stale
    img = r.render(START + timedelta(hours=12))
    assert img.size == (300, 126) and img.mode == "RGBA"
    assert img.getpixel((5, 60))[3] > 0  # background is drawn
    assert img.getpixel((0, 0))[3] == 0  # rounded corner stays transparent


def test_render_sequence_reuses_identical_frames(tmp_path: Path):
    samples = [(START, 1.0), (START + timedelta(hours=12), 2.0)]
    r = overlay.GraphRenderer(samples, START, END, 200, 84, "Europe/Stockholm")
    times = [START + timedelta(minutes=m) for m in (1, 2, 3)] + [END - timedelta(hours=1)]
    paths = overlay.render_sequence(r, times, tmp_path, "x_")
    assert len(paths) == 4 and paths[0] == paths[1] == paths[2] != paths[3]
    assert len(list(tmp_path.glob("*.png"))) == 2


def test_frame_time():
    t = storage.frame_time(Path("/d/CAM/2026-11-18/143005.jpg"), "Europe/Stockholm")
    assert t.isoformat() == "2026-11-18T14:30:05+01:00"
    assert storage.frame_time(Path("/d/CAM/2026-11-18/14.jpg"), "Europe/Stockholm").hour == 14
    assert storage.frame_time(Path("/d/CAM/x/1.jpg"), "Europe/Stockholm") is None
