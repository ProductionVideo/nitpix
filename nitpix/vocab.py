"""Delivery-spec vocabulary.

Presets are compared against what ffprobe reports (`prores`, `bt709`, `24000/1001`),
but people write "ProRes 422 HQ", "Rec.709", "23.98". This module is the single
place that knows both: the options offered in the preset editor, the aliases that
map typed text onto them, and the matching the rules engine uses.

    normalise(field, value) -> canonical value, or None if not recognised
    matches(field, measured, expected_list) -> bool
"""
from __future__ import annotations

import re
from fractions import Fraction

from .timecode import parse_rate

# field -> [(canonical value, label shown in the editor)]
OPTIONS = {
    "containers": [("mov", "QuickTime MOV"), ("mp4", "MP4"), ("mxf", "MXF"), ("mkv", "Matroska MKV"),
                   ("webm", "WebM"), ("avi", "AVI"), ("mpegts", "MPEG-TS")],
    "codecs": [("prores", "Apple ProRes"), ("h264", "H.264 / AVC"), ("hevc", "H.265 / HEVC"),
               ("dnxhd", "DNxHD / DNxHR"), ("av1", "AV1"), ("vp9", "VP9"), ("mpeg2video", "MPEG-2"),
               ("jpeg2000", "JPEG 2000"), ("cfhd", "CineForm"), ("hap", "HAP"), ("notchlc", "NotchLC"), ("qtrle", "QuickTime Animation"),
               ("ffv1", "FFV1"), ("rawvideo", "Uncompressed")],
    "profiles": [("Proxy", "ProRes Proxy"), ("LT", "ProRes LT"), ("Standard", "ProRes 422"), ("HQ", "ProRes HQ"),
                 ("4444", "ProRes 4444"), ("4444 XQ", "ProRes 4444 XQ"),
                 ("Constrained Baseline", "H.264 Constrained Baseline"), ("Main", "H.264/HEVC Main"),
                 ("High", "H.264 High"), ("High 10", "H.264 High 10"), ("High 4:2:2", "H.264 High 4:2:2"),
                 ("Main 10", "HEVC Main 10"), ("Rext", "HEVC Range Ext."),
                 ("DNXHR LB", "DNxHR LB"), ("DNXHR SQ", "DNxHR SQ"), ("DNXHR HQ", "DNxHR HQ"),
                 ("DNXHR HQX", "DNxHR HQX"), ("DNXHR 444", "DNxHR 444"), ("DNXHD", "DNxHD")],
    "fps": [("24000/1001", "23.976"), ("24/1", "24"), ("25/1", "25"), ("30000/1001", "29.97"), ("30/1", "30"),
            ("48/1", "48"), ("50/1", "50"), ("60000/1001", "59.94"), ("60/1", "60")],
    "chroma": [("4:2:0", "4:2:0"), ("4:2:2", "4:2:2"), ("4:4:4", "4:4:4"), ("RGB", "RGB"), ("mono", "Mono")],
    "color_primaries": [("bt709", "Rec.709"), ("bt2020", "Rec.2020"), ("smpte432", "Display P3"),
                        ("smpte431", "DCI-P3"), ("smpte170m", "Rec.601 NTSC"), ("bt470bg", "Rec.601 PAL")],
    "color_trc": [("bt709", "Rec.709"), ("smpte2084", "PQ (ST 2084)"), ("arib-std-b67", "HLG"),
                  ("iec61966-2-1", "sRGB"), ("linear", "Linear"), ("gamma22", "Gamma 2.2"),
                  ("smpte170m", "Rec.601")],
    "color_space": [("bt709", "Rec.709"), ("bt2020nc", "Rec.2020 NCL"), ("bt2020c", "Rec.2020 CL"),
                    ("smpte170m", "Rec.601 NTSC"), ("bt470bg", "Rec.601 PAL"), ("gbr", "RGB")],
    "par": [("1:1", "Square (1:1)"), ("16:15", "PAL 4:3 (16:15)"), ("64:45", "PAL 16:9 (64:45)"),
            ("8:9", "NTSC 4:3 (8:9)"), ("32:27", "NTSC 16:9 (32:27)"), ("4:3", "HDV / anamorphic (4:3)"),
            ("2:1", "2× anamorphic")],
    "audio_codecs": [("pcm_s24le", "PCM 24-bit LE"), ("pcm_s24be", "PCM 24-bit BE"), ("pcm_s16le", "PCM 16-bit LE"),
                     ("pcm_s16be", "PCM 16-bit BE"), ("pcm_s32le", "PCM 32-bit"), ("pcm_f32le", "PCM 32-bit float"),
                     ("aac", "AAC"), ("ac3", "Dolby Digital (AC-3)"), ("eac3", "Dolby Digital Plus (E-AC-3)"),
                     ("mp3", "MP3"), ("opus", "Opus"), ("flac", "FLAC")],
    "sample_rate": [(44100, "44.1 kHz"), (48000, "48 kHz"), (96000, "96 kHz")],
    "formats": [("exr", "OpenEXR"), ("dpx", "DPX"), ("tiff", "TIFF"), ("png", "PNG"), ("jpg", "JPEG")],
    "bit_depth": [(8, "8-bit"), (10, "10-bit"), (12, "12-bit"), (16, "16-bit / half"), (32, "32-bit float")],
}

# Which profiles belong to which codec, so a list like codecs=[prores, h264] profiles=[HQ]
# only constrains ProRes.
CODEC_PROFILES = {
    "prores": {"Proxy", "LT", "Standard", "HQ", "4444", "4444 XQ"},
    "h264": {"Constrained Baseline", "Baseline", "Main", "High", "High 10", "High 4:2:2", "High 4:4:4 Predictive"},
    "hevc": {"Main", "Main 10", "Main Still Picture", "Rext"},
    "dnxhd": {"DNXHD", "DNXHR LB", "DNXHR SQ", "DNXHR HQ", "DNXHR HQX", "DNXHR 444"},
}


def key(v) -> str:
    """Comparison key: case, spaces and punctuation don't matter ("Rec. 709" == "rec709")."""
    return re.sub(r"[\s._\-/:()]+", "", str(v).lower())


# Field-specific aliases (keys already passed through key()). Canonical values map to themselves.
ALIASES = {
    "containers": {"quicktime": "mov", "qt": "mov", "mpeg4": "mp4", "m4v": "mp4", "isom": "mp4",
                   "matroska": "mkv", "ts": "mpegts", "m2ts": "mpegts", "mts": "mpegts", "transportstream": "mpegts"},
    "codecs": {"h264": "h264", "avc": "h264", "avc1": "h264", "x264": "h264", "mpeg4avc": "h264",
               "h265": "hevc", "x265": "hevc", "hvc1": "hevc", "hev1": "hevc",
               "appleprores": "prores", "prores422": "prores", "prores4444": "prores", "proreshq": "prores",
               "dnxhr": "dnxhd", "avid": "dnxhd", "aviddnxhd": "dnxhd", "aviddnxhr": "dnxhd",
               "mpeg2": "mpeg2video", "xdcam": "mpeg2video", "j2k": "jpeg2000", "jp2": "jpeg2000", "j2c": "jpeg2000",
               "cineform": "cfhd", "notch": "notchlc", "nclc": "notchlc", "notchlcrgb": "notchlc", "notchlcrgba": "notchlc", "gopro": "cfhd", "animation": "qtrle", "uncompressed": "rawvideo"},
    "profiles": {"422hq": "HQ", "proreshq": "HQ", "prores422hq": "HQ", "422lt": "LT", "proreslt": "LT",
                 "422proxy": "Proxy", "proresproxy": "Proxy", "422": "Standard", "422standard": "Standard",
                 "prores422": "Standard", "sq": "Standard", "prores4444": "4444", "4444xq": "4444 XQ",
                 "prores4444xq": "4444 XQ", "xq": "4444 XQ", "constrainedbaseline": "Constrained Baseline",
                 "cbp": "Constrained Baseline", "high10": "High 10", "hi10": "High 10", "high422": "High 4:2:2",
                 "main10": "Main 10", "rangeextensions": "Rext"},
    "chroma": {"420": "4:2:0", "yuv420": "4:2:0", "422": "4:2:2", "yuv422": "4:2:2", "444": "4:4:4",
               "yuv444": "4:4:4", "4444": "4:4:4", "gray": "mono", "grey": "mono", "monochrome": "mono"},
    "color_primaries": {"709": "bt709", "rec709": "bt709", "itu709": "bt709", "bt1886": "bt709", "srgb": "bt709",
                        "2020": "bt2020", "rec2020": "bt2020", "p3": "smpte432", "displayp3": "smpte432",
                        "p3d65": "smpte432", "dcip3": "smpte431", "p3dci": "smpte431", "601": "smpte170m",
                        "rec601": "smpte170m", "ntsc": "smpte170m", "pal": "bt470bg", "601pal": "bt470bg",
                        "rec601pal": "bt470bg"},
    "color_trc": {"709": "bt709", "rec709": "bt709", "bt1886": "bt709", "gamma24": "bt709", "pq": "smpte2084",
                  "st2084": "smpte2084", "smptest2084": "smpte2084", "hlg": "arib-std-b67",
                  "aribstdb67": "arib-std-b67", "srgb": "iec61966-2-1", "iec6196621": "iec61966-2-1",
                  "lin": "linear", "22": "gamma22", "601": "smpte170m", "rec601": "smpte170m"},
    "color_space": {"709": "bt709", "rec709": "bt709", "2020": "bt2020nc", "bt2020": "bt2020nc",
                    "rec2020": "bt2020nc", "2020ncl": "bt2020nc", "bt2020ncl": "bt2020nc", "rec2020ncl": "bt2020nc",
                    "2020cl": "bt2020c", "bt2020cl": "bt2020c", "rec2020cl": "bt2020c", "601": "smpte170m", "rec601": "smpte170m", "ntsc": "smpte170m",
                    "pal": "bt470bg", "rgb": "gbr"},
    "audio_codecs": {"aaclc": "aac", "ac3": "ac3", "dolbydigital": "ac3", "dd": "ac3", "eac3": "eac3",
                     "dd+": "eac3", "ddp": "eac3", "dolbydigitalplus": "eac3", "mpeg1layer3": "mp3"},
    "formats": {"openexr": "exr", "tif": "tiff", "jpeg": "jpg", "cineon": "dpx"},
}

# Expected values that stand for a family of measured values ("pcm" = any PCM codec).
FAMILIES = {
    "audio_codecs": {
        "pcm": lambda m: m.startswith("pcm_"), "lpcm": lambda m: m.startswith("pcm_"),
        "wav": lambda m: m.startswith("pcm_"), "uncompressed": lambda m: m.startswith("pcm_"),
        "pcm16": lambda m: m in ("pcm_s16le", "pcm_s16be"), "16bitpcm": lambda m: m in ("pcm_s16le", "pcm_s16be"),
        "pcm24": lambda m: m in ("pcm_s24le", "pcm_s24be"), "24bitpcm": lambda m: m in ("pcm_s24le", "pcm_s24be"),
        "pcm32": lambda m: m in ("pcm_s32le", "pcm_s32be", "pcm_f32le", "pcm_f32be"),
    },
}

_CANON = {f: {key(v): v for v, _ in opts} for f, opts in OPTIONS.items()}


def _num(v) -> float | None:
    m = re.match(r"^\s*([+\-−]?\d+(?:[.,]\d+)?)", str(v))
    return float(m.group(1).replace("−", "-").replace(",", ".")) if m else None


def normalise(field: str, value):
    """Canonical form of a typed value for `field`, or None when it is not recognised."""
    if value is None or value == "":
        return None
    if field == "fps":
        s = re.sub(r"\s*(fps|p|i|psf)\s*$", "", str(value).strip().lower())
        r = parse_rate(s)
        return f"{r.numerator}/{r.denominator}" if r else None
    if field == "sample_rate":
        n = _num(value)
        if n is None:
            return None
        if n < 1000 or re.search(r"k", str(value), re.I):  # "48", "48k", "44.1 kHz"
            n *= 1000
        return int(round(n))
    if field == "bit_depth":
        k = key(value)
        if k in ("half", "float16"):
            return 16
        if k in ("float", "float32", "full"):
            return 32
        n = _num(value)
        return int(n) if n else None
    if field == "par":
        return _ratio_str(value)
    k = key(value)
    if field == "profiles":
        k = re.sub(r"^(apple)?prores", "", k) or k
    if field == "formats":
        k = k.lstrip(".")
    if k in _CANON.get(field, {}):
        return _CANON[field][k]
    alias = ALIASES.get(field, {}).get(k)
    if alias:
        return alias
    if field == "codecs":  # "Apple ProRes 422 HQ", "DNxHR HQX": the family name decides the codec
        for prefix, codec in (("appleprores", "prores"), ("prores", "prores"), ("aviddnxh", "dnxhd"), ("dnxh", "dnxhd")):
            if k.startswith(prefix):
                return codec
    if field == "audio_codecs" and k in FAMILIES["audio_codecs"]:
        return str(value).strip().lower()
    if field == "profiles":  # keep profiles we don't list (ffprobe has many) in their typed form
        return str(value).strip()
    return None


def _ratio_str(v) -> str | None:
    s = str(v).strip().lower()
    if s in ("square", "sq"):
        return "1:1"
    m = re.match(r"^(\d+)\s*[:/]\s*(\d+)$", s)
    try:
        r = Fraction(int(m.group(1)), int(m.group(2))) if m else Fraction(float(s)).limit_denominator(100)
    except (ValueError, ZeroDivisionError):
        return None
    return f"{r.numerator}:{r.denominator}" if r > 0 else None


def clean_list(field: str, values) -> tuple[list, list[str]]:
    """Normalise a preset list. Returns (values to store, values that were not recognised).
    Unrecognised values are kept as typed so the reviewer's intent is not lost."""
    out, unknown = [], []
    for v in values if isinstance(values, list) else [values]:
        n = normalise(field, v)
        if n is None:
            unknown.append(str(v))
            n = v
        if n not in out:
            out.append(n)
    return out, unknown


def matches(field: str, measured, expected) -> bool:
    """True when `measured` satisfies any value in `expected` (a value or list)."""
    exp = expected if isinstance(expected, list) else [expected]
    if measured is None:
        return False
    if field == "fps":
        mr = parse_rate(measured)
        return any(mr is not None and parse_rate(normalise("fps", e)) == mr for e in exp)
    if field == "par":
        m = _ratio_str(measured)
        return any(m is not None and _ratio_str(e) == m for e in exp)
    if field == "start_tc":
        digits = lambda t: re.sub(r"\D", "", str(t))
        return any(digits(measured) == digits(e) for e in exp)
    m = normalise(field, measured)
    m = m if m is not None else measured
    for e in exp:
        fam = FAMILIES.get(field, {}).get(key(e))
        if fam and isinstance(m, str) and fam(m):
            return True
        n = normalise(field, e)
        n = n if n is not None else e
        if (key(n) == key(m)) if isinstance(n, str) and isinstance(m, str) else n == m:
            return True
        if field == "profiles" and isinstance(m, str) and _profile_tail(m) == key(n):
            return True  # "HQ" accepts DNxHR's "DNXHR HQ"
    return False


def _profile_tail(p: str) -> str:
    return re.sub(r"^(dnxhr|dnxhd)", "", key(p))


def profiles_for(codec: str, profiles: list) -> list:
    """Subset of preset profiles that apply to `codec`. Empty means "no profile rule for it"."""
    known = CODEC_PROFILES.get(codec)
    if not known:  # a codec without profiles: only profiles that belong to no known codec apply
        owned = {key(p) for ps in CODEC_PROFILES.values() for p in ps} | {_profile_tail(p) for ps in CODEC_PROFILES.values() for p in ps}
        return [p for p in profiles if key(normalise("profiles", p) or p) not in owned]
    keys = {key(p) for p in known} | {_profile_tail(p) for p in known}
    return [p for p in profiles if key(normalise("profiles", p) or p) in keys]


def container_of(meta: dict, path: str = "") -> str | None:
    """Real container. ffprobe reports 'mov,mp4,m4a,3gp,3g2,mj2' for MOV and MP4 alike;
    the major brand ('qt  ' vs 'isom'/'mp42') tells them apart."""
    names = (meta.get("container") or "").split(",")
    if "mov" in names:
        brand = str((meta.get("format_tags") or {}).get("major_brand", "")).strip().lower()
        if brand == "qt":
            return "mov"
        if brand:
            return "mp4"
        return "mov" if path.lower().endswith(".mov") else "mp4"
    if "matroska" in names or "webm" in names:
        return "webm" if path.lower().endswith(".webm") else "mkv"
    first = names[0] if names and names[0] else None
    return normalise("containers", first) or first


def options_payload() -> dict:
    """Editor vocabulary for the web UI."""
    out = {f: [{"value": v, "label": l} for v, l in opts] for f, opts in OPTIONS.items()}
    out["codec_profiles"] = {c: sorted(ps) for c, ps in CODEC_PROFILES.items()}
    return out


# Preset section -> {key: vocabulary field} for list/choice values.
PRESET_FIELDS = {
    "video": {"containers": "containers", "codecs": "codecs", "profiles": "profiles", "fps": "fps",
              "chroma": "chroma", "color_primaries": "color_primaries", "color_trc": "color_trc",
              "color_space": "color_space"},
    "audio": {"codecs": "audio_codecs"},
    "sequence": {"formats": "formats", "bit_depth": "bit_depth"},
    "still": {"formats": "formats"},
}
PRESET_NUMBERS = {
    "video": {"width": int, "height": int, "bit_depth_min": int, "min_duration": float, "max_duration": float},
    "audio": {"sample_rate": int, "channels": int, "loudness_target": float, "loudness_tolerance": float,
              "true_peak_max": float},
    "sequence": {"width": int, "height": int, "start_frame": int, "padding": int},
    "still": {"width": int, "height": int},
}
_TC_RE = re.compile(r"^\d{1,2}[:;.]\d{2}[:;.]\d{2}[:;.]\d{2,3}$")


def clean_preset(spec: dict) -> tuple[dict, list[str]]:
    """Validate and canonicalise a preset spec from the editor.
    Raises ValueError for values that cannot work; returns warnings for unknown-but-kept values."""
    out, warnings = {k: v for k, v in spec.items() if k not in PRESET_FIELDS}, []
    for sec in PRESET_FIELDS:
        src = spec.get(sec)
        if not src:
            continue
        o = {}
        for k, v in src.items():
            if v is None or v == "" or v == []:
                continue
            if k in PRESET_FIELDS[sec]:
                vals, unknown = clean_list(PRESET_FIELDS[sec][k], v)
                o[k] = vals
                if unknown and PRESET_FIELDS[sec][k] != "profiles":
                    warnings.append(f"{sec.title()} {k.replace('_', ' ')}: {', '.join(unknown)} not recognised; "
                                    "it will be compared exactly as typed.")
            elif k in PRESET_NUMBERS[sec]:
                kind = PRESET_NUMBERS[sec][k]
                if k == "sample_rate":
                    n = normalise("sample_rate", v)
                else:  # "1,920" is a thousands separator in a whole number, a decimal comma otherwise
                    n = _num(re.sub(r"[,\s]", "", str(v)) if kind is int else v)
                if n is None:
                    raise ValueError(f"{sec.title()} {k.replace('_', ' ')}: {v!r} is not a number.")
                o[k] = kind(n)
            elif k == "par":
                r = _ratio_str(v)
                if not r:
                    raise ValueError(f"Pixel aspect {v!r}: use a ratio like 1:1 or 64:45.")
                o[k] = r
            elif k == "start_tc":
                if not _TC_RE.match(str(v).strip()):
                    raise ValueError(f"Start timecode {v!r}: use HH:MM:SS:FF (or HH:MM:SS;FF for drop-frame).")
                o[k] = str(v).strip()
            elif k == "naming_regex":
                try:
                    re.compile(v)
                except re.error as e:
                    raise ValueError(f"Name prefix regex {v!r} is not valid: {e}.")
                o[k] = v
            else:
                o[k] = v
        lo, hi = o.get("min_duration"), o.get("max_duration")
        if lo is not None and hi is not None and lo > hi:
            raise ValueError("Min duration is longer than max duration.")
        if o:
            out[sec] = o
    return out, warnings


def display_preset(p: dict) -> dict:
    """Preset with list values shown in canonical form, so older hand-typed presets
    ("ProRes", "Rec709") light up the right options in the editor."""
    out = dict(p)
    for sec, fields in PRESET_FIELDS.items():
        if isinstance(out.get(sec), dict):
            out[sec] = {k: (clean_list(fields[k], v)[0] if k in fields else v) for k, v in out[sec].items()}
    return out
