"""Editing the default delivery spec directly: the first save makes this project's own version
(once), active straight away if the default was active, re-evaluated from stored measurements;
later saves update it; the factory spec, other projects and other built-ins are untouched.
.venv/bin/python -m unittest tests.test_default_spec"""
import os as _os, tempfile as _tempfile; _os.environ.setdefault("NITPIX_STATE_DIR", _tempfile.mkdtemp(prefix="nitpix state "))  # keep the real recent list untouched
import copy
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["NITPIX_NO_MPV"] = "1"

HAS_QT = importlib.util.find_spec("PySide6") is not None
from nitpix import service  # noqa: E402
from nitpix.presets import builtin_by_id  # noqa: E402
from nitpix.probe import find_tool  # noqa: E402
from nitpix.store import Project  # noqa: E402

SRC, OWN = service.default_spec_ids()


class Service(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nitpix spec ")
        self.p = Project(os.path.join(self.tmp, "a.nitpix"))
        self.factory = copy.deepcopy(builtin_by_id()[SRC])

    def tearDown(self):
        self.p.close()
        shutil.rmtree(self.tmp, True)

    def body(self, **video):
        b = {k: copy.deepcopy(v) for k, v in self.factory.items() if k in ("name", "description", "video", "audio", "sequence", "still")}
        b["video"].update(video)
        return b

    def rows(self):
        return [dict(r) for r in self.p.q("SELECT id, name, spec FROM presets")]

    def test_first_save_creates_one_active_project_spec_and_later_saves_update_it(self):
        self.assertEqual(self.p.settings["preset_id"], SRC)
        pid, warnings, active = service.save_default_spec(self.p, self.body(width=3840, height=2160))
        self.assertEqual((pid, warnings, active, self.p.settings["preset_id"]), (OWN, [], True, OWN))
        pid2, _, _ = service.save_default_spec(self.p, {**self.body(width=1280, height=720), "name": "Show content, 720"})
        self.assertEqual(pid2, OWN)
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["name"], json.loads(rows[0]["spec"])["video"]["width"]), ("Show content, 720", 1280))
        self.assertEqual(builtin_by_id()[SRC], self.factory)              # factory definition untouched
        self.p.close()                                                     # survives closing and reopening
        self.p = Project(os.path.join(self.tmp, "a.nitpix"))
        self.assertEqual(self.p.settings["preset_id"], OWN)
        self.assertEqual(service.active_preset(self.p)["video"]["height"], 720)

    def test_invalid_fields_change_nothing(self):
        with self.assertRaises(ValueError):
            service.save_default_spec(self.p, self.body(width="wide"))
        self.assertEqual((self.rows(), self.p.settings["preset_id"]), ([], SRC))

    def test_saving_while_another_spec_is_active_does_not_switch(self):
        service.update_settings(self.p, {"preset_id": "lhs_hd_prores_25"})
        pid, _, active = service.save_default_spec(self.p, self.body(width=1280))
        self.assertEqual((pid, active, self.p.settings["preset_id"]), (OWN, False, "lhs_hd_prores_25"))

    def test_other_projects_keep_the_factory_default(self):
        service.save_default_spec(self.p, self.body(width=1280))
        other = Project(os.path.join(self.tmp, "b.nitpix"))
        try:
            self.assertEqual(other.settings["preset_id"], SRC)
            self.assertEqual(list(other.q("SELECT id FROM presets")), [])
            self.assertEqual(service.active_preset(other)["video"]["width"], self.factory["video"]["width"])
        finally:
            other.close()


@unittest.skipUnless(HAS_QT, "PySide6 not installed in this interpreter")
class Screen(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication

        from nitpix.ui import theme
        cls.app = QApplication.instance() or QApplication([])
        cls.settings_dir = tempfile.mkdtemp(prefix="nitpix settings ")
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)  # keep tests out of the real preferences
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, cls.settings_dir)
        cls.app.setStyleSheet(theme.STYLESHEET)
        cls.tmp = tempfile.mkdtemp(prefix="nitpix spec ui ")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)
        shutil.rmtree(cls.settings_dir, True)

    def setUp(self):
        from nitpix.ui.app import MainWindow
        self.win = MainWindow(service.Session())
        self.win.open_project(tempfile.mkdtemp(dir=self.tmp) + "/s.nitpix")
        self.p = self.win.session.project
        self.p.upsert_asset("/m/a.mov", kind="video", name="a.mov", fps="25/1", frames=100, duration=4.0, scan_state="done")
        self.win.show_view("presets")
        self.ps = self.win.presets
        self.rechecks = []
        self.recheck = mock.patch.object(self.win.session.jobs, "recheck", side_effect=lambda ids, reanalyse=True: self.rechecks.append(reanalyse))
        self.recheck.start()

    def tearDown(self):
        self.recheck.stop()
        jobs = self.win.session.jobs
        if jobs and jobs.active:
            jobs.cancel()
            for _ in range(100):
                if not jobs.active:
                    break
                time.sleep(0.02)
        self.win.ask_unsaved = lambda *_a: "discard"     # a test's leftover draft: quitting drops it
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()

    def custom_rows(self):
        return [r["id"] for r in self.p.q("SELECT id FROM presets")]

    def open_default(self):
        self.ps.refresh(SRC)
        self.assertEqual(self.ps.current["id"], SRC)

    def test_edit_the_active_default_and_save(self):
        ps = self.ps
        self.open_default()
        self.assertTrue(ps.editor.isEnabled())
        self.assertTrue(ps.save_btn.isVisibleTo(ps) and not ps.banner.isVisibleTo(ps))   # no read-only banner
        self.assertEqual(self.custom_rows(), [])                     # opening it creates nothing
        ps.editor.widgets[("video", "width")].setText("3840")
        ps.editor.widgets[("video", "height")].setText("2160")
        self.assertTrue(ps.save_btn.isEnabled())
        self.assertTrue(ps._save())
        self.assertEqual((ps.current["id"], ps.current["name"], self.p.settings["preset_id"]), (OWN, builtin_by_id()[SRC]["name"], OWN))
        self.assertTrue(ps.active_chip.isVisibleTo(ps) and not ps.use_btn.isVisibleTo(ps))   # active at once, no Use needed
        self.assertEqual(self.rechecks, [False])                     # stored measurements, no re-decode
        ps.editor.widgets[("video", "width")].setText("1920")       # second save: same spec, updated
        ps.editor.name.setText("Show content, edited")
        self.assertTrue(ps._save())
        self.assertEqual((self.custom_rows(), ps.current["name"], ps.current["video"]["width"]), ([OWN], "Show content, edited", 1920))
        self.assertEqual(self.rechecks, [False, False])
        # the factory entry now points at the project's version instead of making another copy
        ps.refresh(SRC)
        self.assertFalse(ps.editor.isEnabled())
        self.assertTrue(ps.banner.isVisibleTo(ps) and "Show content, edited" in ps.banner.text())
        ps._dup()                                                    # Duplicate still starts from the factory spec
        self.assertTrue(ps.current["name"].endswith("(copy)"))
        self.assertEqual(ps.current["video"]["width"], builtin_by_id()[SRC]["video"]["width"])
        self.assertEqual(len(self.custom_rows()), 2)
        self.assertEqual(self.p.settings["preset_id"], OWN)

    def test_invalid_edit_saves_nothing(self):
        ps = self.ps
        self.open_default()
        ps.editor.widgets[("video", "start_tc")].setText("ten o'clock")
        self.assertFalse(ps._save())
        self.assertEqual((self.custom_rows(), self.p.settings["preset_id"], self.rechecks), ([], SRC, []))

    def test_discarding_edits_creates_no_copy(self):
        ps = self.ps
        self.open_default()
        ps.editor.widgets[("video", "width")].setText("640")
        self.assertTrue(ps.dirty)
        with mock.patch.object(self.win, "ask_unsaved", return_value="discard"):
            ps.list.setCurrentRow(next(i for i in range(ps.list.count()) if ps.list.item(i).data(256) == "lhs_hd_prores_25"))
        self.assertEqual((ps.current["id"], self.custom_rows(), self.p.settings["preset_id"]), ("lhs_hd_prores_25", [], SRC))

    def test_editing_the_inactive_default_does_not_switch_until_use(self):
        ps = self.ps
        service.update_settings(self.p, {"preset_id": "web_h264_1080"})
        self.open_default()
        ps.editor.widgets[("video", "width")].setText("1280")
        self.assertTrue(ps._save())
        self.assertEqual((ps.current["id"], self.p.settings["preset_id"], self.rechecks), (OWN, "web_h264_1080", []))
        ps._use()
        self.assertEqual((self.p.settings["preset_id"], self.rechecks), (OWN, [False]))

    def test_use_with_unsaved_edits_saves_them_first(self):
        ps = self.ps
        service.update_settings(self.p, {"preset_id": "none"})
        self.open_default()
        ps.editor.widgets[("video", "width")].setText("1280")
        ps._use()
        self.assertEqual((self.p.settings["preset_id"], ps.current["video"]["width"], self.custom_rows()), (OWN, 1280, [OWN]))
        self.assertEqual(self.rechecks, [False])                     # one re-evaluation, not two

    def test_use_with_invalid_edits_changes_nothing(self):
        ps = self.ps
        service.update_settings(self.p, {"preset_id": "none"})
        self.open_default()
        ps.editor.widgets[("video", "width")].setText("wide")
        ps._use()
        self.assertEqual((self.p.settings["preset_id"], self.custom_rows()), ("none", []))

    def test_other_built_ins_stay_read_only(self):
        ps = self.ps
        for pid in ("lhs_hd_prores_25", "none"):
            ps.refresh(pid)
            self.assertFalse(ps.editor.isEnabled())
            self.assertFalse(ps.save_btn.isVisibleTo(ps))
            self.assertTrue(ps.banner.isVisibleTo(ps))
            self.assertFalse(ps._save())
        self.assertEqual(self.custom_rows(), [])


@unittest.skipUnless(HAS_QT and find_tool("ffmpeg") and find_tool("ffprobe"), "needs PySide6, ffmpeg and ffprobe")
class Reevaluation(unittest.TestCase):
    """A real scan, then an edit to the active default re-evaluates from what was stored."""

    def test_edit_applies_to_stored_measurements(self):
        tmp = tempfile.mkdtemp(prefix="nitpix spec scan ")
        try:
            media = os.path.join(tmp, "media")
            os.makedirs(media)
            subprocess.run([find_tool("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=64x36:r=25:d=1",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", os.path.join(media, "clip.mov")], check=True)
            s = service.Session()
            p = s.open(os.path.join(tmp, "p.nitpix"))
            s.jobs.scan_roots([media])
            wait(s)
            res = lambda: next(f for f in p.q("SELECT * FROM findings") if f["check_key"] == "format.resolution")["auto_result"]
            self.assertEqual(res(), "fail")                              # 64×36 against the 1920×1080 default
            body = {k: v for k, v in copy.deepcopy(builtin_by_id()[SRC]).items() if k in ("name", "description", "video", "audio", "sequence", "still")}
            body["video"].update(width=64, height=36)
            no_decode = AssertionError("re-read or re-decoded the media")
            with mock.patch("nitpix.scanner.ffprobe", side_effect=no_decode), mock.patch("nitpix.scanner.analyse", side_effect=no_decode):
                _, _, active = service.save_default_spec(p, body)
                self.assertTrue(active)
                s.jobs.recheck([r["id"] for r in p.q("SELECT id FROM assets")], reanalyse=False)   # what Save triggers
                wait(s)
            self.assertEqual(res(), "pass")
            s.close()
        finally:
            shutil.rmtree(tmp, True)


def wait(s, timeout=60):
    end = time.time() + timeout
    while s.jobs.active and time.time() < end:
        time.sleep(0.05)
    assert not s.jobs.active, "job did not finish"


class PersonalDefaults(unittest.TestCase):
    """Defaults for new projects (nitpix/defaults.py): remembered only when asked, copied into a new
    project once, never into one that is opened; a custom spec travels as its whole definition."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nitpix defaults ")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.sessions = []
        self.factory = copy.deepcopy(builtin_by_id()[SRC])

    def tearDown(self):
        for s in self.sessions:
            s.close()

    def session(self, name, defaults=None):
        s = service.Session()
        self.sessions.append(s)
        s.open(os.path.join(self.tmp, name), defaults=defaults)
        return s, s.project

    def test_remembered_once_then_each_project_keeps_its_own(self):
        from nitpix import defaults
        old_s, old = self.session("old.nitpix")                             # an older project, set up differently
        service.update_settings(old, {"reviewer": "Old Hand", "preset_id": "none", "client": "Old client",
                                      "analysis": {"loudness": True, "workers": 3}})
        before_old = old.settings
        a_s, a = self.session("a.nitpix")
        body = {k: copy.deepcopy(v) for k, v in self.factory.items() if k in ("name", "description", "video", "audio", "sequence", "still")}
        body["name"], body["video"]["width"] = "House 4K", 3840
        pid, _w = service.save_preset(a, body)
        service.update_settings(a, {"reviewer": "Ann", "client": "Client A", "project_name": "Show A", "preset_id": pid,
                                    "analysis": {"loudness": False, "workers": 4}, "iina": {"socket_mode": "fixed"}})
        aid = a.upsert_asset("/m/a.mov", kind="video", name="a.mov", fps="25/1", frames=10, duration=0.4, scan_state="done")
        service.add_note(a, aid, text="checked")
        service.signoff(a, "approved")
        d = json.loads(json.dumps(defaults.capture(a)))                      # as the preferences keep it

        b_s, b = self.session("b.nitpix", defaults=d)                         # created now: seeded
        sb = b.settings
        self.assertEqual((sb["reviewer"], sb["preset_id"], sb["analysis"]["loudness"], sb["analysis"]["workers"],
                          sb["iina"]["socket_mode"]), ("Ann", pid, False, 4, "fixed"))
        self.assertEqual((sb["client"], sb["project_name"]), ("", "Untitled delivery"))      # project data stays in its project
        self.assertIsNone(b.get_meta("signoff"))
        self.assertEqual(b.q("SELECT * FROM notes"), [])
        row = lambda p: json.loads(p.q1("SELECT spec FROM presets WHERE id=?", (pid,))["spec"])
        self.assertEqual(row(b), row(a))                                     # the whole definition, in B's own table
        self.assertEqual(row(b)["video"]["width"], 3840)
        self.assertEqual(b_s.seed_problems, [])
        body["video"]["width"] = 1920                                        # editing B's copy changes nothing else
        service.save_preset(b, {**body, "id": pid})
        self.assertEqual(row(a)["video"]["width"], 3840)
        self.assertEqual(d["preset"]["spec"]["video"]["width"], 3840)

        old_s.close()                                                        # reopened with defaults: unchanged
        old_s.open(os.path.join(self.tmp, "old.nitpix"), defaults=d)
        self.assertEqual(old_s.project.settings, before_old)
        a_s.close()
        a_s.open(os.path.join(self.tmp, "a.nitpix"), defaults={**d, "reviewer": "Ben"})
        a = a_s.project
        self.assertEqual(a.settings["reviewer"], "Ann")
        self.assertEqual([n["author"] for n in a.q("SELECT author FROM notes")], ["Ann"])   # history keeps its authors
        self.assertEqual(a.get_meta("signoff")["by"], "Ann")
        _c_s, c = self.session("c.nitpix", defaults={**d, "reviewer": "Ben"})
        self.assertEqual(c.settings["reviewer"], "Ben")
        _n_s, n = self.session("n.nitpix")                                   # no defaults chosen: the factory settings
        self.assertEqual(n.settings["preset_id"], SRC)
        self.assertEqual(n.settings["reviewer"], "")

    def test_a_default_spec_that_cannot_be_used_is_said_not_swapped(self):
        from nitpix import defaults
        _s, a = self.session("a.nitpix")
        d = defaults.capture(a)
        for name, spec in (("unknown built-in", {"id": "gone_spec", "builtin": True, "name": "Gone"}),
                           ("invalid custom", {"id": "custom_x", "builtin": False, "name": "Broken", "spec": {"video": {"width": "wide"}}}),
                           ("no definition", {"id": "custom_y", "builtin": False, "name": "Empty"})):
            with self.subTest(name):
                s, p = self.session(f"{name}.nitpix", defaults={**d, "preset": spec, "reviewer": "Ann"})
                self.assertEqual(p.settings["preset_id"], SRC)               # the factory default, and said so
                self.assertEqual(len(s.seed_problems), 1)
                self.assertIn(spec["name"], s.seed_problems[0])
                self.assertEqual(p.settings["reviewer"], "Ann")              # the rest still applies
        s, p = self.session("damaged.nitpix", defaults={"version": None})
        self.assertIn("not used", s.seed_problems[0])
        self.assertEqual(p.settings["preset_id"], SRC)


@unittest.skipUnless(HAS_QT, "PySide6 not installed in this interpreter")
class DefaultsInTheApp(unittest.TestCase):
    """Settings → Use these for new projects, kept in the app's own (variant) preferences across a restart."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.settings_dir = tempfile.mkdtemp(prefix="nitpix settings ")
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, cls.settings_dir)

    def test_use_these_for_new_projects_survives_a_restart(self):
        from nitpix import variant
        from nitpix.ui.app import DEFAULTS_KEY, MainWindow
        from nitpix.ui.prefs import prefs
        tmp = tempfile.mkdtemp(prefix="nitpix defaults ui ")
        self.addCleanup(shutil.rmtree, tmp, True)
        prefs().clear()
        prefs().setValue("updates/auto", False)                             # app preferences that must survive
        prefs().setValue("window/rail", "narrow")
        w = MainWindow(service.Session())
        w.open_project(os.path.join(tmp, "a.nitpix"))
        self.assertIsNone(prefs().value(DEFAULTS_KEY))                       # opening or creating infers nothing
        w.show_view("settings")
        st = w.settings_screen
        st.reviewer.setText("Ann")
        st.client.setText("Client A")
        st.loudness.setChecked(False)
        st.defaults_btn.click()
        self.assertIn("Ann", st.defaults_lab.text())
        w.close()
        w.deleteLater()
        w2 = MainWindow(service.Session())                                   # "restart"
        self.addCleanup(lambda: (w2.close(), w2.deleteLater()))
        w2.open_project(os.path.join(tmp, "b.nitpix"))
        s = w2.session.project.settings
        self.assertEqual((s["reviewer"], s["client"], s["analysis"]["loudness"]), ("Ann", "", False))
        self.assertEqual((prefs().value("updates/auto", type=bool), prefs().value("window/rail")), (False, "narrow"))
        with mock.patch.object(variant, "SETTINGS", ("nitpix-exp", "nitpix-exp")):
            self.assertIsNone(prefs().value(DEFAULTS_KEY))                   # an experimental build keeps its own
        w2.show_view("settings")
        w2.settings_screen.forget_btn.click()
        w2.open_project(os.path.join(tmp, "c.nitpix"))
        self.assertEqual(w2.session.project.settings["reviewer"], "")


if __name__ == "__main__":
    unittest.main()
