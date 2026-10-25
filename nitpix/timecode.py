"""SMPTE timecode maths.

All internal positions are stored as integer frame indices from the start of the
media (frame 0 = first frame). Timecode is derived for display using the asset's
start timecode and nominal frame rate. Drop-frame is supported for 29.97 / 59.94.
"""
from __future__ import annotations

from fractions import Fraction
import re

TC_RE = re.compile(r"^(\d{1,2})[:;.](\d{2})[:;.](\d{2})([:;.])(\d{2,3})$")


def _decimal_rate(f: float) -> Fraction | None:
    """Decimal rates as people write them: 25 -> 25, 23.976 / 23.98 -> 24000/1001, 12.5 -> 25/2."""
    if f <= 0:
        return None
    if abs(f - round(f)) < 1e-6:
        return Fraction(round(f))
    ntsc = f * 1.001
    if abs(ntsc - round(ntsc)) < 0.006 * max(1, round(ntsc) / 24) and round(ntsc) % 6 == 0:
        return Fraction(round(ntsc) * 1000, 1001)
    return Fraction(f).limit_denominator(1001)


def parse_rate(value) -> Fraction | None:
    """Parse ffprobe style rates ('25/1', '24000/1001', '25', '23.976') into a Fraction."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return _decimal_rate(float(value))
    s = str(value).strip()
    if not s or s in ("0/0", "0"):
        return None
    try:
        if "/" in s:
            n, d = s.split("/", 1)
            n, d = int(n), int(d)
            if d == 0 or n == 0:
                return None
            return Fraction(n, d)
        return _decimal_rate(float(s))
    except (ValueError, ZeroDivisionError):
        return None


def asset_rate(asset: dict) -> Fraction:
    """Exact frame rate of an asset for frame <-> seconds maths. fps_float is rounded to
    3 dp (23.976 != 24000/1001, a frame out after ~40 s), so prefer the rational string."""
    return parse_rate(asset.get("fps")) or parse_rate(asset.get("fps_float")) or Fraction(25)


def nominal_fps(rate: Fraction) -> int:
    """Integer timebase used for timecode counting (24, 25, 30, 50, 60...)."""
    return max(1, round(float(rate)))


def is_drop_frame_rate(rate: Fraction) -> bool:
    return rate in (Fraction(30000, 1001), Fraction(60000, 1001))


def tc_to_frames(tc: str, rate: Fraction, drop: bool | None = None) -> int:
    m = TC_RE.match(tc.strip())
    if not m:
        raise ValueError(f"Not a timecode: {tc!r}")
    hh, mm, ss, sep, ff = int(m[1]), int(m[2]), int(m[3]), m[4], int(m[5])
    fps = nominal_fps(rate)
    if drop is None:
        drop = sep == ";" and is_drop_frame_rate(rate)
    total = ((hh * 60 + mm) * 60 + ss) * fps + ff
    if drop:
        dropn = 2 if fps == 30 else 4
        total_minutes = hh * 60 + mm
        total -= dropn * (total_minutes - total_minutes // 10)
    return total


def frames_to_tc(frames: int, rate: Fraction | None, drop: bool = False) -> str:
    if rate is None:
        return "--:--:--:--"
    fps = nominal_fps(rate)
    neg = frames < 0
    frames = abs(int(frames))
    if drop and fps in (30, 60):
        dropn = 2 if fps == 30 else 4
        per10 = fps * 600 - dropn * 9
        per1 = fps * 60 - dropn
        d, m = divmod(frames, per10)
        if m > dropn:
            frames += dropn * 9 * d + dropn * ((m - dropn) // per1)
        else:
            frames += dropn * 9 * d
    ff = frames % fps
    ss = (frames // fps) % 60
    mm = (frames // (fps * 60)) % 60
    hh = (frames // (fps * 3600)) % 24
    sep = ";" if drop else ":"
    return f"{'-' if neg else ''}{hh:02d}:{mm:02d}:{ss:02d}{sep}{ff:02d}"


def seconds_to_frame(seconds: float, rate: Fraction) -> int:
    # small epsilon: mpv/ffmpeg report pts like 0.039999 for frame 1 at 25p
    return int(float(seconds) * float(rate) + 1e-4)


def frame_to_seconds(frame: int, rate: Fraction) -> float:
    return float(Fraction(frame) / rate)


class TimecodeContext:
    """Bundles rate + start TC so callers can convert both ways consistently."""

    def __init__(self, rate: Fraction | None, start_tc: str | None = None):
        self.rate = rate
        self.drop = bool(start_tc and ";" in start_tc and rate and is_drop_frame_rate(rate))
        self.start_frames = 0
        self.start_tc = start_tc or "00:00:00:00"
        if rate and start_tc:
            try:
                self.start_frames = tc_to_frames(start_tc, rate, self.drop)
            except ValueError:
                self.start_frames = 0
                self.start_tc = "00:00:00:00"

    def tc(self, frame: int | None) -> str | None:
        if frame is None or self.rate is None:
            return None
        return frames_to_tc(self.start_frames + int(frame), self.rate, self.drop)

    def frame_from_tc(self, tc: str) -> int:
        return tc_to_frames(tc, self.rate, self.drop) - self.start_frames

    def frame_from_seconds(self, seconds: float) -> int:
        return seconds_to_frame(seconds, self.rate)
