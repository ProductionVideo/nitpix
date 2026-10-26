"""Spot a blurry window: nitpix drawn at 1× on a Retina display.

macOS does that when "Open in Low Resolution" is ticked in the app's Get Info window. Every
pixel is then doubled, so text looks soft and blocky (a tester's screenshot showed exactly this:
all detail on alternate pixel boundaries). Qt cannot tell it is happening, because in that mode
the screen reports a 1× ratio to the app; CoreGraphics still reports the display's real mode."""
from __future__ import annotations

import ctypes
import ctypes.util
import platform


def _cg_modes() -> list[tuple[int, int, int, int]]:
    """(width, height, pixel width, pixel height) of each active display's current mode."""
    if platform.system() != "Darwin":
        return []
    cg = ctypes.CDLL(ctypes.util.find_library("CoreGraphics") or "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    cg.CGGetActiveDisplayList.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32)]
    cg.CGDisplayCopyDisplayMode.restype = ctypes.c_void_p
    cg.CGDisplayCopyDisplayMode.argtypes = [ctypes.c_uint32]
    for fn in ("CGDisplayModeGetWidth", "CGDisplayModeGetHeight", "CGDisplayModeGetPixelWidth", "CGDisplayModeGetPixelHeight"):
        getattr(cg, fn).restype = ctypes.c_size_t
        getattr(cg, fn).argtypes = [ctypes.c_void_p]
    cg.CGDisplayModeRelease.argtypes = [ctypes.c_void_p]
    ids = (ctypes.c_uint32 * 16)()
    n = ctypes.c_uint32(0)
    if cg.CGGetActiveDisplayList(16, ids, ctypes.byref(n)) != 0:
        return []
    out = []
    for i in range(n.value):
        m = cg.CGDisplayCopyDisplayMode(ids[i])
        if not m:
            continue
        out.append((cg.CGDisplayModeGetWidth(m), cg.CGDisplayModeGetHeight(m),
                    cg.CGDisplayModeGetPixelWidth(m), cg.CGDisplayModeGetPixelHeight(m)))
        cg.CGDisplayModeRelease(m)
    return out


def low_resolution(screen_size: tuple[int, int], qt_ratio: float, modes=None) -> bool:
    """True when the display showing the window is HiDPI but the app is drawing at 1×."""
    if qt_ratio >= 1.5:
        return False
    try:
        modes = _cg_modes() if modes is None else modes
    except Exception:
        return False
    w, h = screen_size
    for mw, mh, pw, ph in modes:
        if (mw, mh) == (w, h) and mw and pw / mw >= 1.5:
            return True
    return False


def describe(screen) -> str:
    """One line for Settings ▸ Tools and bug reports."""
    g = screen.geometry()
    return f"{screen.name()}: {g.width()}×{g.height()} at {screen.devicePixelRatio():g}×"
