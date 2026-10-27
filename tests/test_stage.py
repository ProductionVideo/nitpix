"""One Mapping stage from several model files (nitpix/stage.py), from tests/fixtures/scenes/parts:
separately exported screens arrive where they were authored, keys never collide, and removing a
file takes only its objects. No Qt.
python3 -W ignore -m unittest tests.test_stage"""
import importlib.util
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
PARTS = os.path.join(HERE, "fixtures", "scenes", "parts")
HAS = importlib.util.find_spec("assimp_py") is not None
if HAS:
    from nitpix.mapping import Assignments
    from nitpix.scene import load_scene
    from nitpix.stage import Stage, split_key, stage_key

FILES = ["centre.obj", "left.fbx", "right.fbx", "venue.obj", "a/screen.obj", "b/screen.obj"]


def at_uv(stage, key, u, v):
    """Scene-space point of a stage object's vertex whose first UV is (u, v)."""
    o = stage.object(key)
    for gid in o.geometries:
        g = stage.geometries[gid]
        for i in range(len(g.positions) // 3):
            if abs(g.uv_sets[0][2 * i] - u) < 1e-6 and abs(g.uv_sets[0][2 * i + 1] - v) < 1e-6:
                x, y, z = g.positions[3 * i: 3 * i + 3]
                w = o.world
                return [w[a] * x + w[4 + a] * y + w[8 + a] * z + w[12 + a] for a in range(3)]
    raise AssertionError(f"{key}: no vertex at UV {(u, v)}")


@unittest.skipUnless(HAS, "needs assimp-py")
class Composed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenes = {f: load_scene(os.path.join(PARTS, f)) for f in FILES}
        with open(os.path.join(PARTS, "expected.json")) as fh:
            cls.expected = json.load(fh)

    def make(self, files=FILES):
        st = Stage()
        srcs = {f: st.add(self.scenes[f]) for f in files}
        return st, srcs

    def test_each_file_where_it_was_authored(self):
        """No per-file centring, fitting or scaling: every UV corner lands where Blender put it,
        including the node-placed FBX and the centimetre Z-up one."""
        st, srcs = self.make()
        for f, objects in self.expected.items():
            for name, want in objects.items():
                key = stage_key(srcs[f].id, name)
                self.assertIsNotNone(st.object(key), key)
                for corner, p in want["corners"].items():
                    u, v = map(float, corner.split(","))
                    got = at_uv(st, key, u, v)
                    for a in range(3):
                        self.assertAlmostEqual(got[a], p[a], places=3, msg=(f, name, corner))
        lo, hi = st.bounds                                     # the whole stage: venue floor to truss, both side screens
        self.assertAlmostEqual(lo[0], -6.0, 3)
        self.assertAlmostEqual(hi[0], 6.0, 3)
        self.assertAlmostEqual(hi[1], 5.0, 3)

    def test_same_names_and_paths_in_different_files_stay_apart(self):
        st, srcs = self.make()
        screens = [o.key for o in st.objects if o.display == "Screen"]
        self.assertEqual(len(screens), 5)                      # one per screen file (right.fbx's is Rig/Screen)
        self.assertEqual(len(set(screens)), 5)
        self.assertEqual(len({split_key(k)[1] for k in screens}), 2)   # "Screen" and "Rig/Screen" inside the files
        self.assertEqual(len(st.geometries), sum(len(s.geometries) for s in self.scenes.values()))
        a, b = srcs["a/screen.obj"], srcs["b/screen.obj"]
        self.assertEqual((st.label(a), st.label(b)), ("a/screen.obj", "b/screen.obj"))
        self.assertEqual(st.label(srcs["centre.obj"]), "centre.obj")
        asg = Assignments(st)
        asg.apply([stage_key(a.id, "Screen")])
        self.assertEqual(list(asg.uv), [stage_key(a.id, "Screen")])   # b's Screen untouched

    def test_one_file_twice_is_two_sources(self):
        """Adding is by import, not by path: the screen guards against the same file twice; here two
        imports of one file still never share keys or geometry."""
        st = Stage()
        s1, s2 = st.add(self.scenes["centre.obj"]), st.add(self.scenes["centre.obj"])
        self.assertNotEqual(s1.id, s2.id)
        self.assertEqual(len(st.objects), 2)
        self.assertEqual(st.has_path(os.path.join(PARTS, "x", "..", "centre.obj")), s1)   # any spelling of the path

    def test_remove_one_file(self):
        st, srcs = self.make()
        asg = Assignments(st)
        asg.apply_all()
        left = srcs["left.fbx"]
        n = len(st.objects)
        st.remove(left.id)
        asg.prune()
        self.assertEqual(len(st.objects), n - 1)
        self.assertIsNone(st.object(stage_key(left.id, "Screen")))
        self.assertFalse(any(g.startswith(left.id + ":") for g in st.geometries))
        self.assertEqual(len(asg.uv), len([o for o in st.objects if o.uv_sets]))
        self.assertIsNone(st.remove(left.id))                  # already gone
        again = st.add(self.scenes["left.fbx"])
        self.assertNotEqual(again.id, left.id)                 # ids are never reused...
        self.assertEqual(Stage(next_id=st.next_id).add(self.scenes["left.fbx"]).id, f"m{st.next_id}")   # ...nor by a replacing stage
        for s in list(st.sources):
            st.remove(s.id)
        self.assertFalse(st)
        self.assertEqual((st.objects, st.geometries), ((), {}))

    def test_geometry_is_shared_not_copied(self):
        st, srcs = self.make(["centre.obj"])
        o = st.objects[0]
        self.assertIs(st.geometries[o.geometries[0]], self.scenes["centre.obj"].geometries[split_key(o.geometries[0])[1]])


if __name__ == "__main__":
    unittest.main()
