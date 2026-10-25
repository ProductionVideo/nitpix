"""Per-frame checks for image sequences (header read + sampled hash)."""
from __future__ import annotations

import hashlib
import os
import statistics
from collections import Counter

from .imageheaders import read_header

CHUNK = 64 * 1024


def sampled_hash(path: str, size: int) -> str | None:
    """Hash of size + first/middle/last 64 KB. Fast on 50 MB EXRs; identical renders
    match, but this is a sample, so results are reported as medium confidence."""
    h = hashlib.blake2b(digest_size=16)
    h.update(str(size).encode())
    try:
        with open(path, "rb") as f:
            if size <= CHUNK * 3:
                h.update(f.read())
            else:
                h.update(f.read(CHUNK))
                f.seek(size // 2)
                h.update(f.read(CHUNK))
                f.seek(size - CHUNK)
                h.update(f.read(CHUNK))
    except OSError:
        return None
    return h.hexdigest()


def scan_frames(seq: dict, settings: dict, should_stop=None, wait_if_paused=None, on_progress=None) -> dict:
    an = settings["analysis"]
    frames = seq["frames"]
    results = {"bad": [], "dims_odd": [], "repeated": [], "size_outliers": [], "header": None}
    dims = Counter()
    per = []  # (n, name, size, hdr)
    prev_hash, prev_n = None, None
    total = len(frames)
    for i, (n, name, size) in enumerate(frames):
        if wait_if_paused:
            wait_if_paused()
        if should_stop and should_stop():
            raise InterruptedError
        path = os.path.join(seq["dir"], name)
        hdr = read_header(path)
        status = "ok"
        if hdr.get("zero"):
            results["bad"].append([n, "zero-byte file"])
            status = "bad"
        elif hdr.get("ok") is False:
            results["bad"].append([n, hdr.get("error", "unreadable")])
            status = "bad"
        if hdr.get("width"):
            dims[(hdr["width"], hdr["height"])] += 1
        if results["header"] is None and hdr.get("ok"):
            results["header"] = {k: v for k, v in hdr.items() if k not in ("ok", "size")}
        if an.get("seq_hash_duplicates", True) and status == "ok":
            hsh = sampled_hash(path, size)
            if hsh and hsh == prev_hash and prev_n is not None and n != prev_n:
                results["repeated"].append([n, prev_n])
                status = "rep"
            prev_hash, prev_n = hsh, n
        per.append([n, name, size, status, hdr.get("width"), hdr.get("height")])
        if on_progress and (i % 25 == 0 or i == total - 1):
            on_progress((i + 1) / total)
    if dims:
        (mw, mh), _ = dims.most_common(1)[0]
        results["dims_majority"] = [mw, mh]
        for row in per:
            if row[4] and (row[4], row[5]) != (mw, mh):
                results["dims_odd"].append([row[0], row[4], row[5]])
                if row[3] == "ok":
                    row[3] = "odd"
    good_sizes = [r[2] for r in per if r[3] in ("ok", "rep", "odd") and r[2]]
    if len(good_sizes) >= 5:
        med = statistics.median(good_sizes)
        ratio = an.get("seq_size_outlier_ratio", 0.35)
        for row in per:
            if row[3] == "ok" and row[2] < med * ratio:
                results["size_outliers"].append([row[0], row[2], med])
                row[3] = "small"
    dup_nums = {d["frame"] for d in seq.get("duplicate_numbers", [])}
    for row in per:
        if row[0] in dup_nums and row[3] == "ok":
            row[3] = "dup"
    seq["frames"] = per
    seq["frame_results"] = results
    return seq
