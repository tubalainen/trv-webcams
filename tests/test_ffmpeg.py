from pathlib import Path

from app import ffmpeg
from app.ffmpeg import EncoderChoice


def test_detect_prefers_nvidia():
    assert ffmpeg.detect_encoder("auto", probe=lambda m, d: True).mode == "nvidia"


def test_detect_falls_back():
    assert ffmpeg.detect_encoder("auto", probe=lambda m, d: m == "vaapi").mode == "vaapi"
    assert ffmpeg.detect_encoder("auto", probe=lambda m, d: False).mode == "cpu"
    assert ffmpeg.detect_encoder("nvidia", probe=lambda m, d: False).mode == "cpu"
    assert ffmpeg.detect_encoder("cpu", probe=lambda m, d: True).mode == "cpu"


def test_mp4_command_nvenc():
    cmd = ffmpeg.mp4_command(
        Path("l.txt"), Path("o.mp4"), EncoderChoice("nvidia", "h264_nvenc"), 30, 641, 481
    )
    assert "h264_nvenc" in cmd
    vf = cmd[cmd.index("-vf") + 1]
    assert vf.startswith("scale=640:480")
    assert cmd[-1] == "o.mp4"


def test_mp4_command_vaapi_and_cpu():
    v = ffmpeg.mp4_command(
        Path("l"), Path("o"), EncoderChoice("vaapi", "h264_vaapi"), 30, 640, 480, "/dev/dri/x"
    )
    assert v[v.index("-vaapi_device") + 1] == "/dev/dri/x"
    assert "hwupload" in v[v.index("-vf") + 1]
    c = ffmpeg.mp4_command(Path("l"), Path("o"), EncoderChoice("cpu", "libx264"), 30, 640, 480)
    assert "libx264" in c


def test_concat_file(tmp_path):
    frames = [tmp_path / "a'b.jpg", tmp_path / "c.jpg"]
    dest = tmp_path / "list"
    ffmpeg.write_concat_file(frames, 10, dest)
    text = dest.read_text()
    assert text.startswith("ffconcat version 1.0")
    assert r"a'\''b.jpg" in text
    assert text.count("duration 0.100000") == 2
    assert text.strip().endswith("c.jpg'")
