"""Built-in frame viewer: exact frame grabs via ffmpeg (fallback when IINA is
unavailable, and for formats IINA cannot show). Output goes to the project cache."""
from __future__ import annotations

import os
import subprocess
from fractions import Fraction

from .probe import find_tool
from .timecode import asset_rate


class FrameError(Exception):
    pass


def grab(project, asset: dict, frame: int, width: int = 1280) -> str:
    ff = find_tool("ffmpeg")
    if not ff:
        raise FrameError("ffmpeg not found; cannot render frames.")
    frame = max(0, int(frame))
    # _v2: grabs cached before the exact-rate fix can be one frame late at 23.976/29.97.
    out = project.safe_path("cache", "frames", f"a{asset['id']}_w{width}_f{frame}_v2.jpg")
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() and out.stat().st_mtime >= (asset.get("mtime") or 0):
        return str(out)
    vf = f"scale='min({width},iw)':-2:flags=bicubic"
    if asset["kind"] == "sequence":
        seq = asset["seq"]
        n = seq["first"] + frame
        names = {r[0]: r for r in seq["frames"]}
        row = names.get(n)
        if not row:
            raise FrameError(f"Frame {n} is missing from the sequence.")
        if row[3] == "bad":
            raise FrameError(f"Frame {n} is unreadable.")
        src = os.path.join(seq["dir"], row[1])
        pre = ["-apply_trc", "iec61966_2_1"] if seq["ext"] == "exr" else []
        cmd = [ff, "-hide_banner", "-loglevel", "error", "-y", *pre, "-i", src, "-frames:v", "1",
               "-vf", vf + ",format=yuvj444p", "-q:v", "3", str(out)]
    elif asset["kind"] == "image":
        cmd = [ff, "-hide_banner", "-loglevel", "error", "-y", "-i", asset["path"], "-frames:v", "1",
               "-vf", vf + ",format=yuvj444p", "-q:v", "3", str(out)]
    elif asset["kind"] == "video":
        # Output -ss drops frames whose pts < t. Aim a quarter-frame early with the exact rate:
        # frame / 23.976 overshoots the real pts and skips to the next frame after ~40 s.
        t = max(0.0, float((frame - Fraction(1, 4)) / asset_rate(asset)))
        # Fast seek to ~2 s before, then accurate decode to the exact frame.
        pre = max(0.0, t - 2.0)
        cmd = [ff, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{pre:.6f}", "-i", asset["path"],
               "-ss", f"{t - pre:.6f}", "-frames:v", "1", "-vf", vf + ",format=yuvj444p", "-q:v", "3", str(out)]
    else:
        raise FrameError("No picture for this asset type.")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if r.returncode or not out.exists():
        raise FrameError((r.stderr or "ffmpeg failed").strip().splitlines()[-1])
    return str(out)


def thumb(project, asset: dict) -> str:
    if asset["kind"] == "video":
        frames = asset.get("frames") or int((asset.get("duration") or 0) * float(asset_rate(asset)))
        f = int(frames * 0.25)
    elif asset["kind"] == "sequence":
        f = (asset["seq"]["last"] - asset["seq"]["first"]) // 4
        names = {r[0]: r for r in asset["seq"]["frames"]}
        # pick nearest readable frame
        for d in range(0, 50):
            r = names.get(asset["seq"]["first"] + f + d)
            if r and r[3] != "bad":
                f = f + d
                break
    else:
        f = 0
    return grab(project, asset, f, width=320)
