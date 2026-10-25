"""The review player: libmpv rendering into a Qt OpenGL widget.

Frame maths lives here so every caller agrees: frame n starts at n / rate, an exact seek
aims a quarter-frame early (mpv shows the first frame whose pts >= target, and float
rounding can otherwise land on frame n+1), and the position read back from time-pos is
round(pos * rate). Verified frame-exact against bar-coded clips at 25, 23.976 and 29.97,
from both the decoded frame and the pixels drawn.

mpv calls back from its own threads; everything crosses to the GUI thread through Qt
signals before touching widgets.
"""
from __future__ import annotations

import os
import sys
from fractions import Fraction

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QOpenGLContext, QSurfaceFormat
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from .. import libmpv, playlist
from ..timecode import asset_rate

# Copy-back decoding only, so frames land in system memory and screenshots / QC readback work.
# On macOS the decoder is named explicitly: "auto" lets FFmpeg 8 pick its Vulkan ProRes decoder,
# which runs through MoltenVK and crashed the app on file switches (Metal completion handler
# touching a destroyed queue). VideoToolbox covers ProRes, H.264 and HEVC; the rest decode in
# software. NITPIX_HWDEC overrides it for debugging.
HWDEC = os.environ.get("NITPIX_HWDEC") or ("videotoolbox-copy" if sys.platform == "darwin" else "auto-copy-safe")


def gl_format() -> QSurfaceFormat:
    """Core-profile context; legacy GL 2.1 leaves mpv without its scalers and hwdec."""
    f = QSurfaceFormat()
    f.setVersion(3, 3)
    f.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    f.setSwapInterval(1)
    return f


def _get_proc_address(_, name):
    ctx = QOpenGLContext.currentContext()
    if ctx is None:
        return 0
    return int(ctx.getProcAddress(name.decode() if isinstance(name, bytes) else name))


def _end_reason(ev) -> str:
    try:
        d = ev.as_dict() if hasattr(ev, "as_dict") else {}
        data = d.get("event") or {}
        return str(data.get("reason") or d.get("reason") or "")
    except Exception:
        return ""


def _safe(fn):
    """mpv commands raise if the file is gone or still loading; a Qt slot must not blow up."""
    def wrap(self, *a, **k):
        try:
            return fn(self, *a, **k)
        except Exception as e:  # SystemError from python-mpv, or a closed core
            self.error.emit(f"Player: {e}")
    return wrap


class _Relay(QObject):
    """mpv-thread callbacks to GUI-thread signals."""
    wake = Signal()
    prop = Signal(str, object)
    log = Signal(str, str, str)


class MpvWidget(QOpenGLWidget):
    frame_changed = Signal(int)      # position, in asset frames
    pause_changed = Signal(bool)
    file_loaded = Signal()
    end_reached = Signal(bool)
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFormat(gl_format())
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMinimumSize(160, 90)
        self.relay = _Relay()
        self.relay.wake.connect(self.update, Qt.ConnectionType.QueuedConnection)
        self.relay.prop.connect(self._on_prop, Qt.ConnectionType.QueuedConnection)
        self.relay.log.connect(self._on_log, Qt.ConnectionType.QueuedConnection)
        self.ctx = None
        self._proc = None
        self.rate = Fraction(25)
        self.asset = None
        self.target = None
        self.frame = 0
        self.paused = True
        self.loaded = False
        self.m = None
        self._pending = None  # a load requested before the GL context existed
        if libmpv.available() and not os.environ.get("NITPIX_NO_MPV"):  # tests build the UI without a player
            self.m = libmpv.mpv.MPV(vo="libmpv", keep_open="yes", pause=True, hr_seek="yes", hwdec=HWDEC,
                                   resume_playback=False, idle="yes", osd_level=1, osd_duration=1500,
                                   input_default_bindings=False, input_vo_keyboard=False, ytdl=False,
                                   load_scripts=False, osc=False,  # no Lua stats/console/OSC threads; the app draws its own UI
                                   screenshot_format="png", screenshot_high_bit_depth="no",
                                   log_handler=lambda lvl, pfx, msg: self.relay.log.emit(lvl, pfx, msg), loglevel="warn")
            for p in ("time-pos", "pause", "eof-reached"):
                self.m.observe_property(p, lambda name, val: self.relay.prop.emit(name, val))
            # `path` is set as loading *begins*; only file-loaded means seeks and steps will work
            self.m.event_callback("file-loaded")(lambda ev: self.relay.prop.emit("file-loaded", None))
            self.m.event_callback("end-file")(lambda ev: self.relay.prop.emit("end-file", _end_reason(ev)))

    # ---- GL ------------------------------------------------------------------
    def initializeGL(self):
        if not self.m:
            return
        self._proc = libmpv.mpv.MpvGlGetProcAddressFn(_get_proc_address)
        self.ctx = libmpv.mpv.MpvRenderContext(self.m, "opengl", opengl_init_params={"get_proc_address": self._proc})
        self.ctx.update_cb = self.relay.wake.emit
        if self._pending:
            # Loading before the render context exists leaves mpv's video output unconfigured
            # (black picture, no frame stepping) until the next file, so loads wait for it.
            QTimer.singleShot(0, self._flush_pending)

    def _flush_pending(self):
        if self._pending:
            project, asset, frame = self._pending
            self._pending = None
            self.load(project, asset, frame)

    def paintGL(self):
        if not self.ctx:
            return
        dpr = self.devicePixelRatioF()
        self.ctx.render(flip_y=True, opengl_fbo={"w": int(self.width() * dpr), "h": int(self.height() * dpr),
                                                  "fbo": self.defaultFramebufferObject()})

    def shutdown(self):
        """Free the render context with a current GL context, then stop mpv."""
        if self.ctx:
            self.makeCurrent()
            self.ctx.free()
            self.ctx = None
            self.doneCurrent()
        if self.m:
            self.m.terminate()
            self.m = None

    # ---- events from mpv --------------------------------------------------------
    def _on_prop(self, name, val):
        if name == "time-pos":
            if isinstance(val, (int, float)):
                f = round(val * float(self.rate))
                if f != self.frame:
                    self.frame = f
                    self.frame_changed.emit(f)
        elif name == "pause":
            self.paused = bool(val)
            self.pause_changed.emit(self.paused)
        elif name == "eof-reached":
            self.end_reached.emit(bool(val))
        elif name == "file-loaded":
            if self.target and not self.loaded:
                self.loaded = True
                self.file_loaded.emit()
        elif name == "end-file":
            if val and val not in ("eof", "stop", "redirect") and self.target:
                self.error.emit(f"Could not play this file ({val}).")

    def _on_log(self, level, prefix, msg):
        if level in ("error", "fatal"):
            self.error.emit(f"{prefix}: {msg.strip()}")

    def asset_path(self):
        return self.asset.get("path") if self.asset else None

    # ---- control ------------------------------------------------------------------
    @_safe
    def load(self, project, asset: dict, frame: int = 0):
        if not self.m:
            self.error.emit(libmpv.MPV_ERROR or "libmpv is not available")
            return
        self.asset = asset
        self.rate = asset_rate(asset)
        self.loaded = False
        self.frame = frame
        if self.ctx is None:
            self._pending = (project, asset, frame)
            return
        self.target = playlist.target(project, asset)
        self.m.pause = True
        self.m.ab_loop_a = "no"
        self.m.ab_loop_b = "no"
        self.m.force_media_title = asset.get("name") or ""
        if asset["kind"] == "sequence":
            self.m.mf_fps = float(self.rate)
        start = max(0.0, float((frame - Fraction(1, 4)) / self.rate))
        self.m.loadfile(self.target, "replace", start=f"{start:.6f}")

    @_safe
    def unload(self):
        if self.m and self.m.path:
            self.m.command("stop")
        self.asset = self.target = None
        self.loaded = False

    @_safe
    def seek_frame(self, frame: int):
        if not self.m or not self.loaded:
            return
        t = max(0.0, float((frame - Fraction(1, 4)) / self.rate))
        self.m.command("seek", t, "absolute+exact")

    @_safe
    def step(self, n: int):
        if not self.m or not self.loaded:
            return
        if n == 1:
            self.m.command("frame-step")
        elif n == -1:
            self.m.command("frame-back-step")
        else:
            self.m.command("seek", float(n / self.rate), "relative+exact")

    @_safe
    def set_pause(self, paused: bool | None = None):
        if not self.m or not self.loaded:
            return
        self.m.pause = (not self.m.pause) if paused is None else bool(paused)

    @_safe
    def set_loop(self, a_frame, b_frame):
        if not self.m:
            return
        self.m.ab_loop_a = "no" if a_frame is None else float(a_frame / self.rate)
        self.m.ab_loop_b = "no" if b_frame is None else float((b_frame + 1) / self.rate)

    def osd(self, text: str, ms: int = 1500):
        if self.m and self.loaded:
            try:
                self.m.command("show-text", text, ms)
            except Exception:
                pass

    def screenshot(self, path: str) -> bool:
        """Write the current frame (no OSD) to `path` as PNG."""
        if not self.m or not self.loaded:
            return False
        try:
            self.m.command("screenshot-to-file", path, "video")
            return os.path.exists(path)
        except Exception:
            return False

    def video_params(self) -> dict:
        if not self.m or not self.loaded:
            return {}
        return {"codec": self.m.video_codec, "hwdec": self.m.hwdec_current, **(self.m.video_params or {})}
