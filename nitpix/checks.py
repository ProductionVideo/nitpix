"""Turns metadata + analysis into QC findings.

Every finding carries:
  kind        measured  – read directly from the file / header (facts)
              detected  – heuristic detection that a reviewer should confirm
  auto_result pass | warning | fail | info
  confidence  high | medium | low
  why         why the check matters to a delivery
  frame_in/out  0-based frame offsets (sequences: offset from first frame)
"""
from __future__ import annotations

import statistics
from fractions import Fraction

from . import vocab
from .analysis import flash_frames
from .timecode import parse_rate

WHY = {
    "integrity.zero_byte": "A zero-byte file is an empty placeholder or a failed copy/render; it cannot be played and usually means the real file is missing.",
    "integrity.readable": "If the container cannot be opened, nothing downstream (playout, edit, LED server) will be able to use it.",
    "integrity.decode": "Decoder errors mean damaged or truncated media; playback may glitch, drop frames or stop.",
    "integrity.stream_durations": "Audio and video of different lengths often signals a bad export or sync drift.",
    "format.container": "Playback servers and broadcasters accept specific wrappers; the wrong container can be rejected on ingest.",
    "format.codec": "The codec and profile determine quality, compatibility and whether the file meets the delivery spec.",
    "format.resolution": "A resolution mismatch means scaling, letterboxing or rejection by the destination system.",
    "format.fps": "Wrong or variable frame rate causes judder, sync drift and frame-rate conversion artefacts.",
    "format.scan": "Interlaced material shown on progressive displays (LED, web) produces combing artefacts.",
    "format.duration": "Durations outside the agreed length usually mean a wrong version or a cut problem.",
    "format.aspect": "Non-square pixels or an unexpected display aspect ratio make images look stretched if not handled.",
    "format.orientation": "Rotation flags are honoured by some players and ignored by others, so the picture may appear sideways.",
    "format.timecode": "Start timecode is used to conform, log notes and line up with EDLs and running orders.",
    "colour.tags": "Untagged or wrong colour metadata makes players guess, producing visible colour and contrast shifts.",
    "colour.bit_depth": "Lower bit depth than required causes banding in gradients and limits grading headroom.",
    "colour.chroma": "Chroma subsampling affects keying, fine colour detail and spec compliance.",
    "colour.alpha": "An unexpected or missing alpha channel breaks compositing or wastes bandwidth.",
    "colour.hdr": "HDR transfer functions need HDR-aware playback; on SDR chains they look washed out.",
    "audio.presence": "Missing or unexpected audio tracks are a common reason for rejection.",
    "audio.codec": "Delivery specs usually require uncompressed PCM or a specific codec.",
    "audio.sample_rate": "Wrong sample rates cause pitch/sync errors or forced resampling.",
    "audio.channels": "Channel count and layout must match the mix format the destination expects.",
    "audio.bit_depth": "Lower bit depth reduces dynamic range and can fail technical specs.",
    "audio.loudness": "Broadcasters and platforms normalise or reject programmes outside their loudness target.",
    "audio.true_peak": "True peaks above the limit distort after encoding or D/A conversion.",
    "content.black": "Unexpected black can mean a missing shot, an export gap or a render failure.",
    "content.freeze": "Frozen frames often mean a dropped render, a held frame in the edit or a stalled capture.",
    "content.flash": "Single-frame flashes are usually stray frames or edit errors and can be a photosensitivity risk.",
    "content.silence": "Unexpected silence may mean a missing mix stem or a mute left in the edit.",
    "content.clipping": "Clipped audio is audibly distorted and cannot be repaired cleanly downstream.",
    "content.channel_dropout": "A channel that goes silent while others play usually means a routing or stem error.",
    "seq.naming": "Consistent naming and padding is needed for sequence import in comp, edit and media servers.",
    "seq.range": "The frame range must match the shot's cut length and the agreed start frame (often 1001).",
    "seq.missing": "Missing frames cause holds, black frames or import failures in the sequence.",
    "seq.duplicate_numbers": "Two files claiming the same frame number make the sequence ambiguous.",
    "seq.padding": "Mixed padding breaks pattern matching (####) in most software.",
    "seq.repeated": "Byte-identical consecutive frames suggest a stuck render or a copied placeholder frame.",
    "seq.dimensions": "Frames with different dimensions break the sequence or cause scaling jumps.",
    "seq.unreadable": "Truncated or zero-byte frames are failed renders/copies and will drop out on playback.",
    "seq.size_outlier": "A frame much smaller than its neighbours is often a partial or blank render.",
    "seq.format": "File format and bit depth must match the pipeline's expectations.",
    "spec.preset": "Shows which delivery spec these results were compared against.",
}

CHANNEL_NAMES = {
    "mono": ["C"], "stereo": ["L", "R"], "2.1": ["L", "R", "LFE"], "3.0": ["L", "R", "C"],
    "quad": ["L", "R", "Ls", "Rs"], "5.0": ["L", "R", "C", "Ls", "Rs"],
    "5.1": ["L", "R", "C", "LFE", "Ls", "Rs"], "5.1(side)": ["L", "R", "C", "LFE", "Ls", "Rs"],
    "7.1": ["L", "R", "C", "LFE", "Lb", "Rb", "Ls", "Rs"],
}


# astats Flat_factor at or above which a full-scale window counts as clipped. Calibrated
# with real ffmpeg: clean sine at -0.1 dBFS and brick-wall limited noise read exactly 0;
# hard-clipped sine and noise read 12-33.
CLIP_FLAT_MIN = 1.0


class Builder:
    def __init__(self, fps: Fraction | None, start_time: float = 0.0):
        self.items: list[dict] = []
        self.fps = fps
        self.t0 = start_time or 0.0

    def frame(self, t):
        if t is None or not self.fps:
            return None
        return max(0, int(round((t - self.t0) * float(self.fps))))

    def add(self, key, category, result, title, detail="", measured=None, expected=None,
            kind="measured", confidence="high", t_in=None, t_out=None, frame_in=None, frame_out=None):
        if frame_in is None and t_in is not None:
            frame_in = self.frame(t_in)
        if frame_out is None and t_out is not None:
            frame_out = max(frame_in or 0, self.frame(t_out) - 1)
        self.items.append({
            "check_key": key, "category": category, "kind": kind, "auto_result": result,
            "confidence": confidence, "title": title, "detail": detail,
            "measured": None if measured is None else str(measured),
            "expected": None if expected is None else str(expected),
            "why": WHY.get(key, ""), "frame_in": frame_in, "frame_out": frame_out,
        })


def _norm_list(v):
    if v is None:
        return None
    return v if isinstance(v, list) else [v]


def _spec(b, key, cat, title, measured, expected, ok=None, fmt=lambda x: x, field=None):
    """Compare a measured value against an expected value or list. With `field`, values
    are matched through the preset vocabulary ("Rec.709" == "bt709", "23.98" == 24000/1001)."""
    exp = _norm_list(expected)
    if exp is None:
        b.add(key, cat, "info", title, measured=fmt(measured))
        return
    if ok is None and field:
        ok = vocab.matches(field, measured, exp)
    if ok is None:
        ok = measured in exp or (isinstance(measured, str) and any(str(e).lower() == measured.lower() for e in exp))
    b.add(key, cat, "pass" if ok else "fail", title, measured=fmt(measured),
          expected=" or ".join(str(fmt(e)) for e in exp),
          detail="" if ok else "Does not match the delivery spec.")


def _fmt_fps(s):
    r = parse_rate(s)
    if not r:
        return s
    f = float(r)
    return f"{f:.3f}".rstrip("0").rstrip(".") + " fps"


def media_findings(kind: str, meta: dict, ana: dict | None, preset: dict, settings: dict) -> list[dict]:
    an = settings["analysis"]
    vs = (meta.get("video_streams") or [None])[0]
    aus = meta.get("audio_streams") or []
    a0 = aus[0] if aus else None
    fps = parse_rate(vs["fps"]) if vs and vs.get("fps") else Fraction(25)
    b = Builder(fps, meta.get("start_time", 0.0))
    pv = preset.get("video") or {}
    pa = preset.get("audio") or {}
    has_spec = bool(pv or pa)

    b.add("integrity.zero_byte", "integrity", "pass", "File has data", measured=_size(meta.get("size")))
    b.add("integrity.readable", "integrity", "pass", "Container opens and streams are readable",
          measured=meta.get("container_long") or meta.get("container"))
    if ana and ana.get("error"):
        b.add("integrity.decode", "integrity", "info", "Decode check not run", detail=ana["error"])
    elif ana is not None:
        n = ana.get("decode_error_count", 0)
        b.add("integrity.decode", "integrity", "fail" if n else "pass",
              f"{n} decoder error(s) during full decode" if n else "Full decode completed without errors",
              detail="\n".join(ana.get("decode_errors", [])[:8]), measured=f"{n} errors",
              confidence="high")
        if ana.get("returncode") not in (0, None):
            b.add("integrity.decode", "integrity", "fail", "ffmpeg could not finish decoding the file",
                  detail=f"exit code {ana.get('returncode')}")
    if vs and a0 and vs.get("duration") and a0.get("duration"):
        diff = abs(vs["duration"] - a0["duration"])
        frame = 1 / float(fps)
        res = "pass" if diff <= frame * 1.01 else ("warning" if diff < 1 else "fail")
        b.add("integrity.stream_durations", "integrity", res, "Audio and video durations match" if res == "pass"
              else "Audio and video durations differ", measured=f"video {vs['duration']:.3f}s / audio {a0['duration']:.3f}s",
              expected="within 1 frame")

    # ---- format ---------------------------------------------------------
    if kind == "audio":
        b.add("format.container", "format", "info", "Container", measured=meta.get("container_long") or meta.get("container") or "?",
              detail="Audio-only file: video container rules in the spec are not applied.")
        pv = {}
    else:
        name = vocab.container_of(meta)
        label = f"{name.upper()} · {meta.get('container_long')}" if name and meta.get("container_long") else \
            (meta.get("container_long") or meta.get("container") or "?")
        exp = pv.get("containers")
        if exp:
            ok = vocab.matches("containers", name, exp)
            b.add("format.container", "format", "pass" if ok else "fail", "Container", measured=label,
                  expected=" or ".join(exp), detail="" if ok else "Does not match the delivery spec.")
        else:
            b.add("format.container", "format", "info", "Container", measured=label)
    if vs:
        codec = vs.get("codec") + (f" {vs['profile']}" if vs.get("profile") else "")
        ok = None
        exp = None
        if pv.get("codecs"):
            # Profiles only constrain the codecs they belong to (HQ is a ProRes profile, not an H.264 one).
            profs = vocab.profiles_for(vs.get("codec"), pv.get("profiles") or [])
            ok = vocab.matches("codecs", vs.get("codec"), pv["codecs"]) and \
                (not profs or vocab.matches("profiles", vs.get("profile"), profs))
            exp = []
            for c in pv["codecs"]:
                cp = vocab.profiles_for(vocab.normalise("codecs", c) or c, pv.get("profiles") or [])
                exp += [f"{c} {p}".strip() for p in (cp or [""])]
        _spec(b, "format.codec", "format", "Video codec / profile", codec, exp, ok=ok)
        w, h = vs.get("width"), vs.get("height")
        exp = f"{pv['width']}×{pv['height']}" if pv.get("width") else None
        _spec(b, "format.resolution", "format", "Resolution", f"{w}×{h}", exp,
              ok=None if not exp else (w == pv["width"] and h == pv["height"]))
        _spec(b, "format.fps", "format", "Frame rate", vs.get("fps"), pv.get("fps"), fmt=_fmt_fps, field="fps")
        if vs.get("vfr"):
            b.add("format.fps", "format", "warning", "Variable frame rate suspected",
                  measured=f"nominal {_fmt_fps(vs['fps'])}, average {_fmt_fps(vs.get('avg_fps'))}",
                  detail="Average frame rate differs from the stream's nominal rate.", confidence="medium")
        fo = vs.get("field_order") or "unknown"
        prog = fo == "progressive"
        if pv.get("progressive"):
            b.add("format.scan", "format", "pass" if prog else ("warning" if fo == "unknown" else "fail"),
                  "Scan type", measured=fo, expected="progressive",
                  detail="" if prog else ("Field order is not flagged in the file." if fo == "unknown" else "Interlaced field order flagged."))
        else:
            b.add("format.scan", "format", "info" if fo in ("progressive", "unknown") else "warning", "Scan type", measured=fo)
        par = vs.get("sar") or "1:1"
        if pv.get("par"):
            _spec(b, "format.aspect", "format", "Pixel aspect ratio", par, pv["par"], field="par")
        else:
            b.add("format.aspect", "format", "pass" if par == "1:1" else "warning", "Pixel aspect ratio",
                  measured=f"PAR {par} · DAR {vs.get('dar')}",
                  detail="" if par == "1:1" else "Non-square pixels: display width differs from stored width.")
        rot = vs.get("rotation") or 0
        orient = vs.get("orientation")
        exp_orient = None
        if pv.get("width") and pv.get("height"):
            exp_orient = "portrait" if pv["height"] > pv["width"] else "landscape"
        res = "pass"
        if rot:
            res = "warning"
        if exp_orient and orient != exp_orient:
            res = "fail"
        b.add("format.orientation", "format", res, "Orientation / rotation",
              measured=f"{orient}" + (f", rotation flag {rot}°" if rot else ""),
              expected=exp_orient, detail="Rotation metadata present; some players ignore it." if rot else "")
    dur = meta.get("duration")
    if dur is not None:
        lo, hi = pv.get("min_duration"), pv.get("max_duration")
        ok = (lo is None or dur >= lo) and (hi is None or dur <= hi)
        b.add("format.duration", "format", "info" if lo is None and hi is None else ("pass" if ok else "fail"),
              "Duration", measured=f"{dur:.3f} s" + (f" ({vs.get('frames')} frames)" if vs and vs.get("frames") else ""),
              expected=None if lo is None and hi is None else f"{lo or 0}–{hi or '∞'} s")
    if vs:
        tc = meta.get("start_tc")
        if pv.get("start_tc"):
            _spec(b, "format.timecode", "format", "Start timecode", tc or "none", pv["start_tc"], field="start_tc")
        elif pv.get("timecode") is True:  # the preset asks for a timecode track, any value
            b.add("format.timecode", "format", "pass" if tc else "warning", "Start timecode",
                  measured=tc or "none", expected="a timecode track",
                  detail="" if tc else "No timecode track or tag; notes will use 00:00:00:00 as start.")
        else:  # most deliveries (LED, web, playback servers) carry none; just say what we found
            b.add("format.timecode", "format", "info", "Start timecode", measured=tc or "none (00:00:00:00 assumed)")

    # ---- colour ---------------------------------------------------------
    if vs:
        def u(x):
            return x if x and x not in ("unknown", "unspecified", "reserved") else None
        prim, trc, mat = u(vs.get("color_primaries")), u(vs.get("color_trc")), u(vs.get("color_space"))
        measured = f"primaries {prim or '—'} · transfer {trc or '—'} · matrix {mat or '—'} · range {vs.get('color_range') or '—'}"
        exp_parts, ok = [], True
        for k, val in (("color_primaries", prim), ("color_trc", trc), ("color_space", mat)):
            if pv.get(k):
                exp_parts.append(f"{k.split('_')[1]} {'/'.join(pv[k])}")
                ok = ok and vocab.matches(k, val, pv[k])
        if vs.get("codec") == "notchlc":
            # NotchLC carries no colour metadata; ffmpeg's decoder always reports RGB / sRGB for it, which
            # says nothing about the content (measured against disguise it is Rec.601-range YCbCr).
            b.add("colour.tags", "colour", "info", "Colour metadata", measured=measured,
                  expected=" · ".join(exp_parts) if exp_parts else None,
                  detail="NotchLC has no colour tags; the values shown are the decoder's fixed defaults, not checked against the spec.")
        elif exp_parts:
            b.add("colour.tags", "colour", "pass" if ok else "fail", "Colour metadata", measured=measured,
                  expected=" · ".join(exp_parts), detail="" if ok else "Colour tags missing or different from spec.")
        else:
            missing = not (prim and trc and mat)
            b.add("colour.tags", "colour", "warning" if missing else "pass", "Colour metadata", measured=measured,
                  detail="One or more colour tags are unspecified; players will guess." if missing else "")
        if trc in ("smpte2084", "arib-std-b67") or vs.get("hdr_side_data"):
            b.add("colour.hdr", "colour", "info", "HDR signalling present", measured=f"transfer {trc}; {', '.join(vs.get('hdr_side_data') or [])}")
        bd = vs.get("bit_depth")
        if pv.get("bit_depth_min"):
            b.add("colour.bit_depth", "colour", "pass" if (bd or 0) >= pv["bit_depth_min"] else "fail", "Bit depth",
                  measured=f"{bd}-bit ({vs.get('pix_fmt')})", expected=f"≥ {pv['bit_depth_min']}-bit")
        else:
            b.add("colour.bit_depth", "colour", "info", "Bit depth", measured=f"{bd}-bit ({vs.get('pix_fmt')})")
        _spec(b, "colour.chroma", "colour", "Chroma subsampling", vs.get("chroma") or "?", pv.get("chroma"), field="chroma")
        al = bool(vs.get("alpha"))
        mode = pv.get("alpha", "any")
        # Measured over every decoded frame by the analysis pass, when the format has alpha.
        am = (ana or {}).get("alpha") or {}
        opaque = al and am.get("frames") and am.get("min") is not None and am["min"] >= am.get("full", 0)
        res, detail = "info", ""
        if mode == "required":
            res = "warning" if opaque else ("pass" if al else "fail")
            if opaque:
                detail = "The format has an alpha plane but it is fully opaque in every frame. Check it was exported with transparency."
        elif mode == "forbidden":
            res = "warning" if opaque else ("fail" if al else "pass")
            if opaque:
                detail = ("The alpha plane carries no transparency. Some decoders (NotchLC) always report one; "
                          "otherwise it costs bandwidth for nothing.")
        if opaque:
            measured = f"plane present, fully opaque in all {am['frames']} frames"
        elif al and am.get("frames") and am.get("min") is not None:
            measured = f"present, transparent pixels (lowest {am['min']:.0f} of {am.get('full')})"
        else:
            measured = "present" if al else "none"
        b.add("colour.alpha", "colour", res, "Alpha channel", measured=measured, detail=detail,
              expected={"required": "required", "forbidden": "not allowed"}.get(mode))

    # ---- audio ----------------------------------------------------------
    if not aus:
        req = pa.get("required")
        b.add("audio.presence", "audio", "fail" if req else ("info" if req is None else "pass"),
              "No audio streams", measured="0 streams", expected="audio required" if req else None)
    else:
        layouts = ", ".join(f"#{s['index']} {s.get('layout')} ({s.get('channels')}ch)" for s in aus)
        b.add("audio.presence", "audio", "pass", f"{len(aus)} audio stream(s)", measured=layouts)
        if len({(s.get('channels'), s.get('layout')) for s in aus}) > 1:
            b.add("audio.channels", "audio", "warning", "Audio streams have different layouts", measured=layouts,
                  detail="Mixed channel layouts across tracks; check the track allocation sheet.")
        # Spec rules apply to every stream; report the first, and any stream that differs.
        khz = lambda x: f"{x/1000:g} kHz" if isinstance(x, (int, float)) and x else x
        _spec(b, "audio.codec", "audio", "Audio codec", a0.get("codec"), pa.get("codecs"), field="audio_codecs")
        _spec(b, "audio.sample_rate", "audio", "Sample rate", a0.get("sample_rate"), pa.get("sample_rate"),
              fmt=khz, field="sample_rate")
        for s in aus[1:]:
            for key, prop, rule, field, fmt in (("audio.codec", "codec", "codecs", "audio_codecs", str),
                                                ("audio.sample_rate", "sample_rate", "sample_rate", "sample_rate", khz)):
                if s.get(prop) == a0.get(prop):
                    continue
                title = f"Audio #{s['index']} {prop.replace('_', ' ')} differs from #{a0['index']}"
                if pa.get(rule) is None:
                    b.add(key, "audio", "warning", title, measured=fmt(s.get(prop)), expected=fmt(a0.get(prop)),
                          detail="Streams in one deliverable normally share this; check the track allocation sheet.")
                else:
                    _spec(b, key, "audio", title, s.get(prop), pa.get(rule), fmt=fmt, field=field)
        if pa.get("channels"):
            total = sum(s.get("channels") or 0 for s in aus)
            b.add("audio.channels", "audio", "pass" if total == pa["channels"] else "fail", "Channel count",
                  measured=f"{total} ch ({layouts})", expected=f"{pa['channels']} ch")
        else:
            b.add("audio.channels", "audio", "info", "Channel layout", measured=layouts)
        if a0.get("bit_depth"):
            b.add("audio.bit_depth", "audio", "info", "Audio bit depth", measured=f"{a0['bit_depth']}-bit {a0.get('sample_fmt') or ''}".strip())

    if ana and not ana.get("error"):
        loud = ana.get("loudness") or {}
        if loud.get("integrated") is not None:
            I = loud["integrated"]
            tgt, tol = pa.get("loudness_target"), pa.get("loudness_tolerance", 1.0)
            if tgt is not None:
                ok = abs(I - tgt) <= tol
                b.add("audio.loudness", "audio", "pass" if ok else "fail", "Integrated loudness (EBU R128 / BS.1770)",
                      measured=f"{I:.1f} LUFS · LRA {loud.get('lra', 0):.1f} LU", expected=f"{tgt:.1f} ± {tol:g} LUFS",
                      detail="Measured on the first audio stream." + ("" if ok else f" Off by {I - tgt:+.1f} LU."))
            else:
                b.add("audio.loudness", "audio", "info", "Integrated loudness (EBU R128 / BS.1770)",
                      measured=f"{I:.1f} LUFS · LRA {loud.get('lra', 0):.1f} LU", detail="Measured on the first audio stream.")
        if loud.get("true_peak") is not None:
            tp = loud["true_peak"]
            mx = pa.get("true_peak_max")
            if mx is not None:
                b.add("audio.true_peak", "audio", "pass" if tp <= mx else "fail", "True peak",
                      measured=f"{tp:.1f} dBTP", expected=f"≤ {mx:.1f} dBTP")
            else:
                b.add("audio.true_peak", "audio", "warning" if tp > 0 else "info", "True peak", measured=f"{tp:.1f} dBTP")
        if vs:
            _content_video(b, ana, an, fps, meta)
        if aus:
            tracks = [dict(ana, index=a0["index"])] + list(ana.get("audio_tracks") or [])
            for tr in tracks:
                s = next((x for x in aus if x["index"] == tr["index"]), None)
                if s is None:
                    continue
                if len(aus) == 1:
                    _content_audio(b, tr, an, s, meta)
                    continue
                # Several streams: run each through its own builder and name the stream.
                sub = Builder(b.fps, b.t0)
                _content_audio(sub, tr, an, s, meta)
                L = tr.get("loudness") or {}
                if s is not a0 and L.get("integrated") is not None:
                    sub.add("audio.loudness", "audio", "info", "Integrated loudness (EBU R128 / BS.1770)",
                            measured=f"{L['integrated']:.1f} LUFS · LRA {L.get('lra') or 0:.1f} LU"
                                     + (f" · TP {L['true_peak']:.1f} dBTP" if L.get("true_peak") is not None else ""))
                tp, mx = L.get("true_peak"), pa.get("true_peak_max")
                if s is not a0 and tp is not None and tp > (0.0 if mx is None else mx):
                    sub.add("audio.true_peak", "audio", "warning" if mx is None else "fail", "True peak",
                            measured=f"{tp:.1f} dBTP", expected=f"≤ {mx:.1f} dBTP" if mx is not None else None)
                for f in sub.items:
                    f["title"] = f"Audio #{s['index']}: {f['title']}"
                b.items.extend(sub.items)

    if has_spec:
        b.add("spec.preset", "spec", "info", "Compared against delivery spec", measured=preset.get("name"))
    return b.items


def _size(n):
    if n is None:
        return None
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:.1f} {u}" if u != "B" else f"{n} B"
        n /= 1024


def _edge(t0, t1, dur):
    if dur and t0 <= 0.05:
        return "head"
    if dur and t1 >= dur - 0.05:
        return "tail"
    return None


def _content_video(b, ana, an, fps, meta):
    dur = meta.get("duration") or 0
    t0 = meta.get("start_time", 0) or 0
    blacks = ana.get("black", [])
    for s, e in blacks[:50]:
        edge = _edge(s - t0, e - t0, dur)
        b.add("content.black", "content", "info" if edge else "warning",
              f"Black {'at ' + edge if edge else 'segment'} ({e - s:.2f}s)",
              detail=("Black at the " + edge + " is often intentional (lead-in / tail); confirm it is wanted.") if edge
              else "Picture is near-black for this range.",
              measured=f"{e - s:.2f}s", kind="detected", confidence="high" if e - s > 0.5 else "medium",
              t_in=s, t_out=e)
    if not blacks:
        b.add("content.black", "content", "pass", "No black segments detected",
              detail=f"Threshold: ≥{an['black_min_duration']}s with pixels below {an['black_pixel_threshold']:.0%} luma.",
              kind="detected", confidence="medium")
    frz = []
    for s, e in ana.get("freeze", []):
        inside_black = any(bs - 0.05 <= s and e <= be + 0.05 for bs, be in blacks)
        if not inside_black:
            frz.append((s, e))
    for s, e in frz[:50]:
        b.add("content.freeze", "content", "warning", f"Frozen picture ({e - s:.2f}s)",
              detail="Consecutive frames are nearly identical. Intentional holds and static graphics also trigger this.",
              measured=f"{e - s:.2f}s", kind="detected", confidence="medium", t_in=s, t_out=e)
    if not frz:
        b.add("content.freeze", "content", "pass", "No frozen segments detected",
              detail=f"Threshold: ≥{an['freeze_min_duration']}s of static picture (black holds excluded).",
              kind="detected", confidence="medium")
    yavg = ana.get("yavg") or []
    flashes = flash_frames(yavg, float(fps), an["flash_luma_jump"])
    for i in flashes[:50]:
        y = [v for _, v in yavg]
        b.add("content.flash", "content", "warning", "Possible flash / stray frame",
              detail=f"Mean luma jumps {y[i-1]:.0f} → {y[i]:.0f} → {y[i+1]:.0f} (0–255) for a single frame. "
                     "Hard cuts between very different shots can also trigger this.",
              measured=f"ΔY {abs(y[i] - y[i-1]):.0f}", kind="detected", confidence="low",
              frame_in=i, frame_out=i)
    if not flashes and yavg:
        b.add("content.flash", "content", "pass", "No single-frame flashes detected",
              detail=f"Threshold: mean-luma jump ≥ {an['flash_luma_jump']:.0f} vs both neighbours.",
              kind="detected", confidence="low")


def _content_audio(b, ana, an, a0, meta):
    dur = meta.get("duration") or 0
    t0 = meta.get("start_time", 0) or 0
    sil = ana.get("silence", [])
    for s, e in sil[:50]:
        if e is None:
            continue
        if dur and e - s >= 0.95 * dur:
            b.add("content.silence", "content", "warning", "Silent for the whole file",
                  detail=f"All channels stay below {an['silence_threshold_db']} dBFS. Check the track allocation.",
                  measured=f"{e - s:.2f}s", kind="detected", confidence="high")
            continue
        edge = _edge(s - t0, e - t0, dur)
        b.add("content.silence", "content", "info" if edge else "warning",
              f"Silence {'at ' + edge if edge else ''} ({e - s:.2f}s)".replace("  ", " "),
              detail=f"All channels below {an['silence_threshold_db']} dBFS.",
              measured=f"{e - s:.2f}s", kind="detected", confidence="high", t_in=s, t_out=e)
    if not sil and ana.get("audio_windows"):
        b.add("content.silence", "content", "pass", "No silent gaps detected",
              detail=f"Threshold: ≥{an['silence_min_duration']}s below {an['silence_threshold_db']} dBFS.",
              kind="detected", confidence="high")
    wins = ana.get("audio_windows") or []
    if not wins:
        return
    names = CHANNEL_NAMES.get((a0.get("layout") or "").lower(), [])
    chname = lambda c: f"ch{c}" + (f" ({names[c-1]})" if c - 1 < len(names) else "")
    win = (wins[1]["t"] - wins[0]["t"]) if len(wins) > 1 and wins[1]["t"] is not None else 0.5
    # Clipping: a window whose peak is at full scale AND flat-topped (consecutive samples
    # stuck at the peak). Peak level alone is not clipping: clean tone and hot limited
    # masters reach -0.1 dBFS many times per window with a flat factor of exactly 0.
    clip_times, clip_flat = {}, {}
    peak_all = -200.0
    for w in wins:
        for c, st in w["ch"].items():
            pk = st.get("Peak_level")
            if pk is not None and pk != float("-inf"):
                peak_all = max(peak_all, pk)
                ff = st.get("Flat_factor") or 0
                if pk >= an["clip_threshold_dbfs"] and ff >= CLIP_FLAT_MIN:
                    clip_times.setdefault(c, []).append(w["t"])
                    clip_flat.setdefault(c, []).append((w["t"], ff))
    from .analysis import merge_regions
    nclip = 0
    for c, ts in sorted(clip_times.items()):
        for s, e in merge_regions(ts, win * 1.5)[:20]:
            nclip += 1
            ff = max(f for t, f in clip_flat[c] if s <= t <= e)
            b.add("content.clipping", "content", "warning", f"Clipping on {chname(c)}",
                  detail="The waveform is flat-topped: runs of consecutive samples are stuck at full scale. "
                         "Short isolated runs (often from decoding lossy audio) may be inaudible; confirm by listening.",
                  measured=f"flat factor {ff:.1f}", expected=f"0 at ≥ {an['clip_threshold_dbfs']} dBFS",
                  kind="detected", confidence="high" if ff >= 6 else "medium", t_in=s, t_out=e + win)
    b.add("content.clipping", "content", "pass" if not nclip else "info",
          "Sample peak" if nclip else "No clipping detected",
          measured=f"{peak_all:.1f} dBFS" if peak_all > -200 else "silent", kind="measured")
    # Channel drop-outs and dead channels.
    dead = an["dead_channel_db"]
    nch = max((len(w["ch"]) for w in wins), default=0)
    if nch > 1:
        for c in range(1, nch + 1):
            active = [(w["t"], (w["ch"].get(c) or {}).get("RMS_level")) for w in wins]
            others_active = [any(((w["ch"].get(o) or {}).get("RMS_level") or -999) > dead for o in w["ch"] if o != c) for w in wins]
            is_dead = [(r is None or r == float("-inf") or r <= dead) for _, r in active]
            if all(is_dead) and any(others_active):
                lfe = c - 1 < len(names) and names[c - 1] == "LFE"
                b.add("content.channel_dropout", "content", "info" if lfe else "warning",
                      f"{chname(c)} silent for the whole file",
                      detail="LFE is often unused." if lfe else "Channel carries no signal while others do. Check routing / stem export.",
                      kind="detected", confidence="high")
                continue
            drop_ts = [wins[i]["t"] for i in range(len(wins)) if is_dead[i] and others_active[i]]
            if drop_ts and not all(is_dead):
                for s, e in merge_regions(drop_ts, win * 1.5)[:10]:
                    if e - s + win < 1.0:
                        continue
                    b.add("content.channel_dropout", "content", "warning", f"{chname(c)} drops out ({e - s + win:.1f}s)",
                          detail="This channel goes silent while other channels continue.",
                          kind="detected", confidence="medium", t_in=s, t_out=e + win)
        if not any(f["check_key"] == "content.channel_dropout" for f in b.items):
            b.add("content.channel_dropout", "content", "pass", "All channels carry signal throughout",
                  kind="detected", confidence="medium")


# ---------------------------------------------------------------------------
# Image sequences
# ---------------------------------------------------------------------------

def sequence_findings(seq: dict, first_meta: dict | None, preset: dict, settings: dict) -> list[dict]:
    ps = preset.get("sequence") or {}
    b = Builder(None)
    base = seq["first"]
    idx = lambda n: n - base
    an = settings["analysis"]

    # naming
    import re as _re
    name_ok = True
    detail = []
    if ps.get("naming_regex"):
        try:
            if not _re.match(ps["naming_regex"], seq["prefix"]):
                name_ok = False
                detail.append(f"Prefix '{seq['prefix']}' does not match naming rule {ps['naming_regex']}")
        except _re.error as e:
            name_ok = False
            detail.append(f"Naming rule {ps['naming_regex']!r} in the spec is not a valid regex ({e})")
    if ps.get("padding") and seq["padding"] != ps["padding"]:
        name_ok = False
        detail.append(f"Padding {seq['padding'] or 'none'} (expected {ps['padding']})")
    b.add("seq.naming", "sequence", ("pass" if name_ok else "fail") if ps else "info", "Naming pattern",
          measured=f"{seq['pattern']} (padding {seq['padding'] or 'none'})",
          expected=(f"padding {ps.get('padding')}" if ps.get("padding") else None), detail="; ".join(detail))
    # range
    exp_start = ps.get("start_frame")
    b.add("seq.range", "sequence", ("pass" if seq["first"] == exp_start else "fail") if exp_start else "info",
          "Frame range", measured=f"{seq['first']}–{seq['last']} · {seq['count']} frames present of {seq['expected']}",
          expected=f"starts at {exp_start}" if exp_start else None, frame_in=0, frame_out=seq["last"] - base)
    # missing
    if seq["missing_count"]:
        for a, z in seq["missing"][:50]:
            b.add("seq.missing", "sequence", "fail", f"Missing frame{'s' if z > a else ''} {a}" + (f"–{z}" if z > a else ""),
                  measured=f"{z - a + 1} frame(s)", frame_in=idx(a), frame_out=idx(z))
        if len(seq["missing"]) > 50:
            b.add("seq.missing", "sequence", "fail", f"…and {len(seq['missing']) - 50} more gaps")
    else:
        b.add("seq.missing", "sequence", "pass", "No missing frames in range", measured=f"{seq['count']} frames contiguous")
    for d in seq["duplicate_numbers"][:50]:
        b.add("seq.duplicate_numbers", "sequence", "fail", f"Frame {d['frame']} exists twice",
              measured=" / ".join(d["files"]), frame_in=idx(d["frame"]), frame_out=idx(d["frame"]))
    if not seq["duplicate_numbers"]:
        b.add("seq.duplicate_numbers", "sequence", "pass", "No duplicate frame numbers")
    if seq["inconsistent_padding_count"]:
        b.add("seq.padding", "sequence", "warning", f"{seq['inconsistent_padding_count']} file(s) use different padding",
              measured=", ".join(seq["inconsistent_padding"][:6]))
    fr = seq.get("frame_results") or {}
    # repeated content
    reps = fr.get("repeated", [])
    if an.get("seq_hash_duplicates", True):
        # Group consecutive repeats into runs so a stuck render is one finding, not thousands.
        runs = []
        for n, prev in reps:
            if runs and n == runs[-1][1] + 1:
                runs[-1][1] = n
            else:
                runs.append([n, n, prev])
        for a, z, prev in runs[:50]:
            if a == z:
                title = f"Frame {a} identical to frame {prev}"
            else:
                title = f"Frames {a}–{z} identical to frame {prev} ({z - a + 1} frames)"
            b.add("seq.repeated", "sequence", "warning", title,
                  detail="Sampled content hash and file size match the previous frame. A long run usually means a "
                         "stuck render or a placeholder; a short run can be an intentional hold.",
                  kind="detected", confidence="medium", frame_in=idx(a), frame_out=idx(z))
        if len(runs) > 50:
            b.add("seq.repeated", "sequence", "warning", f"…and {len(runs) - 50} more repeated runs",
                  kind="detected", confidence="medium")
        if not reps:
            b.add("seq.repeated", "sequence", "pass", "No repeated (identical) frames", kind="detected", confidence="medium")
    # dimensions
    dims = fr.get("dims_majority")
    odd = fr.get("dims_odd", [])
    for n, w, h in odd[:50]:
        b.add("seq.dimensions", "sequence", "fail", f"Frame {n} is {w}×{h}",
              measured=f"{w}×{h}", expected=f"{dims[0]}×{dims[1]}" if dims else None, frame_in=idx(n), frame_out=idx(n))
    if dims and not odd:
        b.add("seq.dimensions", "sequence", "pass", "Consistent dimensions", measured=f"{dims[0]}×{dims[1]}")
    for n, err in fr.get("bad", [])[:100]:
        b.add("seq.unreadable", "sequence", "fail", f"Frame {n}: {err}", frame_in=idx(n), frame_out=idx(n))
    if not fr.get("bad"):
        b.add("seq.unreadable", "sequence", "pass", "All frames readable", measured="headers verified")
    for n, size, med in fr.get("size_outliers", [])[:50]:
        b.add("seq.size_outlier", "sequence", "warning", f"Frame {n} much smaller than typical",
              measured=f"{_size(size)} vs median {_size(med)}", kind="detected", confidence="low",
              detail="May be a partial render or a frame with little content (e.g. a fade to black).",
              frame_in=idx(n), frame_out=idx(n))
    # format / depth / alpha
    hdr = fr.get("header") or {}
    fmt = seq["ext"]
    if ps.get("formats"):
        _spec(b, "seq.format", "sequence", "File format", fmt, ps["formats"], field="formats")
    else:
        b.add("seq.format", "sequence", "info", "File format", measured=fmt)
    if dims:
        exp = f"{ps['width']}×{ps['height']}" if ps.get("width") else None
        if exp:
            _spec(b, "format.resolution", "format", "Resolution", f"{dims[0]}×{dims[1]}", exp)
    bd = hdr.get("bit_depth")
    if bd:
        meas = f"{bd}-bit{' float' if hdr.get('float') else ''}"
        if ps.get("bit_depth"):
            b.add("colour.bit_depth", "colour", "pass" if vocab.matches("bit_depth", bd, ps["bit_depth"]) else "fail", "Bit depth",
                  measured=meas, expected=" or ".join(f"{x}-bit" for x in ps["bit_depth"]))
        else:
            b.add("colour.bit_depth", "colour", "info", "Bit depth", measured=meas)
    al = hdr.get("alpha")
    if al is not None:
        mode = ps.get("alpha", "any")
        res = "info" if mode == "any" else (("pass" if al else "fail") if mode == "required" else ("fail" if al else "pass"))
        b.add("colour.alpha", "colour", res, "Alpha channel", measured=("present" if al else "none") +
              (f" · channels {','.join(hdr['channel_names'])}" if hdr.get("channel_names") else ""))
    if hdr.get("compression"):
        b.add("seq.format", "sequence", "info", "EXR compression", measured=hdr["compression"])
    if first_meta and first_meta.get("video_streams"):
        v = first_meta["video_streams"][0]
        prim, trc = v.get("color_primaries"), v.get("color_trc")
        b.add("colour.tags", "colour", "info", "Colour metadata (first frame)",
              measured=f"pix_fmt {v.get('pix_fmt')} · primaries {prim or '—'} · transfer {trc or '—'}",
              detail="Image sequences rarely carry reliable colour tags; confirm colour space with the vendor (e.g. ACEScg, Rec.709, sRGB).")
    if ps:
        b.add("spec.preset", "spec", "info", "Compared against delivery spec", measured=preset.get("name"))
    return b.items


def still_findings(hdr: dict, meta: dict | None, preset: dict, settings: dict) -> list[dict]:
    b = Builder(None)
    ps = preset.get("still") or {}
    b.add("integrity.zero_byte", "integrity", "pass", "File has data", measured=_size(hdr.get("size")))
    if hdr.get("ok") is False:
        b.add("integrity.readable", "integrity", "fail", "Image header invalid", detail=hdr.get("error"))
    else:
        b.add("integrity.readable", "integrity", "pass", "Image readable",
              measured=(meta or {}).get("container_long") or hdr.get("format"))
    fmt = hdr.get("format")
    if fmt and ps.get("formats"):
        _spec(b, "format.container", "format", "Image format", fmt, ps["formats"], field="formats")
    w = hdr.get("width") or ((meta or {}).get("video_streams") or [{}])[0].get("width")
    h = hdr.get("height") or ((meta or {}).get("video_streams") or [{}])[0].get("height")
    if w:
        exp = f"{ps['width']}×{ps['height']}" if ps.get("width") and ps.get("height") else None
        res = "info" if not exp else ("pass" if (w, h) == (ps["width"], ps["height"]) else "fail")
        b.add("format.resolution", "format", res, "Resolution", measured=f"{w}×{h}", expected=exp)
        b.add("format.orientation", "format", "info", "Orientation",
              measured="portrait" if h > w else ("square" if h == w else "landscape"))
    v = ((meta or {}).get("video_streams") or [None])[0]
    al = hdr.get("alpha") if hdr.get("alpha") is not None else (v.get("alpha") if v else None)
    if al is not None:
        mode = ps.get("alpha", "any")
        res = "info" if mode == "any" else (("pass" if al else "fail") if mode == "required" else ("fail" if al else "pass"))
        b.add("colour.alpha", "colour", res, "Alpha channel", measured="present" if al else "none",
              expected={"required": "alpha required", "forbidden": "no alpha"}.get(mode))
    if v:
        b.add("colour.bit_depth", "colour", "info", "Bit depth", measured=f"{v.get('bit_depth')}-bit ({v.get('pix_fmt')})")
        b.add("colour.tags", "colour", "info", "Colour metadata",
              measured=f"primaries {v.get('color_primaries') or '—'} · transfer {v.get('color_trc') or '—'}")
    if ps:
        b.add("spec.preset", "spec", "info", "Compared against delivery spec", measured=preset.get("name"))
    return b.items


def failure_findings(path_kind: str, error: str, zero: bool) -> list[dict]:
    b = Builder(None)
    if zero:
        b.add("integrity.zero_byte", "integrity", "fail", "Zero-byte file", measured="0 bytes",
              detail="The file is empty.")
    else:
        b.add("integrity.zero_byte", "integrity", "pass", "File has data")
    b.add("integrity.readable", "integrity", "fail", "File cannot be read", detail=error, measured="unreadable")
    return b.items
