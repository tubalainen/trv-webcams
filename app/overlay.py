"""Road temperature graph drawn on top of timelapse frames.

One small RGBA PNG is rendered per video frame. The x axis covers the whole clip
period and the curve is drawn up to the frame's own timestamp, so the graph grows
while the clip plays. ffmpeg composites the PNG sequence with the `overlay` filter.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

BG = (12, 16, 22, 170)
AXIS = (255, 255, 255, 90)
ZERO = (120, 190, 255, 150)
LINE = (255, 255, 255, 235)
COLD = (125, 200, 255, 255)  # at or below 0 °C: risk of ice
WARM = (255, 190, 110, 255)
TEXT = (255, 255, 255, 230)
MUTED = (255, 255, 255, 150)

# A sample older than this (relative to the frame) is not shown as the current value
STALE_SECONDS = 2 * 3600


@dataclass(frozen=True)
class OverlayInfo:
    width: int
    height: int
    margin: int


def overlay_size(video_width: int, fraction: float = 0.30, min_width: int = 150) -> OverlayInfo:
    w = max(min_width, int(video_width * fraction))
    w -= w % 2
    h = int(w * 0.42)
    h -= h % 2
    return OverlayInfo(w, h, max(4, int(video_width * 0.015)))


WEEKDAYS = ["mån", "tis", "ons", "tor", "fre", "lör", "sön"]


def fmt_temp(v: float) -> str:
    return f"{v:.1f}".replace("-", "\u2212").replace(".", ",") + " °C"


# DejaVu Sans (Debian package fonts-dejavu-core) covers å, ä, ö and °. Pillow's bundled
# fallback font is ASCII only, so Swedish letters would show up as boxes with it.
FONT_FILES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "DejaVuSans.ttf",
)


@lru_cache(maxsize=32)
def _font(size: int) -> ImageFont.ImageFont:
    size = max(9, size)
    for f in FONT_FILES:
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _deg(v: float) -> str:
    return fmt_temp(v)[:-3] + "°"


class GraphRenderer:
    def __init__(
        self,
        samples: list[tuple[datetime, float]],
        start: datetime,
        end: datetime,
        width: int,
        height: int,
        tz: str,
        label: str = "Vägtemp.",
    ) -> None:
        self.start, self.end = start, end
        self.w, self.h = width, height
        self.tz = ZoneInfo(tz)
        k = width / 300
        self.k = k
        self.f_small = _font(int(11 * k))
        self.f_big = _font(int(17 * k))
        self.pad_l, self.pad_r = int(8 * k), int(8 * k)
        self.pad_t, self.pad_b = int(26 * k), int(15 * k)
        self.plot_w = width - self.pad_l - self.pad_r
        self.plot_h = height - self.pad_t - self.pad_b
        self.label = label

        self.times = [t for t, _ in samples]
        self.values = [v for _, v in samples]
        vmin, vmax = (min(self.values), max(self.values)) if samples else (-1.0, 1.0)
        self.data_min, self.data_max = vmin, vmax
        if vmax - vmin < 4:
            mid = (vmax + vmin) / 2
            vmin, vmax = mid - 2, mid + 2
        span = vmax - vmin
        self.vmin, self.vmax = vmin - span * 0.08, vmax + span * 0.08

        # One point per pixel column (mean of the samples in it) keeps a year's data cheap.
        self.points: list[tuple[float, float, datetime]] = []  # (x, y, last time in column)
        cols: dict[int, list[int]] = {}
        for i, t in enumerate(self.times):
            cols.setdefault(int(self._x(t)), []).append(i)
        for x in sorted(cols):
            idx = cols[x]
            mean = sum(self.values[i] for i in idx) / len(idx)
            self.points.append((float(x), self._y(mean), self.times[idx[-1]]))
        self.point_times = [p[2] for p in self.points]
        self.base = self._base()

    def _x(self, t: datetime) -> float:
        total = (self.end - self.start).total_seconds() or 1.0
        frac = min(1.0, max(0.0, (t - self.start).total_seconds() / total))
        return self.pad_l + frac * self.plot_w

    def _y(self, v: float) -> float:
        frac = (v - self.vmin) / (self.vmax - self.vmin)
        return self.pad_t + (1 - frac) * self.plot_h

    def _axis_label(self, t: datetime) -> str:
        lt = t.astimezone(self.tz)
        if (self.end - self.start).total_seconds() <= 2 * 86400:
            return f"{WEEKDAYS[lt.weekday()]} {lt:%H:%M}"
        return f"{lt.day}/{lt.month}"

    def _base(self) -> Image.Image:
        img = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([0, 0, self.w - 1, self.h - 1], radius=int(6 * self.k), fill=BG)
        x0, x1 = self.pad_l, self.pad_l + self.plot_w
        y1 = self.pad_t + self.plot_h
        d.line([(x0, y1), (x1, y1)], fill=AXIS, width=1)
        if self.vmin < 0 < self.vmax:  # freezing line
            yz = self._y(0)
            dash = max(3, int(4 * self.k))
            for x in range(x0, x1, dash * 2):
                d.line([(x, yz), (min(x + dash, x1), yz)], fill=ZERO, width=1)
            d.text((x1, yz - 1), "0°", font=self.f_small, fill=ZERO, anchor="rb")
        if self.values:  # lowest and highest value of the whole period, between the dates
            d.text(
                ((x0 + x1) / 2, self.h - int(2 * self.k)),
                f"min {_deg(self.data_min)}  max {_deg(self.data_max)}",
                font=self.f_small,
                fill=MUTED,
                anchor="mb",
            )
        d.text(
            (x0, self.h - int(2 * self.k)),
            self._axis_label(self.start),
            font=self.f_small,
            fill=MUTED,
            anchor="lb",
        )
        d.text(
            (x1, self.h - int(2 * self.k)),
            self._axis_label(self.end),
            font=self.f_small,
            fill=MUTED,
            anchor="rb",
        )
        d.text((x0, int(4 * self.k)), self.label, font=self.f_small, fill=MUTED, anchor="lt")
        return img

    def visible(self, t: datetime) -> int:
        """Number of graph points at or before t."""
        return bisect.bisect_right(self.point_times, t)

    def current(self, t: datetime) -> float | None:
        i = bisect.bisect_right(self.times, t) - 1
        if i < 0 or (t - self.times[i]).total_seconds() > STALE_SECONDS:
            return None
        return self.values[i]

    def render(self, t: datetime) -> Image.Image:
        img = self.base.copy()
        d = ImageDraw.Draw(img)
        n = self.visible(t)
        pts = [(x, y) for x, y, _ in self.points[:n]]
        lw = max(1, int(round(1.6 * self.k)))
        if len(pts) >= 2:
            d.line(pts, fill=LINE, width=lw, joint="curve")
        cur = self.current(t)
        if pts and cur is not None:
            r = max(2, int(2.5 * self.k))
            x, y = pts[-1]
            col = COLD if cur <= 0 else WARM
            d.ellipse([x - r, y - r, x + r, y + r], fill=col)
        text = fmt_temp(cur) if cur is not None else "\u2013 °C"
        col = MUTED if cur is None else (COLD if cur <= 0 else WARM)
        d.text((self.w - self.pad_r, int(2 * self.k)), text, font=self.f_big, fill=col, anchor="rt")
        return img


def render_sequence(
    renderer: GraphRenderer, frame_times: list[datetime], out_dir: Path, prefix: str
) -> list[Path]:
    """Render one PNG per frame. Frames that would look identical share the same file."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    last_key, last_path = None, None
    for i, t in enumerate(frame_times):
        cur = renderer.current(t)
        key = (renderer.visible(t), None if cur is None else round(cur, 1))
        if key != last_key:
            last_path = out_dir / f"{prefix}{i:06d}.png"
            renderer.render(t).save(last_path, compress_level=1)
            last_key = key
        paths.append(last_path)
    return paths


def render_to_dir(
    samples: list[tuple[datetime, float]],
    start: datetime,
    end: datetime,
    width: int,
    height: int,
    tz: str,
    frame_times: list[datetime],
    out_dir: Path,
    prefix: str,
) -> list[Path]:
    """Build the renderer and render the sequence."""
    renderer = GraphRenderer(samples, start, end, width, height, tz)
    return render_sequence(renderer, frame_times, out_dir, prefix)
