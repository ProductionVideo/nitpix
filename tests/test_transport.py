"""The review transport stays put while the timecode and frame counter run: the timecode block
is sized per asset for its widest possible text, so the buttons and go-to field beside it do
not move as digits change or are added. Offscreen and without libmpv; geometry, not metrics.
.venv/bin/python -m unittest tests.test_transport"""
import os as _os, tempfile as _tempfile; _os.environ.setdefault("NITPIX_STATE_DIR", _tempfile.mkdtemp(prefix="nitpix state "))  # keep the real recent list untouched
import importlib.util
import os
import shutil
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["NITPIX_NO_MPV"] = "1"

HAS_QT = importlib.util.find_spec("PySide6") is not None
if HAS_QT:
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication

    from nitpix import service
    from nitpix.store import Project
    from nitpix.ui import theme
    from nitpix.ui.review import ReviewScreen
    from nitpix.ui.widgets import repolish

# awkward positions: proportional digits (111 / 888), digit-count boundaries, the last frame
PROBES = [0, 1, 9, 10, 11, 88, 99, 100, 111, 888, 999, 1000, 1111, 8888, 9999, 10000, 11111, 88888, 99999, 100000]


@unittest.skipUnless(HAS_QT, "PySide6 not installed in this interpreter")
class StationaryTransport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        theme.load_fonts()
        cls.app.setStyleSheet(theme.STYLESHEET)
        cls.tmp = tempfile.mkdtemp(prefix="nitpix transport ")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def setUp(self):
        self.s = service.Session()
        self.s.project = p = Project(tempfile.mkdtemp(dir=self.tmp) + "/t.nitpix")
        add = lambda path, **f: p.upsert_asset(path, scan_state="done", name=os.path.basename(path), **f)
        self.ids = {
            "long25": add("/m/long.mov", kind="video", fps="25/1", frames=15000, duration=600.0),
            "hour_rollover": add("/m/tc.mov", kind="video", fps="25/1", frames=2000, duration=80.0, start_tc="09:59:30:00"),
            "df2997": add("/m/df.mov", kind="video", fps="30000/1001", frames=120000, duration=4004.0, start_tc="00:59:59;00"),
            "r23976": add("/m/23.mov", kind="video", fps="24000/1001", frames=5000, duration=208.5),
            "short": add("/m/short.mov", kind="video", fps="25/1", frames=50, duration=2.0),
        }
        for name, first, last in (("seq999", 990, 1012), ("seq_big", 1, 120000)):
            rows = [[n, f"f.{n:06d}.exr", 1000, "ok"] for n in range(first, min(last, first + 3) + 1)]
            self.ids[name] = add(f"/m/{name}/f.######.exr", kind="sequence", fps="24/1", dir=f"/m/{name}",
                                 seq=service.json.dumps({"first": first, "last": last, "dir": "/m", "ext": "exr", "frames": rows}))
        self.rv = ReviewScreen(self.s)
        self.rv.show()

    def tearDown(self):
        self.rv.close()
        self.rv.deleteLater()
        self.s.project.close()
        self.app.processEvents()

    def settle(self):
        for _ in range(4):
            self.app.processEvents()

    def positions(self):
        rv = self.rv
        at = lambda w: w.mapTo(rv, QPoint(0, 0))
        return at(rv.play_btn.parentWidget()), at(rv.tc_in)

    def run_through(self, key, width, frames=PROBES, pending_flip=False):
        rv = self.rv
        rv.resize(width, 900)
        rv.load_asset(self.ids[key])
        self.settle()
        base = self.positions()
        hints = []
        for i, f in enumerate(f for f in frames if f < rv.ctx.count):
            rv.frame = f
            if pending_flip:
                rv.playhead.target = f if i % 2 else None   # dimmed / normal timecode in turn
            rv._update_tc()
            self.settle()
            with self.subTest(asset=key, width=width, frame=f, tc=rv.tc_lab.text(), counter=rv.tc_sub.text()):
                self.assertEqual(self.positions(), base)
                for lab in (rv.tc_lab, rv.tc_sub):   # nothing clipped
                    self.assertLessEqual(lab.sizeHint().width(), lab.width())
            hints.append(max(rv.tc_lab.sizeHint().width(), rv.tc_sub.sizeHint().width()))
        rv.playhead.target = None
        return base, max(hints)

    def test_controls_do_not_move_while_the_counter_runs(self):
        for width in (1040, 1200, 1600):
            for key in self.ids:
                self.rv.resize(width, 900)
                self.rv.load_asset(self.ids[key])
                last = self.rv.ctx.count - 1
                _, widest = self.run_through(key, width, sorted(set(PROBES + [last, last // 2])))
                with self.subTest(asset=key, width=width):
                    # reserved for the worst case, but not grossly more than the text ever needs
                    self.assertLessEqual(self.rv.tcbox.minimumWidth() - widest, 16)

    def test_timecode_rollovers_and_dimmed_timecode(self):
        # 09:59:59:24 -> 10:00:00:00, and 00:59:59;29 -> 01:00:00;02 (drop frame)
        self.run_through("hour_rollover", 1200, [0, 749, 750, 751, 1999], pending_flip=True)
        self.run_through("df2997", 1200, [0, 29, 30, 31, 1799, 1800, 119999], pending_flip=True)

    def test_switching_assets_resizes_for_each(self):
        rv = self.rv
        rv.resize(1200, 900)
        rv.load_asset(self.ids["short"])
        self.settle()
        short = rv.tcbox.minimumWidth()
        rv.load_asset(self.ids["df2997"])
        self.settle()
        long = rv.tcbox.minimumWidth()
        rv.load_asset(self.ids["short"])
        self.settle()
        self.assertGreater(long, short)                 # more digits need more room...
        self.assertEqual(rv.tcbox.minimumWidth(), short)  # ...and a short asset gets its own back
        self.run_through("short", 1200)

    def test_counter_text(self):
        rv = self.rv
        rv.load_asset(self.ids["long25"])
        rv.frame = 123
        rv._update_tc()
        self.assertEqual(rv.tc_sub.text(), "123 / 15000 frames")
        rv.load_asset(self.ids["seq999"])
        rv.frame = 3
        rv._update_tc()
        self.assertEqual((rv.tc_lab.text(), rv.tc_sub.text()), ("993", "idx 3 / 23 frames"))

    def test_narrow_window_still_wraps(self):
        rv = self.rv
        rv.resize(1040, 900)
        rv.load_asset(self.ids["df2997"])
        self.settle()
        tc_y = rv.tcbox.mapTo(rv, QPoint(0, 0)).y()
        markers_y = max(w.mapTo(rv, QPoint(0, 0)).y() for w in rv.findChildren(type(rv.play_btn)) if w.text() == "›" and w.isVisible())
        self.assertGreater(markers_y, tc_y)   # the last cluster went to a second row rather than widening the window
        self.assertLessEqual(rv.width(), 1040)

    def test_font_change_re_sizes(self):
        rv = self.rv
        rv.resize(1200, 900)
        rv.load_asset(self.ids["long25"])
        self.settle()
        before = rv.tcbox.minimumWidth()
        rv.tc_sub.setStyleSheet("font-size: 20px;")   # e.g. a different style or display
        self.settle()
        self.assertGreater(rv.tcbox.minimumWidth(), before)
        rv.tc_sub.setStyleSheet("")
        self.settle()
        self.assertEqual(rv.tcbox.minimumWidth(), before)


if __name__ == "__main__":
    unittest.main()
