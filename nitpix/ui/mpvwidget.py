"""The review player: libmpv rendering into a Qt OpenGL widget.

Frame maths lives here so every caller agrees: frame n starts at n / rate, an exact seek
aims a quarter-frame early (mpv shows the first frame whose pts >= target, and float
rounding can otherwise land on frame n+1), and the position read back from time-pos is
round(pos * rate). Verified frame-exact against bar-coded clips at 25, 23.976 and 29.97,
from both the decoded frame and the pixels drawn.

mpv calls back from its own threads; everything crosses to the GUI thread through Qt
signals before touching widgets.

Timeline scrubbing seeks without blocking (`scrub_to`). After a seek, mpv's core waits until the
new frame has been drawn, and only paintGL on the GUI thread draws it; a blocking libmpv call
made from the GUI thread in that window stalls both until libmpv's 200 ms render timeout, which
capped scrubbing at about five pictures a second. mpv_command_async is documented as safe to call
from a render thread, so scrub seeks go through it: one command in flight, the newest target
waiting. mpv serves a blocking call before queued async ones, so while a scrub seek is
unanswered, seeks, steps, pause changes and OSD text join the same ordered async lane.
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


def _quiet_primary_handle(m):
    """python-mpv reads observations, command replies and log messages from a second client handle
    of its own; nothing reads the primary handle, yet libmpv queues file and seek events there too.
    After about a thousand of them libmpv logs "Too many events queued" at error level, which
    reached the user as a player error. Switch those events off on the primary handle only (the
    shutdown event cannot be switched off, and does no harm)."""
    for ev in libmpv.mpv.MpvEventID.ANY:
        if ev != libmpv.mpv.MpvEventID.SHUTDOWN:
            libmpv.mpv._mpv_request_event(m.handle, ev, 0)


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
    scrub_settled = Signal()         # the last scrub seek has been carried out and its frame shown

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
        # async lane (see the module docstring)
        self._gen = 0              # bumped on load / unload / shutdown; replies from older ones are ignored
        self._lane = 0             # async commands sent and not yet answered
        self._lane_next = None     # newest scrub target not sent yet
        self._lane_target = None   # newest frame sent or waiting on the lane
        self._lane_ids = 0
        self._seek_id = None       # the newest seek sent on the lane, then how far mpv has got with it:
        self._seek_state = None    # "sent" -> "acked" (queued in mpv) -> "started" (SEEK) -> "done" (PLAYBACK_RESTART)
        if libmpv.available() and not os.environ.get("NITPIX_NO_MPV"):  # tests build the UI without a player
            self.m = libmpv.mpv.MPV(vo="libmpv", keep_open="yes", pause=True, hr_seek="yes", hwdec=HWDEC,
                                   resume_playback=False, idle="yes", osd_level=1, osd_duration=1500,
                                   input_default_bindings=False, input_vo_keyboard=False, ytdl=False,
                                   load_scripts=False, osc=False,  # no Lua stats/console/OSC threads; the app draws its own UI
                                   screenshot_format="png", screenshot_high_bit_depth="no",
                                   log_handler=lambda lvl, pfx, msg: self.relay.log.emit(lvl, pfx, msg), loglevel="warn")
            _quiet_primary_handle(self.m)
            for p in ("time-pos", "pause", "eof-reached"):
                self.m.observe_property(p, lambda name, val: self.relay.prop.emit(name, val))
            # `path` is set as loading *begins*; only file-loaded means seeks and steps will work
            self.m.event_callback("file-loaded")(lambda ev: self.relay.prop.emit("file-loaded", None))
            self.m.event_callback("end-file")(lambda ev: self.relay.prop.emit("end-file", _end_reason(ev)))
            # Replies and events share python-mpv's event handle, so they arrive in order: the reply to
            # the last scrub seek, then its SEEK, then its PLAYBACK_RESTART once the frame is shown.
            self.m.event_callback("seek")(lambda ev: self.relay.prop.emit("seek-started", None))
            self.m.event_callback("playback-restart")(lambda ev: self.relay.prop.emit("seek-done", None))

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
        self._reset_lane()
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
        elif name == "lane-reply":
            gen, rid, err = val
            if gen != self._gen:
                return  # an answer for a file that has since been replaced
            self._lane = max(0, self._lane - 1)
            if rid == self._seek_id and self._seek_state == "sent":
                self._seek_state = "acked" if err is None else "done"
            if err is not None:
                self.error.emit(f"Player: {err}")
            if self._lane == 0:
                self._lane_flush()
            self._check_settled()
        elif name == "seek-started":
            if self._seek_state == "acked":
                self._seek_state = "started"
        elif name == "seek-done":
            if self._seek_state == "started":
                self._seek_state = "done"
                self._check_settled()
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
        self._reset_lane()
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
        self._reset_lane()

    def _seek_time(self, frame: int) -> float:
        return max(0.0, float((frame - Fraction(1, 4)) / self.rate))

    @_safe
    def seek_frame(self, frame: int):
        if not self.m or not self.loaded:
            return
        if self.scrub_pending():  # an absolute seek: it replaces the target waiting on the lane
            self.scrub_to(frame)
            return
        self.m.command("seek", self._seek_time(frame), "absolute+exact")

    # ---- scrubbing: the async lane ---------------------------------------------------
    def scrub_pending(self) -> bool:
        return self._lane > 0 or self._lane_next is not None

    @_safe
    def scrub_to(self, frame: int):
        """Exact seek without blocking the GUI thread. Targets that arrive while one is in flight
        replace each other; the newest is sent when mpv answers."""
        if not self.m or not self.loaded:
            return
        if self.scrub_pending() and frame == self._lane_target:
            return
        self._lane_target = self._lane_next = frame
        if self._lane == 0:
            self._lane_flush()

    def _lane_flush(self):
        if self._lane_next is not None:
            f, self._lane_next = self._lane_next, None
            self._lane_send("seek", self._seek_time(f), "absolute+exact")

    def _lane_send(self, *cmd):
        gen = self._gen
        self._lane_ids += 1
        rid = self._lane_ids
        if cmd[0] == "seek":
            self._seek_id, self._seek_state = rid, "sent"
        self._lane += 1
        try:
            fut = self.m.command_async(*cmd)
        except Exception:
            self._lane -= 1  # not queued, so no answer will come
            if cmd[0] == "seek":
                self._seek_state = "done"
            self._check_settled()
            raise
        # The future completes on every outcome, shutdown and event overflow included (a callback=
        # argument is skipped in those cases), so the lane cannot stay busy for good.
        fut.add_done_callback(lambda f: self.relay.prop.emit("lane-reply", (gen, rid, f.exception())))

    def _check_settled(self):
        if self._seek_state == "done" and not self.scrub_pending():
            self._seek_id = self._seek_state = None
            self.scrub_settled.emit()

    def _reset_lane(self):
        self._gen += 1
        self._lane = 0
        self._lane_next = self._lane_target = None
        self._seek_id = self._seek_state = None

    @_safe
    def step(self, n: int):
        if not self.m or not self.loaded:
            return
        if self.scrub_pending():  # relative to the newest target on the lane, so no step is lost
            self.scrub_to(max(0, self._lane_target + n))
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
        if self.scrub_pending():  # after the waiting seek, in order
            self._lane_flush()
            self._lane_send(*(("cycle", "pause") if paused is None else ("set", "pause", "yes" if paused else "no")))
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
                if self.scrub_pending():
                    self._lane_send("show-text", text, ms)
                    return
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
