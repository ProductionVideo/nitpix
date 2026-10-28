"""Model files kept in a project (nitpix/models.py) and the saved setup document (nitpix/mapsave.py).
No Qt. python3 -W ignore -m unittest tests.test_models"""
import copy
import hashlib
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
FIX = os.path.join(HERE, "fixtures", "scenes")
from nitpix import mapsave, models  # noqa: E402


def digest(folder):
    out = {}
    for d, _dirs, files in os.walk(folder):
        for f in files:
            p = os.path.join(d, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, folder)] = hashlib.sha256(fh.read()).hexdigest()
    return out


class Managed(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nitpix models ")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.src = os.path.join(self.tmp, "src")
        for rel in ("parts/a/screen.obj", "parts/b/screen.obj", "parts/left.fbx", "stage.obj", "stage.mtl"):
            os.makedirs(os.path.dirname(os.path.join(self.src, rel)), exist_ok=True)
            shutil.copyfile(os.path.join(FIX, rel), os.path.join(self.src, rel))
        self.proj = os.path.join(self.tmp, "p.nitpix")
        os.makedirs(self.proj)
        self.before = digest(self.src)

    def tearDown(self):
        self.assertEqual(digest(self.src), self.before)                     # originals only ever read

    def test_obj_with_its_material_library(self):
        r = models.manage(os.path.join(self.src, "stage.obj"), self.proj)
        self.assertEqual((r["name"], r["sidecars"], r["missing"]), ("stage.obj", ["stage.mtl"], []))
        kept = models.resolve(self.proj, r)
        self.assertTrue(kept.startswith(os.path.realpath(os.path.join(self.proj, "models")) + os.sep))
        self.assertTrue(os.path.isfile(os.path.join(os.path.dirname(kept), "stage.mtl")))    # mtllib still resolves
        self.assertEqual(r["origin"], os.path.join(self.src, "stage.obj"))
        self.assertIsNone(models.check(self.proj, r))
        self.assertEqual(sorted(os.listdir(os.path.join(self.proj, "models"))), [r["id"]])  # nothing else copied

    def test_same_name_different_files_and_same_bytes(self):
        a = models.manage(os.path.join(self.src, "parts/a/screen.obj"), self.proj)
        b = models.manage(os.path.join(self.src, "parts/b/screen.obj"), self.proj)
        self.assertNotEqual(a["id"], b["id"])
        self.assertNotEqual(a["path"], b["path"])
        twin = os.path.join(self.tmp, "elsewhere", "screen.obj")            # the same bytes somewhere else
        os.makedirs(os.path.dirname(twin))
        shutil.copyfile(os.path.join(self.src, "parts/a/screen.obj"), twin)
        again = models.manage(twin, self.proj)
        self.assertEqual(again["id"], a["id"])                               # shares the stored copy...
        self.assertFalse(again["created"])
        self.assertEqual(again["origin"], twin)                              # ...but keeps its own origin
        self.assertEqual(len(os.listdir(os.path.join(self.proj, "models"))), 2)

    def test_same_bytes_under_another_name_never_disturbs_a_kept_copy(self):
        a = os.path.join(self.tmp, "twins", "a.obj")
        os.makedirs(os.path.dirname(a))
        shutil.copyfile(os.path.join(self.src, "parts/a/screen.obj"), a)
        ra = models.manage(a, self.proj)
        kept_a = models.resolve(self.proj, ra)
        b = os.path.join(self.tmp, "twins", "b.obj")
        shutil.copyfile(a, b)
        rb = models.manage(b, self.proj)
        self.assertNotEqual(ra["id"], rb["id"])
        self.assertTrue(os.path.isfile(kept_a))                              # the first copy is still where it was saved
        self.assertIsNone(models.check(self.proj, ra))
        self.assertIsNone(models.check(self.proj, rb))
        with open(models.resolve(self.proj, rb), "ab") as fh:                 # a damaged copy of this same file is replaced...
            fh.write(b"x")
        self.assertTrue(models.manage(b, self.proj)["created"])
        self.assertIsNone(models.check(self.proj, rb))
        self.assertIsNone(models.check(self.proj, ra))                       # ...and nothing else is touched

    def test_missing_and_outside_sidecars_are_reported(self):
        obj = os.path.join(self.tmp, "odd", "odd.obj")
        os.makedirs(os.path.dirname(obj))
        with open(obj, "w") as fh:
            fh.write("mtllib gone.mtl ../outside.mtl\nmtllib /abs/x.mtl\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
        open(os.path.join(self.tmp, "outside.mtl"), "w").close()
        r = models.manage(obj, self.proj)
        self.assertEqual(r["sidecars"], [])
        self.assertEqual(r["missing"], ["gone.mtl: missing next to the model", "../outside.mtl: outside the model's folder, not copied",
                                        "/abs/x.mtl: outside the model's folder, not copied"])

    def test_changed_or_missing_copies_and_paths_outside(self):
        r = models.manage(os.path.join(self.src, "parts/left.fbx"), self.proj)
        kept = models.resolve(self.proj, r)
        with open(kept, "ab") as fh:
            fh.write(b"tampered")
        self.assertIn("changed", models.check(self.proj, r))
        os.remove(kept)
        self.assertIn("missing", models.check(self.proj, r))
        self.assertIsNone(models.resolve(self.proj, {**r, "path": "../escape.fbx"}))
        self.assertIn("not inside", models.check(self.proj, {**r, "path": "/etc/hosts"}))
        again = models.manage(os.path.join(self.src, "parts/left.fbx"), self.proj)    # the damaged copy is replaced
        self.assertIsNone(models.check(self.proj, again))

    def test_interrupted_copies_are_cleared_not_kept(self):
        os.makedirs(os.path.join(self.proj, "models", ".incoming-x"))
        r = models.manage(os.path.join(self.src, "stage.obj"), self.proj)
        models.clean_incoming(self.proj)
        self.assertEqual(os.listdir(os.path.join(self.proj, "models")), [r["id"]])


GOOD = {"version": 1,
        "resources": {"abc": {"path": "models/abc/x.obj", "name": "x.obj", "sha256": "0" * 64, "size": 10, "origin": "/o/x.obj",
                              "sidecars": [], "missing": []}},
        "sources": [{"id": "m2", "resource": "abc"}], "next_id": 1,
        "assignments": {"m2:Screen": 0}, "clip": 3, "tab": "projectors",
        "camera": {"yaw": 10, "pitch": 5, "dist": 9, "target": [0, 1, 0]},
        "projectors": [{"id": "p1", "position": [0, 2, 10], "target": [0, 2, 0], "h_fov": 30, "aspect": None,
                        "enabled": True, "show_frustum": False}]}


class Document(unittest.TestCase):
    def test_a_good_document(self):
        d = mapsave.validate(copy.deepcopy(GOOD))
        self.assertEqual(d["next_id"], 3)                                    # never below a saved id
        p = mapsave.projector_from(d["projectors"][0])
        self.assertEqual((p.position, round(p.h_fov, 9), p.show_frustum), ((0.0, 2.0, 10.0), 30.0, False))   # migrated from version 1
        self.assertEqual(mapsave.projector_to(p)["position"], [0.0, 2.0, 10.0])

    def test_what_is_refused(self):
        bad = [("not a dict", []), ("version", {**GOOD, "version": 2}), ("source id", {**GOOD, "sources": [{"id": "x", "resource": "abc"}]}),
               ("unlisted", {**GOOD, "sources": [{"id": "m1", "resource": "zzz"}]}),
               ("assignment", {**GOOD, "assignments": {"m9:Screen": 0}}), ("uv", {**GOOD, "assignments": {"m2:Screen": -1}}),
               ("tab", {**GOOD, "tab": "lights"}), ("camera", {**GOOD, "camera": {"yaw": 1, "pitch": 1, "dist": 0, "target": [0, 0, 0]}}),
               ("nan", {**GOOD, "camera": {"yaw": float("nan"), "pitch": 1, "dist": 1, "target": [0, 0, 0]}}),
               ("fov", {**GOOD, "projectors": [{**GOOD["projectors"][0], "h_fov": 500}]}),
               ("view", {**GOOD, "camera": {**GOOD["camera"], "view": "iso"}}),
               ("two", {**GOOD, "projectors": GOOD["projectors"] * 2}),
               ("checksum", {**GOOD, "resources": {"abc": {**GOOD["resources"]["abc"], "sha256": "short"}}}),
               ("bool as number", {**GOOD, "clip": True})]
        for name, doc in bad:
            with self.subTest(name):
                with self.assertRaises(mapsave.SetupError):
                    mapsave.validate(copy.deepcopy(doc))


if __name__ == "__main__":
    unittest.main()
