"""ffprobe wrapper and metadata normalisation."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from fractions import Fraction

from .timecode import parse_rate
from .vocab import container_of

_EXTRA_PATHS = ["/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin", "/usr/bin"]


def _bundle_dirs() -> list[str]:
    """Where a packaged app keeps its own ffmpeg/ffprobe; preferred over anything on PATH."""
    if not getattr(sys, "frozen", False):
        return []
    base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return [base, os.path.join(base, "bin"), os.path.dirname(sys.executable)]


def find_tool(name: str, override: str | None = None) -> str | None:
    if override and os.path.isfile(override) and os.access(override, os.X_OK):
        return override
    exe = name + (".exe" if os.name == "nt" else "")
    for d in _bundle_dirs():
        c = os.path.join(d, exe)
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    p = shutil.which(name)
    if p:
        return p
    for d in _EXTRA_PATHS:
        c = os.path.join(d, name)
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def tool_versions() -> dict:
    out = {}
    for t in ("ffprobe", "ffmpeg"):
        p = find_tool(t)
        if not p:
            out[t] = {"available": False, "path": None, "version": None}
            continue
        try:
            v = subprocess.run([p, "-version"], capture_output=True, text=True, timeout=10).stdout.split("\n")[0]
        except Exception as e:
            v = f"error: {e}"
        out[t] = {"available": True, "path": p, "version": v}
    return out


class ProbeError(Exception):
    pass


def ffprobe(path: str, timeout: int = 60) -> dict:
    exe = find_tool("ffprobe")
    if not exe:
        raise ProbeError("ffprobe not found. Install FFmpeg (brew install ffmpeg).")
    cmd = [exe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ProbeError("ffprobe timed out (file may be on a slow or stalled volume)")
    if r.returncode != 0:
        msg = (r.stderr or "").strip().splitlines()
        last = msg[-1] if msg else f"ffprobe exited {r.returncode}"
        if last.startswith(path + ":"):
            last = last[len(path) + 1:].strip()
        raise ProbeError(f"Unreadable: {last}" + (" (file may be truncated or still copying)" if "Invalid data" in last or "moov atom" in last else ""))
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        raise ProbeError("ffprobe returned unreadable output")


_DEPTH_RE = re.compile(r"(?:p|le|be|f)?(\d{2})(?:le|be)?$")


def pix_fmt_info(pix_fmt: str | None) -> dict:
    if not pix_fmt:
        return {"bit_depth": None, "chroma": None, "alpha": None, "float": False}
    pf = pix_fmt.lower()
    depth = 8
    m = re.search(r"(\d+)(le|be)?$", pf)
    if "f32" in pf or pf.endswith("f32le") or pf.endswith("f32be"):
        depth = 32
    elif "f16" in pf:
        depth = 16
    elif pf.startswith(("rgb48", "bgr48", "rgba64", "bgra64")):
        depth = 16
    elif m and pf.startswith(("yuv", "gbr", "gray", "ya", "p0", "p2", "p4", "x2", "xv")):
        n = int(m.group(1))
        if n in (9, 10, 12, 14, 16):
            depth = n
        elif pf.startswith(("p010", "x2rgb10", "x2bgr10")):
            depth = 10
        elif pf.startswith("p016"):
            depth = 16
    elif pf.startswith(("p010", "y210")):
        depth = 10
    if pf.startswith(("yuv420", "yuva420", "yuvj420", "nv12", "nv21", "p010", "p016")):
        chroma = "4:2:0"
    elif pf.startswith(("yuv422", "yuva422", "yuvj422", "uyvy", "yuyv", "nv16", "y210", "v210")):
        chroma = "4:2:2"
    elif pf.startswith(("yuv444", "yuva444", "yuvj444", "nv24")):
        chroma = "4:4:4"
    elif pf.startswith(("yuv411",)):
        chroma = "4:1:1"
    elif pf.startswith(("gray", "ya")):
        chroma = "mono"
    elif pf.startswith(("rgb", "bgr", "gbr", "argb", "abgr", "rgba", "bgra", "x2rgb", "x2bgr", "0rgb", "rgb0", "pal8")):
        chroma = "RGB"
    else:
        chroma = None
    alpha = bool(re.match(r"^(yuva|gbrap|rgba|bgra|argb|abgr|ya)", pf))
    return {"bit_depth": depth, "chroma": chroma, "alpha": alpha, "float": "f32" in pf or "f16" in pf}


def _rotation(stream: dict) -> int:
    for sd in stream.get("side_data_list", []) or []:
        if "rotation" in sd:
            try:
                return int(round(float(sd["rotation"]))) % 360
            except (TypeError, ValueError):
                pass
    try:
        return int(stream.get("tags", {}).get("rotate", 0)) % 360
    except ValueError:
        return 0


def _ratio(s: str | None) -> Fraction | None:
    if not s or s in ("0:1", "N/A"):
        return None
    try:
        a, b = s.split(":")
        return Fraction(int(a), int(b)) if int(b) else None
    except Exception:
        return None


def normalise(probe: dict, path: str) -> dict:
    fmt = probe.get("format", {}) or {}
    streams = probe.get("streams", []) or []
    v = [s for s in streams if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")]
    a = [s for s in streams if s.get("codec_type") == "audio"]
    d = [s for s in streams if s.get("codec_type") == "data"]
    out = {
        "container": fmt.get("format_name"),
        "container_long": fmt.get("format_long_name"),
        "duration": float(fmt["duration"]) if fmt.get("duration") not in (None, "N/A") else None,
        "start_time": float(fmt["start_time"]) if fmt.get("start_time") not in (None, "N/A") else 0.0,
        "bit_rate": int(fmt["bit_rate"]) if str(fmt.get("bit_rate", "")).isdigit() else None,
        "size": int(fmt["size"]) if str(fmt.get("size", "")).isdigit() else None,
        "stream_count": len(streams),
        "video_streams": [],
        "audio_streams": [],
        "data_streams": [{"index": s.get("index"), "codec": s.get("codec_tag_string") or s.get("codec_name"),
                          "tags": s.get("tags", {})} for s in d],
        "format_tags": fmt.get("tags", {}),
    }
    tc = (fmt.get("tags") or {}).get("timecode")
    for s in v:
        rate = parse_rate(s.get("r_frame_rate"))
        avg = parse_rate(s.get("avg_frame_rate"))
        pinfo = pix_fmt_info(s.get("pix_fmt"))
        bits = s.get("bits_per_raw_sample")
        if str(bits).isdigit() and int(bits) > 0:
            pinfo["bit_depth"] = int(bits)
        sar = _ratio(s.get("sample_aspect_ratio"))
        dar = _ratio(s.get("display_aspect_ratio"))
        w, h = s.get("width"), s.get("height")
        rot = _rotation(s)
        if dar is None and w and h:
            dar = Fraction(w, h) * (sar or 1)
        dw, dh = (h, w) if rot in (90, 270) else (w, h)
        nb = s.get("nb_frames")
        dur = float(s["duration"]) if s.get("duration") not in (None, "N/A") else out["duration"]
        frames = int(nb) if str(nb).isdigit() and int(nb) > 0 else (round(dur * float(rate)) if dur and rate else None)
        vt = (s.get("tags") or {}).get("timecode")
        tc = tc or vt
        out["video_streams"].append({
            "index": s.get("index"), "codec": s.get("codec_name"), "codec_long": s.get("codec_long_name"),
            "profile": s.get("profile"), "codec_tag": s.get("codec_tag_string"),
            "width": w, "height": h, "display_width": dw, "display_height": dh,
            "rotation": rot,
            "orientation": "portrait" if (dw and dh and dh > dw) else ("square" if dw == dh else "landscape"),
            "sar": f"{sar.numerator}:{sar.denominator}" if sar else "1:1",
            "par": float(sar) if sar else 1.0,
            "dar": f"{dar.numerator}:{dar.denominator}" if dar else None,
            "dar_float": round(float(dar), 4) if dar else None,
            "fps": f"{rate.numerator}/{rate.denominator}" if rate else None,
            "fps_float": round(float(rate), 3) if rate else None,
            "avg_fps": f"{avg.numerator}/{avg.denominator}" if avg else None,
            "vfr": bool(rate and avg and abs(float(rate) - float(avg)) > 0.01),
            "field_order": s.get("field_order", "unknown"),
            "pix_fmt": s.get("pix_fmt"),
            "bit_depth": pinfo["bit_depth"], "chroma": pinfo["chroma"], "alpha": pinfo["alpha"],
            "float": pinfo["float"],
            "color_range": s.get("color_range"),
            "color_primaries": s.get("color_primaries"),
            "color_trc": s.get("color_transfer"),
            "color_space": s.get("color_space"),
            "duration": dur, "frames": frames,
            "bit_rate": int(s["bit_rate"]) if str(s.get("bit_rate", "")).isdigit() else None,
            "hdr_side_data": [sd.get("side_data_type") for sd in (s.get("side_data_list") or [])
                              if "Mastering" in str(sd.get("side_data_type")) or "Content light" in str(sd.get("side_data_type"))],
        })
    for s in a:
        out["audio_streams"].append({
            "index": s.get("index"), "codec": s.get("codec_name"), "codec_long": s.get("codec_long_name"),
            "sample_rate": int(s["sample_rate"]) if str(s.get("sample_rate", "")).isdigit() else None,
            "channels": s.get("channels"), "layout": s.get("channel_layout") or "unknown",
            "sample_fmt": s.get("sample_fmt"),
            "bit_depth": int(s["bits_per_raw_sample"]) if str(s.get("bits_per_raw_sample", "")).isdigit() and int(s["bits_per_raw_sample"]) else (int(s.get("bits_per_sample") or 0) or None),
            "duration": float(s["duration"]) if s.get("duration") not in (None, "N/A") else None,
            "language": (s.get("tags") or {}).get("language"),
        })
    for s in d:
        if (s.get("tags") or {}).get("timecode"):
            tc = tc or s["tags"]["timecode"]
    out["start_tc"] = tc
    return out


def summary_fields(meta: dict) -> dict:
    """Flattened columns used for list filtering."""
    v = meta["video_streams"][0] if meta.get("video_streams") else {}
    a = meta["audio_streams"][0] if meta.get("audio_streams") else {}
    total_ch = sum((s.get("channels") or 0) for s in meta.get("audio_streams", []))
    return {
        "container": container_of(meta),
        "vcodec": v.get("codec"), "acodec": a.get("codec"),
        "width": v.get("display_width") or v.get("width"),
        "height": v.get("display_height") or v.get("height"),
        "fps": v.get("fps"), "fps_float": v.get("fps_float"),
        "duration": meta.get("duration") or v.get("duration") or a.get("duration"),
        "frames": v.get("frames"),
        "start_tc": meta.get("start_tc"),
        "audio_channels": total_ch or None,
        "sample_rate": a.get("sample_rate"),
        "pix_fmt": v.get("pix_fmt"), "bit_depth": v.get("bit_depth"),
        "chroma": v.get("chroma"), "alpha": 1 if v.get("alpha") else 0,
        "color_primaries": v.get("color_primaries"), "color_trc": v.get("color_trc"),
        "color_space": v.get("color_space"),
    }
