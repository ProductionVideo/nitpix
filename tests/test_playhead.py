"""The Playhead (nitpix/ui/playhead.py) on its own, without a review screen: first against a
plain stand-in that has only the player interface it uses, then driving a real MpvWidget with
the libmpv stand-in from test_scrub, the way another screen would. Offscreen.
.venv/bin/python -m unittest tests.test_playhead"""
import os as _os, tempfile as _tempfile; _os.environ.setdefault("NITPIX_STATE_DIR", _tempfile.mkdtemp(prefix="nitpix state "))  # keep the real recent list untouched
import importlib.util
import os
import sys
import unittest
from fractions import Fraction

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["NITPIX_NO_MPV"] = "1"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HAS_QT = importlib.util.find_spec("PySide6") is not None
if HAS_QT:
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QApplication

    from nitpix.ui.mpvwidget import MpvWidget
    from nitpix.ui.playhead import Playhead
    from test_scrub import FakeMpv, seek_time

    class StubPlayer(QObject):
        """Exactly the player interface the Playhead uses, recording what it is asked."""
        frame_changed = Signal(int)
        scrub_settled = Signal()

        def __init__(self):
            super().__init__()
            self.loaded, self.frame, self.paused, self.busy = True, 0, True, False
            self.calls = []

        def scrub_to(self, f):
            self.calls.append(("scrub_to", f))

        def seek_frame(self, f):
            self.calls.append(("seek_frame", f))

        def step(self, n):
            self.calls.append(("step", n))

        def set_pause(self, p):
            self.calls.append(("set_pause", p))

        def scrub_pending(self):
            return self.busy

        def report(self, f):            # what time-pos observations turn into
            self.frame = f
            self.frame_changed.emit(f)


@unittest.skipUnless(HAS_QT, "PySide6 not installed in this interpreter")
class Contract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.pl = StubPlayer()
        self.shown = True
        self.ph = Playhead(self.pl, lambda: self.shown)
        self.events = []
        self.ph.moved.connect(lambda f: self.events.append(("moved", f)))
        self.ph.pending_changed.connect(lambda p: self.events.append(("pending", p)))
        self.ph.still_seek.connect(lambda f: self.events.append(("still", f)))
        self.ph.begin(100, 0)

    def test_nothing_happens_before_there_is_media(self):
        ph = Playhead(self.pl, lambda: True)
        ph.scrub(5)
        ph.seek(5)
        self.pl.report(7)
        self.assertEqual((ph.frame, ph.pending, self.pl.calls), (0, False, []))

    def test_the_requested_frame_owns_the_position_until_it_is_shown(self):
        ph = self.ph
        for f in (10, 30, 60):
            ph.scrub(f)
        self.assertEqual(self.events, [("pending", True), ("moved", 10), ("moved", 30), ("moved", 60)])
        self.assertEqual(self.pl.calls, [("scrub_to", 10), ("scrub_to", 30), ("scrub_to", 60)])
        self.pl.report(10)                                   # an earlier target, late
        self.assertEqual((ph.frame, ph.target), (60, 60))    # what a note or bookmark would use now
        self.pl.report(60)
        self.assertEqual((ph.frame, ph.pending), (60, False))
        self.assertEqual(self.events[-2:], [("pending", False), ("moved", 60)])
        self.pl.report(61)                                   # settled: reports apply (playback)
        self.assertEqual(ph.frame, 61)

    def test_settled_means_the_player_position_is_the_truth(self):
        ph = self.ph
        ph.scrub(99)
        self.pl.frame = 96                                   # the file really ends there; no new report
        self.pl.scrub_settled.emit()
        self.assertEqual((ph.frame, ph.pending), (96, False))
        ph.scrub(40)
        self.pl.frame = 40                                   # shown without a change to report
        self.pl.scrub_settled.emit()
        self.assertEqual((ph.frame, ph.pending, self.events[-1]), (40, False, ("pending", False)))

    def test_the_backstop_waits_for_a_busy_lane_then_takes_the_player(self):
        ph = self.ph
        ph.scrub(50)
        self.assertTrue(ph._timer.isActive())
        self.pl.busy = True
        ph._settle_timeout()
        self.assertTrue(ph.pending and ph._timer.isActive())
        self.pl.busy, self.pl.frame = False, 47
        ph._settle_timeout()
        self.assertEqual((ph.frame, ph.pending, ph._timer.isActive()), (47, False, False))

    def test_seeks_and_steps_behind_a_pending_scrub_start_from_the_request(self):
        ph = self.ph
        ph.scrub(70)
        ph.step(-1)                                          # ←
        ph.seek(0)                                           # Home
        ph.step(1)                                           # →
        ph.step(25)                                          # ⇧→
        self.assertEqual(self.pl.calls, [("scrub_to", 70), ("scrub_to", 69), ("scrub_to", 0), ("scrub_to", 1), ("scrub_to", 26)])
        self.assertEqual((ph.frame, ph.target), (26, 26))

    def test_idle_steps_ask_the_player_and_follow_its_report(self):
        ph = self.ph
        ph.seek(10)
        ph.step(1)
        self.assertEqual(ph.frame, 10)
        self.pl.report(11)
        ph.step(-25)                                         # more than one frame: an exact seek
        self.assertEqual(self.pl.calls, [("seek_frame", 10), ("step", 1), ("seek_frame", 0)])
        self.assertEqual(ph.frame, 0)                        # clamped

    def test_without_the_players_picture_seeks_go_to_the_still(self):
        self.shown = False
        ph = self.ph
        ph.scrub(30)
        ph.step(1)
        self.assertEqual((ph.frame, ph.pending, self.pl.calls), (31, False, []))
        self.assertEqual([e for e in self.events if e[0] == "still"], [("still", 30), ("still", 31)])

    def test_new_media_drops_a_pending_scrub_and_a_refresh_keeps_it(self):
        ph = self.ph
        ph.scrub(60)
        ph.begin(100, new=False)                             # the same asset refreshed
        self.assertEqual((ph.frame, ph.target), (60, 60))
        ph.begin(50, 12, new=True)                           # another asset, opened at its last frame
        self.assertEqual((ph.frame, ph.pending, ph.count, ph._timer.isActive()), (12, False, 50, False))
        self.pl.report(60)                                   # the old file's target arriving late
        self.assertEqual(ph.frame, 49)                       # taken as a report, clamped to the new file

    def test_play_pause_and_replay_from_the_top(self):
        ph, pl = self.ph, self.pl
        self.assertTrue(ph.toggle_play(looping=False))
        self.assertEqual(pl.calls[-1], ("set_pause", None))
        ph.seek(99)
        pl.calls.clear()
        self.assertTrue(ph.toggle_play(looping=False))       # at the end: from the top, then play
        self.assertEqual(pl.calls, [("seek_frame", 0), ("set_pause", False)])
        ph.seek(99)
        pl.calls.clear()
        ph.toggle_play(looping=True)                         # looping: just play
        self.assertEqual(pl.calls, [("set_pause", None)])
        pl.loaded = False
        self.assertFalse(ph.toggle_play(looping=False))

    def test_stop_drops_the_scrub_and_stops_following_the_player(self):
        ph = self.ph
        ph.scrub(60)
        ph.stop()
        ph.stop()                                            # twice is fine
        self.assertEqual((ph.pending, ph._timer.isActive()), (False, False))
        n = len(self.events)
        self.pl.report(60)
        self.pl.scrub_settled.emit()
        ph.seek(5)
        self.assertEqual(len(self.events), n)


@unittest.skipUnless(HAS_QT, "PySide6 not installed in this interpreter")
class AnotherCaller(unittest.TestCase):
    """A real MpvWidget (libmpv stand-in) driven by a Playhead, as a second screen would."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.w = MpvWidget()
        self.m = self.w.m = FakeMpv()
        self.w.loaded, self.w.rate, self.w.target = True, Fraction(25), "/m/a"
        self.errors = []
        self.w.error.connect(self.errors.append)
        self.ph = Playhead(self.w, lambda: True)
        self.ph.begin(250, 0)

    def tearDown(self):
        self.ph.stop()
        self.w.m = None
        self.w.deleteLater()
        self.flush()

    def flush(self):
        for _ in range(3):
            self.app.processEvents()

    def answer(self, err=None):
        f = next(f for f in self.m.futures if not f.done())
        f.set_exception(err) if err else f.set_result(None)
        self.flush()

    def shown(self, frame):
        """mpv carries out the seek: SEEK, PLAYBACK_RESTART, and time-pos for the frame."""
        self.w.relay.prop.emit("seek-started", None)
        self.w.relay.prop.emit("seek-done", None)
        self.w._on_prop("time-pos", float(Fraction(frame) / 25))
        self.flush()

    def async_seeks(self):
        return [a[1] for k, a in self.m.sent if k == "async" and a[0] == "seek"]

    def test_transport_and_settling_without_a_review_screen(self):
        ph = self.ph
        for f in (10, 120, 200):
            ph.scrub(f)
        self.assertEqual(self.async_seeks(), [seek_time(10)])          # one in flight, the newest waiting
        ph.step(-1)                                                    # ← behind it
        self.answer()                                                  # 10 queued in mpv; 199 sent
        self.assertEqual(self.async_seeks(), [seek_time(10), seek_time(199)])
        self.w._on_prop("time-pos", float(Fraction(10) / 25))          # 10 drawn late
        self.assertEqual((ph.frame, ph.target), (199, 199))
        self.answer()
        self.shown(199)
        self.assertEqual((ph.frame, ph.pending, self.w.scrub_pending()), (199, False, False))
        self.assertFalse([a for k, a in self.m.sent if k == "sync"])   # nothing blocking

    def test_a_replaced_files_late_replies_change_nothing(self):
        ph = self.ph
        ph.scrub(80)
        ph.scrub(90)
        old = list(self.m.futures)
        self.w.unload()                                                # another file comes in
        self.w.loaded, self.w.target = True, "/m/b"
        ph.begin(100, 3, new=True)
        for f in old:
            f.set_result(None)
        self.w.relay.prop.emit("seek-started", None)                   # its events too
        self.w.relay.prop.emit("seek-done", None)
        self.flush()
        self.assertEqual((ph.frame, ph.pending, self.w.scrub_pending()), (3, False, False))
        self.assertEqual(self.async_seeks(), [seek_time(80)])          # 90 never went to the new file

    def test_refused_and_failed_seeks(self):
        ph = self.ph
        self.w._on_prop("time-pos", float(Fraction(5) / 25))
        self.m.fail_next = True
        ph.scrub(30)                                                   # refused: settles on the frame shown
        self.assertEqual((ph.frame, ph.pending), (5, False))
        self.assertIn("refused", self.errors[0])
        ph.scrub(40)
        ph.scrub(50)
        self.answer(err=RuntimeError("seek failed"))                   # an error reply: reported, the next goes
        self.assertEqual(self.errors[-1], "Player: seek failed")
        self.assertEqual(self.async_seeks()[-1], seek_time(50))
        self.answer()
        self.shown(50)
        self.assertEqual((ph.frame, ph.pending), (50, False))

    def test_shutdown_while_a_scrub_is_pending(self):
        ph = self.ph
        moved = []
        ph.moved.connect(moved.append)
        ph.scrub(60)
        ph.stop()                                                      # the screen stops it, then the player
        self.w.shutdown()
        self.assertIsNone(self.w.m)
        self.w._on_prop("time-pos", 2.4)                               # anything still arriving is ignored
        self.w.relay.prop.emit("seek-done", None)
        self.flush()
        self.assertEqual((moved, ph.pending, ph._timer.isActive()), ([60], False, False))


if __name__ == "__main__":
    unittest.main()
