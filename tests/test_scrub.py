"""Timeline scrubbing without blocking the GUI thread: the player's async lane (ordering, newest
target wins, generations, failures, the settled signal) and the review screen's ownership of the
position while a scrub settles. Offscreen, with a stand-in for libmpv whose replies the tests
deliver by hand; tests/test_scrub_live.py checks drawn frames against real libmpv.
.venv/bin/python -m unittest tests.test_scrub"""
import os as _os, tempfile as _tempfile; _os.environ.setdefault("NITPIX_STATE_DIR", _tempfile.mkdtemp(prefix="nitpix state "))  # keep the real recent list untouched
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import Future
from fractions import Fraction

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["NITPIX_NO_MPV"] = "1"

HAS_QT = importlib.util.find_spec("PySide6") is not None
if HAS_QT:
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    from nitpix import service
    from nitpix.store import Project
    from nitpix.ui import theme
    from nitpix.ui.mpvwidget import MpvWidget
    from nitpix.ui.review import ReviewScreen


class FakeMpv:
    """Records what the player sends. Async commands return futures the test completes."""

    def __init__(self):
        self.sent = []        # ("sync" | "async", args)
        self.futures = []
        self.pause = True
        self.path = None
        self.fail_next = False

    def command(self, *a):
        self.sent.append(("sync", a))

    def command_async(self, *a):
        if self.fail_next:
            self.fail_next = False
            raise SystemError("mpv refused the command")
        f = Future()
        f.set_running_or_notify_cancel()
        self.sent.append(("async", a))
        self.futures.append(f)
        return f

    def loadfile(self, *a, **k):
        self.sent.append(("sync", ("loadfile", *a)))


def seek_time(frame, rate=Fraction(25)):
    """The quarter-frame-early rule, as the player applies it."""
    return max(0.0, float((frame - Fraction(1, 4)) / rate))


@unittest.skipUnless(HAS_QT, "PySide6 not installed in this interpreter")
class Lane(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.w = MpvWidget()
        self.m = self.w.m = FakeMpv()
        self.w.loaded = True
        self.w.rate = Fraction(25)
        self.errors, self.settled = [], []
        self.w.error.connect(self.errors.append)
        self.w.scrub_settled.connect(lambda: self.settled.append(True))

    def tearDown(self):
        self.w.m = None
        self.w.deleteLater()

    def flush(self):
        for _ in range(3):
            self.app.processEvents()

    def answer(self, i=0, err=None):
        """Complete the oldest unanswered async command (or the i-th of those)."""
        f = [f for f in self.m.futures if not f.done()][i]
        f.set_exception(err) if err else f.set_result(None)
        self.flush()

    def async_sent(self):
        return [a for kind, a in self.m.sent if kind == "async"]

    def test_one_in_flight_and_the_newest_target_waits(self):
        for f in (10, 11, 12, 13):
            self.w.scrub_to(f)
        self.assertEqual(self.async_sent(), [("seek", seek_time(10), "absolute+exact")])
        self.assertTrue(self.w.scrub_pending())
        self.answer()
        self.assertEqual(self.async_sent()[-1], ("seek", seek_time(13), "absolute+exact"))  # 11 and 12 replaced
        self.w.scrub_to(13)                                                                 # already on its way
        self.assertEqual(len(self.async_sent()), 2)
        self.answer()
        self.assertFalse(self.w.scrub_pending())
        self.assertEqual([k for k, _ in self.m.sent], ["async", "async"])                  # nothing blocking

    def test_rational_rates_keep_the_quarter_frame_rule(self):
        for rate in (Fraction(24000, 1001), Fraction(30000, 1001)):
            self.w.rate = rate
            self.m.sent.clear()
            self.w.scrub_to(1000)
            self.assertEqual(self.async_sent()[-1][1], seek_time(1000, rate))
            self.answer()

    def test_idle_seeks_and_steps_stay_blocking(self):
        self.w.seek_frame(40)
        self.w.step(1)
        self.w.step(-1)
        self.w.set_pause(False)
        self.assertEqual(self.m.sent, [("sync", ("seek", seek_time(40), "absolute+exact")),
                                       ("sync", ("frame-step",)), ("sync", ("frame-back-step",))])
        self.assertFalse(self.m.pause)

    def test_seeks_and_steps_behind_a_scrub_are_ordered_and_exact(self):
        self.w.scrub_to(50)
        self.w.seek_frame(0)        # Home
        self.w.step(1)              # then →: frame 1, not one after wherever mpv happens to be
        self.w.step(25)             # ⇧→
        self.assertEqual(self.w._lane_target, 26)
        self.answer()
        self.assertEqual(self.async_sent()[-1], ("seek", seek_time(26), "absolute+exact"))
        self.answer()
        self.assertFalse([a for k, a in self.m.sent if k == "sync"])

    def test_pause_behind_a_scrub_goes_after_the_waiting_seek(self):
        self.w.scrub_to(80)
        self.w.scrub_to(0)
        self.w.set_pause(False)
        self.w.osd("00:00:00:00")
        self.assertEqual(self.async_sent(), [("seek", seek_time(80), "absolute+exact"), ("seek", seek_time(0), "absolute+exact"),
                                             ("set", "pause", "no"), ("show-text", "00:00:00:00", 1500)])
        self.w.set_pause(None)
        self.assertEqual(self.async_sent()[-1], ("cycle", "pause"))

    def test_replies_for_a_replaced_file_are_ignored(self):
        self.w.scrub_to(10)
        self.w.scrub_to(20)                 # waiting behind 10
        old = self.m.futures[0]
        self.w.unload()                     # another asset comes in
        self.w.loaded = True
        self.w.scrub_to(5)
        old.set_result(None)                # the old file's answer arrives late
        self.flush()
        self.assertEqual(self.async_sent(), [("seek", seek_time(10), "absolute+exact"), ("seek", seek_time(5), "absolute+exact")])
        self.assertTrue(self.w.scrub_pending())   # still waiting for its own answer
        self.answer()
        self.assertFalse(self.w.scrub_pending())

    def test_a_refused_command_does_not_leave_the_lane_busy(self):
        self.m.fail_next = True
        self.w.scrub_to(10)
        self.assertTrue(self.errors and "refused" in self.errors[0])
        self.assertFalse(self.w.scrub_pending())
        self.w.scrub_to(11)
        self.assertEqual(self.async_sent(), [("seek", seek_time(11), "absolute+exact")])

    def test_an_error_reply_is_reported_and_the_next_target_still_goes(self):
        self.w.scrub_to(10)
        self.w.scrub_to(30)
        self.answer(err=RuntimeError("seek failed"))
        self.assertEqual(self.errors, ["Player: seek failed"])
        self.assertEqual(self.async_sent()[-1], ("seek", seek_time(30), "absolute+exact"))

    def test_shutdown_reply_frees_the_lane(self):
        self.w.scrub_to(10)
        self.answer(err=AttributeError("'NoneType' object has no attribute 'unpack'"))  # what python-mpv gives on shutdown
        self.assertFalse(self.w.scrub_pending())

    def test_settled_needs_the_reply_then_seek_then_restart_of_the_last_seek(self):
        self.w.scrub_to(10)
        self.w.relay.prop.emit("seek-started", None)   # an earlier seek's events: too early to count
        self.w.relay.prop.emit("seek-done", None)
        self.flush()
        self.w.scrub_to(20)
        self.answer()                                  # 10 queued in mpv; 20 sent
        self.w.relay.prop.emit("seek-started", None)   # this is 10 starting
        self.w.relay.prop.emit("seek-done", None)
        self.flush()
        self.assertEqual(self.settled, [])
        self.answer()                                  # 20 queued in mpv
        self.assertEqual(self.settled, [])
        self.w.relay.prop.emit("seek-started", None)
        self.flush()
        self.assertEqual(self.settled, [])
        self.w.relay.prop.emit("seek-done", None)
        self.flush()
        self.assertEqual(self.settled, [True])
        self.w.relay.prop.emit("seek-done", None)      # later restarts (playback, other seeks) do not repeat it
        self.flush()
        self.assertEqual(self.settled, [True])


@unittest.skipUnless(HAS_QT, "PySide6 not installed in this interpreter")
class Review(unittest.TestCase):
    """Ownership of the position while a scrub settles."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyleSheet(theme.STYLESHEET)
        cls.tmp = tempfile.mkdtemp(prefix="nitpix scrub ")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def setUp(self):
        self.session = service.Session()
        self.session.project = Project(tempfile.mkdtemp(dir=self.tmp) + "/s.nitpix")
        p = self.session.project
        self.aid = p.upsert_asset("/m/a.mov", kind="video", name="a.mov", fps="25/1", frames=100, duration=4.0, scan_state="done")
        self.bid = p.upsert_asset("/m/b.mov", kind="video", name="b.mov", fps="25/1", frames=50, duration=2.0, scan_state="done")
        self.rv = ReviewScreen(self.session)
        self.rv.resize(1200, 800)
        self.rv.show()
        self.m = self.rv.player.m = FakeMpv()
        self.open(self.aid)

    def tearDown(self):
        self.rv.player.m = None
        self.rv.close()
        self.rv.deleteLater()
        self.session.project.close()
        self.flush()

    def open(self, aid):
        self.rv.load_asset(aid)
        self.rv.viewer_stack.setCurrentWidget(self.rv.player)  # the stand-in paths do not exist on disk
        pl = self.rv.player
        pl._pending = None
        pl.target, pl.loaded = f"/m/{aid}", True   # what file-loaded does once libmpv has the file
        self.m.sent.clear()

    def flush(self):
        for _ in range(3):
            self.app.processEvents()

    def report(self, frame):
        self.rv.player._on_prop("time-pos", float(Fraction(frame) / 25))

    def answer_all(self):
        while any(not f.done() for f in self.m.futures):
            next(f for f in self.m.futures if not f.done()).set_result(None)
            self.flush()

    def settle(self):
        """mpv carries out the last seek: SEEK, then PLAYBACK_RESTART."""
        self.answer_all()
        self.rv.player.relay.prop.emit("seek-started", None)
        self.rv.player.relay.prop.emit("seek-done", None)
        self.flush()

    def mouse(self, kind, x, held=True):
        tl = self.rv.timeline
        pos = QPointF(x, tl._track().top() + 4)
        button = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseMove else Qt.MouseButton.LeftButton
        QApplication.sendEvent(tl, QMouseEvent(kind, pos, tl.mapToGlobal(pos), button,
                                               Qt.MouseButton.LeftButton if held else Qt.MouseButton.NoButton,
                                               Qt.KeyboardModifier.NoModifier))

    def x_of(self, frame):
        return self.rv.timeline._x(frame)

    def drag(self, *frames, release_at=None):
        self.mouse(QEvent.Type.MouseButtonPress, self.x_of(frames[0]))
        for f in frames[1:]:
            self.mouse(QEvent.Type.MouseMove, self.x_of(f))
        self.mouse(QEvent.Type.MouseButtonRelease, release_at if release_at is not None else self.x_of(frames[-1]), held=False)

    def seeks(self):
        return [round(a[1] * 25 + 0.25) for k, a in self.m.sent if a and a[0] == "seek"]

    def test_drag_seeks_without_blocking_and_late_reports_cannot_pull_the_playhead_back(self):
        rv = self.rv
        self.drag(10, 30, 60)
        self.assertEqual([k for k, _ in self.m.sent], ["async"])          # 10 in flight; 60 waiting
        self.assertEqual((rv.frame, rv.timeline.frame, bool(rv.tc_lab.property("pending"))), (60, 60, True))
        self.report(10)                                                      # earlier target shown late
        self.assertEqual((rv.frame, rv.timeline.frame), (60, 60))
        rv.bookmark()                                                        # a bookmark now goes on the frame asked for
        rv.set_inout("in")
        self.assertEqual(([n["frame"] for n in rv.asset["notes"]], rv.asset["in_frame"]), ([60], 60))
        self.answer_all()
        self.report(60)
        self.assertEqual((rv.frame, bool(rv.tc_lab.property("pending"))), (60, False))
        self.report(61)                                                      # settled: reports apply again (playback)
        self.assertEqual(rv.frame, 61)
        self.assertEqual(self.seeks(), [10, 60])

    def test_release_on_a_new_target_outside_the_widget_with_a_seek_pending(self):
        rv = self.rv
        self.mouse(QEvent.Type.MouseButtonPress, self.x_of(20))
        self.mouse(QEvent.Type.MouseMove, self.x_of(40))
        self.mouse(QEvent.Type.MouseButtonRelease, rv.timeline.width() + 300, held=False)  # past the right edge, no dwell
        self.assertEqual(rv.frame, 99)
        self.report(40)
        self.assertEqual(rv.frame, 99)
        self.settle()
        self.assertEqual(self.seeks()[-1], 99)
        self.report(99)
        self.assertEqual((rv.frame, bool(rv.tc_lab.property("pending"))), (99, False))

    def test_settled_without_a_report_when_the_frame_does_not_change(self):
        rv = self.rv
        self.report(30)
        self.drag(30, 31, 30)          # mpv may never move, so time-pos never changes
        self.settle()
        self.assertEqual((rv.frame, bool(rv.tc_lab.property("pending"))), (30, False))

    def test_a_file_shorter_than_its_frame_count_ends_on_the_real_last_frame(self):
        rv = self.rv
        self.drag(50, 99)
        self.report(96)               # the file really ends at 96: ignored while settling...
        self.assertEqual(rv.frame, 99)
        self.settle()                 # ...and taken once mpv says the seek is done
        self.assertEqual(rv.frame, 96)

    def test_click_on_a_marker_lands_on_the_marker_not_the_release_pixel(self):
        rv = self.rv
        service.add_note(self.session.project, self.aid, "bookmark", 37, None, "info", "B")
        rv.refresh()
        rv.timeline.grab()            # paint, so the marker has a hit box
        hit = next(h for h in rv.timeline._hits if h[1] == "note")
        x = hit[0].center().x()
        self.mouse(QEvent.Type.MouseButtonPress, x)
        self.mouse(QEvent.Type.MouseButtonRelease, x + 2, held=False)
        self.assertEqual((rv.frame, self.seeks()), (37, [37]))

    def test_release_then_left_arrow_steps_back_from_the_release_frame(self):
        rv = self.rv
        self.drag(10, 70)
        rv.step(-1)
        self.assertEqual(rv.frame, 69)
        self.report(10)
        self.assertEqual(rv.frame, 69)
        self.settle()
        self.assertEqual(self.seeks()[-1], 69)
        self.assertFalse([a for k, a in self.m.sent if k == "sync"])

    def test_home_then_right_arrow_while_a_scrub_is_pending(self):
        rv = self.rv
        self.drag(10, 70)
        rv.seek(0)
        rv.step(1)
        self.assertEqual(rv.frame, 1)
        self.settle()
        self.assertEqual(self.seeks()[-1], 1)

    def test_end_then_space_while_pending_plays_from_the_top_in_order(self):
        rv = self.rv
        self.drag(10, 50)
        rv.seek(99)                    # End
        rv.toggle_play()               # at the end: back to the top, then play
        self.answer_all()
        tail = [a[:2] if a[0] == "seek" else a for k, a in self.m.sent][-2:]
        self.assertEqual(tail, [("seek", seek_time(0)), ("set", "pause", "no")])

    def test_marker_jump_while_pending_goes_through_the_lane(self):
        rv = self.rv
        service.add_note(self.session.project, self.aid, "bookmark", 80, None, "info", "B")
        rv.refresh()
        self.drag(10, 20)
        rv.jump_finding(1)
        self.assertEqual(rv.frame, 80)
        self.answer_all()
        self.assertEqual(self.seeks()[-1], 80)
        self.assertFalse([a for k, a in self.m.sent if k == "sync"])

    def test_switching_asset_drops_the_old_scrub(self):
        rv = self.rv
        self.drag(10, 70)
        old = [f for f in self.m.futures if not f.done()]
        rv.load_asset(self.bid)
        rv.viewer_stack.setCurrentWidget(rv.player)
        self.assertIsNone(rv._settle)
        self.assertFalse(rv.player.scrub_pending())
        pl = rv.player
        pl._pending, pl.target, pl.loaded = None, "/m/b", True
        for f in old:
            f.set_result(None)
        self.flush()
        self.assertEqual(self.seeks(), [10])   # 70 was never sent to the new file
        self.report(12)
        self.assertEqual(rv.frame, 12)

    def test_backstop_takes_the_player_position_if_mpv_never_answers(self):
        rv = self.rv
        self.drag(10, 40)
        self.answer_all()
        self.report(39)
        rv._settle_timeout()
        self.assertEqual((rv.frame, bool(rv.tc_lab.property("pending"))), (39, False))

    def test_clicks_without_a_player_still_seek(self):
        rv = self.rv
        rv.player.loaded = False
        self.mouse(QEvent.Type.MouseButtonPress, self.x_of(25))
        self.assertEqual((rv.frame, rv._settle), (25, None))

    def test_player_gone_while_settling_falls_back_to_a_plain_seek(self):
        rv = self.rv
        self.drag(10, 40)
        rv.player.loaded = False
        rv.seek(12)
        rv.step(1)
        self.assertEqual((rv.frame, rv._settle), (13, None))


@unittest.skipUnless(HAS_QT, "PySide6 not installed in this interpreter")
class Startup(unittest.TestCase):
    def test_player_starts_under_a_non_c_locale(self):
        """QApplication takes LC_NUMERIC from the environment; libmpv refuses a non-C one and
        python-mpv then crashed on the null handle (a launch from a UK-locale terminal)."""
        from nitpix import libmpv
        if not libmpv.available():
            self.skipTest("libmpv not installed")
        env = {k: v for k, v in os.environ.items() if k != "NITPIX_NO_MPV"}
        env.update(LANG="en_GB.UTF-8", LC_ALL="", QT_QPA_PLATFORM="offscreen", PYTHONDONTWRITEBYTECODE="1")
        # imported before QApplication exists, as the app does: python-mpv's own locale fix runs first
        code = ("from nitpix.ui.mpvwidget import MpvWidget; from PySide6.QtWidgets import QApplication; app = QApplication([]); "
                "w = MpvWidget(); assert w.m is not None; w.shutdown(); print('ok')")
        r = subprocess.run([sys.executable, "-B", "-c", code], env=env, capture_output=True, text=True, timeout=60,
                           cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "ok"), r.stderr[-500:])


if __name__ == "__main__":
    unittest.main()
