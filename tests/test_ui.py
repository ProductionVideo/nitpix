"""Desktop UI, offscreen and without libmpv: frame maths, screens build and react, timeline
paints. Needs PySide6 (the .venv): .venv/bin/python -m unittest tests.test_ui"""
import os as _os, tempfile as _tempfile; _os.environ.setdefault("NITPIX_STATE_DIR", _tempfile.mkdtemp(prefix="nitpix state "))  # keep the real recent list untouched
import importlib.util
import json
import os
import shutil
import tempfile
import time
import unittest
from fractions import Fraction

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["NITPIX_NO_MPV"] = "1"

HAS_QT = importlib.util.find_spec("PySide6") is not None
if HAS_QT:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication

    from nitpix import service
    from nitpix.store import Project
    from nitpix.ui import theme
    from nitpix.ui.app import MainWindow
    from nitpix.ui.review import PosCtx


def video(aid, name, fps="25/1", frames=250, start_tc=None, **more):
    return {"id": aid, "kind": "video", "name": name, "path": f"/m/{name}", "fps": fps, "frames": frames,
            "duration": frames / float(Fraction(fps)) if frames else None, "start_tc": start_tc, **more}


@unittest.skipUnless(HAS_QT, "PySide6 not installed in this interpreter")
class Positions(unittest.TestCase):
    def test_video_timecode_labels(self):
        c = PosCtx(video(1, "a.mov", "30000/1001", 3600, "00:59:58;00"))
        # 3598 s × 30 minus 2 frames for each of the 54 non-tenth minutes
        self.assertEqual((c.count, c.drop, c.start), (3600, True, 107832))
        self.assertEqual(c.label(0), "00:59:58;00")
        self.assertEqual(c.label(60), "01:00:00;00")   # minute 60 is a tenth minute: nothing dropped
        self.assertEqual(c.label(1860), "01:01:00;02")  # minute 61 drops ;00 and ;01
        self.assertEqual(c.parse("01:01:00;02"), 1860)
        self.assertEqual(c.parse("61"), 61)
        self.assertIsNone(c.parse("nonsense"))

    def test_sequence_labels_are_frame_numbers(self):
        c = PosCtx({"id": 2, "kind": "sequence", "fps": "24/1", "seq": {"first": 1001, "last": 1048}})
        self.assertEqual((c.count, c.label(0), c.label(47), c.sub(3)), (48, "1001", "1048", "idx 3"))
        self.assertEqual(c.parse("1010"), 9)
        self.assertIsNone(c.parse("00:00:01:00"))

    def test_frame_count_fallbacks(self):
        self.assertEqual(PosCtx({**video(3, "x.mov", "25/1", None), "duration": 4.0}).count, 100)
        self.assertEqual(PosCtx({"id": 4, "kind": "image", "fps": None}).count, 1)


@unittest.skipUnless(HAS_QT, "PySide6 not installed in this interpreter")
class Screens(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        from PySide6.QtCore import QSettings
        cls.settings_dir = tempfile.mkdtemp(prefix="nitpix settings ")
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)  # keep tests out of the real preferences
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, cls.settings_dir)
        cls.app.setStyleSheet(theme.STYLESHEET)
        cls.tmp = tempfile.mkdtemp(prefix="nitpix ui ")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def setUp(self):
        self.win = MainWindow(service.Session())
        self.win.open_project(tempfile.mkdtemp(dir=self.tmp) + "/ui.nitpix")  # a fresh project per test
        p = self.win.session.project
        meta = {"video_streams": [{"index": 0, "codec": "prores", "width": 1920, "height": 1080, "fps": "25/1", "fps_float": 25.0,
                                   "pix_fmt": "yuv422p10le", "bit_depth": 10, "chroma": "4:2:2"}],
                "audio_streams": [], "analysis": {"yavg": [16, 40, 200, 40, 16] * 10, "audio_rms": {"window": 0.5, "channels": {"1": [-20, -18, -60, -19]}}}}
        self.aid = p.upsert_asset("/m/a.mov", kind="video", name="a.mov", fps="25/1", frames=250, duration=10.0, width=1920, height=1080,
                                  scan_state="done", metadata=json.dumps(meta), start_tc="01:00:00:00")
        self.bid = p.upsert_asset("/m/b.mov", kind="video", name="b.mov", fps="25/1", frames=100, duration=4.0, scan_state="done")
        p.replace_findings(self.aid, [
            {"check_key": "content.black", "category": "content", "kind": "detected", "auto_result": "warning", "confidence": "high",
             "title": "Black segment", "detail": "", "measured": "1.0s", "expected": None, "why": "", "frame_in": 10, "frame_out": 34},
            {"check_key": "video.codec", "category": "format", "kind": "measured", "auto_result": "pass", "confidence": "high",
             "title": "Video codec", "detail": "", "measured": "prores", "expected": "prores", "why": "", "frame_in": None, "frame_out": None}])
        p.recompute_result(self.aid)

    def tearDown(self):
        jobs = self.win.session.jobs
        if jobs and jobs.active:  # otherwise closeEvent asks whether to cancel the scan
            jobs.cancel()
            for _ in range(100):
                if not jobs.active:
                    break
                time.sleep(0.02)
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()

    def key(self, text, key=None, shift=False):
        mods = Qt.KeyboardModifier.ShiftModifier if shift else Qt.KeyboardModifier.NoModifier
        self.win.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, key or Qt.Key.Key_unknown, mods, text))

    def test_media_list_filters_and_opens(self):
        self.win.show_view("media")
        self.assertEqual(self.win.media.proxy.rowCount(), 2)
        self.win.media.f_result.setCurrentIndex(self.win.media.f_result.findData("warning"))
        self.assertEqual(self.win.media.visible_ids(), [self.aid])
        self.win.media.f_result.setCurrentIndex(0)
        self.win.media.search.setText("check:content.black")
        self.win._refresh_list(force=True)
        self.assertEqual(self.win.media.visible_ids(), [self.aid])
        self.win.media.search.clear()
        self.win.show_asset(self.aid)
        self.assertEqual(self.win.current_view, "review")
        self.assertEqual(self.win.review.asset["id"], self.aid)

    def test_review_screen_without_a_player(self):
        self.win.show_asset(self.aid)
        rv = self.win.review
        self.assertEqual(rv.tc_lab.text(), "01:00:00:00")
        self.assertEqual(rv.tabs.tabText(0), "Checks (1)")
        rv.seek(35)
        self.assertEqual((rv.frame, rv.tc_lab.text()), (35, "01:00:01:10"))
        self.key("", Qt.Key.Key_Right, shift=True)
        self.assertEqual(rv.frame, 60)
        self.key("", Qt.Key.Key_Left)
        self.assertEqual(rv.frame, 59)
        self.key("[")
        self.assertEqual(rv.frame, 10)  # the black segment
        self.key("i")
        rv.seek(20)
        self.key("o")
        self.assertEqual((rv.asset["in_frame"], rv.asset["out_frame"]), (10, 20))
        self.key("b")
        self.assertEqual([n["kind"] for n in rv.asset["notes"]], ["bookmark"])
        # decide the open detection with the keyboard, then approve
        self.key("j")
        self.key("!", Qt.Key.Key_Exclam, shift=True)          # ⇧1 decides; plain 1 is for screens
        f = [x for x in rv.asset["findings"] if x["check_key"] == "content.black"][0]
        self.assertEqual((f["reviewer_status"], rv.asset["qc_result"]), ("pass", "pass"))
        self.key("a")
        self.assertEqual(rv.asset["review_status"], "approved")
        self.assertEqual(self.win.media.model.rows[0]["review_status"], "approved")
        # prev / next
        rv.set_order([self.aid, self.bid])
        self.key(".")
        self.assertEqual(rv.asset["id"], self.bid)
        self.assertFalse(rv.next_btn.isEnabled())
        img = rv.timeline.grab().toImage()
        self.assertGreater(img.width(), 100)

    def test_markers_jump_across_findings_and_notes(self):
        self.win.show_asset(self.aid)
        rv = self.win.review
        rv.seek(50)
        self.key("b")                                         # a bookmark at 50
        rv.seek(0)
        self.key("]")
        self.assertEqual((rv.frame, rv.tabs.currentIndex()), (10, 0))   # the black segment (finding)
        self.key("]")
        self.assertEqual((rv.frame, rv.tabs.currentIndex()), (50, 1))   # then the bookmark (notes tab)
        self.key("]")
        self.assertEqual(rv.frame, 10)                                  # wraps round
        self.key("[")
        self.assertEqual(rv.frame, 50)

    def test_note_form_saves(self):
        self.win.show_asset(self.aid)
        rv = self.win.review
        rv.seek(42)
        rv.start_note()
        rv.note_text.setPlainText("soft focus")
        rv.note_sev.setCurrentIndex(rv.note_sev.findData("issue"))
        rv.save_note()
        n = rv.asset["notes"][0]
        self.assertEqual((n["frame"], n["severity"], n["text"]), (42, "issue", "soft focus"))
        self.assertEqual(rv.asset["qc_result"], "fail")

    def test_presets_editor_round_trip(self):
        self.win.show_view("presets")
        ps = self.win.presets
        ps._new()                                   # creates and selects a blank custom preset
        self.assertFalse(ps.current["builtin"])
        ed = ps.editor
        ed.name.setText("Festival DCP-ish")
        ed.widgets[("video", "codecs")].set_values(["prores"])
        ed.widgets[("video", "profiles")].set_values(["HQ"])
        ed.widgets[("video", "codecs")].add.setText("Avid DNxHR, h264")
        ed.widgets[("video", "codecs")].add.returnPressed.emit()  # typed values map onto listed options
        self.assertEqual(sorted(ed.widgets[("video", "codecs")].values()), ["dnxhd", "h264", "prores"])
        ed.widgets[("video", "width")].setText("1,920")
        ed.widgets[("video", "height")].setText("1080")
        ed.widgets[("audio", "loudness_target")].setText("−23")     # typographic minus
        ed.widgets[("audio", "sample_rate")].set_value(48000)
        ed.widgets[("video", "fps")].set_values(["25/1"])
        ed.widgets[("video", "par")].setEditText("Square (1:1)")
        ed.widgets[("audio", "required")].setCurrentIndex(1)
        self.assertTrue(ps.dirty)
        ps._save()
        saved = ps.current
        self.assertEqual(saved["name"], "Festival DCP-ish")
        self.assertEqual((saved["video"]["width"], saved["video"]["height"], saved["video"]["par"]), (1920, 1080, "1:1"))
        self.assertEqual((saved["audio"]["loudness_target"], saved["audio"]["sample_rate"], saved["audio"]["required"]), (-23.0, 48000, True))
        self.assertEqual(saved["video"]["fps"], ["25/1"])
        # a bad field blocks saving with a message, not a crash
        ed.widgets[("video", "start_tc")].setText("ten o'clock")
        self.assertFalse(ed.widgets[("video", "start_tc")].validate())
        with self.assertRaises(ValueError):
            ed.collect(saved["id"])
        # built-ins are read-only; duplicating one gives an editable copy with the same spec
        ps.refresh("lhs_hd_prores_25")
        self.assertFalse(ps.editor.isEnabled())
        ps._dup()
        self.assertTrue(ps.current["name"].endswith("(copy)"))
        self.assertEqual(ps.current["video"]["codecs"], ["prores"])
        ps._use()
        self.assertEqual(self.win.session.project.settings["preset_id"], ps.current["id"])

    def test_review_screen_stays_narrow(self):
        # a very long name and path must elide, not force the window wider than a laptop screen
        p = self.win.session.project
        cid = p.upsert_asset("/Volumes/Projects/" + "deep_folder_name/" * 8 + "A_VERY_LONG_FILE_NAME_" * 4 + ".mov", kind="video",
                             name="A_VERY_LONG_FILE_NAME_" * 4 + ".mov", fps="25/1", frames=10, duration=0.4, scan_state="done")
        self.win.show_asset(cid)
        self.win.resize(1100, 650)
        self.app.processEvents()
        self.assertLessEqual(self.win.review.minimumSizeHint().width(), 900)   # fits a 1280-wide screen with the rail
        self.assertLessEqual(self.win.minimumSizeHint().width(), 1100)
        self.assertLessEqual(self.win.width(), 1100)
        self.assertEqual(self.win.media.model.headerData(0, Qt.Orientation.Horizontal), "File")

    def test_goto_field_gives_keys_back(self):
        self.win.show_asset(self.aid)
        rv = self.win.review
        self.key("g")
        self.assertIs(self.win.focusWidget(), rv.tc_in)
        rv.tc_in.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier, ""))
        self.assertEqual(rv.frame, 1)                         # empty field: the arrow stepped the player
        self.assertIsNot(self.win.focusWidget(), rv.tc_in)
        self.key("g")
        rv.tc_in.setText("01:00:00:10")
        rv.tc_in.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier, " "))
        self.assertEqual(rv.tc_in.text(), "01:00:00:10 ")      # with text, keys type as usual
        rv.tc_in.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier, ""))
        self.assertEqual(rv.tc_in.text(), "")
        self.assertIsNot(self.win.focusWidget(), rv.tc_in)

    def test_transport_keys_work_from_a_focused_button(self):
        self.win.show_asset(self.aid)
        rv = self.win.review
        rv.in_btn.setFocus()                                   # as after clicking it with the mouse
        self.app.sendEvent(rv.in_btn, QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier, ""))
        self.assertEqual(rv.frame, 1)
        self.app.sendEvent(rv.in_btn, QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier, " "))
        self.assertIsNone(rv.asset["in_frame"])               # Space did not "click" the In button
        rv.start_note()                                        # but a text field keeps its keys
        self.app.sendEvent(rv.note_text, QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier, ""))
        self.assertEqual(rv.frame, 1)

    def test_use_preset_keeps_unsaved_edits(self):
        self.win.show_view("presets")
        ps = self.win.presets
        ps._new()
        ps.editor.name.setText("Edited then used")
        ps.editor.widgets[("video", "width")].setText("2048")
        ps._use()
        self.assertEqual(ps.current["name"], "Edited then used")
        self.assertEqual(ps.current["video"]["width"], 2048)
        self.assertEqual(self.win.session.project.settings["preset_id"], ps.current["id"])

    def test_update_results_are_reported(self):
        self.win._update_ready({"current": "0.5.1", "error": "Could not reach GitHub: offline"}, True)
        self.assertIn("Could not reach", self.win.toast_lab.text())
        self.win._update_ready({"current": "0.5.1", "latest": "0.5.1", "newer": False}, True)
        self.assertIn("up to date", self.win.toast_lab.text())
        self.win.toast_lab.setText("")
        self.win._update_ready({"current": "0.5.1", "error": "offline"}, False)   # the start-up check stays quiet
        self.assertEqual(self.win.toast_lab.text(), "")
        self.win.show_view("settings")
        from nitpix import __version__
        self.assertIn(__version__, self.win.settings_screen.version_lab.text())

    def test_drop_media_scans_straight_away(self):
        from PySide6.QtCore import QMimeData, QPointF, QUrl
        from PySide6.QtGui import QDropEvent
        media = tempfile.mkdtemp(dir=self.tmp, prefix="drop ")
        for i in range(1, 4):
            open(os.path.join(media, f"plate.{1000 + i}.png"), "wb").write(b"x")
        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(media)])
        calls = []
        self.win.session.jobs.scan_roots = lambda roots, force=False: calls.append(roots)
        self.win.dropEvent(QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction, md, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        self.app.processEvents()
        self.assertEqual(calls, [[os.path.abspath(media)]])
        self.assertEqual(self.win.current_view, "media")

    def test_drop_with_no_project_makes_one(self):
        self.win.close_project()
        media = tempfile.mkdtemp(dir=self.tmp, prefix="Client delivery ")
        home = tempfile.mkdtemp(dir=self.tmp)
        old = os.environ.get("HOME")
        os.environ["HOME"] = home
        try:
            self.win.ingest([media])
        finally:
            os.environ["HOME"] = old
        p = self.win.session.project
        self.assertIsNotNone(p)
        self.assertTrue(os.path.realpath(str(p.folder)).startswith(os.path.realpath(os.path.join(home, "nitpix Projects"))), p.folder)
        self.assertEqual(p.settings["project_name"], os.path.basename(media))

    def test_drop_during_a_scan_queues(self):
        jobs = self.win.session.jobs
        started = []
        jobs.scan_roots = lambda roots, force=False: started.append(roots)
        from unittest import mock
        with mock.patch.object(type(jobs), "active", new_callable=mock.PropertyMock, return_value=object()):
            self.win.ingest([self.tmp])
            self.assertEqual((started, self.win.pending_roots), ([], [os.path.abspath(self.tmp)]))
        self.win._poll()                                    # the batch is over: the queue starts
        self.assertEqual((started, self.win.pending_roots), ([[os.path.abspath(self.tmp)]], []))

    def test_question_mark_opens_contextual_shortcuts(self):
        from nitpix.ui.shortcuts import ShortcutsDialog
        opened = []
        self.win.show_help = lambda: opened.append(self.win.current_view)
        self.win.show_view("media")
        self.win.media.table.setFocus()
        self.app.sendEvent(self.win.media.table, QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Question, Qt.KeyboardModifier.ShiftModifier, "?"))
        self.assertEqual(opened, ["media"])                  # the table's type-to-find no longer eats it
        dlg = ShortcutsDialog(self.win, "review")
        self.assertIn("this screen", dlg.nav.item(0).text())
        self.assertIn("Space", dlg.body.text())
        dlg.deleteLater()

    def test_number_keys_switch_screens_and_shift_numbers_decide(self):
        self.win.show_asset(self.aid)
        rv = self.win.review
        rv.setFocus()
        self.app.sendEvent(rv, QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_2, Qt.KeyboardModifier.NoModifier, "2"))
        self.assertEqual(self.win.current_view, "media")           # plain 2 is Media, even from Review
        self.assertIsNone(next(f for f in rv.asset["findings"] if f["check_key"] == "content.black").get("reviewer_status"))
        self.win.show_asset(self.aid)
        rv.setFocus()
        # UK layout: ⇧2 types a double quote; the key is still "2", so it means Issue
        self.app.sendEvent(rv, QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_QuoteDbl, Qt.KeyboardModifier.ShiftModifier, '"'))
        self.assertEqual(self.win.current_view, "review")
        f = next(f for f in rv.asset["findings"] if f["check_key"] == "content.black")
        self.assertEqual(f["reviewer_status"], "issue")

    def test_low_resolution_detection(self):
        from nitpix.ui import display
        retina = [(1512, 982, 3024, 1964)]
        self.assertTrue(display.low_resolution((1512, 982), 1.0, retina))     # Retina drawn at 1×: blurry
        self.assertFalse(display.low_resolution((1512, 982), 2.0, retina))
        self.assertFalse(display.low_resolution((1920, 1080), 1.0, [(1920, 1080, 1920, 1080)]))  # a real 1× monitor

    def test_dropdowns_fit_their_longest_entry(self):
        self.win.show_view("media")
        combo = self.win.media.f_review
        widest = max(combo.fontMetrics().horizontalAdvance(combo.itemText(i)) for i in range(combo.count()))
        self.assertGreaterEqual(combo.view().minimumWidth(), widest + 20)

    def test_other_screens_build(self):
        for v in ("dashboard", "presets", "reports", "settings"):
            self.win.show_view(v)
            self.assertEqual(self.win.current_view, v)
        self.assertIn("Fail", [self.win.dashboard.kpis.itemAt(i).widget().findChildren(type(self.win.dashboard.title))[0].text().title()
                               for i in range(self.win.dashboard.kpis.count())])
        self.win.settings_screen.reviewer.setText("Sam")
        self.win.settings_screen.save()
        self.assertEqual(self.win.session.project.settings["reviewer"], "Sam")
        self.win.show_view("media")
        self.key("2")
        self.assertEqual(self.win.current_view, "media")


if __name__ == "__main__":
    unittest.main()
