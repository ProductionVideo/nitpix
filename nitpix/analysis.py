"""Content analysis with ffmpeg filters (single decode pass per file).

Video: blackdetect, freezedetect, signalstats YAVG (for flash-frame heuristics).
Audio (first audio stream): silencedetect, windowed per-channel astats
(peak / RMS / peak-count for clipping and channel drop-outs), ebur128 (integrated
loudness, LRA, true peak).

Everything here is *detection*: results are heuristics and are reported as such.
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import threading

from .probe import find_tool, pix_fmt_info


class Cancelled(Exception):
    pass


class ProcControl:
    """Lets the job manager pause / resume / kill the running ffmpeg process."""

    def __init__(self):
        self.procs: set[subprocess.Popen] = set()
        self.lock = threading.Lock()
        self.cancelled = False
        self.paused = False

    def add(self, p):
        with self.lock:
            self.procs.add(p)
            if self.paused and hasattr(signal, "SIGSTOP"):
                try:
                    p.send_signal(signal.SIGSTOP)
                except Exception:
                    pass

    def remove(self, p):
        with self.lock:
            self.procs.discard(p)

    def pause(self):
        with self.lock:
            self.paused = True
            for p in self.procs:
                if hasattr(signal, "SIGSTOP"):
                    try:
                        p.send_signal(signal.SIGSTOP)
                    except Exception:
                        pass

    def resume(self):
        with self.lock:
            self.paused = False
            for p in self.procs:
                if hasattr(signal, "SIGCONT"):
                    try:
                        p.send_signal(signal.SIGCONT)
                    except Exception:
                        pass

    def cancel(self):
        with self.lock:
            self.cancelled = True
            for p in self.procs:
                try:
                    if hasattr(signal, "SIGCONT"):
                        p.send_signal(signal.SIGCONT)
                    p.kill()
                except Exception:
                    pass


_KV = re.compile(r"(\w+):\s*(-?[\d.]+|N/A)")
_PTS = re.compile(r"pts_time:\s*(-?[\d.]+)")
_TAG = re.compile(r"^\[(\w+?)(?:_(\d+))? @ [0-9a-fx]+\] (.*)$")


def analyse(path: str, meta: dict, settings: dict, ctl: ProcControl | None = None,
            on_progress=None, seq_input: dict | None = None) -> dict:
    """Run one ffmpeg pass. Returns raw detection results.

    seq_input: for image sequences {"printf": ..., "start": n, "fps": 25} (unused for now)
    """
    ff = find_tool("ffmpeg")
    if not ff:
        return {"error": "ffmpeg not found; content checks skipped"}
    an = settings["analysis"]
    vs = meta.get("video_streams") or []
    aus = meta.get("audio_streams") or []
    do_content = an.get("content_checks", True)
    do_loud = an.get("loudness", True)
    if not vs and not aus:
        return {}
    graphs, maps = [], []
    # ffmpeg names filters Parsed_<name>_<n>, numbered by position across the whole graph;
    # owner[n] is the audio track (index into aus) that filter n belongs to, or "alpha".
    owner, n_filters = {}, 0

    def chain(src, parts, out, track=None):
        nonlocal n_filters
        for i in range(len(parts)):
            if track is not None:
                owner[n_filters + i] = track
        n_filters += len(parts)
        graphs.append(src + ",".join(parts) + out)
        if out.count("[") == 1:
            maps.extend(["-map", out])

    if vs and do_content:
        v = vs[0]
        src = f"[0:{v['index']}]"
        if v.get("alpha"):
            # Formats with an alpha plane: track its minimum over every frame. NotchLC (and
            # others) always decode with alpha, so "has alpha" alone says nothing. Area
            # downscaling keeps an all-opaque plane exactly at full value, and any
            # transparent pixel pulls its block below it.
            chain(src, ["split"], "[vmain][valpha]")
            # The extracted plane inherits the video's range tag; alpha is always full range,
            # and a limited-range tag would clamp opaque (4095) to 3760 in conversion.
            chain("[valpha]", ["extractplanes=a", "setparams=range=pc", "scale=w='max(1,iw/4)':h='max(1,ih/4)':flags=area", "signalstats",
                               "metadata=mode=print:key=lavfi.signalstats.YMIN"], "[va]", track="alpha")
            src = "[vmain]"
        chain(src, ["scale=w='min(640,iw)':h=-2:flags=fast_bilinear", "format=yuv420p",
                    f"blackdetect=d={an['black_min_duration']}:pix_th={an['black_pixel_threshold']}",
                    f"freezedetect=n={an['freeze_noise']}:d={an['freeze_min_duration']}",
                    "signalstats", "metadata=mode=print:key=lavfi.signalstats.YAVG"], "[vo]")
    if do_content or do_loud:
        for k, a in enumerate(aus):
            sr = a.get("sample_rate") or 48000
            parts = []
            if do_content:
                parts.append(f"silencedetect=n={an['silence_threshold_db']}dB:d={an['silence_min_duration']}")
                parts.append(f"asetnsamples=n={max(1024, sr // 2)}:p=0")
                parts.append("astats=metadata=1:reset=1:measure_perchannel=Peak_level+RMS_level+Peak_count+Flat_factor:measure_overall=none")
                parts.append("ametadata=mode=print")
            if do_loud:
                parts.append("ebur128=peak=true")
            chain(f"[0:{a['index']}]", parts, f"[ao{k}]", track=k)
    if not graphs:
        return {}
    cmd = [ff, "-hide_banner", "-nostdin", "-nostats", "-loglevel", "info", "-i", path]
    if an.get("max_analysis_seconds"):
        cmd[cmd.index("-i"):cmd.index("-i")] = ["-t", str(an["max_analysis_seconds"])]
    cmd += ["-filter_complex", ";".join(graphs), *maps, "-f", "null", "-"]

    res = {
        "black": [], "freeze": [], "yavg": [], "decode_errors": [], "decode_error_count": 0, "analysed_seconds": 0.0,
        "command": " ".join(cmd[:1] + ["…"] + cmd[-6:]),
    }
    alpha = {"min": None, "frames": 0}
    if vs and vs[0].get("alpha"):
        depth = pix_fmt_info(vs[0].get("pix_fmt"))["bit_depth"] or 8
        alpha["full"] = (1 << min(depth, 16)) - 1
        res["alpha"] = alpha
    # One entry per audio stream; the first is also kept under the top-level keys.
    tracks = [{"index": a["index"], "silence": [], "audio_windows": [], "loudness": {}} for a in aus]
    duration = meta.get("duration") or 0
    p = subprocess.Popen(cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True,
                         errors="replace", bufsize=1)
    if ctl:
        ctl.add(p)
    cur_freeze = None
    cur_aw = {}
    summary = None  # track whose ebur128 summary is being printed
    last_t = 0.0
    try:
        for line in p.stderr:
            if ctl and ctl.cancelled:
                raise Cancelled()
            line = line.rstrip("\n")
            m = _TAG.match(line)
            if m:
                tag, body = m.group(1), m.group(3)
                own = owner.get(int(m.group(2))) if m.group(2) else None
                tr = tracks[own] if isinstance(own, int) else None
                # Detectors also write their events into frame metadata, which ametadata then
                # prints as lavfi.silence_start=…, so match each detector by its own tag.
                if tag == "Parsed_blackdetect" and "black_start" in body:
                    kv = dict(_KV.findall(body))
                    res["black"].append([float(kv["black_start"]), float(kv["black_end"])])
                elif tag == "Parsed_freezedetect" and "freezedetect.freeze_start:" in body:
                    cur_freeze = [float(body.split(":")[-1]), None]
                elif tag == "Parsed_freezedetect" and "freezedetect.freeze_end:" in body and cur_freeze:
                    cur_freeze[1] = float(body.split(":")[-1])
                    res["freeze"].append(cur_freeze)
                    cur_freeze = None
                elif tr and tag == "Parsed_silencedetect" and "silence_start:" in body:
                    tr["silence"].append([float(body.split("silence_start:")[1].split()[0]), None])
                elif (tr and tag == "Parsed_silencedetect" and "silence_end:" in body
                      and tr["silence"] and tr["silence"][-1][1] is None):
                    tr["silence"][-1][1] = float(body.split("silence_end:")[1].split()[0].strip("|"))
                elif own == "alpha" and tag == "Parsed_metadata":
                    if body.startswith("lavfi.signalstats.YMIN="):
                        ymin = float(body.split("=")[1])
                        alpha["min"] = ymin if alpha["min"] is None else min(alpha["min"], ymin)
                        alpha["frames"] += 1
                elif tag.startswith("Parsed_metadata") or tag == "Parsed_metadata":
                    if body.startswith("frame:"):
                        pm = _PTS.search(body)
                        t = float(pm.group(1)) if pm else None
                        res["yavg"].append([t, None])
                        if t is not None:
                            last_t = max(last_t, t)
                    elif body.startswith("lavfi.signalstats.YAVG=") and res["yavg"]:
                        res["yavg"][-1][1] = float(body.split("=")[1])
                elif tr and tag == "Parsed_ametadata":
                    if body.startswith("frame:"):
                        pm = _PTS.search(body)
                        aw = cur_aw[tr["index"]] = {"t": float(pm.group(1)) if pm else None, "ch": {}}
                        tr["audio_windows"].append(aw)
                        if aw["t"] is not None:
                            last_t = max(last_t, aw["t"])
                    elif body.startswith("lavfi.astats.") and tr["index"] in cur_aw:
                        k, _, val = body.partition("=")
                        parts = k.split(".")
                        if len(parts) == 4 and parts[2].isdigit():
                            ch = int(parts[2])
                            try:
                                fv = float(val)
                            except ValueError:
                                fv = float("-inf") if "inf" in val else None
                            cur_aw[tr["index"]]["ch"].setdefault(ch, {})[parts[3]] = fv
                elif tr and tag == "Parsed_ebur128":
                    if "Summary:" in body:
                        summary = tr
                elif not tag.startswith("Parsed_") and _is_decode_error(body):
                    res["decode_error_count"] += 1
                    if len(res["decode_errors"]) < 20:
                        res["decode_errors"].append(f"{tag}: {body}")
            else:
                if summary is not None:
                    _parse_loudness_line(line, summary["loudness"])
                elif _is_decode_error(line) and not line.startswith(("Input", "Output", "Stream", "  ")):
                    res["decode_error_count"] += 1
                    if len(res["decode_errors"]) < 20:
                        res["decode_errors"].append(line.strip())
            if on_progress and duration and last_t:
                on_progress(min(1.0, last_t / duration))
        p.wait()
    finally:
        if ctl:
            ctl.remove(p)
        if p.poll() is None:
            p.kill()
    if ctl and ctl.cancelled:
        raise Cancelled()
    if cur_freeze:  # freeze running to end of file
        cur_freeze[1] = duration or last_t
        res["freeze"].append(cur_freeze)
    for tr in tracks:
        if tr["silence"] and tr["silence"][-1][1] is None:
            tr["silence"][-1][1] = duration or last_t
    first = tracks[0] if tracks else {"silence": [], "audio_windows": [], "loudness": {}}
    res.update(silence=first["silence"], audio_windows=first["audio_windows"], loudness=first["loudness"],
               audio_tracks=tracks[1:])
    res["analysed_seconds"] = last_t
    res["returncode"] = p.returncode
    return res


def _is_decode_error(s: str) -> bool:
    s2 = s.lower()
    return any(k in s2 for k in ("error while decoding", "invalid data", "corrupt", "concealing",
                                 "missing reference", "truncat", "partial file", "invalid nal",
                                 "decode_slice", "packet too small", "moov atom not found"))


def _parse_loudness_line(line: str, out: dict):
    s = line.strip()
    for key, name in (("I:", "integrated"), ("LRA:", "lra"), ("Peak:", "true_peak"),
                      ("Threshold:", None), ("LRA low:", "lra_low"), ("LRA high:", "lra_high")):
        if s.startswith(key) and name:
            try:
                out[name] = float(s.split(":", 1)[1].split()[0])
            except (ValueError, IndexError):
                out[name] = None


def flash_frames(yavg: list, fps: float, jump: float) -> list[int]:
    """Frame indices whose mean luma jumps away from BOTH neighbours, which in turn
    agree with each other: the signature of a single-frame flash or drop-out."""
    vals = [v for _, v in yavg if v is not None]
    out = []
    for i in range(1, len(vals) - 1):
        a, b, c = vals[i - 1], vals[i], vals[i + 1]
        if abs(b - a) >= jump and abs(b - c) >= jump and abs(a - c) < jump / 3:
            out.append(i)
    return out


def merge_regions(times: list[float], gap: float) -> list[list[float]]:
    out = []
    for t in sorted(times):
        if out and t - out[-1][1] <= gap:
            out[-1][1] = t
        else:
            out.append([t, t])
    return out
