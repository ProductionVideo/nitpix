"""The few libmpv render-API and OpenGL details the Mapping view needs beyond what python-mpv and
PySide6 expose cleanly. Nothing else in nitpix uses private python-mpv calls or raw GL entry points.

- Frame timing. To keep the camera responsive during playback the Mapping view renders each new
  frame without blocking (block_for_target_time=0) and schedules the repaint for the frame's
  target time, read with MPV_RENDER_PARAM_NEXT_FRAME_INFO. python-mpv 1.0.8 cannot make that
  request (`ctx.next_frame_info` builds the parameter without a value), so it is made here.
- Clock units. render.h documents target_time in mpv_get_time_us() units; libmpv 0.41 (client
  API 2.5) reports nanoseconds. The unit is decided once per player, from the first target time,
  and must agree with KNOWN for the versions verified; a value matching neither clock turns
  scheduled rendering off (the view then renders the way the QC player does: correct, but the
  camera waits on the video clock while playing).
- Indexed drawing. PySide6 6.12's glDrawElements rejects a null offset into a bound index buffer,
  so the context's own entry point is called. Checked once per context.

Verified with: python-mpv 1.0.8, libmpv client API 2.5 (mpv 0.41.0), PySide6 6.12, macOS OpenGL
3.3 core. Anything else is checked as it is used, and failures are reported, not guessed around.
"""
from __future__ import annotations

import ctypes

from .. import libmpv

KNOWN = {(2, 5): "ns"}              # client API version -> target_time unit, as verified
FLAG_PRESENT, FLAG_REDRAW = 1, 2


def api_version() -> tuple[int, int] | None:
    if not libmpv.available():
        return None
    v = libmpv.mpv.backend.mpv_client_api_version()
    return (v >> 16, v & 0xFFFF)


class FrameClock:
    """Next-frame information and the matching clock for one player."""

    def __init__(self, m):
        self.m = m
        self.unit = None                     # "ns" | "us" once decided; "off" if scheduling is unavailable
        self.reason = ""
        try:
            self._ns = libmpv.mpv.backend.mpv_get_time_ns
            self._ns.restype, self._ns.argtypes = ctypes.c_int64, [ctypes.c_void_p]
        except AttributeError:
            self._ns = None

    def next_frame(self, ctx) -> tuple[int, int] | None:
        """(flags, target_time) of the frame libmpv has waiting, or None if it cannot say."""
        try:
            mpv = libmpv.mpv
            param = mpv.MpvRenderParam("next_frame_info", {"flags": 0, "target_time": 0})
            mpv._mpv_render_context_get_info(ctx._handle, param)   # python-mpv raises on an error code
            d = param.value.as_dict()
            return int(d.get("flags", 0)), int(d.get("target_time", 0))
        except Exception as e:  # a python-mpv that no longer has these internals
            self.off(f"frame timing unavailable ({type(e).__name__}: {e})")
            return None

    def off(self, reason: str):
        self.unit, self.reason = "off", reason

    def _now(self, unit: str) -> int:
        return self._ns(self.m.handle) if unit == "ns" else libmpv.mpv._mpv_get_time_us(self.m.handle)

    def ms_until(self, target: int) -> float | None:
        """Milliseconds from now until `target`; None when scheduling is off."""
        if self.unit == "off" or not target:
            return None
        if self.unit is None:
            self._decide(target)
            if self.unit == "off":
                return None
        now = self._now(self.unit)
        return (target - now) / (1e6 if self.unit == "ns" else 1e3)

    def _decide(self, target: int):
        """Which clock a target time is on: it must be within a few seconds of exactly one."""
        near = []
        if self._ns is not None and abs(target - self._now("ns")) < 5e9:
            near.append("ns")
        if abs(target - self._now("us")) < 5e6:
            near.append("us")
        known = KNOWN.get(api_version())
        if len(near) != 1:
            self.off(f"frame times on an unknown clock (libmpv {api_version()})")
        elif known and near[0] != known:
            self.off(f"frame times in {near[0]}, expected {known} for libmpv {api_version()}")
        else:
            self.unit = near[0]
            self.reason = "" if known else f"frame time unit measured as {near[0]} (libmpv {api_version()} not verified)"


class IndexedDraw:
    """glDrawElements(mode, count, GL_UNSIGNED_INT, offset 0) with an index buffer bound."""

    def __init__(self, glcontext):
        addr = int(glcontext.getProcAddress(b"glDrawElements") or 0)
        if not addr:
            raise RuntimeError("this OpenGL context has no glDrawElements")
        self._fn = ctypes.CFUNCTYPE(None, ctypes.c_uint, ctypes.c_int, ctypes.c_uint, ctypes.c_void_p)(addr)

    def __call__(self, mode: int, count: int):
        self._fn(mode, count, 0x1405, None)  # GL_UNSIGNED_INT
