"""Walks delivery folders, classifies files and groups image sequences."""
from __future__ import annotations

import os
import re
from collections import defaultdict
from dataclasses import dataclass, field

VIDEO_EXT = {".mov", ".mp4", ".m4v", ".mxf", ".avi", ".mkv", ".webm", ".mpg", ".mpeg", ".ts",
             ".m2ts", ".mts", ".wmv", ".flv", ".3gp", ".hap", ".y4m"}
AUDIO_EXT = {".wav", ".bwf", ".aif", ".aiff", ".aifc", ".mp3", ".aac", ".m4a", ".flac", ".ogg",
             ".opus", ".ac3", ".ec3", ".eac3", ".caf", ".w64", ".rf64"}
IMAGE_EXT = {".exr", ".dpx", ".png", ".tif", ".tiff", ".jpg", ".jpeg", ".tga", ".bmp", ".webp",
             ".hdr", ".cin", ".psd", ".jp2", ".j2c", ".sgi", ".rgb", ".heic"}
MEDIA_EXT = VIDEO_EXT | AUDIO_EXT | IMAGE_EXT
SKIP_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini", ".localized"}

SEQ_RE = re.compile(r"^(?P<prefix>.*?)(?P<num>\d+)(?P<suffix>\.[A-Za-z0-9]+)$")
MIN_SEQ_FRAMES = 3


@dataclass
class Discovered:
    files: list = field(default_factory=list)       # (kind, path, size, mtime)
    sequences: list = field(default_factory=list)   # dicts
    ignored: list = field(default_factory=list)     # non-media paths
    zero_other: list = field(default_factory=list)  # zero-byte non-media files
    errors: list = field(default_factory=list)


def kind_for(path: str) -> str | None:
    ext = os.path.splitext(path)[1].lower()
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in IMAGE_EXT:
        return "image"
    return None


def walk(roots: list[str], cancel=None, progress=None) -> Discovered:
    out = Discovered()
    images_by_dir: dict[str, list] = defaultdict(list)
    count = 0
    seen: set[str] = set()
    for root in roots:
        root = os.path.abspath(os.path.expanduser(root))
        if os.path.isfile(root):
            entries = [(os.path.dirname(root), sequence_siblings(root))]
        else:
            entries = ((d, f) for d, dirs, f in os.walk(root, onerror=lambda e: out.errors.append(str(e)))
                       if not _prune(dirs))
        for d, files in entries:
            for name in sorted(files):
                if cancel and cancel():
                    return out
                if name in SKIP_NAMES or name.startswith("._"):
                    continue
                p = os.path.join(d, name)
                if p in seen:  # a frame given explicitly and its folder as well
                    continue
                seen.add(p)
                try:
                    st = os.stat(p)
                except OSError as e:
                    out.errors.append(f"{p}: {e.strerror}")
                    continue
                count += 1
                if progress and count % 200 == 0:
                    progress(count, d)
                k = kind_for(p)
                if k is None:
                    if st.st_size == 0:
                        out.zero_other.append(p)
                    else:
                        out.ignored.append(p)
                    continue
                if k == "image":
                    images_by_dir[d].append((name, st.st_size, st.st_mtime))
                else:
                    out.files.append((k, p, st.st_size, st.st_mtime))
    for d, items in images_by_dir.items():
        singles, seqs = group_sequences(d, items)
        out.sequences.extend(seqs)
        out.files.extend(("image", os.path.join(d, n), s, m) for n, s, m in singles)
    if progress:
        progress(count, "")
    return out


def sequence_siblings(path: str) -> list[str]:
    """One frame of an image sequence stands for the whole sequence: the names in its folder
    with the same prefix and extension and a frame number where this one has its number.
    Any other file stands for itself."""
    name = os.path.basename(path)
    m = SEQ_RE.match(name)
    if kind_for(path) != "image" or not m:
        return [name]
    want = (m["prefix"], m["suffix"].lower())
    try:
        names = os.listdir(os.path.dirname(path) or ".")
    except OSError:
        return [name]
    out = []
    for n in names:
        mm = SEQ_RE.match(n)
        if mm and (mm["prefix"], mm["suffix"].lower()) == want and os.path.isfile(os.path.join(os.path.dirname(path), n)):
            out.append(n)
    return out or [name]


def _prune(dirs: list) -> bool:
    dirs[:] = [x for x in dirs if not x.startswith(".") and not x.endswith(".nitpix")]
    return False


def group_sequences(directory: str, items: list):
    groups: dict[tuple, list] = defaultdict(list)
    singles = []
    for name, size, mtime in items:
        m = SEQ_RE.match(name)
        if not m:
            singles.append((name, size, mtime))
            continue
        key = (m["prefix"], m["suffix"].lower())
        groups[key].append((int(m["num"]), m["num"], name, size, mtime))
    seqs = []
    for (prefix, suffix), frames in groups.items():
        if len(frames) < MIN_SEQ_FRAMES:
            singles.extend((n, s, mt) for _, _, n, s, mt in frames)
            continue
        frames.sort(key=lambda f: (f[0], f[1]))
        seqs.append(describe_sequence(directory, prefix, suffix, frames))
    return singles, seqs


def compress_ranges(nums: list[int]) -> list[list[int]]:
    out = []
    for n in sorted(nums):
        if out and n == out[-1][1] + 1:
            out[-1][1] = n
        else:
            out.append([n, n])
    return out


def describe_sequence(directory, prefix, suffix, frames):
    nums = [f[0] for f in frames]
    from collections import Counter
    widths = Counter(len(f[1]) for f in frames)
    has_leading_zero = any(len(f[1]) > 1 and f[1].startswith("0") for f in frames)
    pad, _ = widths.most_common(1)[0]
    if has_leading_zero or len(widths) == 1:
        # The most common width is the convention; anything else is inconsistent.
        inconsistent = [f[2] for f in frames if len(f[1]) != pad]
    else:
        # No zero-padding anywhere: an unpadded (%d) sequence that crosses digit boundaries.
        pad = 0
        inconsistent = []
    seen, dupes = {}, []
    for n, raw, name, *_ in frames:
        if n in seen:
            dupes.append({"frame": n, "files": [seen[n], name]})
        else:
            seen[n] = name
    first, last = min(nums), max(nums)
    present = set(nums)
    missing = [n for n in range(first, last + 1) if n not in present]
    hashes = "#" * pad if pad else "#"
    ext = suffix.lstrip(".")
    return {
        "dir": directory,
        "prefix": prefix,
        "suffix": suffix,
        "ext": ext.lower(),
        "pattern": f"{prefix}{hashes}{suffix}",
        "printf": f"{prefix}%0{pad}d{suffix}" if pad else f"{prefix}%d{suffix}",
        "padding": pad,
        "first": first,
        "last": last,
        "count": len(present),
        "file_count": len(frames),
        "expected": last - first + 1,
        "missing": compress_ranges(missing),
        "missing_count": len(missing),
        "duplicate_numbers": dupes,
        "inconsistent_padding": inconsistent[:200],
        "inconsistent_padding_count": len(inconsistent),
        "total_size": sum(f[3] for f in frames),
        "mtime": max(f[4] for f in frames),
        "frames": [[n, name, size] for n, _, name, size, _ in frames],
    }
