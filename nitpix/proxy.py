"""Browser playback proxies: one H.264/AAC MP4 per asset, made with ffmpeg on demand.

Browsers cannot decode ProRes (outside Safari), DNxHR, NotchLC, HAP, EXR/DPX sequences
and so on, so the in-app player plays a proxy instead of the source. The proxy is built
to keep the app's frame maths valid:

  * same exact frame rate, first frame at t = 0, so frame n is at n / rate. The player
    seeks to the frame's centre, (n + 0.5) / rate, and reads frames back with
    requestVideoFrameCallback;
  * image sequences go through a frame list that holds the previous frame over gaps,
    like the IINA bridge, so indices stay aligned with the sequence;
  * colour is converted to Rec.709 limited range. NotchLC is tagged with an RGB matrix
    but its planes are YCbCr; disguise's own decoder matches BT.601 full range within
    one code value, so that is forced. Untagged HD is read as Rec.709, not swscale's
    BT.601 default.

Alpha is dropped (H.264 has none) and only the first audio stream is kept, as stereo.
Proxies are for looking and listening; measurements always come from the source.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import threading
import time
from fractions import Fraction

from .probe import find_tool
from .timecode import asset_rate

MAX_WIDTH = 1920
VIDEO_BITRATE = "10M"


def key(asset: dict) -> str:
    seq = asset.get("seq") or {}
    src = f"{asset.get('path')}|{asset.get('size')}|{asset.get('mtime')}|{asset.get('fps')}|{seq.get('count')}|{MAX_WIDTH}|v1"
    return hashlib.sha1(src.encode()).hexdigest()[:12]


def _encoder(ff: str) -> list[str]:
    try:
        out = subprocess.run([ff, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        out = ""
    if "h264_videotoolbox" in out:
        return ["-c:v", "h264_videotoolbox", "-b:v", VIDEO_BITRATE, "-maxrate", "16M", "-profile:v", "high"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-profile:v", "high"]


def _colour_filters(meta: dict) -> list[str]:
    v = (meta.get("video_streams") or [{}])[0]
    pre = []
    if v.get("codec") == "notchlc":
        pre = ["setparams=colorspace=bt470bg:range=pc"]
    elif not v.get("color_space") and (v.get("height") or 0) >= 720:
        pre = ["setparams=colorspace=bt709"]
    return pre


def _sequence_list(project, asset: dict, rate: Fraction) -> str:
    seq = asset["seq"]
    frames = {r[0]: r[1] for r in seq["frames"] if len(r) < 4 or r[3] != "bad"}
    dur = float(1 / rate)
    lines, last = ["ffconcat version 1.0"], None
    for n in range(seq["first"], seq["last"] + 1):
        name = frames.get(n) or last
        if name is None:
            continue
        last = name
        path = os.path.join(seq["dir"], name).replace("'", "'\\''")
        lines += [f"file '{path}'", f"duration {dur:.9f}"]
    out = project.safe_path("cache", f"proxy_seq_{asset['id']}.ffconcat")
    out.write_text("\n".join(lines) + "\n")
    return str(out)


def command(project, asset: dict, out: str) -> list[str]:
    ff = find_tool("ffmpeg")
    if not ff:
        raise RuntimeError("ffmpeg not found; cannot make a browser proxy.")
    meta = asset.get("metadata") or {}
    rate = asset_rate(asset)
    r = f"{rate.numerator}/{rate.denominator}"
    vf = _colour_filters(meta)
    if asset["kind"] == "sequence":
        src = ["-f", "concat", "-safe", "0"]
        if (asset["seq"].get("ext") or "").lower() == "exr":
            src += ["-apply_trc", "iec61966_2_1"]
        src += ["-i", _sequence_list(project, asset, rate)]
        maps, audio = ["-map", "0:v:0"], ["-an"]
        timing = ["-fps_mode", "cfr", "-r", r]
    elif asset["kind"] == "video":
        src = ["-i", asset["path"]]
        has_audio = bool(meta.get("audio_streams"))
        maps = ["-map", "0:v:0"] + (["-map", "0:a:0"] if has_audio else [])
        audio = ["-c:a", "aac", "-b:a", "192k", "-ac", "2", "-ar", "48000"] if has_audio else ["-an"]
        # The app numbers frames as n / rate, so make the proxy constant-rate on that grid
        # (a no-op for CFR sources; VFR sources get frames held or dropped onto it).
        timing = ["-fps_mode", "cfr", "-r", r]
    else:
        raise ValueError("Only video files and image sequences have a picture to play.")
    vf += [f"scale=w='min({MAX_WIDTH},iw)':h=-2:flags=bicubic:out_color_matrix=bt709:out_range=tv",
           "format=yuv420p"]
    gop = max(1, round(float(rate)))
    return [ff, "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-progress", "pipe:1", "-nostats",
            *src, *maps, "-vf", ",".join(vf), *timing, *_encoder(ff), "-g", str(gop),
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
            *audio, "-movflags", "+faststart", "-f", "mp4", out]


class ProxyManager:
    """Builds proxies one at a time in a background thread."""

    def __init__(self, project):
        self.project = project
        self.lock = threading.Lock()
        self.status: dict[int, dict] = {}
        self.queue: list[dict] = []
        self.proc: subprocess.Popen | None = None
        self.worker: threading.Thread | None = None

    def path(self, asset: dict):
        return self.project.safe_path("cache", "proxy", f"a{asset['id']}_{key(asset)}.mp4")

    def state(self, asset: dict) -> dict:
        p = self.path(asset)
        rate = asset_rate(asset)
        base = {"asset_id": asset["id"], "rate": f"{rate.numerator}/{rate.denominator}"}
        if p.exists():
            return {**base, "state": "ready", "progress": 1.0, "url": f"/api/proxy/{asset['id']}/{p.name}",
                    "size": p.stat().st_size}
        with self.lock:
            st = self.status.get(asset["id"])
            if st and st.get("key") == key(asset):
                return {**base, **{k: v for k, v in st.items() if k != "key"}}
        return {**base, "state": "none", "progress": 0.0}

    def request(self, asset: dict) -> dict:
        if asset["kind"] not in ("video", "sequence"):
            raise ValueError("Only video files and image sequences have a picture to play.")
        cur = self.state(asset)
        if cur["state"] in ("ready", "queued", "building"):
            return cur
        with self.lock:
            self.status[asset["id"]] = {"key": key(asset), "state": "queued", "progress": 0.0}
            self.queue.append(asset)
            if not self.worker or not self.worker.is_alive():
                self.worker = threading.Thread(target=self._run, daemon=True)
                self.worker.start()
        return self.state(asset)

    def _run(self):
        while True:
            with self.lock:
                if not self.queue:
                    return
                asset = self.queue.pop(0)
            self._build(asset)

    def _build(self, asset: dict):
        aid, k = asset["id"], key(asset)
        out = self.path(asset)
        out.parent.mkdir(parents=True, exist_ok=True)
        part = out.with_suffix(".part.mp4")
        rate = asset_rate(asset)
        if asset["kind"] == "sequence":
            total = (asset["seq"]["last"] - asset["seq"]["first"] + 1) / float(rate)
        else:
            total = asset.get("duration") or 0
        st = {"key": k, "state": "building", "progress": 0.0, "started": time.time()}
        with self.lock:
            self.status[aid] = st
        try:
            cmd = command(self.project, asset, str(part))
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                         errors="replace")
            for line in self.proc.stdout:
                if line.startswith("out_time_us=") and total:
                    try:
                        st["progress"] = min(0.99, int(line.split("=")[1]) / 1e6 / total)
                    except ValueError:
                        pass
            err = self.proc.stderr.read()
            self.proc.wait()
            if self.proc.returncode or not part.exists():
                raise RuntimeError((err.strip().splitlines() or [f"ffmpeg exited {self.proc.returncode}"])[-1])
            # Old proxies of this asset (an earlier version of the file) are no longer needed.
            for old in out.parent.glob(f"a{aid}_*.mp4"):
                if old != part:
                    old.unlink(missing_ok=True)
            part.replace(out)
            st.update(state="ready", progress=1.0)
        except Exception as e:
            part.unlink(missing_ok=True)
            st.update(state="error", error=str(e))
        finally:
            self.proc = None

    def close(self):
        with self.lock:
            self.queue.clear()
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
