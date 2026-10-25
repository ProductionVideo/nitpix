"""Fast, dependency-free header readers for still / sequence formats.

Used so that very large image sequences can be checked frame-by-frame without
launching ffprobe per frame. Each reader returns a dict with at least
`width`, `height`, and `ok`; on failure `ok` is False and `error` explains why.
Readers only ever open files read-only and read a few KB.
"""
from __future__ import annotations

import os
import struct

READ = 64 * 1024


def _fail(msg):
    return {"ok": False, "error": msg}


def read_header(path: str) -> dict:
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return _fail(f"cannot stat: {e.strerror}")
    if size == 0:
        return {"ok": False, "error": "zero-byte file", "zero": True, "size": 0}
    try:
        with open(path, "rb") as f:
            head = f.read(READ)
            f.seek(max(0, size - 16))
            tail = f.read(16)
    except OSError as e:
        return _fail(f"unreadable: {e.strerror}")
    ext = os.path.splitext(path)[1].lower()
    try:
        if head.startswith(b"\x89PNG\r\n\x1a\n"):
            r = _png(head, tail)
        elif head[:4] in (b"SDPX", b"XPDS"):
            r = _dpx(head, size)
        elif head[:4] == b"\x76\x2f\x31\x01":
            r = _exr(head, path, size)
        elif head[:2] == b"\xff\xd8":
            r = _jpeg(head, path, tail)
        elif head[:4] in (b"II*\x00", b"MM\x00*"):
            r = _tiff(head)
        else:
            r = {"ok": None, "error": f"no native reader for {ext or 'file'}; using ffprobe"}
    except Exception as e:  # malformed header
        r = _fail(f"header parse failed: {e}")
    r["size"] = size
    return r


def _png(head, tail):
    if head[12:16] != b"IHDR":
        return _fail("PNG missing IHDR")
    w, h, depth, ctype = struct.unpack(">IIBB", head[16:26])
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(ctype, 0)
    r = {"ok": True, "format": "png", "width": w, "height": h, "bit_depth": depth,
         "channels": channels, "alpha": ctype in (4, 6)}
    if b"IEND" not in tail:
        r.update(ok=False, error="PNG truncated (no IEND chunk)")
    return r


def _dpx(head, size):
    be = head[:4] == b"SDPX"
    e = ">" if be else "<"
    data_offset, = struct.unpack(e + "I", head[4:8])
    file_size, = struct.unpack(e + "I", head[16:20])
    w, h = struct.unpack(e + "II", head[772:780])
    descriptor = head[800]
    transfer = head[801]
    depth = head[803]
    channels = {50: 3, 51: 4, 52: 4, 6: 1, 100: 3, 102: 3, 103: 4}.get(descriptor, 3)
    r = {"ok": True, "format": "dpx", "width": w, "height": h, "bit_depth": depth,
         "channels": channels, "alpha": channels == 4, "dpx_transfer": transfer}
    if file_size and size < file_size:
        r.update(ok=False, error=f"DPX truncated ({size} of {file_size} bytes)")
    elif size <= data_offset:
        r.update(ok=False, error="DPX has no image data")
    return r


_EXR_LINES = {0: 1, 1: 1, 2: 1, 3: 16, 4: 32, 5: 16, 6: 32, 7: 32, 8: 32, 9: 256}
_EXR_COMP = {0: "none", 1: "rle", 2: "zips", 3: "zip", 4: "piz", 5: "pxr24", 6: "b44",
             7: "b44a", 8: "dwaa", 9: "dwab"}


def _exr(head, path, size):
    flags = head[5]
    tiled = bool(flags & 0x02)
    multipart = bool(flags & 0x10)
    i = 8
    attrs = {}
    while i < len(head):
        if head[i] == 0:
            i += 1
            break
        n_end = head.index(b"\x00", i)
        name = head[i:n_end].decode("latin1")
        t_end = head.index(b"\x00", n_end + 1)
        atype = head[n_end + 1:t_end].decode("latin1")
        sz, = struct.unpack("<i", head[t_end + 1:t_end + 5])
        val = head[t_end + 5:t_end + 5 + sz]
        attrs[name] = (atype, val)
        i = t_end + 5 + sz
    if "dataWindow" not in attrs:
        return _fail("EXR header missing dataWindow")
    x0, y0, x1, y1 = struct.unpack("<iiii", attrs["dataWindow"][1])
    w, h = x1 - x0 + 1, y1 - y0 + 1
    chans, types = [], set()
    if "channels" in attrs:
        cv = attrs["channels"][1]
        j = 0
        while j < len(cv) and cv[j] != 0:
            e = cv.index(b"\x00", j)
            chans.append(cv[j:e].decode("latin1"))
            ptype, = struct.unpack("<i", cv[e + 1:e + 5])
            types.add({0: 32, 1: 16, 2: 32}.get(ptype, 0))
            j = e + 1 + 16
    comp = attrs.get("compression", ("", b"\x00"))[1][0]
    r = {"ok": True, "format": "exr", "width": w, "height": h,
         "bit_depth": max(types) if types else None, "float": True,
         "channels": len(chans), "channel_names": chans,
         "alpha": "A" in chans or any(c.endswith(".A") for c in chans),
         "compression": _EXR_COMP.get(comp, str(comp)), "tiled": tiled}
    if "displayWindow" in attrs:
        dx0, dy0, dx1, dy1 = struct.unpack("<iiii", attrs["displayWindow"][1])
        r["display_width"], r["display_height"] = dx1 - dx0 + 1, dy1 - dy0 + 1
    # Truncation check for single-part scanline files: last chunk offset must be in file.
    if not tiled and not multipart:
        nchunks = -(-h // _EXR_LINES.get(comp, 1))
        table_start = i
        need = table_start + nchunks * 8
        with open(path, "rb") as f:
            f.seek(table_start)
            table = f.read(nchunks * 8)
        if len(table) < nchunks * 8 or need > size:
            r.update(ok=False, error="EXR truncated (offset table incomplete)")
        else:
            last = max(struct.unpack(f"<{nchunks}Q", table))
            if last >= size:
                r.update(ok=False, error="EXR truncated (chunk data missing)")
    return r


def _jpeg(head, path, tail):
    i = 2
    while i < len(head) - 9:
        if head[i] != 0xFF:
            i += 1
            continue
        marker = head[i + 1]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            depth = head[i + 4]
            h, w = struct.unpack(">HH", head[i + 5:i + 9])
            ch = head[i + 9]
            r = {"ok": True, "format": "jpeg", "width": w, "height": h, "bit_depth": depth,
                 "channels": ch, "alpha": False}
            if b"\xff\xd9" not in tail:
                r.update(ok=False, error="JPEG truncated (no EOI marker)")
            return r
        seglen, = struct.unpack(">H", head[i + 2:i + 4])
        i += 2 + seglen
    return _fail("JPEG SOF marker not found in first 64KB")


def _tiff(head):
    e = "<" if head[:2] == b"II" else ">"
    ifd, = struct.unpack(e + "I", head[4:8])
    if ifd + 2 > len(head):
        return {"ok": None, "error": "TIFF IFD beyond header window; using ffprobe"}
    n, = struct.unpack(e + "H", head[ifd:ifd + 2])
    tags = {}
    for k in range(n):
        off = ifd + 2 + 12 * k
        tag, typ, cnt = struct.unpack(e + "HHI", head[off:off + 8])
        if typ == 3 and cnt <= 2:
            val, = struct.unpack(e + "H", head[off + 8:off + 10])
        else:
            val, = struct.unpack(e + "I", head[off + 8:off + 12])
        tags[tag] = (val, cnt, typ)
    w = tags.get(256, (0,))[0]
    h = tags.get(257, (0,))[0]
    spp = tags.get(277, (1,))[0]
    bps = tags.get(258, (8, 1, 3))
    depth = bps[0] if bps[1] == 1 else None
    if depth is None and bps[2] == 3 and bps[0] + 2 <= len(head):  # offset to array
        depth, = struct.unpack(e + "H", head[bps[0]:bps[0] + 2])
    return {"ok": True, "format": "tiff", "width": w, "height": h, "bit_depth": depth,
            "channels": spp, "alpha": spp in (2, 4)}
