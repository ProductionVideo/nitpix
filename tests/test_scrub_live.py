"""Scrubbing against real libmpv in a real window: the frame drawn after a scrub, read back from
the player's framebuffer, is the frame asked for, at 25, 24000/1001 and 30000/1001, including
releases on a new target while a seek is still pending; and 2,000 seeks leave libmpv's event
delivery intact. Needs libmpv, ffmpeg and a display (not the offscreen platform):
    NITPIX_LIVE_PLAYER=1 .venv/bin/python -m unittest tests.test_scrub_live -v
"""
import os as _os, tempfile as _tempfile; _os.environ.setdefault("NITPIX_STATE_DIR", _tempfile.mkdtemp(prefix="nitpix state "))  # keep the real recent list untouched
import importlib.util
import os
import shutil
import subprocess
import tempfile
import time
import unittest

LIVE = os.environ.get("NITPIX_LIVE_PLAYER") == "1" and importlib.util.find_spec("PySide6") is not None
if LIVE:
    from PySide6.QtCore import QEvent, QPointF, QSettings, Qt
    from PySide6.QtGui import QMouseEvent, QSurfaceFormat
    from PySide6.QtWidgets import QApplication

    from nitpix import libmpv, service
    from nitpix.probe import ffprobe, find_tool, normalise, summary_fields
    from nitpix.store import Project
    from nitpix.ui import theme
    from nitpix.ui.mpvwidget import gl_format
    from nitpix.ui.review import ReviewScreen

    FFMPEG = find_tool("ffmpeg")
    LIVE = libmpv.available() and bool(FFMPEG)

W, H, BARS = 640, 360, 12   # 12 bars of 53 px: frame numbers up to 4095


def pump(sec):
    end = time.monotonic() + sec
    while time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.002)


@unittest.skipUnless(LIVE, "set NITPIX_LIVE_PLAYER=1 with libmpv, ffmpeg and a display")
class LiveScrub(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        cls.tmp = tempfile.mkdtemp(prefix="nitpix live scrub ")
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, cls.tmp)
        QSurfaceFormat.setDefaultFormat(gl_format())
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyleSheet(theme.STYLESHEET)
        cls.session = service.Session()
        cls.session.project = p = Project(os.path.join(cls.tmp, "live.nitpix"))
        cls.ids = {}
        for name, rate in (("25", "25"), ("23976", "24000/1001"), ("2997", "30000/1001")):
            path = os.path.join(cls.tmp, f"bars {name}.mp4")
            # bar k white when bit k of the frame index is set; long GOP so exact seeks must decode forward
            subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
                            f"color=black:s={W}x{H}:r={rate}:d=14,format=gray,"
                            f"geq=lum='255*mod(floor(N/pow(2\\,floor(X*{BARS}/W)))\\,2)'",
                            "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0", "-g", "48", "-bf", "2", path],
                           check=True)
            st = os.stat(path)
            cls.ids[name] = p.upsert_asset(path, kind="video", name=os.path.basename(path), size=st.st_size, mtime=st.st_mtime,
                                           scan_state="done", **summary_fields(normalise(ffprobe(path), path)))
        cls.rv = ReviewScreen(cls.session)
        cls.rv.resize(1100, 800)
        cls.rv.show()
        cls.logs = []
        cls.rv.player.relay.log.connect(lambda lvl, pfx, msg: cls.logs.append((lvl, pfx, msg.strip())))
        cls.errors = []
        cls.rv.player.error.connect(cls.errors.append)

    @classmethod
    def tearDownClass(cls):
        cls.rv.shutdown()
        cls.rv.close()
        cls.session.project.close()
        shutil.rmtree(cls.tmp, True)

    # ---- helpers ------------------------------------------------------------------------
    def open(self, name):
        self.rv.load_asset(self.ids[name])
        end = time.monotonic() + 10
        while not self.rv.player.loaded and time.monotonic() < end:
            pump(0.01)
        self.assertTrue(self.rv.player.loaded, "libmpv did not load the clip")
        pump(0.3)

    def settled(self, timeout=6):
        rv = self.rv
        end = time.monotonic() + timeout
        while (rv._settle is not None or rv.player.scrub_pending()) and time.monotonic() < end:
            pump(0.005)
        pump(0.15)
        self.assertIsNone(rv._settle, "scrub never settled")

    def drawn(self):
        """Frame number decoded from the bars in what the player draws."""
        img = self.rv.player.grabFramebuffer()
        iw, ih = img.width(), img.height()
        vw, vh = (ih * W / H, ih) if iw / ih > W / H else (iw, iw * H / W)
        x0, y = (iw - vw) / 2, ih // 2
        bits = [img.pixelColor(int(x0 + (k + 0.5) * vw / BARS), y).green() > 128 for k in range(BARS)]
        return sum(1 << k for k, b in enumerate(bits) if b)

    def mouse(self, kind, x, held=True):
        tl = self.rv.timeline
        pos = QPointF(x, tl._track().top() + 4)
        button = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseMove else Qt.MouseButton.LeftButton
        QApplication.sendEvent(tl, QMouseEvent(kind, pos, tl.mapToGlobal(pos), button,
                                               Qt.MouseButton.LeftButton if held else Qt.MouseButton.NoButton,
                                               Qt.KeyboardModifier.NoModifier))

    def drag(self, *fractions, release_x=None, gap=0.004):
        """Press, move through the given positions (fractions of the track) at ~250 Hz, release at
        the last one or at release_x, with no dwell: seeks are still pending at the release."""
        tr = self.rv.timeline._track()
        xs = [tr.left() + f * tr.width() for f in fractions]
        self.mouse(QEvent.Type.MouseButtonPress, xs[0])
        for a, b in zip(xs, xs[1:]):
            for i in range(1, 21):
                self.mouse(QEvent.Type.MouseMove, a + (b - a) * i / 20)
                pump(gap)
        self.mouse(QEvent.Type.MouseButtonRelease, xs[-1] if release_x is None else release_x, held=False)

    def check(self, want):
        rv = self.rv
        self.settled()
        self.assertEqual((self.drawn(), rv.frame, rv.player.frame, bool(rv.tc_lab.property("pending"))), (want, want, want, False))

    # ---- tests --------------------------------------------------------------------------
    def test_release_on_a_new_target_draws_that_frame(self):
        for name in ("25", "23976", "2997"):
            with self.subTest(rate=name):
                self.open(name)
                rv = self.rv
                tr = rv.timeline._track()
                self.drag(0.1, 0.8, 0.35, release_x=tr.left() + 0.62 * tr.width())
                self.assertTrue(rv.player.scrub_pending() or rv._settle is not None)   # released while pending
                self.check(rv.timeline._frame_at(tr.left() + 0.62 * tr.width()))
                self.drag(0.5, 0.9, release_x=rv.timeline.width() + 250)          # outside the widget
                self.check(rv.ctx.count - 1)

    def test_keys_and_bookmarks_while_a_scrub_is_pending(self):
        self.open("23976")
        rv = self.rv
        self.drag(0.2, 0.7)
        rv.seek(0)                      # Home
        rv.step(1)                      # →
        self.check(1)
        self.drag(0.3, 0.6)
        target = rv.frame
        rv.step(-1)                     # release then ←
        self.check(target - 1)
        self.drag(0.1, 0.45)
        target = rv.frame
        rv.bookmark()                   # while the picture is still catching up
        self.assertIn(target, [n["frame"] for n in rv.asset["notes"]])
        self.check(target)

    def test_end_then_space_plays_from_the_top(self):
        self.open("25")
        rv = self.rv
        self.drag(0.2, 0.6)
        rv.seek(rv.ctx.count - 1)       # End
        rv.toggle_play()                # Space at the end: from the top
        pump(0.6)
        self.assertFalse(rv.player.paused)
        f = rv.frame
        rv.player.set_pause(True)
        pump(0.2)
        self.assertLess(f, 40)

    def test_two_thousand_seeks_keep_events_flowing(self):
        self.open("2997")
        rv, pl = self.rv, self.rv.player
        start = len(self.logs)
        loaded, done = [], []
        pl.file_loaded.connect(lambda: loaded.append(True))
        pl.scrub_settled.connect(lambda: done.append(True))
        for i in range(2000):           # each seek carried out by mpv, not merged with the next
            pl.scrub_to(1 + (i * 37) % (rv.ctx.count - 2))
            end = time.monotonic() + 2
            while len(done) <= i and time.monotonic() < end:
                QApplication.processEvents()
        self.assertEqual(len(done), 2000)
        self.assertFalse(pl.scrub_pending())
        self.assertFalse([l for l in self.logs[start:] if "Too many events" in l[2]])
        rv.scrub(123)                   # observations still arrive...
        self.check(123)
        n = len(self.errors)
        pl._lane_send("set", "no-such-property", "1")   # ...error replies too...
        pump(0.3)
        self.assertGreater(len(self.errors), n)
        self.assertFalse(pl.scrub_pending())
        self.open("25")                 # ...and file events
        self.assertTrue(loaded)


if __name__ == "__main__":
    unittest.main()
