"""The Mapping workspace, offscreen and without libmpv: assignment rules (nitpix/mapping.py), the
workspace's object list and controls, stage import through the helper process, and the main
window's handling of one playback session across QC and Mapping. tests/test_mapping_live.py
checks drawn pictures and frames against real libmpv.
.venv/bin/python -m unittest tests.test_mapping"""
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

    from nitpix import service, variant
    from nitpix.mapping import MIXED, Assignments
    from nitpix.scene import load_scene
    from nitpix.store import Project
    from nitpix.ui import theme
    from test_scrub import FakeMpv


def pump(app, until=None, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.01)
    return until is None


@unittest.skipUnless(HAS, "needs PySide6 and assimp-py")
class Rules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scene = load_scene(os.path.join(FIX, "stage.fbx"))

    def setUp(self):
        self.a = Assignments(self.scene)

    def test_apply_clear_and_all(self):
        a = self.a
        screens = ["Stage/Screens/Centre", "Stage/Screens/Left", "Stage/Screens/Right"]
        self.assertEqual(a.apply(screens), {})
        self.assertEqual(sorted(a.uv), screens)
        self.assertNotIn("Stage/Floor", a.uv)                                # the floor stays neutral
        a.clear(["Stage/Screens/Left"])
        self.assertEqual(sorted(a.uv), ["Stage/Screens/Centre", "Stage/Screens/Right"])
        skipped = a.apply_all()
        self.assertEqual(skipped, {})
        self.assertNotIn("Stage/Truss", a.uv)                                # no UVs: never assigned
        self.assertEqual(len(a.uv), len([o for o in self.scene.objects if o.uv_sets]))

    def test_objects_without_uvs_are_refused_not_given_coordinates(self):
        self.assertEqual(self.a.apply(["Stage/Truss"]), {"Stage/Truss": "no UVs"})
        self.assertIsNone(self.a.warning("Stage/Truss"))                    # nothing assigned, nothing to warn about
        self.assertIn("No UVs", self.a.details("Stage/Truss"))

    def test_uv_sets_and_mixed_selections(self):
        a = self.a
        centre, left = "Stage/Screens/Centre", "Stage/Screens/Left"
        self.assertEqual(a.uv_choices([centre]), [0, 1])                     # Centre has a second set
        self.assertEqual(a.uv_choices([centre, left]), [0])                  # only what both have
        self.assertEqual(a.apply([centre, left], 1), {left: "has no UV set 2"})
        self.assertEqual(a.uv[centre], 1)
        self.assertNotIn(left, a.uv)
        a.apply([left], 0)
        self.assertIs(a.uv_state([centre, left]), MIXED)
        self.assertIs(a.assigned_state([centre, "Stage/Floor"]), MIXED)
        a.apply([centre, left])                                              # None keeps each object's own set
        self.assertEqual((a.uv[centre], a.uv[left]), (1, 0))

    def test_out_of_range_follows_the_chosen_uv_set(self):
        a = self.a
        self.assertTrue(a.outside("Stage/Spill"))                            # authored -0.1..1.1
        self.assertIsNone(a.warning("Stage/Spill"))                          # unassigned: details only
        self.assertIn("U -0.1 to 1.1", a.details("Stage/Spill"))
        a.apply(["Stage/Spill"])
        self.assertEqual(a.warning("Stage/Spill"), "partly outside image (31% neutral)")   # 1 - (1/1.2)², not every triangle
        self.assertFalse(a.outside("Stage/Screens/Centre", 0))
        self.assertFalse(a.outside("Stage/Screens/Centre", 1))
        self.assertFalse(a.outside("Stage/Truss"))


@unittest.skipUnless(HAS, "needs PySide6 and assimp-py")
class UVWarnings(unittest.TestCase):
    """uvedge.fbx: warnings follow assignment and the UV set in use; export noise is no warning."""

    @classmethod
    def setUpClass(cls):
        cls.scene = load_scene(os.path.join(FIX, "uvedge.fbx"))

    def setUp(self):
        self.a = Assignments(self.scene)

    def test_noise_at_the_edge_is_on_the_image(self):
        self.a.apply(["Noise"])
        self.assertFalse(self.a.outside("Noise"))
        self.assertIsNone(self.a.warning("Noise"))

    def test_assigned_objects_are_warned_with_the_share_shown_neutral(self):
        a = self.a
        a.apply(["Beyond", "Islands", "Tiles"])
        self.assertEqual(a.warning("Beyond"), "partly outside image (<1% neutral)")
        self.assertEqual(a.warning("Islands"), "partly outside image (50% neutral)")
        self.assertEqual(a.warning("Tiles"), "partly outside image (88% neutral)")
        d = a.details("Islands")
        self.assertIn("U -0.8 to 1.7", d)
        self.assertIn("up to 0.8 beyond the image", d)
        self.assertIn("4 of 8 triangles", d)

    def test_unassigned_tiled_object_is_not_a_problem(self):
        self.assertTrue(self.a.outside("Tiles"))
        self.assertIsNone(self.a.warning("Tiles"))
        self.assertIn("U -1.69 to 2.74", self.a.details("Tiles"))

    def test_the_uv_set_in_use_decides(self):
        a = self.a
        a.apply(["Sets"], 0)
        self.assertEqual(a.warning("Sets"), "partly outside image (87% neutral)")      # tiled 0-4 x 0-2: 7/8
        a.apply(["Sets"], 1)
        self.assertIsNone(a.warning("Sets"))                                           # "Fit" is all on the image
        self.assertTrue(a.outside("Sets", 0))                                          # the other set is still described
        self.assertIn("all on the image", a.details("Sets"))

    def test_shares_never_round_to_none_or_all(self):
        from nitpix.mapping import share
        self.assertEqual([share(x) for x in (0, 0.001, 0.5, 0.996, 1)], ["0%", "<1%", "50%", ">99%", "100%"])


@unittest.skipUnless(HAS, "needs PySide6 and assimp-py")
class Workspace(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.settings_dir = tempfile.mkdtemp(prefix="nitpix settings ")
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)  # keep tests out of the real preferences
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, cls.settings_dir)
        cls.app.setStyleSheet(theme.STYLESHEET)
        cls.tmp = tempfile.mkdtemp(prefix="nitpix mapping ")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def setUp(self):
        from nitpix.ui.mapping import MappingScreen
        self.session = service.Session()
        self.session.project = p = Project(tempfile.mkdtemp(dir=self.tmp) + "/m.nitpix")
        self.aid = p.upsert_asset("/m/a.mov", kind="video", name="a.mov", fps="25/1", frames=100, duration=4.0, scan_state="done")
        p.upsert_asset("/m/b.wav", kind="audio", name="b.wav", scan_state="done")
        self.ms = MappingScreen(self.session)
        self.ms.resize(1300, 800)
        self.ms.show()
        self.said = []
        self.ms.status.connect(lambda t, e: self.said.append((t, e)))

    def tearDown(self):
        self.ms.shutdown()
        self.ms.close()
        self.ms.deleteLater()
        self.session.project.close()
        pump(self.app, timeout=0.05)

    def import_(self, name):
        self.ms.import_scene(os.path.join(FIX, name))
        self.assertTrue(pump(self.app, lambda: not self.ms.importer.busy and self.ms.import_btn.isEnabled(), 30))
        pump(self.app, timeout=0.05)

    def test_empty_then_importing_then_objects(self):
        ms = self.ms
        self.assertEqual(ms.objects.currentIndex(), 0)                       # what to do next, with its own button
        self.assertIn("Import an OBJ or FBX", ms.empty_lab.text())
        self.assertTrue(ms.empty_import.isVisibleTo(ms))
        self.assertFalse(ms.apply_btn.isEnabled())
        self.assertFalse(ms.apply_btn.property("primary"))                   # no strong style on a button that cannot act
        ms.import_scene(os.path.join(FIX, "stage.fbx"))
        self.assertIn("Importing stage.fbx", ms.empty_lab.text())
        self.assertTrue(ms.cancel_btn.isVisibleTo(ms))
        self.assertTrue(pump(self.app, lambda: not ms.importer.busy and ms.import_btn.isEnabled(), 30))
        pump(self.app, timeout=0.05)
        self.assertEqual(ms.objects.currentIndex(), 1)
        self.assertEqual(ms.all_btn.text(), "Apply to all 12 with UVs")

    def test_a_scene_that_cannot_be_used_says_why_where_the_objects_would_be(self):
        self.import_("garbage.fbx")
        self.assertEqual(self.ms.objects.currentIndex(), 0)
        self.assertIn("could not be used", self.ms.empty_lab.text())

    def test_a_failed_or_cancelled_reimport_keeps_the_stage_and_its_list(self):
        ms = self.ms
        self.import_("stage.fbx")
        ms.select(["Stage/Floor"])
        ms.apply_selected()
        self.import_("garbage.fbx")                                          # damaged: refused
        self.assertEqual(ms.objects.currentIndex(), 1)                       # the stage's objects, not an error page
        self.assertTrue(ms.scene_lab.text().startswith("stage.fbx: 13 objects"), ms.scene_lab.text())
        self.assertTrue(self.said[-1][1] and self.said[-1][0].startswith("garbage.fbx: "), self.said[-1])
        self.assertEqual(list(ms.assign.uv), ["Stage/Floor"])
        ms.import_scene(os.path.join(FIX, "stage.obj"))
        ms.cancel_import()                                                   # while it is still running
        pump(self.app, timeout=1.0)
        self.assertEqual((ms.objects.currentIndex(), ms.scene_lab.text()[:9]), (1, "stage.fbx"))
        self.assertEqual(list(ms.assign.uv), ["Stage/Floor"])

    def test_a_stage_of_many_objects_lists_and_selects_quickly(self):
        """20,000 objects (real show models reach 135,000). Finding objects by key and selecting
        them row by row were both quadratic: minutes, and gigabytes, for a big stage."""
        from array import array
        from nitpix.scene import Geometry, NormalizedScene, SceneObject, uv_range
        n = 20000
        pos, idx, uv = array("f", [0, 0, 0, 1, 0, 0, 0, 1, 0]), array("I", [0, 1, 2]), array("f", [0, 0, 1, 0, 0, 1])
        g = Geometry("mesh:0", pos, idx, (uv,), "Screen", "f", (uv_range(uv, idx, pos),))
        world = tuple(float(i == j) for i in range(4) for j in range(4))
        objs = tuple(SceneObject(f"Rig/Panel_{i:05d}", ("Rig", f"Panel_{i:05d}"), f"Panel_{i:05d}", ("mesh:0",), ("Screen",),
                                 world, 1, 1, False, None, "f") for i in range(n))
        scene = NormalizedScene({"path": "/generated"}, "test", objs, {"mesh:0": g}, ((0, 0, 0), (1, 1, 0)))
        ms = self.ms
        t = time.monotonic()
        ms._set_scene(scene, "many.obj")
        ms.select_shown()
        ms.apply_selected()
        ms.filter.setText("Panel_1999")
        ms.select_shown()
        took = time.monotonic() - t
        self.assertEqual(len(ms.assign.uv), n)
        self.assertEqual(sorted(ms.selected_keys()), [f"Rig/Panel_1999{d}" for d in range(10)])
        self.assertLess(took, 20)                                           # was many minutes

    def test_filter_keeps_selection_and_assignments(self):
        ms = self.ms
        self.import_("stage.fbx")
        ms.select(["Stage/Floor"])
        ms.filter.setText("scre")
        shown = sorted(it.data(0, 0x0100) for it in ms._rows() if not ms._hidden(it))
        self.assertEqual(shown, ["Stage/Screens/Centre", "Stage/Screens/Left", "Stage/Screens/Right"])   # by path, too
        self.assertEqual(ms.selected_keys(), ["Stage/Floor"])               # still selected...
        self.assertIn("1 hidden by the filter", ms.sel_lab.text())           # ...and it says so
        ms.select_shown()
        self.assertEqual(sorted(ms.selected_keys()), shown)
        self.assertEqual(ms.apply_btn.text(), "Apply clip to 3 selected")
        ms.apply_selected()
        ms.filter.clear()
        self.assertTrue(all(not ms._hidden(it) for it in ms._rows()))
        self.assertEqual(sorted(ms.assign.uv), shown)
        self.assertEqual(ms.clear_btn.text(), "Clear 3")

    def test_inspector_names_the_clip_and_warns_for_the_selection(self):
        ms = self.ms
        self.import_("stage.fbx")
        path = os.path.join(self.tmp, "real clip.mov")
        open(path, "wb").close()                                             # present on disk; never decoded here
        aid = self.session.project.upsert_asset(path, kind="video", name="real clip.mov", fps="25/1", frames=50, duration=2.0, scan_state="done")
        ms.refresh_media()
        ms._choose_media(aid)
        self.assertEqual(ms.clip_lab.text(), "Clip: real clip.mov")
        ms.select(["Stage/Spill"])
        self.assertIn("about 31% of this surface would show neutral", ms.warn_lab.text())
        self.assertFalse(ms.warn_lab.isHidden())
        ms.apply_selected()
        self.assertIn("shows neutral", ms.warn_lab.text())
        self.assertIn("U -0.1 to 1.1", ms.warn_lab.toolTip())
        rows = {it.data(0, 0x0100): it for it in ms._rows()}
        self.assertEqual(rows["Stage/Spill"].text(1), "● UV 1 ⚠")
        ms.select(["Stage/Screens/Centre"])
        self.assertTrue(ms.warn_lab.isHidden())                              # nothing to say about a clean selection
        ms.select(["Stage/Screens/Centre", "Stage/Spill", "Stage/Floor"])
        self.assertIn("1 show the clip", ms.sel_lab.text())

    def test_media_list_offers_visual_media_only(self):
        self.ms.refresh_media()
        self.assertEqual([self.ms.media_box.itemText(i) for i in range(self.ms.media_box.count())], ["a.mov"])

    def test_import_list_assign_and_clear(self):
        ms = self.ms
        self.import_("stage.fbx")
        self.assertIsNotNone(ms.scene)
        rows = {it.data(0, 0x0100): it for it in ms._rows()}
        self.assertEqual(rows["Stage/Logo_A#2"].text(0), "Logo_A (2)")       # duplicates told apart
        self.assertEqual(rows["Stage/Truss"].text(1), "no UVs")
        self.assertEqual(rows["Stage/Spill"].text(1), "")                    # unassigned: no warning...
        self.assertIn("U -0.1 to 1.1", rows["Stage/Spill"].toolTip(0))       # ...the numbers in its tooltip
        ms.select(["Stage/Screens/Centre", "Stage/Screens/Left", "Stage/Screens/Right"])
        self.assertTrue(ms.apply_btn.isEnabled())
        ms.apply_selected()
        self.assertEqual(sorted(ms.view.assigned), ["Stage/Screens/Centre", "Stage/Screens/Left", "Stage/Screens/Right"])
        self.assertEqual(rows["Stage/Screens/Centre"].text(1), "● UV 1")
        self.assertEqual(rows["Stage/Floor"].text(1), "")
        ms.select(["Stage/Screens/Left"])
        ms.clear_selected()
        self.assertNotIn("Stage/Screens/Left", ms.view.assigned)
        ms.select(["Stage/Screens/Centre", "Stage/Truss"])
        self.assertIn("without UVs", ms.sel_lab.text())

    def test_mixed_uv_sets_show_as_mixed(self):
        ms = self.ms
        self.import_("stage.fbx")
        ms.select(["Stage/Screens/Centre"])
        ms.uv_box.setCurrentIndex(ms.uv_box.findData(1))
        ms.apply_selected()
        ms.select(["Stage/Screens/Left"])
        ms.apply_selected()
        ms.select(["Stage/Screens/Centre", "Stage/Screens/Left"])
        self.assertEqual(ms.uv_box.itemText(0), "Mixed: choose to set all")
        ms.uv_box.setCurrentIndex(ms.uv_box.findData(0))
        ms._on_uv_chosen(0)
        self.assertEqual((ms.assign.uv["Stage/Screens/Centre"], ms.assign.uv["Stage/Screens/Left"]), (0, 0))

    def test_group_rows_are_not_selectable(self):
        self.import_("stage.fbx")
        from PySide6.QtCore import Qt
        stage = self.ms.tree.topLevelItem(0)
        self.assertEqual(stage.text(0), "Stage")
        self.assertFalse(stage.flags() & Qt.ItemFlag.ItemIsSelectable)

    def test_import_notes_and_errors_are_explained(self):
        d = tempfile.mkdtemp(dir=self.tmp)
        mixed, wire = os.path.join(d, "mixed.obj"), os.path.join(d, "wire.obj")
        with open(mixed, "w") as fh:
            fh.write("o Screen\nv 0 0 0\nv 1 0 0\nv 1 1 0\nvt 0 0\nvt 1 0\nvt 1 1\nf 1/1 2/2 3/3\no Wire\nv 0 0 1\nv 1 0 1\nl 4 5\n")
        with open(wire, "w") as fh:
            fh.write("o Wire\nv 0 0 1\nv 1 0 1\nl 1 2\n")
        self.ms.import_scene(wire)                                            # lines only: refused, and why
        self.assertTrue(pump(self.app, lambda: self.said, 30))
        self.assertEqual(self.said[-1], ("wire.obj: This file has only lines or points: no surfaces to show media on.", True))
        self.ms.import_scene(mixed)                                           # surfaces too: imported, lines noted
        self.assertTrue(pump(self.app, lambda: self.ms.scene is not None, 30))
        pump(self.app, timeout=0.05)
        self.assertEqual(self.said[-1], ("1 line or point element ignored (only surfaces can show media).", False))
        self.assertEqual([o.display for o in self.ms.scene.objects], ["Screen"])
        self.ms._set_scene(None, "")
        self.import_("garbage.fbx")
        self.assertIn("Could not read this scene", self.said[-1][0])
        self.assertIsNone(self.ms.scene)

    def test_a_project_change_drops_an_import_in_flight(self):
        ms = self.ms
        ms.import_scene(os.path.join(FIX, "stage.fbx"))
        ms.reset_project()                           # before the helper answers
        pump(self.app, timeout=2.0)
        self.assertIsNone(ms.scene)
        self.assertTrue(ms.import_btn.isEnabled())


@unittest.skipUnless(HAS, "needs PySide6 and assimp-py")
class OneSession(unittest.TestCase):
    """MainWindow (release build unless stated): Mapping in the sidebar; one player has a file at a time; switching keeps frames."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.settings_dir = tempfile.mkdtemp(prefix="nitpix settings ")
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, cls.settings_dir)
        cls.app.setStyleSheet(theme.STYLESHEET)
        cls.tmp = tempfile.mkdtemp(prefix="nitpix mapping session ")

    def window(self, experimental=False, legacy=None):
        from nitpix.ui.app import MainWindow
        if experimental:
            for name, value in (("IS_EXPERIMENTAL", True), ("VARIANT", "exp"), ("SETTINGS", ("nitpix-exp", "nitpix-exp"))):
                p = mock.patch.object(variant, name, value)
                p.start()
                self.addCleanup(p.stop)
        from nitpix.ui.prefs import prefs
        prefs().clear()                                                      # fresh preferences...
        if legacy is not None:
            prefs().setValue("mapping/enabled", legacy)                      # ...or ones from when Mapping was a setting
        w = MainWindow(service.Session())
        self.addCleanup(lambda: (w.close(), w.deleteLater()))
        d = tempfile.mkdtemp(dir=self.tmp)
        w.open_project(d + "/s.nitpix")
        p = w.session.project
        self.paths = {}
        for name, frames in (("a.mov", 100), ("b.mov", 50)):
            self.paths[name] = path = os.path.join(d, name)
            open(path, "wb").close()                                         # present on disk; never decoded here
            aid = p.upsert_asset(path, kind="video", name=name, fps="25/1", frames=frames, duration=frames / 25, scan_state="done")
            setattr(self, name[0], aid)
        return w

    @staticmethod
    def fake(player, path):
        """What file-loaded does once libmpv has the file (the stand-in paths are not real)."""
        player.m = player.m or FakeMpv()
        player._pending = None
        player.target, player.loaded = path, True
        player.m.path = path

    def nav_order(self, w):
        lay = w.nav["review"].parentWidget().layout()
        pos = {lay.indexOf(b): k for k, b in w.nav.items()}
        return [pos[i] for i in sorted(pos)]

    def test_release_build_shows_mapping_beside_review(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from nitpix.ui.app import VIEWS
        from nitpix.ui.shortcuts import SECTIONS
        w = self.window()
        w.show()
        pump(self.app, timeout=0.05)
        self.assertFalse(variant.IS_EXPERIMENTAL)
        self.assertEqual(self.nav_order(w), ["dashboard", "media", "review", "mapping", "presets", "reports", "settings"])
        self.assertTrue(all(b.isVisible() for b in w.nav.values()))
        self.assertIsNone(w.mapping)                                         # nothing made until it is opened
        w.nav["mapping"].click()
        self.assertEqual(w.current_view, "mapping")
        self.assertTrue(w.nav["mapping"].isChecked())
        self.assertIsNotNone(w.mapping)
        self.assertEqual(VIEWS, ["dashboard", "media", "review", "presets", "reports", "settings"])   # number keys unchanged
        self.assertEqual(w.nav["presets"].toolTip(), "Specs (4)")
        self.assertNotRegex(w.nav["mapping"].toolTip(), r"\(\d\)")
        self.assertEqual(SECTIONS["mapping"][0], "Mapping")
        w.dashboard.setFocus()
        QTest.keyClick(w.dashboard, Qt.Key.Key_4)
        self.assertEqual(w.current_view, "presets")
        w.set_rail_collapsed(True)                                           # icon strip: same order, icon only
        self.assertEqual((w.nav["mapping"].text(), w.nav["mapping"].icon().isNull()), ("", False))
        w.set_rail_collapsed(False)
        self.assertEqual(w.nav["mapping"].text(), "Mapping")
        self.assertFalse(hasattr(w.settings_screen, "mapping_box"))          # no setting to find first

    def test_an_old_mapping_off_preference_hides_nothing(self):
        w = self.window(legacy=False)
        self.assertTrue(w.nav["mapping"].isVisibleTo(w))
        w.show_view("mapping")
        self.assertEqual(w.current_view, "mapping")

    def test_without_a_project_mapping_leads_to_the_start(self):
        w = self.window()
        w.close_project()
        w.show_view("mapping")
        self.assertEqual(w.current_view, "start")
        self.assertIsNone(w.mapping)

    def test_experimental_builds_have_it_too(self):
        w = self.window(experimental=True)
        self.assertEqual(self.nav_order(w)[3], "mapping")
        w.show_view("mapping")
        self.assertEqual(w.current_view, "mapping")

    def test_switching_hands_over_one_session_and_keeps_the_frame(self):
        w = self.window()
        w.ensure_mapping()
        rv, ms = w.review, w.mapping
        ms.view.m = FakeMpv()                                                # the libmpv stand-in for Mapping's player too
        w.show_asset(self.a)
        rv.viewer_stack.setCurrentWidget(rv.player)
        self.fake(rv.player, self.paths["a.mov"])
        rv.scrub(60)                                                         # a scrub still pending...
        self.assertTrue(rv.playhead.pending)
        w.show_view("mapping")                                               # ...when the user switches
        self.assertTrue(rv.released)
        self.assertFalse(rv.player.loaded)                                   # QC's player let go: no decoding, no sound
        self.assertFalse(rv.playhead.pending)
        self.assertEqual(w.session.project.asset(self.a)["last_frame"], 60)
        self.assertEqual(ms.asset["id"], self.a)
        self.assertEqual(ms.playhead.frame, 60)                              # Mapping opens on the requested frame
        self.assertEqual(ms.view._pending[2], 60)                            # and loads its player there
        self.fake(ms.view, self.paths["a.mov"])
        ms.playhead.scrub(75)
        w.show_view("review")                                                # back, with Mapping's scrub pending
        self.assertFalse(ms.view.loaded)
        self.assertFalse(ms.playhead.pending)
        self.assertFalse(rv.released)
        self.assertEqual((rv.asset["id"], rv.frame), (self.a, 75))           # the same asset id, loaded again
        self.assertIsNotNone(rv.player._pending)                             # a fresh load was requested
        self.assertEqual(rv.player._pending[2], 75)

    def test_changing_the_mapped_clip_and_leaving_via_another_screen(self):
        w = self.window()
        w.ensure_mapping()
        w.show_asset(self.a)
        w.show_view("mapping")
        ms = w.mapping
        ms._choose_media(self.b)
        self.assertEqual(ms.asset["id"], self.b)
        w.show_view("dashboard")                                             # not QC: Mapping still lets go
        self.assertFalse(ms.active)
        w.show_view("review")                                                # QC then shows the mapped clip
        self.assertEqual(w.review.asset["id"], self.b)

    def test_typing_in_the_filter_never_reaches_the_transport(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        w = self.window()
        w.ensure_mapping()
        w.show_asset(self.a)
        w.show_view("mapping")
        ms = w.mapping
        ms._set_scene(load_scene(os.path.join(FIX, "stage.fbx")), "stage.fbx")
        toggles = []
        ms.toggle_play = lambda: toggles.append(1)
        ms.view.frame_all = lambda *a, **k: toggles.append("f")
        ms.filter.setFocus()
        pump(self.app, timeout=0.05)
        QTest.keyClicks(ms.filter, "f ")
        QTest.keyClick(ms.filter, Qt.Key.Key_Left)
        self.assertEqual((ms.filter.text(), toggles), ("f ", []))
        QTest.keyClick(ms.filter, Qt.Key.Key_Escape)                         # clears and leaves the field
        self.assertEqual(ms.filter.text(), "")
        ms.tree.setFocus()
        pump(self.app, timeout=0.05)
        QTest.keyClick(ms.tree, Qt.Key.Key_Space)                            # elsewhere the keys still work
        QTest.keyClick(ms.tree, Qt.Key.Key_F)
        self.assertEqual(toggles, [1, "f"])

    def test_space_on_the_closed_clip_or_uv_selector_plays(self):
        """Through the window's key routing, focus left on a selector after choosing from it."""
        from PySide6.QtCore import QEvent, Qt
        from PySide6.QtGui import QKeyEvent
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QDialog
        w = self.window()
        w.show()
        w.show_asset(self.a)
        w.show_view("mapping")
        ms = w.mapping
        ms._set_scene(load_scene(os.path.join(FIX, "stage.fbx")), "stage.fbx")
        ms.select(["Stage/Screens/Centre"])                                  # two UV sets: the UV selector is live
        toggles = []
        ms.toggle_play = lambda: toggles.append(1)

        def focus(widget):                                                   # offscreen, a closed popup can keep focus
            w.activateWindow()
            widget.setFocus()
            pump(self.app, timeout=0.05)
            self.assertIs(QApplication.focusWidget(), widget)

        for box in (ms.media_box, ms.uv_box):
            with self.subTest(box=box.toolTip()[:20]):
                toggles.clear()
                focus(box)
                index = box.currentIndex()
                QTest.keyClick(box, Qt.Key.Key_Space)
                self.assertEqual(toggles, [1])                               # one press, one toggle
                self.assertFalse(box.view().isVisible())                     # the list did not open
                self.assertEqual(box.currentIndex(), index)
                held = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier, " ", True)
                QApplication.sendEvent(box, held)                            # auto-repeat while held: no flicker
                self.assertEqual(toggles, [1])
                QTest.keyClick(box, Qt.Key.Key_Space, Qt.KeyboardModifier.ShiftModifier)   # not plain Space: the selector's
                self.assertEqual(toggles, [1])
                box.hidePopup()
                pump(self.app, timeout=0.05)
        toggles.clear()
        focus(ms.filter)
        QTest.keyClicks(ms.filter, "a b")                                    # text fields keep Space
        self.assertEqual((ms.filter.text(), toggles), ("a b", []))
        ms.filter.clear()
        dlg = QDialog(w)
        dlg.setModal(True)
        dlg.show()
        pump(self.app, timeout=0.05)
        QTest.keyClick(ms.media_box, Qt.Key.Key_Space)                       # a dialog on top keeps the keys
        self.assertEqual(toggles, [])
        dlg.close()
        ms.media_box.hidePopup()                                             # (sent straight to it, it opened)
        pump(self.app, timeout=0.05)
        for target in (ms.tree, ms.play_btn):                                # as before: tree and buttons
            focus(target)
            QTest.keyClick(target, Qt.Key.Key_Space)
        self.assertEqual(toggles, [1, 1])
        w.show_view("review")                                                # QC keeps its own Space
        rv_toggles = []
        w.review.handle_key = lambda ev: (rv_toggles.append(ev.key()), True)[1]
        focus(w.review)
        QTest.keyClick(w.review, Qt.Key.Key_Space)
        self.assertEqual((rv_toggles, toggles), ([Qt.Key.Key_Space], [1, 1]))
        w.show_view("mapping")
        toggles.clear()
        focus(ms.media_box)
        ms.media_box.showPopup()                                             # an open list keeps its keys (last:
        pump(self.app, timeout=0.05)                                         # offscreen, focus stays in the popup)
        QTest.keyClick(ms.media_box.view(), Qt.Key.Key_Space)
        self.assertEqual(toggles, [])
        ms.media_box.hidePopup()
        pump(self.app, timeout=0.05)
        focus(ms.tree)                                                       # leave focus usable for later tests

    def test_close_project_resets_mapping(self):
        w = self.window()
        w.ensure_mapping()
        w.show_view("mapping")
        w.mapping._set_scene(load_scene(os.path.join(FIX, "stage.obj")), "stage.obj")
        w.close_project()
        self.assertIsNone(w.mapping.scene)
        self.assertEqual(w.current_view, "start")
        self.assertFalse(w.review.released)


if __name__ == "__main__":
    unittest.main()
