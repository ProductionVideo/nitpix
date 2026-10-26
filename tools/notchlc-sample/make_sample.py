#!/usr/bin/env python3
"""Write a tiny synthetic NotchLC MOV that exercises interpolated (mode 2) alpha blocks,
plus the exact 12-bit alpha a correct decoder must produce. Written from the frame
layout only (header words, regions, block encodings); no picture content from anyone.

64x32 pixels, 2 frames, flat grey picture, LZ4 envelope (the mode Notch writes).
Alpha macroblocks are built so each decoder fault shows:
  - every interpolated 4x4 block has its own endpoints (catches reusing one block),
  - selectors vary per pixel (catches one selector per block),
  - full 0..255 ramps (catches the missing /7, which overshoots 4095),
  - reversed ramps, and modes 0/1 mixed in (solid blocks must stay exact).

Usage: python make_sample.py out.mov out_expected_alpha.npy
Needs: numpy, lz4, av (PyAV) for muxing."""
import struct
import sys
from fractions import Fraction

import av
import lz4.block
import numpy as np

W, H = 64, 32
BW, BH = W // 4, H // 4          # 4x4 luma blocks
MW, MH = W // 16, H // 16        # 16x16 macroblocks


def u32(*v):
    return struct.pack(f"<{len(v)}I", *v)


def alpha_layout(frame: int):
    """Per macroblock: list of 16 sub-blocks (row order), each ("mode", a0, a1, selectors[16])."""
    layout = []
    for mb in range(MW * MH):
        subs = []
        for sub in range(16):
            k = (mb * 16 + sub + frame * 5) % 23
            if mb == 2 and sub % 5 == 0:
                subs.append(("clear",))
            elif mb == 3 and sub % 3 == 0:
                subs.append(("opaque",))
            elif k % 4 == 0:   # full ramp across the block, varies per pixel
                subs.append(("interp", 0, 255, [(i * 7) // 15 for i in range(16)]))
            elif k % 4 == 1:   # reversed ramp with distinct endpoints
                subs.append(("interp", 240 - 3 * sub, 12 + 2 * mb, [7 - (i % 8) for i in range(16)]))
            elif k % 4 == 2:   # narrow band, pseudo-random selectors
                subs.append(("interp", 60 + 7 * sub, 90 + 9 * sub, [(i * 5 + sub + frame) % 8 for i in range(16)]))
            else:              # selector checkerboard between distinct endpoints
                subs.append(("interp", 20 + sub, 200 - sub, [7 * ((i + i // 4) % 2) for i in range(16)]))
        layout.append(subs)
    return layout


def expected_alpha(layout) -> np.ndarray:
    a = np.zeros((H, W), np.uint16)
    for mb, subs in enumerate(layout):
        mx, my = (mb % MW) * 16, (mb // MW) * 16
        for sub, s in enumerate(subs):
            x, y = mx + (sub % 4) * 4, my + (sub // 4) * 4
            if s[0] == "clear":
                a[y:y + 4, x:x + 4] = 0
            elif s[0] == "opaque":
                a[y:y + 4, x:x + 4] = 4095
            else:
                _, a0, a1, sel = s
                for i in range(16):
                    v = a0 + (a1 - a0) * sel[i] / 7          # 8-bit domain, as the format defines
                    a[y + i // 4, x + i % 4] = int(np.rint(v / 255 * 4095))
    return a


def frame_payload(frame: int):
    layout = alpha_layout(frame)
    # regions, in the order the format lays them out
    luma_row_offsets_pos = 40
    chroma_offsets_pos = luma_row_offsets_pos + 4 * BH
    luma_controls_pos = chroma_offsets_pos + 4 * MW * MH
    alpha_controls_pos = luma_controls_pos + 4 * BW * BH
    chroma_data_pos = alpha_controls_pos + 8 * MW * MH

    # chroma: one 16x16 representation per macroblock (mode word 0), neutral grey
    chroma = b"".join(u32(0) + bytes([128, 128, 128, 128]) + u32(0) for _ in range(MW * MH))
    chroma_offsets = u32(*[3 * i for i in range(MW * MH)])           # in words

    # alpha controls (mode word + offset in words) and data (8 bytes per interpolated block)
    alpha_ctrl, alpha_data = b"", b""
    for subs in layout:
        modes = 0
        offset_words = len(alpha_data) // 4
        for sub, s in enumerate(subs):
            mode = {"clear": 0, "opaque": 1, "interp": 2}[s[0]]
            modes |= mode << (2 * sub)
            if mode == 2:
                _, a0, a1, sel = s
                word = a0 | (a1 << 8)
                for i, v in enumerate(sel):
                    word |= v << (16 + 3 * i)
                alpha_data += struct.pack("<Q", word)
        alpha_ctrl += u32(modes, offset_words)

    # luma: flat mid grey, 1 selector bit per pixel (all zero); each block row starts word-aligned
    ctrl = 2048 | (2048 << 12)                                        # widths 0 => 1 bit per pixel
    luma_controls = u32(*([ctrl] * (BW * BH)))
    row_bytes = ((BW * 16 + 31) // 32) * 4
    luma_data = bytes(row_bytes * BH)
    luma_row_offsets = u32(*[r * row_bytes for r in range(BH)])

    body = (luma_row_offsets + chroma_offsets + luma_controls + alpha_ctrl + chroma + alpha_data + luma_data)
    end = 40 + len(body)
    header = u32(W, H, chroma_offsets_pos // 4, luma_controls_pos // 4, alpha_controls_pos // 4,
                 chroma_data_pos // 4, len(luma_data), len(chroma) // 4, len(alpha_data) // 4, end)
    payload = header + body
    assert len(payload) == end
    return payload, expected_alpha(layout)


def packet(payload: bytes) -> bytes:
    comp = lz4.block.compress(payload, store_size=False)
    return b"1CLN" + u32(len(payload), len(comp), 1) + comp


def main(out_mov: str, out_npy: str):
    rate = Fraction(25)
    expected = []
    with av.open(out_mov, "w", format="mov") as c:
        st = c.add_mux_stream("notchlc", rate=rate, width=W, height=H, time_base=1 / rate)
        for f in range(2):
            payload, exp = frame_payload(f)
            expected.append(exp)
            pk = av.Packet(packet(payload))
            pk.stream = st
            pk.pts = pk.dts = f
            pk.duration = 1
            pk.time_base = 1 / rate
            pk.is_keyframe = True
            c.mux(pk)
    np.save(out_npy, np.stack(expected))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
