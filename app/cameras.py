"""Per camera choices that fall back to the admin defaults."""

from __future__ import annotations

import struct

from app.config import Settings
from app.db import Camera

TEMP_MODES = ("default", "on", "off")
RESOLUTION_MODES = ("default", "high", "low")

# Approximate JPEG sizes, used for the storage estimate
BYTES_LOW = 27_000
BYTES_HIGH = 145_000

# Trafikverket's camera types ("Trafikflödeskamera", "Väglagskamera") as short keys
KINDS = ("flow", "road")


def camera_kind(type_: str | None) -> str:
    """'flow' for traffic flow cameras, 'road' for road condition cameras, '' otherwise."""
    t = (type_ or "").lower()
    if "flöde" in t:
        return "flow"
    if "väglag" in t:
        return "road"
    return ""


def wants_temp_overlay(cam: Camera, settings: Settings) -> bool:
    if cam.temp_overlay_mode == "on":
        return True
    if cam.temp_overlay_mode == "off":
        return False
    return bool(settings.temp_overlay_default)


def fullsize_available(cam: Camera) -> bool:
    """Trafikverket says it has a full size image, and our check (if done) found one."""
    if not cam.has_fullsize:
        return False
    return cam.fullsize_checked_at is None or cam.fullsize_width is not None


def wants_fullsize(cam: Camera, settings: Settings) -> bool:
    """Whether new snapshots for this camera are saved in full size."""
    if cam.resolution_mode == "high":
        want = True
    elif cam.resolution_mode == "low":
        want = False
    else:
        want = bool(settings.trv_fullsize)
    return want and fullsize_available(cam)


def hd_label(cam: Camera) -> str:
    """Short label such as "1080p" for the camera's full size image, empty when unknown/none."""
    if not fullsize_available(cam) or not cam.fullsize_height:
        return ""
    return f"{cam.fullsize_height}p"


def jpeg_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) from the SOF marker of a (partial) JPEG, None if not found."""
    if not data.startswith(b"\xff\xd8"):
        return None
    i = 2
    while i + 9 <= len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xFF:  # fill byte
            i += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:  # markers without length
            i += 2
            continue
        (length,) = struct.unpack(">H", data[i + 2 : i + 4])
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            height, width = struct.unpack(">HH", data[i + 5 : i + 9])
            return width, height
        i += 2 + length
    return None
