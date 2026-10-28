"""The Mapping setup kept with the project, through the Mapping screen offscreen (no libmpv): model
files copied in, the setup saved after edits and restored on reopening (also from a copy of the
project with the original model files gone), and every way that can go wrong: failed, cancelled
and partial imports, a missing or changed kept copy, an unreadable saved document, a save that
fails, and a project closed or switched mid-restore.
.venv/bin/python -m unittest tests.test_mapsetup"""
import os as _os, tempfile as _tempfile; _os.environ.setdefault("NITPIX_STATE_DIR", _tempfile.mkdtemp(prefix="nitpix state "))  # keep the real recent list untouched
import importlib.util
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["NITPIX_NO_MPV"] = "1"
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
FIX = os.path.join(HERE, "fixtures", "scenes")
HAS = importlib.util.find_spec("PySide6") is not None and importlib.util.find_spec("assimp_py") is not None
if HAS:
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    from nitpix import mapsave, service
    from nitpix.ui import theme
    from test_models import digest

MODELS = ["parts/centre.obj", "parts/left.fbx", "parts/a/screen.obj", "parts/b/screen.obj", "stage.obj", "stage.fbx"]


def pump(app, until=None, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.01)
    return until is None


@unittest.skipUnless(HAS, "needs PySide6 and assimp-py")
class Retained(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.settings_dir = tempfile.mkdtemp(prefix="nitpix settings ")
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, cls.settings_dir)
        cls.app.setStyleSheet(theme.STYLESHEET)

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nitpix setups ")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.src = os.path.join(self.tmp, "originals")                      # the model files, as Jake's folders would hold them
        for rel in MODELS + ["stage.mtl", "garbage.fbx"]:
            os.makedirs(os.path.dirname(os.path.join(self.src, rel)), exist_ok=True)
            shutil.copyfile(os.path.join(FIX, rel), os.path.join(self.src, rel))
        self.clip = os.path.join(self.tmp, "media", "clip.mov")
        os.makedirs(os.path.dirname(self.clip))
        open(self.clip, "wb").close()
        self.before = digest(self.src)
        self.screens = []

    def tearDown(self):
        for ms, session in self.screens:
            ms.shutdown()
            ms.close()
            ms.deleteLater()
            if session.project:
                session.project.close()
        pump(self.app, timeout=0.05)
        if os.path.isdir(self.src):
            self.assertEqual(digest(self.src), self.before)                 # originals only ever read

    def P(self, rel):
        return os.path.join(self.src, rel)

    def open(self, folder, new=True):
        """A project and a Mapping screen on it, activated (which restores its saved setup)."""
        from nitpix.ui.mapping import MappingScreen
        session = service.Session()
        session.open(folder)
        if new:
            session.project.upsert_asset(self.clip, kind="video", name="clip.mov", fps="25/1", frames=200, duration=8.0,
                                         width=1280, height=512, scan_state="done")
        ms = MappingScreen(session)
        ms.resize(1300, 800)
        ms.show()
        self.said = []
        ms.status.connect(lambda t, e: self.said.append((t, e)))
        self.screens.append((ms, session))
        ms.activate()
        self.wait(ms)
        return ms, session

    def wait(self, ms):
        self.assertTrue(pump(self.app, lambda: ms._batch is None, 60))
        pump(self.app, timeout=0.05)

    def close(self, ms, session):
        """As the window does: flush into the open project, reset, close."""
        ms.flush()
        ms.reset_project()
        session.project.close()
        session.project = None

    def saved(self, session):
        return session.project.get_meta(mapsave.KEY)

    def build(self, ms):
        """A composed stage: OBJ and FBX, two screen.obj, an OBJ with its .mtl; UV sets, clip,
        projector (Projectors tab) and camera."""
        ms.add_models([self.P(r) for r in MODELS])
        self.wait(ms)
        self.assertEqual(len(ms.stage.sources), 6)
        stage_src = next(s for s in ms.stage.sources if s.name == "stage.fbx")     # its Centre has two UV sets
        centre = next(o.key for o in stage_src.objects if o.display == "Centre")
        ms.select([centre])
        ms.uv_box.setCurrentIndex(ms.uv_box.findData(1))
        ms.apply_selected()
        self.assertEqual(ms.assign.uv[centre], 1)                            # UV set 2
        b = next(s for s in ms.stage.sources if ms.stage.label(s) == "b/screen.obj")
        ms.select([o.key for o in b.objects])
        ms.apply_selected()
        ms.tabs.setCurrentIndex(1)
        ms.add_projector()
        ms.edit_projector({"position": (1.0, 3.0, 11.0), "target": (0.5, 2.0, 0.0), "h_fov": 33.0, "show_frustum": False})
        ms.view.orbit(30, 10)
        ms.view.zoom(2)
        ms.flush()
        return dict(ms.assign.uv)

    # ---- the whole round trip ----------------------------------------------------------------
    def test_reopen_a_copied_project_without_the_original_models(self):
        folder = os.path.join(self.tmp, "show.nitpix")
        ms, session = self.open(folder)
        aid = session.project.q1("SELECT id FROM assets")["id"]
        ms._choose_media(aid)
        uv = self.build(ms)
        order = [(s.id, ms.stage.label(s)) for s in ms.stage.sources]
        cam = (round(ms.view.yaw, 6), round(ms.view.pitch, 6), round(ms.view.dist, 6))
        proj = ms.projector
        kept = sorted(os.listdir(os.path.join(folder, "models")))
        self.assertEqual(len(kept), 6)                                      # one per distinct file
        stage_rid = ms.src_res[next(s.id for s in ms.stage.sources if s.name == "stage.obj")]
        self.assertTrue(os.path.isfile(os.path.join(folder, "models", stage_rid, "stage.mtl")))   # its material library
        self.assertEqual(ms.saver.state, "saved")
        sources = self.saved(session)["sources"]
        self.close(ms, session)

        moved = os.path.join(self.tmp, "elsewhere", "show copy.nitpix")
        shutil.copytree(folder, moved)
        self.assertEqual(digest(self.src), self.before)
        shutil.rmtree(self.src)                                              # the original model files are gone
        ms2, s2 = self.open(moved, new=False)
        self.assertEqual([(s.id, ms2.stage.label(s)) for s in ms2.stage.sources], order)
        self.assertEqual(ms2.assign.uv, uv)
        self.assertEqual(ms2.missing, {})
        self.assertEqual(ms2.projector, proj)
        self.assertEqual(ms2.tabs.currentIndex(), 1)
        self.assertEqual((round(ms2.view.yaw, 6), round(ms2.view.pitch, 6), round(ms2.view.dist, 6)), cam)
        self.assertEqual(ms2.asset["id"], aid)                               # the saved clip, by its project id
        self.assertTrue(all(models_path.startswith(os.path.realpath(moved)) for models_path in
                            (os.path.realpath(s.path) for s in ms2.stage.sources)))
        self.assertEqual(s2.project.get_meta(mapsave.KEY)["sources"], sources)
        ms2.add_models([os.path.join(FIX, "parts", "right.fbx")])            # new ids continue after the saved ones
        self.wait(ms2)
        self.assertEqual(ms2.stage.sources[-1].id, "m7")

    def test_identical_files_under_two_names_both_come_back(self):
        folder = os.path.join(self.tmp, "twins.nitpix")
        ms, session = self.open(folder)
        for n in ("a.obj", "b.obj"):
            shutil.copyfile(self.P("parts/a/screen.obj"), os.path.join(self.src, n))
        self.before = digest(self.src)
        ms.add_models([self.P("a.obj")])
        self.wait(ms)
        ms.add_models([self.P("b.obj")])                                     # same bytes, another name
        self.wait(ms)
        twin = os.path.join(self.tmp, "elsewhere", "a.obj")                 # same name and bytes, another folder
        os.makedirs(os.path.dirname(twin))
        shutil.copyfile(self.P("a.obj"), twin)
        ms.add_models([twin])
        self.wait(ms)
        self.assertEqual([s.origin for s in ms.stage.sources], [self.P("a.obj"), self.P("b.obj"), twin], self.said)
        ms.select([o.key for o in ms.stage.sources[0].objects])
        ms.apply_selected()
        ms.flush()
        uv = dict(ms.assign.uv)
        self.close(ms, session)
        ms2, _s2 = self.open(folder, new=False)
        self.assertEqual(ms2.missing, {})
        self.assertEqual([s.origin for s in ms2.stage.sources], [self.P("a.obj"), self.P("b.obj"), twin])
        self.assertEqual([s.name for s in ms2.stage.sources], ["a.obj", "b.obj", "a.obj"])
        self.assertEqual(ms2.assign.uv, uv)

    def test_an_old_project_opens_and_nothing_is_written_until_a_change(self):
        folder = os.path.join(self.tmp, "old.nitpix")
        ms, session = self.open(folder)
        self.assertIsNone(self.saved(session))
        self.assertIsNone(ms.scene)
        ms.add_models([self.P("parts/centre.obj")])
        self.wait(ms)
        ms.flush()
        self.assertEqual(len(self.saved(session)["sources"]), 1)

    def test_edits_are_saved_in_few_writes(self):
        ms, session = self.open(os.path.join(self.tmp, "w.nitpix"))
        ms.add_models([self.P("parts/centre.obj")])
        self.wait(ms)
        pump(self.app, timeout=0.6)
        n = ms.saver.writes
        for i in range(60):                                                  # a drag, and edits in quick succession
            ms.view.orbit(2, 0)
            if i % 10 == 0:
                ms.apply_all()
                ms.clear_selected()
        self.assertEqual(ms.saver.state, "pending")
        pump(self.app, timeout=2.0)
        self.assertEqual(ms.saver.state, "saved")
        self.assertLessEqual(ms.saver.writes - n, 2)

    # ---- imports that fail or stop -------------------------------------------------------------
    def test_failed_cancelled_and_partial_imports_keep_the_last_saved_setup(self):
        ms, session = self.open(os.path.join(self.tmp, "f.nitpix"))
        ms.add_models([self.P("parts/centre.obj")])
        self.wait(ms)
        ms.apply_all()
        ms.flush()
        base = self.saved(session)
        ms.add_models([self.P("garbage.fbx")])                               # fails: nothing new to save
        self.wait(ms)
        ms.flush()
        self.assertEqual(self.saved(session)["sources"], base["sources"])
        ms.replace_stage([self.P("garbage.fbx"), self.P("parts/left.fbx")])  # partial: replaced with the one that worked
        ms.cancel_import()                                                   # ...but cancelled first
        pump(self.app, timeout=2.0)
        ms.flush()
        self.assertEqual(self.saved(session)["sources"], base["sources"])
        self.assertEqual(self.saved(session)["assignments"], base["assignments"])
        ms.add_models([self.P("parts/left.fbx"), self.P("garbage.fbx"), self.P("parts/a/screen.obj")])
        self.wait(ms)
        ms.flush()
        self.assertEqual([ms.resources[s["resource"]]["name"] for s in self.saved(session)["sources"]],
                         ["centre.obj", "left.fbx", "screen.obj"])
        self.assertEqual(len(os.listdir(os.path.join(session.project.folder, "models"))), 3)   # no copy of the damaged one
        ms.replace_stage([self.P("garbage.fbx")])                            # nothing usable: unchanged
        self.wait(ms)
        ms.flush()
        self.assertEqual(len(self.saved(session)["sources"]), 3)
        ms.replace_stage([self.P("parts/b/screen.obj")])
        self.wait(ms)
        ms.flush()
        doc = self.saved(session)
        self.assertEqual([s["id"] for s in doc["sources"]], ["m4"])          # a cancelled Replace took no ids
        self.assertEqual(doc["assignments"], {})

    def test_removing_a_file_keeps_its_copy_and_the_original(self):
        ms, session = self.open(os.path.join(self.tmp, "r.nitpix"))
        ms.add_models([self.P("parts/centre.obj"), self.P("parts/left.fbx")])
        self.wait(ms)
        ms.apply_all()
        kept = ms.stage.sources[0].path
        ms.remove_source(ms.stage.sources[0].id)
        ms.flush()
        doc = self.saved(session)
        self.assertEqual([s["id"] for s in doc["sources"]], ["m2"])
        self.assertTrue(all(k.startswith("m2:") for k in doc["assignments"]))
        self.assertTrue(os.path.isfile(kept))                               # nothing deleted
        self.assertTrue(os.path.isfile(self.P("parts/centre.obj")))

    # ---- restoring when something is wrong -------------------------------------------------------
    def test_a_missing_or_changed_copy_is_held_back_then_relinked(self):
        folder = os.path.join(self.tmp, "m.nitpix")
        ms, session = self.open(folder)
        ms.add_models([self.P("parts/centre.obj"), self.P("parts/left.fbx"), self.P("parts/a/screen.obj")])
        self.wait(ms)
        ms.apply_all()
        uv = dict(ms.assign.uv)
        paths = {s.name: s.path for s in ms.stage.sources}
        self.close(ms, session)
        os.remove(paths["centre.obj"])                                       # a kept copy deleted...
        with open(paths["left.fbx"], "ab") as fh:                            # ...and one changed
            fh.write(b"\0")
        ms, session = self.open(folder, new=False)
        self.assertEqual([s.name for s in ms.stage.sources], ["screen.obj"])
        self.assertEqual(sorted(ms.missing), ["m1", "m2"])
        self.assertIn("missing", ms.missing["m1"]["why"])
        self.assertIn("changed", ms.missing["m2"]["why"])
        self.assertIn("2 saved model files could not be restored", ms.result_lab.text())
        self.assertEqual(ms.objects.currentIndex(), 1)                       # listed, as missing rows
        shown = [ms.tree.topLevelItem(i).text(0) for i in range(ms.tree.topLevelItemCount()) if not ms.tree.topLevelItem(i).isHidden()]
        self.assertIn("centre.obj — missing", shown)                        # listed and visible
        self.assertIn("left.fbx — missing", shown)
        ms.view.orbit(5, 0)                                                  # an edit meanwhile: saved with them held
        ms.flush()
        doc = self.saved(session)
        self.assertEqual([s["id"] for s in doc["sources"]], ["m3", "m1", "m2"])
        self.assertEqual(doc["assignments"], uv)
        ms.relink("m1", self.P("parts/centre.obj"))                          # the same model: assignments return
        self.wait(ms)
        self.assertNotIn("m1", ms.missing)
        self.assertEqual({k: v for k, v in ms.assign.uv.items() if k.startswith("m1:")},
                         {k: v for k, v in uv.items() if k.startswith("m1:")})
        self.assertTrue(self.said[-1][0].startswith("Relinked centre.obj, with its saved assignments."), self.said[-1])
        ms.relink("m2", self.P("parts/b/screen.obj"))                        # a different model: added without them
        self.wait(ms)
        self.assertFalse(any(k.startswith("m2:") for k in ms.assign.uv))
        self.assertIn("not the model that was saved", self.said[-1][0])
        ms.flush()
        self.assertEqual(self.saved(session)["sources"][-1]["id"], "m2")

    def test_an_unreadable_saved_setup_is_kept_aside(self):
        folder = os.path.join(self.tmp, "u.nitpix")
        ms, session = self.open(folder)
        ms.add_models([self.P("parts/centre.obj")])
        self.wait(ms)
        self.close(ms, session)
        from nitpix.store import Project
        p = Project(folder)
        bad = {**p.get_meta(mapsave.KEY), "version": 99}
        p.set_meta(mapsave.KEY, bad)
        p.close()
        ms, session = self.open(folder, new=False)
        self.assertIsNone(ms.scene)
        self.assertIn("could not be read", ms.problem_lab.text())
        self.assertEqual(self.saved(session), bad)                           # untouched...
        ms.add_models([self.P("parts/left.fbx")])
        self.wait(ms)
        ms.flush()
        aside = [r["key"] for r in session.project.q("SELECT key FROM meta WHERE key LIKE ?", (mapsave.KEY + ".unreadable-%",))]
        self.assertEqual(len(aside), 1)                                      # ...then set aside before the new save
        self.assertEqual(session.project.get_meta(aside[0]), bad)
        self.assertEqual(self.saved(session)["version"], 1)

    def test_a_failed_save_is_shown_and_retried(self):
        ms, session = self.open(os.path.join(self.tmp, "s.nitpix"))
        ms.add_models([self.P("parts/centre.obj")])
        self.wait(ms)
        ms.flush()
        good = self.saved(session)
        with mock.patch.object(session.project, "set_meta", side_effect=OSError("disk full")):
            ms.apply_all()
            self.assertFalse(ms.flush())
            self.assertEqual(ms.saver.state, "error")
            self.assertEqual(ms.save_lab.text(), "Not saved")
            self.assertTrue(ms.retry_btn.isVisibleTo(ms))
            self.assertIn("could not be saved", ms.problem_lab.text())
            self.assertEqual(self.saved(session), good)                      # the last saved setup is intact
        ms.retry_btn.click()
        self.assertEqual(ms.saver.state, "saved")
        self.assertFalse(ms.retry_btn.isVisibleTo(ms))
        self.assertTrue(self.saved(session)["assignments"])

    def test_cancelled_restore_and_project_switch_mid_restore(self):
        folder = os.path.join(self.tmp, "c.nitpix")
        ms, session = self.open(folder)
        ms.add_models([self.P(r) for r in MODELS])
        self.wait(ms)
        ms.apply_all()
        self.close(ms, session)
        doc = None
        from nitpix.store import Project
        p = Project(folder)
        doc = p.get_meta(mapsave.KEY)
        p.close()
        from nitpix.ui.mapping import MappingScreen
        session = service.Session()
        session.open(folder)
        ms = MappingScreen(session)
        self.screens.append((ms, session))
        ms.activate()
        self.assertTrue(pump(self.app, lambda: len(ms.stage.sources) >= 1, 30))
        ms.cancel_import()                                                   # mid-restore
        pump(self.app, timeout=2.0)
        self.assertTrue(ms.missing)
        self.assertTrue(all("cancelled" in m["why"] for m in ms.missing.values()))
        ms.flush()
        self.assertEqual(session.project.get_meta(mapsave.KEY), doc)        # nothing changed: nothing written
        ms.retry_restore(list(ms.missing))
        self.wait(ms)
        self.assertEqual((len(ms.stage.sources), ms.missing), (6, {}))
        self.assertEqual(sorted(ms.assign.uv), sorted(doc["assignments"]))
        self.close(ms, session)

        other = os.path.join(self.tmp, "other.nitpix")                       # switch mid-restore
        session.open(folder)
        ms.activate()
        self.assertTrue(pump(self.app, lambda: ms._batch is not None, 5))
        ms.flush()
        ms.reset_project()
        session.open(other)
        ms.activate()
        pump(self.app, timeout=3.0)
        self.assertIsNone(ms.scene)                                          # no late result from the first project
        self.assertFalse(os.path.exists(os.path.join(other, "models")))
        self.assertIsNone(session.project.get_meta(mapsave.KEY))
        p = Project(folder)
        self.assertEqual(p.get_meta(mapsave.KEY)["sources"], doc["sources"])   # the interrupted one is intact
        p.close()


if __name__ == "__main__":
    unittest.main()
