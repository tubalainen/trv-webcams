"""ffmpeg command construction and hardware encoder detection.

Primary target is NVIDIA NVENC. Fallback order in auto mode:
nvidia (h264_nvenc) -> vaapi (h264_vaapi) -> qsv (h264_qsv) -> cpu (libx264).
GIFs are always encoded on the CPU (palettegen/paletteuse), no GPU GIF encoder exists.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"

ENCODERS = {
    "nvidia": "h264_nvenc",
    "vaapi": "h264_vaapi",
    "qsv": "h264_qsv",
    "cpu": "libx264",
}


@dataclass(frozen=True)
class EncoderChoice:
    mode: str
    codec: str


def _probe_cmd(mode: str, vaapi_device: str) -> list[str]:
    base = [FFMPEG, "-hide_banner", "-loglevel", "error"]
    src = ["-f", "lavfi", "-i", "color=black:s=320x240:d=0.2"]
    if mode == "vaapi":
        return (
            base
            + ["-vaapi_device", vaapi_device]
            + src
            + ["-vf", "format=nv12,hwupload", "-c:v", "h264_vaapi", "-f", "null", "-"]
        )
    if mode == "qsv":
        return base + src + ["-vf", "format=nv12", "-c:v", "h264_qsv", "-f", "null", "-"]
    return base + src + ["-vf", "format=yuv420p", "-c:v", ENCODERS[mode], "-f", "null", "-"]


def probe_encoder(mode: str, vaapi_device: str = "/dev/dri/renderD128") -> tuple[bool, str]:
    """Encode a short test clip; returns (ok, reason when not ok)."""
    try:
        r = subprocess.run(_probe_cmd(mode, vaapi_device), capture_output=True, timeout=30, text=True)
    except subprocess.TimeoutExpired:
        return False, "timed out after 30 s"
    except OSError as e:
        return False, str(e)
    if r.returncode == 0:
        return True, ""
    noise = ("Nothing was written", "Error parsing global options", "Conversion failed")
    lines = [
        ln.strip() for ln in (r.stderr or "").splitlines() if ln.strip() and not any(n in ln for n in noise)
    ]
    return False, " | ".join(lines[:2]) or f"ffmpeg exit code {r.returncode}"


def encoder_works(mode: str, vaapi_device: str = "/dev/dri/renderD128") -> bool:
    return probe_encoder(mode, vaapi_device)[0]


def nvidia_gpus() -> list[str] | None:
    """GPUs reported by nvidia-smi, or None when no NVIDIA GPU is visible in the container.

    nvidia-smi is mounted into the container by the NVIDIA Container Toolkit
    (NVIDIA_DRIVER_CAPABILITIES must include "utility").
    """
    smi = shutil.which("nvidia-smi")
    if not smi:
        return None
    try:
        r = subprocess.run(
            [smi, "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"],
            capture_output=True,
            timeout=15,
            text=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    gpus = []
    for line in r.stdout.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 3:
            gpus.append(f"{parts[0]} (driver {parts[1]}, {parts[2]})")
    return gpus or None


def detect_encoder(
    preference: str = "auto",
    vaapi_device: str = "/dev/dri/renderD128",
    probe=probe_encoder,
    gpus: list[str] | None = None,
) -> EncoderChoice:
    """Pick the first working encoder and log a hardware report.

    `probe(mode, vaapi_device)` returns either a bool or (ok, reason).
    """
    preference = (preference or "auto").lower()
    if preference in ("nvenc", "cuda"):
        preference = "nvidia"
    if preference in ENCODERS and preference != "auto":
        candidates = [preference, "cpu"]
    else:
        candidates = ["nvidia", "vaapi", "qsv", "cpu"]

    log.info("Hardware acceleration check (HWACCEL=%s)", preference)
    if gpus:
        for g in gpus:
            log.info("  NVIDIA GPU found: %s", g)
    elif "nvidia" in candidates:
        log.info("  No NVIDIA GPU visible in the container (nvidia-smi not available)")

    for mode in candidates:
        if mode == "cpu":
            ok, reason = True, ""
        else:
            res = probe(mode, vaapi_device)
            ok, reason = res if isinstance(res, tuple) else (bool(res), "")
        if not ok:
            log.info("  %-6s %-11s FAILED%s", mode, ENCODERS[mode], f": {reason}" if reason else "")
            if mode == "nvidia" and gpus:
                log.warning(
                    "NVIDIA GPU is visible but NVENC does not work. Check that "
                    "NVIDIA_DRIVER_CAPABILITIES includes 'video' and that the GPU supports NVENC."
                )
            continue
        log.info("  %-6s %-11s OK", mode, ENCODERS[mode])
        if mode == "cpu" and preference in ("auto", "nvidia") and not gpus:
            log.warning(
                "Using CPU encoding. For NVIDIA: install the NVIDIA Container Toolkit on the host "
                "and keep the 'deploy: resources: reservations: devices' block in docker-compose."
            )
        if preference not in ("auto", mode):
            log.warning("Requested HWACCEL=%s not available, falling back to %s", preference, mode)
        log.info("Using video encoder %s (%s)", ENCODERS[mode], "GPU" if mode != "cpu" else "CPU")
        return EncoderChoice(mode, ENCODERS[mode])
    return EncoderChoice("cpu", "libx264")  # pragma: no cover


def write_concat_file(frames: list[Path], fps: float, dest: Path) -> None:
    """ffconcat list where every image is shown for 1/fps seconds."""
    dur = 1.0 / fps
    lines = ["ffconcat version 1.0"]
    for f in frames:
        p = str(f.resolve()).replace("'", r"'\''")
        lines.append(f"file '{p}'")
        lines.append(f"duration {dur:.6f}")
    if frames:  # the last entry needs repeating for its duration to apply
        p = str(frames[-1].resolve()).replace("'", r"'\''")
        lines.append(f"file '{p}'")
    dest.write_text("\n".join(lines) + "\n")


def _even(n: int) -> int:
    return max(2, n - (n % 2))


def fit_filter(width: int, height: int) -> str:
    w, h = _even(width), _even(height)
    return f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1"


def mp4_command(
    concat: Path,
    out: Path,
    enc: EncoderChoice,
    fps: int,
    width: int,
    height: int,
    vaapi_device: str = "/dev/dri/renderD128",
) -> list[str]:
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-nostdin"]
    if enc.mode == "vaapi":
        cmd += ["-vaapi_device", vaapi_device]
    cmd += ["-f", "concat", "-safe", "0", "-i", str(concat)]
    vf = fit_filter(width, height)
    if enc.mode == "nvidia":
        vf += ",format=yuv420p"
        codec = [
            "-c:v",
            "h264_nvenc",
            "-preset",
            "p5",
            "-tune",
            "hq",
            "-rc",
            "vbr",
            "-cq",
            "23",
            "-b:v",
            "0",
            "-profile:v",
            "high",
        ]
    elif enc.mode == "vaapi":
        vf += ",format=nv12,hwupload"
        codec = ["-c:v", "h264_vaapi", "-qp", "23"]
    elif enc.mode == "qsv":
        vf += ",format=nv12"
        codec = ["-c:v", "h264_qsv", "-preset", "medium", "-global_quality", "23"]
    else:
        vf += ",format=yuv420p"
        codec = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-profile:v", "high"]
    cmd += [
        "-vf",
        vf,
        "-fps_mode",
        "cfr",
        "-r",
        str(fps),
        *codec,
        "-an",
        "-movflags",
        "+faststart",
        "-f",
        "mp4",
        str(out),
    ]
    return cmd


def gif_command(concat: Path, out: Path, width: int, fps: int) -> list[str]:
    vf = (
        f"fps={fps},scale={width}:-2:flags=lanczos,split[a][b];"
        "[a]palettegen=stats_mode=diff:max_colors=128[p];"
        "[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle"
    )
    return [
        FFMPEG,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat),
        "-filter_complex",
        vf,
        "-loop",
        "0",
        "-f",
        "gif",
        str(out),
    ]


def probe_size(image: Path) -> tuple[int, int] | None:
    try:
        r = subprocess.run(
            [
                FFPROBE,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "json",
                str(image),
            ],
            capture_output=True,
            timeout=30,
            text=True,
        )
        s = json.loads(r.stdout)["streams"][0]
        return int(s["width"]), int(s["height"])
    except Exception:  # noqa: BLE001
        return None


async def run(cmd: list[str], timeout: float = 3600) -> None:
    log.debug("Running %s", " ".join(cmd))
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError("ffmpeg timed out") from None
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed ({proc.returncode}): {err.decode(errors='replace')[-800:]}")
