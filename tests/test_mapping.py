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
class _Screen(unittest.TestCase):
    """A MappingScreen on a throwaway project with one clip."""
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

    def import_(self, *names, replace=False):
        (self.ms.replace_stage if replace else self.ms.add_models)([n if os.path.isabs(n) else os.path.join(FIX, n) for n in names])
        self.assertTrue(pump(self.app, lambda: self.ms._batch is None, 30))
        pump(self.app, timeout=0.05)


@unittest.skipUnless(HAS, "needs PySide6 and assimp-py")
class Workspace(_Screen):
    def test_empty_then_importing_then_objects(self):
        ms = self.ms
        self.assertEqual(ms.objects.currentIndex(), 0)                       # what to do next, with its own button
        self.assertIn("Add one or more OBJ or FBX model files", ms.empty_lab.text())
        self.assertFalse(ms.replace_btn.isEnabled())                         # nothing to replace yet
        self.assertTrue(ms.empty_import.isVisibleTo(ms))
        self.assertFalse(ms.apply_btn.isEnabled())
        self.assertFalse(ms.apply_btn.property("primary"))                   # no strong style on a button that cannot act
        ms.add_models([os.path.join(FIX, "stage.fbx")])
        self.assertIn("Adding stage.fbx", ms.empty_lab.text())
        self.assertTrue(ms.cancel_btn.isVisibleTo(ms))
        self.assertTrue(pump(self.app, lambda: ms._batch is None, 30))
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
        ms.select(["m1:Stage/Floor"])
        ms.apply_selected()
        self.import_("garbage.fbx", replace=True)                            # damaged: refused
        self.assertEqual(ms.objects.currentIndex(), 1)                       # the stage's objects, not an error page
        self.assertTrue(ms.scene_lab.text().startswith("stage.fbx: 13 objects"), ms.scene_lab.text())
        self.assertTrue(self.said[-1][1] and self.said[-1][0].startswith(
            "The stage is unchanged: none of the files could be used. garbage.fbx could not be used: "), self.said[-1])
        self.assertEqual(list(ms.assign.uv), ["m1:Stage/Floor"])
        ms.replace_stage([os.path.join(FIX, "stage.obj")])
        ms.cancel_import()                                                   # while it is still running
        pump(self.app, timeout=1.0)
        self.assertEqual((ms.objects.currentIndex(), ms.scene_lab.text()[:9]), (1, "stage.fbx"))
        self.assertEqual(list(ms.assign.uv), ["m1:Stage/Floor"])

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
        ms._replace_with([scene])
        ms.select_shown()
        ms.apply_selected()
        ms.filter.setText("Panel_1999")
        ms.select_shown()
        took = time.monotonic() - t
        self.assertEqual(len(ms.assign.uv), n)
        self.assertEqual(sorted(ms.selected_keys()), [f"m1:Rig/Panel_1999{d}" for d in range(10)])
        self.assertLess(took, 20)                                           # was many minutes

    def test_filter_keeps_selection_and_assignments(self):
        ms = self.ms
        self.import_("stage.fbx")
        ms.select(["m1:Stage/Floor"])
        ms.filter.setText("scre")
        shown = sorted(it.data(0, 0x0100) for it in ms._rows() if not ms._hidden(it))
        self.assertEqual(shown, ["m1:Stage/Screens/Centre", "m1:Stage/Screens/Left", "m1:Stage/Screens/Right"])   # by path, too
        self.assertEqual(ms.selected_keys(), ["m1:Stage/Floor"])               # still selected...
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
        ms.select(["m1:Stage/Spill"])
        self.assertIn("about 31% of this surface would show neutral", ms.warn_lab.text())
        self.assertFalse(ms.warn_lab.isHidden())
        ms.apply_selected()
        self.assertIn("shows neutral", ms.warn_lab.text())
        self.assertIn("U -0.1 to 1.1", ms.warn_lab.toolTip())
        rows = {it.data(0, 0x0100): it for it in ms._rows()}
        self.assertEqual(rows["m1:Stage/Spill"].text(1), "● UV 1 ⚠")
        ms.select(["m1:Stage/Screens/Centre"])
        self.assertTrue(ms.warn_lab.isHidden())                              # nothing to say about a clean selection
        ms.select(["m1:Stage/Screens/Centre", "m1:Stage/Spill", "m1:Stage/Floor"])
        self.assertIn("1 show the clip", ms.sel_lab.text())

    def test_media_list_offers_visual_media_only(self):
        self.ms.refresh_media()
        self.assertEqual([self.ms.media_box.itemText(i) for i in range(self.ms.media_box.count())], ["a.mov"])

    def test_import_list_assign_and_clear(self):
        ms = self.ms
        self.import_("stage.fbx")
        self.assertIsNotNone(ms.scene)
        rows = {it.data(0, 0x0100): it for it in ms._rows()}
        self.assertEqual(rows["m1:Stage/Logo_A#2"].text(0), "Logo_A (2)")       # duplicates told apart
        self.assertEqual(rows["m1:Stage/Truss"].text(1), "no UVs")
        self.assertEqual(rows["m1:Stage/Spill"].text(1), "")                    # unassigned: no warning...
        self.assertIn("U -0.1 to 1.1", rows["m1:Stage/Spill"].toolTip(0))       # ...the numbers in its tooltip
        ms.select(["m1:Stage/Screens/Centre", "m1:Stage/Screens/Left", "m1:Stage/Screens/Right"])
        self.assertTrue(ms.apply_btn.isEnabled())
        ms.apply_selected()
        self.assertEqual(sorted(ms.view.assigned), ["m1:Stage/Screens/Centre", "m1:Stage/Screens/Left", "m1:Stage/Screens/Right"])
        self.assertEqual(rows["m1:Stage/Screens/Centre"].text(1), "● UV 1")
        self.assertEqual(rows["m1:Stage/Floor"].text(1), "")
        ms.select(["m1:Stage/Screens/Left"])
        ms.clear_selected()
        self.assertNotIn("m1:Stage/Screens/Left", ms.view.assigned)
        ms.select(["m1:Stage/Screens/Centre", "m1:Stage/Truss"])
        self.assertIn("without UVs", ms.sel_lab.text())

    def test_mixed_uv_sets_show_as_mixed(self):
        ms = self.ms
        self.import_("stage.fbx")
        ms.select(["m1:Stage/Screens/Centre"])
        ms.uv_box.setCurrentIndex(ms.uv_box.findData(1))
        ms.apply_selected()
        ms.select(["m1:Stage/Screens/Left"])
        ms.apply_selected()
        ms.select(["m1:Stage/Screens/Centre", "m1:Stage/Screens/Left"])
        self.assertEqual(ms.uv_box.itemText(0), "Mixed: choose to set all")
        ms.uv_box.setCurrentIndex(ms.uv_box.findData(0))
        ms._on_uv_chosen(0)
        self.assertEqual((ms.assign.uv["m1:Stage/Screens/Centre"], ms.assign.uv["m1:Stage/Screens/Left"]), (0, 0))

    def test_group_rows_are_not_selectable(self):
        self.import_("stage.fbx")
        from PySide6.QtCore import Qt
        file = self.ms.tree.topLevelItem(0)
        self.assertEqual(file.text(0), "stage.fbx")                          # the model file, then its own hierarchy
        stage = file.child(0)
        self.assertEqual(stage.text(0), "Stage")
        for row in (file, stage):
            self.assertFalse(row.flags() & Qt.ItemFlag.ItemIsSelectable)

    def test_import_notes_and_errors_are_explained(self):
        d = tempfile.mkdtemp(dir=self.tmp)
        mixed, wire = os.path.join(d, "mixed.obj"), os.path.join(d, "wire.obj")
        with open(mixed, "w") as fh:
            fh.write("o Screen\nv 0 0 0\nv 1 0 0\nv 1 1 0\nvt 0 0\nvt 1 0\nvt 1 1\nf 1/1 2/2 3/3\no Wire\nv 0 0 1\nv 1 0 1\nl 4 5\n")
        with open(wire, "w") as fh:
            fh.write("o Wire\nv 0 0 1\nv 1 0 1\nl 1 2\n")
        self.ms.add_models([wire])                                            # lines only: refused, and why
        self.assertTrue(pump(self.app, lambda: self.said, 30))
        self.assertEqual(self.said[-1], ("wire.obj could not be used: This file has only lines or points: no surfaces to show media on.", True))
        self.ms.add_models([mixed])                                           # surfaces too: imported, lines noted
        self.assertTrue(pump(self.app, lambda: self.ms.scene is not None, 30))
        pump(self.app, timeout=0.05)
        self.assertEqual(self.said[-1], ("Added mixed.obj. mixed.obj: 1 line or point element ignored (only surfaces can show media).", False))
        self.assertEqual([o.display for o in self.ms.scene.objects], ["Screen"])
        self.ms._replace_with([])
        self.import_("garbage.fbx")
        self.assertIn("Could not read this scene", self.said[-1][0])
        self.assertIsNone(self.ms.scene)

    def test_a_project_change_drops_an_import_in_flight(self):
        ms = self.ms
        ms.add_models([os.path.join(FIX, "stage.fbx")])
        ms.reset_project()                           # before the helper answers
        pump(self.app, timeout=2.0)
        self.assertIsNone(ms.scene)
        self.assertTrue(ms.add_btn.isEnabled())


def part(name):
    return os.path.join(FIX, "parts", name)


@unittest.skipUnless(HAS, "needs PySide6 and assimp-py")
class Composition(_Screen):
    """One stage from several model files: Add keeps what is there, Replace swaps only on success,
    cancel and failures are reported per file, and removing a file takes only its objects."""
    def wait(self):
        self.assertTrue(pump(self.app, lambda: self.ms._batch is None, 60))
        pump(self.app, timeout=0.05)

    def keys(self, label):
        src = next(s for s in self.ms.stage.sources if self.ms.stage.label(s) == label)
        return [o.key for o in src.objects]

    def test_adding_keeps_the_stage_assignments_clip_and_camera(self):
        ms = self.ms
        clip = os.path.join(self.tmp, "clip.mov")
        open(clip, "wb").close()
        aid = self.session.project.upsert_asset(clip, kind="video", name="clip.mov", fps="25/1", frames=200, duration=8.0, scan_state="done")
        ms.refresh_media()
        ms._choose_media(aid)
        ms.playhead.begin(200, 77, new=True)
        self.import_(part("centre.obj"))
        self.assertGreater(ms.view.dist, 0)                                  # framed on the first file
        ms.select(self.keys("centre.obj"))
        ms.apply_selected()
        ms.view.orbit(40, 10)
        cam = (ms.view.yaw, ms.view.pitch, ms.view.dist, ms.view.cam_target)
        ms.add_models([part("left.fbx"), part("right.fbx"), part("venue.obj")])
        self.assertTrue(ms.cancel_btn.isVisibleTo(ms))
        self.assertTrue(ms.scene_lab.text().startswith("Adding 1 of 3: left.fbx"), ms.scene_lab.text())
        self.assertEqual(ms.objects.currentIndex(), 1)                       # the list stays usable meanwhile
        self.wait()
        self.assertEqual([ms.stage.label(s) for s in ms.stage.sources], ["centre.obj", "left.fbx", "right.fbx", "venue.obj"])
        self.assertEqual([ms.tree.topLevelItem(i).text(0) for i in range(ms.tree.topLevelItemCount())],
                         ["centre.obj", "left.fbx", "right.fbx", "venue.obj"])
        self.assertEqual(sorted(ms.assign.uv), self.keys("centre.obj"))     # still showing the clip
        self.assertEqual(sorted(ms.view.assigned), self.keys("centre.obj"))
        self.assertEqual((ms.view.yaw, ms.view.pitch, ms.view.dist, ms.view.cam_target), cam)
        self.assertEqual((ms.asset["id"], ms.playhead.frame), (aid, 77))
        self.assertEqual(ms.scene_lab.text(), "4 model files: 5 objects, 110 triangles")
        self.assertIn("venue.obj: 1 object without UVs", self.said[-1][0])
        self.assertTrue(self.said[-1][0].startswith("Added left.fbx, right.fbx and venue.obj."), self.said[-1])
        self.assertFalse(ms.result.isHidden())
        self.assertEqual(ms.all_btn.text(), "Apply to all 4 with UVs")       # across every file (Truss has no UVs)
        ms.view.frame_all()
        lo, hi = ms.stage.bounds
        self.assertEqual((round(lo[0], 3), round(hi[0], 3)), (-6.0, 6.0))    # Frame all: the whole composed stage

    def test_same_file_names_and_object_names_stay_apart(self):
        ms = self.ms
        self.import_(part("a/screen.obj"), part("b/screen.obj"), part("centre.obj"))
        labels = [ms.tree.topLevelItem(i).text(0) for i in range(3)]
        self.assertEqual(labels, ["a/screen.obj", "b/screen.obj", "centre.obj"])
        b = ms.tree.topLevelItem(1)
        ms._on_row_clicked(b, 0)                                             # a file's row selects its objects
        self.assertEqual(ms.selected_keys(), self.keys("b/screen.obj"))
        self.assertIn("in b/screen.obj", ms.sel_lab.text())
        self.assertEqual(ms.remove_btn.text(), "Remove b/screen.obj from stage")
        ms.apply_selected()
        self.assertEqual(sorted(ms.assign.uv), self.keys("b/screen.obj"))
        ms.select(self.keys("a/screen.obj"))
        ms.remove_selected_source()
        self.assertEqual([s.name for s in ms.stage.sources], ["screen.obj", "centre.obj"])
        self.assertEqual(ms.stage.sources[0].origin, part("b/screen.obj"))   # path is now its copy in the project
        self.assertEqual(ms.tree.topLevelItem(0).text(0), "screen.obj")     # no longer needs its folder
        self.assertEqual(sorted(ms.assign.uv), self.keys("screen.obj"))     # b's assignment untouched
        self.assertEqual(self.said[-1], ("Removed screen.obj from the stage (the file is unchanged).", False))
        self.assertTrue(os.path.exists(part("a/screen.obj")))

    def test_the_same_file_is_not_added_twice(self):
        ms = self.ms
        self.import_(part("centre.obj"))
        self.import_(os.path.join(FIX, "parts", "a", "..", "centre.obj"), part("left.fbx"), part("left.fbx"))
        self.assertEqual([ms.stage.label(s) for s in ms.stage.sources], ["centre.obj", "left.fbx"])
        self.assertEqual(self.said[-1][0], "Added left.fbx. centre.obj: already on the stage as centre.obj, not added again. "
                                           "left.fbx: chosen twice.")
        self.import_(part("centre.obj"))                                     # nothing new at all: said, nothing read
        self.assertEqual(len(ms.stage.sources), 2)
        self.assertIn("already on the stage", self.said[-1][0])

    def test_a_batch_with_a_damaged_file_adds_the_rest(self):
        ms = self.ms
        self.import_(part("centre.obj"), os.path.join(FIX, "garbage.fbx"), part("left.fbx"))
        self.assertEqual([s.name for s in ms.stage.sources], ["centre.obj", "left.fbx"])
        text, bad = self.said[-1]
        self.assertTrue(bad)
        self.assertTrue(text.startswith("Added 2 of 3 model files: centre.obj and left.fbx. garbage.fbx could not be used: "), text)
        self.assertFalse(ms.result.isHidden())
        self.assertIn("garbage.fbx", ms.result_lab.text())

    def test_cancelling_an_add_keeps_what_it_added(self):
        ms = self.ms
        ms.add_models([part("centre.obj"), os.path.join(FIX, "stage.fbx"), part("left.fbx"), part("right.fbx")])
        self.assertTrue(pump(self.app, lambda: len(ms.stage.sources) == 1, 30))
        ms.cancel_import()
        self.assertIsNone(ms._batch)
        pump(self.app, timeout=3.0)                                          # a late answer must not land
        self.assertEqual([s.name for s in ms.stage.sources], ["centre.obj"])
        self.assertEqual(self.said[-1][0], "Cancelled: centre.obj added (kept on the stage); stage.fbx, left.fbx and right.fbx not read.")
        self.assertTrue(ms.add_btn.isEnabled() and ms.replace_btn.isEnabled())

    def test_replace_swaps_only_what_could_be_read(self):
        ms = self.ms
        self.import_(part("centre.obj"))
        ms.select(self.keys("centre.obj"))
        ms.apply_selected()
        ms.view.orbit(40, 0)
        ms.replace_stage([os.path.join(FIX, "garbage.fbx"), part("left.fbx")])
        self.assertTrue(ms.scene_lab.text().startswith("Reading 1 of 2: garbage.fbx"), ms.scene_lab.text())
        self.assertEqual([s.name for s in ms.stage.sources], ["centre.obj"])   # not cleared at the start
        self.wait()
        self.assertEqual([s.name for s in ms.stage.sources], ["left.fbx"])
        self.assertEqual(ms.assign.uv, {})
        self.assertTrue(self.said[-1][0].startswith("Stage replaced with 1 of 2 model files. garbage.fbx could not be used"), self.said[-1])
        self.assertEqual(ms.view.yaw, 0.0)                                   # a new stage is framed afresh
        ms.select(self.keys("left.fbx"))
        ms.apply_selected()
        self.import_(os.path.join(FIX, "garbage.fbx"), os.path.join(FIX, "truncated.obj"), replace=True)
        self.assertEqual([s.name for s in ms.stage.sources], ["left.fbx"])    # nothing usable: unchanged
        self.assertEqual(sorted(ms.assign.uv), self.keys("left.fbx"))
        self.assertTrue(self.said[-1][0].startswith("The stage is unchanged: none of the files could be used."), self.said[-1])
        ms.replace_stage([part("right.fbx"), part("venue.obj")])
        ms.cancel_import()
        pump(self.app, timeout=3.0)
        self.assertEqual([s.name for s in ms.stage.sources], ["left.fbx"])
        self.assertEqual(self.said[-1][0], "Replace cancelled: the stage is unchanged.")
        self.import_(part("right.fbx"), part("venue.obj"), replace=True)
        self.assertEqual([s.name for s in ms.stage.sources], ["right.fbx", "venue.obj"])
        self.assertEqual(self.said[-1][0], "Stage replaced. venue.obj: 1 object without UVs (neutral in Surfaces; a projector still lights them).")

    def test_a_project_change_mid_batch_drops_the_rest(self):
        ms = self.ms
        ms.add_models([part("centre.obj"), os.path.join(FIX, "stage.fbx"), part("left.fbx")])
        self.assertTrue(pump(self.app, lambda: len(ms.stage.sources) == 1, 30))
        ms.reset_project()
        pump(self.app, timeout=3.0)
        self.assertIsNone(ms.scene)
        self.assertIsNone(ms._batch)
        self.assertEqual(ms.objects.currentIndex(), 0)
        self.assertTrue(ms.add_btn.isEnabled())

    def test_removing_files_down_to_an_empty_stage(self):
        ms = self.ms
        raised = []                                                          # an exception in a Qt slot only prints
        hook = mock.patch("sys.excepthook", lambda *e: raised.append(e[1]))
        hook.start()
        self.addCleanup(lambda: self.assertEqual(raised, []))
        self.addCleanup(hook.stop)
        self.import_(part("centre.obj"), part("left.fbx"), part("venue.obj"))
        ms.apply_all()
        ms.select(self.keys("left.fbx"))
        self.assertTrue(ms.remove_btn.isVisibleTo(ms))
        self.assertIn("The file itself is not changed", ms.remove_btn.toolTip())
        ms.remove_selected_source()
        self.assertEqual(sorted(ms.view.assigned), sorted(k for k in ms.assign.uv))
        self.assertEqual(len(ms.assign.uv), 2)                               # centre and the floor
        self.assertEqual(ms.selected_keys(), [])
        self.assertFalse(ms.remove_btn.isVisibleTo(ms))
        ms.select([*self.keys("centre.obj"), *self.keys("venue.obj")])       # two files: no single one to remove
        self.assertFalse(ms.remove_btn.isVisibleTo(ms))
        for sid in [s.id for s in ms.stage.sources]:
            ms.remove_source(sid)
        self.assertIsNone(ms.scene)
        self.assertIsNone(ms.assign)
        self.assertEqual(ms.view.scene, None)
        self.assertEqual(ms.objects.currentIndex(), 0)
        self.assertIn("Add one or more", ms.empty_lab.text())
        self.assertFalse(ms.replace_btn.isEnabled())
        self.assertEqual(ms.scene_lab.text(), "No stage imported")
        self.import_(part("right.fbx"))                                      # and starts again
        self.assertEqual(len(ms.stage.sources), 1)

    def test_filter_and_selection_across_files(self):
        ms = self.ms
        self.import_(part("centre.obj"), part("left.fbx"), part("venue.obj"), part("a/screen.obj"))
        ms.select(self.keys("venue.obj")[:1])                                # Floor
        ms.filter.setText("screen")
        shown = [it.data(0, 0x0100) for it in ms._rows() if not ms._hidden(it)]
        self.assertEqual(len(shown), 3)                                      # a Screen in three files
        self.assertTrue(ms.tree.topLevelItem(2).isHidden())                  # venue.obj: nothing shown
        self.assertIn("1 hidden by the filter", ms.sel_lab.text())
        ms.select_shown()
        self.assertEqual(sorted(ms.selected_keys()), sorted(shown))
        self.assertEqual(ms.apply_btn.text(), "Apply clip to 3 selected")
        self.assertFalse(ms.remove_btn.isVisibleTo(ms))
        ms.apply_selected()
        ms.filter.clear()
        self.assertEqual(sorted(ms.assign.uv), sorted(shown))
        self.assertEqual(ms.all_btn.text(), "Apply to all 4 with UVs")

    def test_replacing_while_filtered_with_one_selected(self):
        """The new stage was put in before the old rows went: clearing the filter (or the rows)
        then refreshed the inspector with a key of the old stage, an exception in a Qt slot."""
        ms = self.ms
        raised = []
        with mock.patch("sys.excepthook", lambda *e: raised.append(e[1])):
            self.import_(part("centre.obj"), part("left.fbx"))
            ms.filter.setText("scr")
            ms.select(self.keys("left.fbx"))
            self.import_(part("venue.obj"), replace=True)
        self.assertEqual(raised, [])
        self.assertEqual((ms.filter.text(), ms.selected_keys()), ("", []))
        self.assertEqual([s.id for s in ms.stage.sources], ["m3"])            # ids carry on: m1 stays centre.obj's

    def test_random_edits_never_raise_in_the_inspector(self):
        """A seeded mix of add / replace / cancel / remove / select / filter / apply. Replacing the stage
        while one object was selected made the inspector look up an old key in the new stage
        (an exception in a Qt slot: printed, not shown, so only a hook catches it)."""
        import random
        from nitpix.ui.mapping import KEY
        raised = []
        with mock.patch("sys.excepthook", lambda *e: raised.append(e[1])):
            files = [part(n) for n in ("centre.obj", "left.fbx", "venue.obj", "a/screen.obj")] + [os.path.join(FIX, "garbage.fbx")]
            ms = self.ms
            for seed in (2, 4):
                r = random.Random(seed)
                ms.reset_project()
                for _ in range(60):
                    rows = ms._rows()
                    op = r.choice(["add", "add", "replace", "cancel", "remove", "one", "some", "file", "filter", "apply", "clear"])
                    if op in ("add", "replace"):
                        (ms.add_models if op == "add" else ms.replace_stage)(r.sample(files, r.randint(1, 2)))
                        if r.random() < 0.7:
                            self.wait()
                    elif op == "cancel":
                        ms.cancel_import()
                    elif op == "remove" and ms.stage.sources:
                        ms.remove_source(r.choice(ms.stage.sources).id)
                    elif op == "one" and rows:
                        ms.tree.setCurrentItem(r.choice(rows))
                    elif op == "some" and rows:
                        ms.select([it.data(0, KEY) for it in r.sample(rows, min(3, len(rows)))])
                    elif op == "file" and ms.tree.topLevelItemCount():
                        ms._on_row_clicked(ms.tree.topLevelItem(r.randrange(ms.tree.topLevelItemCount())), 0)
                    elif op == "filter":
                        ms.filter.setText(r.choice(["", "scr", "floor"]))
                    elif op == "apply":
                        ms.apply_selected()
                    elif op == "clear":
                        ms.clear_selected()
                    pump(self.app, timeout=0.01)
                self.wait()
                ids = [s.id for s in ms.stage.sources]
                self.assertEqual(len(ids), len(set(ids)))
                self.assertTrue(all(ms.stage.object(k) for k in ms.selected_keys()))
        self.assertEqual(raised, [])

    def test_many_objects_in_several_files_stay_quick(self):
        """Four files of 5,000 objects: adding each, selecting all, applying, filtering and removing
        one. Hardening made one 20,000-object file quick; composing must not bring back quadratic work."""
        from array import array
        from nitpix.scene import Geometry, NormalizedScene, SceneObject, uv_range
        pos, idx, uv = array("f", [0, 0, 0, 1, 0, 0, 0, 1, 0]), array("I", [0, 1, 2]), array("f", [0, 0, 1, 0, 0, 1])
        world = tuple(float(i == j) for i in range(4) for j in range(4))

        def scene(n, tag):
            g = Geometry("mesh:0", pos, idx, (uv,), "Screen", "f", (uv_range(uv, idx, pos),))
            objs = tuple(SceneObject(f"Rig/Panel_{i:05d}", ("Rig", f"Panel_{i:05d}"), f"Panel_{i:05d}", ("mesh:0",), ("Screen",),
                                     world, 1, 1, False, None, "f") for i in range(n))
            return NormalizedScene({"path": f"/generated/{tag}/rig.obj"}, "test", objs, {"mesh:0": g}, ((0, 0, 0), (1, 1, 0)))
        ms = self.ms
        scenes = [scene(5000, t) for t in "abcd"]
        t = time.monotonic()
        for sc in scenes:
            ms._add_scene(sc)
        ms.select_shown()
        ms.apply_selected()
        ms.filter.setText("Panel_0499")
        ms.select_shown()
        ms.remove_source(ms.stage.sources[1].id)
        took = time.monotonic() - t
        self.assertEqual(len(ms.stage.objects), 15000)
        self.assertEqual(len(ms.assign.uv), 15000)
        self.assertEqual(len(ms.selected_keys()), 30)                        # ten in each of the three left
        self.assertEqual(ms.tree.topLevelItem(0).text(0), "a/rig.obj")
        self.assertLess(took, 20)


@unittest.skipUnless(HAS, "needs PySide6 and assimp-py")
class ProjectorsTab(_Screen):
    """The Projectors tab: its states, the projector it edits, and what it leaves alone."""
    def test_empty_states_then_a_projector_from_the_view(self):
        ms = self.ms
        ms.tabs.setCurrentIndex(1)
        self.assertEqual((ms.view.mode, ms.mode_lab.text()), ("projectors", "Projector preview"))
        self.assertEqual(ms.proj_panel.pages.currentIndex(), 0)              # no stage: add models first
        self.import_(os.path.join(FIX, "projector", "projection.obj"))
        self.assertEqual(ms.proj_panel.pages.currentIndex(), 1)              # a stage, no projector
        self.assertIsNone(ms.view.projector)
        ms.view.cam_target.setX(0.5)
        ms.add_projector()
        p = ms.projector
        self.assertEqual(ms.proj_panel.pages.currentIndex(), 2)
        self.assertIs(ms.view.projector, p)
        eye = ms.view._eye()
        self.assertEqual(p.position, (eye.x(), eye.y(), eye.z()))           # where the camera is...
        self.assertEqual(p.target[0], 0.5)                                   # ...aimed where it looks
        self.assertIsNone(p.aspect)                                          # following the clip
        self.assertIn("Visibility 2048×1152", ms.proj_panel.info.text())     # 16:9 without a clip

    def test_numbers_edit_the_projector_and_problems_are_said(self):
        ms = self.ms
        self.import_(os.path.join(FIX, "projector", "projection.obj"))
        ms.tabs.setCurrentIndex(1)
        ms.add_projector()
        panel = ms.proj_panel
        for spin, v in zip(panel.pos, (0, 2, 10)):
            spin.setValue(v)
        for spin, v in zip(panel.tgt, (0, 2, 0)):
            spin.setValue(v)
        panel.fov.setValue(30)
        self.assertEqual((ms.projector.position, ms.projector.target, ms.projector.h_fov), ((0, 2, 10), (0, 2, 0), 30))
        self.assertIn("Throw ratio 1.87:1 · 10.00 m to the target", panel.info.text())
        panel.from_clip.setChecked(False)
        panel.aspect.setValue(2.0)
        self.assertEqual(ms.projector.aspect, 2.0)
        self.assertIn("Visibility 2048×1024", panel.info.text())
        panel.from_clip.setChecked(True)
        self.assertIsNone(ms.projector.aspect)
        panel.tgt[2].setValue(10)                                            # target on the projector
        self.assertFalse(panel.problem.isHidden())
        self.assertIn("at the projector's position", panel.problem.text())
        self.assertFalse(panel.look_btn.isEnabled())
        self.assertFalse(ms.view.projecting())
        panel.enabled.setChecked(False)
        self.assertFalse(ms.projector.enabled)

    def test_tabs_and_the_projector_leave_the_stage_clip_and_assignments_alone(self):
        ms = self.ms
        clip = os.path.join(self.tmp, "wide.mov")
        open(clip, "wb").close()
        aid = self.session.project.upsert_asset(clip, kind="video", name="wide.mov", fps="25/1", frames=200, duration=8.0,
                                                width=1280, height=512, scan_state="done")
        ms.refresh_media()
        ms._choose_media(aid)
        self.import_(part("centre.obj"), os.path.join(FIX, "projector", "projection.obj"))
        ms.select([ms.stage.sources[0].objects[0].key])
        ms.apply_selected()
        uv, cam = dict(ms.assign.uv), (ms.view.yaw, ms.view.dist)
        loads = []
        ms.view.load = lambda *a, **k: loads.append(a)
        ms.tabs.setCurrentIndex(1)
        ms.add_projector()
        self.assertAlmostEqual(ms.projector.image_aspect(ms.clip_aspect), 2.5)   # from the clip
        self.assertIn("aspect 2.500 (clip)", ms.proj_panel.info.text())
        ms.tabs.setCurrentIndex(0)
        self.assertEqual(ms.view.mode, "surfaces")
        self.assertEqual((ms.assign.uv, ms.view.assigned, (ms.view.yaw, ms.view.dist)), (uv, uv, cam))
        self.assertEqual((ms.asset["id"], loads), (aid, []))
        ms.tabs.setCurrentIndex(1)
        ms.remove_projector()
        self.assertIsNone(ms.view.projector)
        self.assertEqual((ms.assign.uv, len(ms.stage.sources)), (uv, 2))
        ms.add_projector()
        ms.remove_source(ms.stage.sources[1].id)                             # the stage changes under it: kept
        self.assertIsNotNone(ms.view.projector)
        ms.reset_project()                                                   # another project: it goes
        self.assertIsNone(ms.projector)
        self.assertEqual(ms.proj_panel.pages.currentIndex(), 0)


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
        ms._replace_with([load_scene(os.path.join(FIX, "stage.fbx"))])
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

    def test_projector_numbers_keep_their_keys_and_space_plays_after(self):
        """Typing in a number never reaches the transport or the screen keys; Enter commits it and
        hands the keys back, so Space plays. So does a click or drag in the view, without Enter."""
        from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
        from PySide6.QtGui import QKeyEvent, QWheelEvent
        from PySide6.QtTest import QTest
        w = self.window()
        w.show()
        w.show_asset(self.a)
        w.show_view("mapping")
        ms = w.mapping
        ms._replace_with([load_scene(os.path.join(FIX, "projector", "projection.obj"))])
        ms.tabs.setCurrentIndex(1)
        ms.add_projector()
        toggles = []
        ms.toggle_play = lambda: toggles.append(1)
        spin = ms.proj_panel.pos[0]
        w.activateWindow()
        spin.setFocus()
        pump(self.app, timeout=0.05)
        spin.selectAll()
        QTest.keyClicks(spin, "3")
        QTest.keyClick(spin, Qt.Key.Key_Space)
        self.assertEqual((toggles, w.current_view), ([], "mapping"))         # no play, no screen change for "3"
        QTest.keyClick(spin, Qt.Key.Key_Return)
        pump(self.app, timeout=0.05)
        self.assertEqual(ms.projector.position[0], 3.0)
        self.assertIsNot(QApplication.focusWidget(), spin)
        QTest.keyClick(QApplication.focusWidget() or w, Qt.Key.Key_Space)
        self.assertEqual(toggles, [1])
        # No Enter: a click or a drag in the view commits the number and gives Space back
        for gesture in ("click", "drag"):
            with self.subTest(gesture):
                toggles.clear()
                QTest.mouseClick(spin, Qt.MouseButton.LeftButton)
                pump(self.app, timeout=0.05)
                self.assertIs(QApplication.focusWidget(), spin)
                spin.selectAll()
                QTest.keyClicks(spin, "4" if gesture == "click" else "5")
                QTest.keyClick(spin, Qt.Key.Key_Space)
                QTest.keyClick(spin, Qt.Key.Key_Right)                       # the field's caret, not a frame step
                self.assertEqual(toggles, [])
                c = ms.view.rect().center()
                if gesture == "click":
                    QTest.mouseClick(ms.view, Qt.MouseButton.LeftButton, pos=c)
                else:
                    QTest.mousePress(ms.view, Qt.MouseButton.LeftButton, pos=c)
                    QTest.mouseMove(ms.view, c + QPoint(30, 5))
                    QTest.mouseRelease(ms.view, Qt.MouseButton.LeftButton, pos=c + QPoint(30, 5))
                pump(self.app, timeout=0.05)
                self.assertIs(QApplication.focusWidget(), ms.view)
                self.assertEqual(ms.projector.position[0], 4.0 if gesture == "click" else 5.0)   # committed, valid
                QTest.keyClick(ms.view, Qt.Key.Key_Space)
                held = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier, " ", True)
                QApplication.sendEvent(ms.view, held)                        # held: still one toggle
                self.assertEqual(toggles, [1])
        spin.setFocus()                                                      # Escape drops what was typed and gives the keys back
        pump(self.app, timeout=0.05)
        spin.selectAll()
        QTest.keyClicks(spin, "9")
        QTest.keyClick(spin, Qt.Key.Key_Escape)
        pump(self.app, timeout=0.05)
        self.assertEqual(ms.projector.position[0], 5.0)
        self.assertIsNot(QApplication.focusWidget(), spin)
        before = spin.value()                                                # the wheel never edits a number
        QApplication.sendEvent(spin, QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, 120), Qt.MouseButton.NoButton,
                                                 Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False))
        self.assertEqual((spin.value(), ms.projector.position[0]), (before, 5.0))

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
        ms._replace_with([load_scene(os.path.join(FIX, "stage.fbx"))])
        ms.select(["m1:Stage/Screens/Centre"])                                  # two UV sets: the UV selector is live
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
        w.mapping._replace_with([load_scene(os.path.join(FIX, "stage.obj"))])
        w.close_project()
        self.assertIsNone(w.mapping.scene)
        self.assertEqual(w.current_view, "start")
        self.assertFalse(w.review.released)

    def test_a_failed_save_keeps_the_project_open_until_the_user_decides(self):
        w = self.window()
        w.show_view("mapping")                                               # restores (nothing saved yet) and binds the saver
        ms, p = w.mapping, w.session.project
        ms._replace_with([load_scene(os.path.join(FIX, "stage.obj"))])
        ms.flush()
        good = p.get_meta("mapping.setup")
        asked = []
        answers = []
        w.ask_unsaved_mapping = lambda action, why: (asked.append((action, why)), answers.pop(0))[1]
        broken = mock.patch.object(p, "set_meta", side_effect=OSError("disk full"))
        broken.start()
        ms.view.orbit(40, 0)                                                 # an unsaved change
        answers[:] = ["cancel", "retry", "cancel", "cancel"]
        self.assertFalse(w.close_project())
        self.assertFalse(w.open_project(tempfile.mkdtemp(dir=self.tmp) + "/other.nitpix"))   # retried, failed again, then kept
        self.assertFalse(w.close())                                          # quitting is refused before anything stops
        self.assertEqual([a for a, _ in asked], ["Closing the project", "Opening another project", "Opening another project", "Quitting"])
        self.assertIn("disk full", asked[0][1])
        self.assertIs(w.session.project, p)                                  # nothing closed, reset or torn down
        self.assertEqual(w.current_view, "mapping")
        self.assertTrue(ms.scene)
        self.assertIs(ms.saver.project, p)                                   # still bound: the next change or Retry saves
        self.assertEqual(ms.saver.state, "error")
        broken.stop()
        answers[:] = ["retry"]                                               # the disk is back: Retry saves, then closes
        p.set_meta("mapping.setup", good)
        broken = mock.patch.object(p, "set_meta", side_effect=[OSError("disk full"), None])
        broken.start()
        ms.view.orbit(10, 0)
        self.assertTrue(w.close_project())
        broken.stop()
        self.assertIsNone(w.session.project)

    def test_discarding_unsaved_mapping_changes_closes_without_writing(self):
        w = self.window()
        w.show_view("mapping")
        ms, p = w.mapping, w.session.project
        ms._replace_with([load_scene(os.path.join(FIX, "stage.obj"))])
        ms.flush()
        good = p.get_meta("mapping.setup")
        w.ask_unsaved_mapping = lambda action, why: "discard"
        with mock.patch.object(p, "set_meta", side_effect=OSError("read-only")) as sm:
            ms.view.orbit(40, 0)
            folder = str(p.folder)
            self.assertTrue(w.close_project())
            calls = sm.call_count
        self.assertEqual(calls, 1)                                           # the failed attempt only: nothing after the choice
        self.assertIsNone(w.session.project)
        from nitpix.store import Project
        q = Project(folder)
        self.assertEqual(q.get_meta("mapping.setup"), good)                  # the last good save is what the project keeps
        q.close()

    def test_nothing_to_lose_asks_nothing(self):
        w = self.window()
        w.ask_unsaved_mapping = lambda *a: self.fail("asked with nothing at risk")
        self.assertTrue(w.close_project())                                   # Mapping never opened
        w.open_project(tempfile.mkdtemp(dir=self.tmp) + "/r.nitpix")
        w.show_view("mapping")
        ms = w.mapping
        ms.saver.suspended = True                                            # a restore under way: writes nothing, loses nothing
        with mock.patch.object(w.session.project, "set_meta", side_effect=OSError("x")):
            ms.saver.state = "pending"
            self.assertIsNone(ms.settle())
            ms.saver.suspended = False
            ms.saver.unbind()                                                # no project bound
            self.assertIsNone(ms.settle())


if __name__ == "__main__":
    unittest.main()
