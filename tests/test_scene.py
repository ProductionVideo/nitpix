"""Stage import for the Mapping workspace (nitpix/scene.py) against the fixtures in
tests/fixtures/scenes, made by tools/scenes/make_fixtures.py. Expected values come from that
script's own scene data, not from the importer under test. Skipped without assimp-py.

    NITPIX_SLOW=1 adds a 1,000,000-triangle import and reports its time.
"""
import importlib.util
import json
from array import array
import os
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures", "scenes")
HAVE = importlib.util.find_spec("assimp_py") is not None
if HAVE:
    from nitpix.scene import UV_EDGE, SceneImportError, load_scene


def fx(name):
    return os.path.join(FIX, name)


def world_vertices(scene, obj, uv_set=0):
    """(x, y, z, u, v) for every vertex of an object, in scene space."""
    w = obj.world
    out = []
    for gid in obj.geometries:
        g = scene.geometries[gid]
        p, uv = g.positions, g.uv_sets[uv_set] if uv_set < len(g.uv_sets) else None
        for i in range(len(p) // 3):
            x, y, z = p[3 * i], p[3 * i + 1], p[3 * i + 2]
            q = tuple(w[a] * x + w[4 + a] * y + w[8 + a] * z + w[12 + a] for a in range(3))
            out.append(q + ((uv[2 * i], uv[2 * i + 1]) if uv else (None, None)))
    return out


def det3(w):
    return (w[0] * (w[5] * w[10] - w[9] * w[6]) - w[4] * (w[1] * w[10] - w[9] * w[2]) + w[8] * (w[1] * w[6] - w[5] * w[2]))


@unittest.skipUnless(HAVE, "needs assimp-py")
class StageFBX(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(fx("expected.json")) as fh:
            cls.exp = json.load(fh)
        cls.s = load_scene(fx("stage.fbx"))

    def keys(self):
        """Expected objects under the keys the importer should give them: Logo_B was renamed to
        Logo_A in the file, so it is the second Logo_A."""
        out = {}
        for path, e in self.exp["objects"].items():
            out["Stage/Logo_A#2" if path == "Stage/Logo_B" else path] = e
        return out

    def test_objects_by_node_path(self):
        exp = self.keys()
        self.assertEqual(sorted(o.key for o in self.s.objects), sorted(exp))
        for o in self.s.objects:
            with self.subTest(o.key):
                e = exp[o.key]
                self.assertEqual(o.triangles, e["triangles"])
                self.assertEqual(list(o.slots), e["slots"])
                self.assertEqual(o.uv_sets, e["uv_sets"])
                self.assertEqual(o.outside_0_1, e["outside_0_1"])
                self.assertEqual(o.path, tuple(o.key.split("#")[0].split("/")))

    def test_uvs_exactly_as_authored(self):
        """Every authored (position, UV) corner arrives, the UV bit for bit (float32), for every
        UV set, on the right place in the scene."""
        exp = self.keys()
        for o in self.s.objects:
            for k, corners in enumerate(exp[o.key]["corners"]):
                with self.subTest(o.key, uv_set=k):
                    got = world_vertices(self.s, o, k)
                    for x, y, z, u, v in corners:
                        hit = [g for g in got if abs(g[0] - x) < 2e-4 and abs(g[1] - y) < 2e-4 and abs(g[2] - z) < 2e-4 and (g[3], g[4]) == (u, v)]
                        self.assertTrue(hit, f"corner {(x, y, z)} uv {(u, v)} missing")
                    self.assertEqual(len({(round(g[0], 3), round(g[1], 3), round(g[2], 3), g[3], g[4]) for g in got}), len(corners))

    def test_duplicate_names_stay_apart(self):
        a, b = self.s.object("Stage/Logo_A"), self.s.object("Stage/Logo_A#2")
        self.assertEqual((a.display, b.display), ("Logo_A", "Logo_A (2)"))
        self.assertNotEqual(a.fingerprint, b.fingerprint)             # different UVs
        self.assertNotEqual(a.geometries, b.geometries)

    def test_instances_share_geometry(self):
        p1, p2 = self.s.object("Stage/Panel_1"), self.s.object("Stage/Panel_2")
        self.assertEqual(p1.geometries, p2.geometries)
        self.assertEqual(p1.fingerprint, p2.fingerprint)
        self.assertNotEqual(p1.world, p2.world)

    def test_material_slots_belong_to_one_object(self):
        w = self.s.object("Stage/Wall")
        self.assertEqual((len(w.geometries), w.slots), (2, ("LED_A", "LED_B")))
        self.assertTrue(w.assignable)

    def test_objects_that_cannot_show_media(self):
        self.assertEqual(self.s.object("Stage/Truss").issue, "no UVs")
        self.assertFalse(self.s.object("Stage/Truss").assignable)
        self.assertTrue(self.s.object("Stage/Spill").outside_0_1)
        self.assertTrue(self.s.object("Stage/Spill").assignable)       # shown, with neutral beyond the image
        self.assertEqual(len(self.s.warnings), 2)

    def test_mirrored_object_keeps_its_negative_scale(self):
        """The renderer needs it to flip the winding; the importer must not hide it."""
        self.assertLess(det3(self.s.object("Stage/Mirror").world), 0)
        self.assertGreater(det3(self.s.object("Stage/Screens/Centre").world), 0)

    def test_metres_and_y_up(self):
        lo, hi = self.s.bounds
        self.assertAlmostEqual(hi[1], 5.0, 3)                           # truss top: 4.6 + 0.4 m, Y up
        self.assertAlmostEqual(lo[0], -6.0, 3)
        c = self.exp["objects"]["Stage/Screens/Centre"]["bounds"]
        got = world_vertices(self.s, self.s.object("Stage/Screens/Centre"))
        for a in range(3):
            self.assertAlmostEqual(min(g[a] for g in got), c[0][a], 3)
            self.assertAlmostEqual(max(g[a] for g in got), c[1][a], 3)

    def test_repeat_import_is_identical(self):
        again = load_scene(fx("stage.fbx"))
        self.assertEqual([(o.key, o.fingerprint, o.geometries) for o in again.objects],
                         [(o.key, o.fingerprint, o.geometries) for o in self.s.objects])
        self.assertEqual(again.source["sha256"], self.s.source["sha256"])

    def test_centimetre_z_up_file_arrives_the_same(self):
        """The same stage authored in centimetres and exported Z up: same size, same way up,
        same UVs. (Logo_B keeps its own name in this file.)"""
        cm = load_scene(fx("stage_cm_zup.fbx"))
        for a in range(3):
            self.assertAlmostEqual(cm.bounds[0][a], self.s.bounds[0][a], 3)
            self.assertAlmostEqual(cm.bounds[1][a], self.s.bounds[1][a], 3)
        for o in self.s.objects:
            key = "Stage/Logo_B" if o.key == "Stage/Logo_A#2" else o.key
            other = cm.object(key)
            with self.subTest(o.key):
                self.assertIsNotNone(other)
                key3 = lambda g: (round(g[0], 3) + 0.0, round(g[1], 3) + 0.0, round(g[2], 3) + 0.0, g[3], g[4])
                a, b = sorted(world_vertices(self.s, o), key=key3), sorted(world_vertices(cm, other), key=key3)
                self.assertEqual(len(a), len(b))
                for p, q in zip(a, b):
                    self.assertTrue(all(abs(p[i] - q[i]) < 1e-3 for i in range(3)), (p, q))
                    self.assertEqual(p[3:], q[3:])


@unittest.skipUnless(HAVE, "needs assimp-py")
class StageOBJ(unittest.TestCase):
    def test_same_stage_from_obj(self):
        """OBJ has no hierarchy or instances: keys are object names, each object its own mesh.
        UVs match Blender's to the 6 decimals OBJ stores."""
        with open(fx("expected.json")) as fh:
            exp = json.load(fh)["objects"]
        s = load_scene(fx("stage.obj"))
        self.assertEqual(sorted(o.key for o in s.objects), sorted(k.rsplit("/", 1)[1] for k in exp))
        self.assertNotEqual(s.object("Panel_1").geometries, s.object("Panel_2").geometries)
        for path, e in exp.items():
            o = s.object(path.rsplit("/", 1)[1])
            with self.subTest(path):
                self.assertEqual((o.triangles, o.uv_sets, list(o.slots)), (e["triangles"], min(e["uv_sets"], 1), e["slots"]))
                if e["uv_sets"]:
                    got = world_vertices(s, o)
                    for x, y, z, u, v in e["corners"][0]:
                        self.assertTrue(any(abs(g[0] - x) < 2e-4 and abs(g[1] - y) < 2e-4 and abs(g[2] - z) < 2e-4
                                            and abs(g[3] - u) < 1e-6 and abs(g[4] - v) < 1e-6 for g in got), (path, x, y, z, u, v))

    def test_edge_cases(self):
        with open(fx("expected.json")) as fh:
            exp = json.load(fh)["edge_obj"]
        s = load_scene(fx("edge.obj"))
        self.assertEqual(sorted(o.key for o in s.objects), sorted(exp))
        for key, e in exp.items():
            o = s.object(key)
            with self.subTest(key):
                self.assertEqual(o.triangles, e["triangles"])
                if e["uv"] is None:
                    self.assertEqual((o.uv_sets, o.issue), (0, "no UVs"))
                else:
                    got = {(round(g[3], 6), round(g[4], 6)) for g in world_vertices(s, o)}
                    self.assertEqual(got, {(float(u), float(v)) for u, v in e["uv"]})


@unittest.skipUnless(HAVE, "needs assimp-py")
class BadInput(unittest.TestCase):
    def test_damaged_files_raise_a_readable_error(self):
        for name in ("truncated.fbx", "garbage.fbx", "truncated.obj"):
            with self.subTest(name), self.assertRaises(SceneImportError) as cm:
                load_scene(fx(name))
            self.assertTrue(str(cm.exception).startswith("Could not read this scene: "), str(cm.exception))
            self.assertNotIn(FIX, str(cm.exception))                    # Assimp's path prefix removed

    def test_missing_unsupported_and_cancelled(self):
        with self.assertRaisesRegex(SceneImportError, "File not found"):
            load_scene(fx("nope.fbx"))
        with self.assertRaisesRegex(SceneImportError, "OBJ and FBX"):
            load_scene(fx("expected.json"))
        stop = threading.Event()
        stop.set()
        with self.assertRaisesRegex(SceneImportError, "cancelled"):
            load_scene(fx("stage.fbx"), cancel=stop)

    def test_lines_and_points(self):
        """assimp-py 1.2 refuses a file with any line geometry. An OBJ is read again without its
        line and point statements (SketchUp writes every edge as one, Blender loose edges); a
        file of lines alone has nothing to show media on. The source file is left as it was."""
        with tempfile.TemporaryDirectory() as d:
            for name, text in (("lines.obj", "o Wire\nv 0 0 0\nv 1 0 0\nv 1 1 0\nl 1 2 3\n"),
                               ("mixed.obj", "mtllib mixed lib.mtl\no Screen\nv 0 0 0\nv 1 0 0\nv 1 1 0\nvt 0 0\nvt 1 0\nvt 1 1\n"
                                             "usemtl LED\nf 1/1 2/2 3/3\no Wire\nv 0 0 1\nv 1 0 1\nl 4 5\np 4\nl 1 4\n")):
                with open(os.path.join(d, name), "w") as fh:
                    fh.write(text)
            with open(os.path.join(d, "mixed lib.mtl"), "w") as fh:
                fh.write("newmtl LED\nKd 1 1 1\n")
            with self.assertRaisesRegex(SceneImportError, "only lines or points"):
                load_scene(os.path.join(d, "lines.obj"))
            before = open(os.path.join(d, "mixed.obj")).read()
            s = load_scene(os.path.join(d, "mixed.obj"))
            self.assertEqual([(o.display, o.triangles, o.slots, o.uv_sets) for o in s.objects], [("Screen", 1, ("LED",), 1)])
            self.assertIn("3 line or point elements ignored (only surfaces can show media).", s.warnings)
            self.assertEqual(s.source["path"], os.path.join(d, "mixed.obj"))
            self.assertEqual(open(os.path.join(d, "mixed.obj")).read(), before)

    def test_empty_and_unexplained_failures_say_what_to_do(self):
        with tempfile.TemporaryDirectory() as d:
            empty = os.path.join(d, "empty.obj")
            open(empty, "w").close()
            with self.assertRaisesRegex(SceneImportError, r"empty \(0 bytes\)"):
                load_scene(empty)
            from nitpix.scene import _assimp_message
            fbx = os.path.join(d, "new.fbx")
            with open(fbx, "wb") as fh:
                fh.write(b"Kaydara FBX Binary  \x00\x1a\x00" + (7700).to_bytes(4, "little") + bytes(64))
            msg = _assimp_message(RuntimeError(f"Assimp error loading '{fbx}': "), fbx)
            self.assertTrue(msg.startswith("the importer gave no reason (FBX 7.7). "), msg)
            self.assertIn("export it again", msg)

    def test_no_surfaces(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "empty.obj")
            with open(p, "w") as fh:
                fh.write("o Nothing\nv 0 0 0\nv 1 0 0\n")
            with self.assertRaises(SceneImportError):
                load_scene(p)


@unittest.skipUnless(HAVE, "needs assimp-py")
class UVEdge(unittest.TestCase):
    """uvedge.fbx (tools/scenes/make_uv_fixtures.py): where UVs fall, measured on triangles."""

    @classmethod
    def setUpClass(cls):
        cls.s = load_scene(fx("uvedge.fbx"))

    def r(self, key, uv_set=0):
        o = self.s.object(key)
        return [self.s.geometries[g].uv_ranges[uv_set] for g in o.geometries][0]

    def test_ranges_and_the_surface_that_shows_neutral(self):
        e = 1e-6                                                       # float32 in the file
        for key, uv_set, u, v, outside, neutral in (
                ("Noise", 0, (-8e-5, 1 + 8e-5), (-8e-5, 1 + 8e-5), 0, 0.0),            # export noise: on the image
                ("Beyond", 0, (-4e-4, 1 + 4e-4), (-4e-4, 1 + 4e-4), 8, 1 - (1.0002 / 1.0008) ** 2),
                ("Islands", 0, (-0.8, 1.7), (0.0, 1.0), 4, 0.5),                       # two of four quads
                ("Tiles", 0, (-1.69, 2.74), (-0.475, 1.476), 8, 1 - 1 / (4.43 * 1.951)),
                ("Sets", 0, (0.0, 4.0), (0.0, 2.0), 8, 1 - 1 / 8),                     # tiled 0-4 x 0-2
                ("Sets", 1, (0.0, 1.0), (0.0, 1.0), 0, 0.0)):                          # its "Fit" set
            with self.subTest(key, uv_set=uv_set):
                r = self.r(key, uv_set)
                self.assertTrue(all(abs(a - b) < e for a, b in zip(r.u + r.v, u + v)), (r.u, r.v))
                self.assertEqual((r.outside, r.triangles), (outside, 8))
                self.assertAlmostEqual(r.neutral_share, neutral, places=4)
        self.assertFalse(self.s.object("Noise").outside_0_1)
        self.assertTrue(self.s.object("Beyond").outside_0_1)
        self.assertAlmostEqual(self.r("Tiles").overshoot, 1.74, places=5)

    def test_the_tolerance_is_small(self):
        self.assertLess(UV_EDGE, 0.5 / 3840)                           # under half a texel of the largest preview

    def test_unused_vertices_cover_nothing(self):
        from nitpix.scene import uv_range
        uv = array("f", [0, 0, 1, 0, 1, 1, 7.5, -3.0])                 # vertex 3 is used by no triangle
        pos = array("f", [0, 0, 0, 1, 0, 0, 1, 1, 0, 9, 9, 9])
        r = uv_range(uv, array("I", [0, 1, 2]), pos)
        self.assertEqual((r.u, r.v, r.outside, r.neutral_share), ((0.0, 1.0), (0.0, 1.0), 0, 0.0))

    def test_partly_outside_triangle_is_clipped_exactly(self):
        from nitpix.scene import uv_range
        uv = array("f", [0, 0, 2, 0, 0, 1])                            # half of it (by area) lies beyond U 1
        pos = array("f", [0, 0, 0, 2, 0, 0, 0, 1, 0])
        r = uv_range(uv, array("I", [0, 1, 2]), pos)
        self.assertEqual(r.outside, 1)
        self.assertAlmostEqual(r.neutral_share, 0.25, places=3)       # the corner beyond U 1: (1/2)² of it


@unittest.skipUnless(HAVE and os.environ.get("NITPIX_SLOW") == "1", "set NITPIX_SLOW=1 (and have assimp-py)")
class Large(unittest.TestCase):
    def test_million_triangles(self):
        n = 500                                       # 500 x 1000 quads = 1,000,000 triangles
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "big.obj")
            with open(p, "w") as fh:
                fh.write("o Wall\n")
                for j in range(n + 1):
                    fh.writelines(f"v {i / (2 * n):.6f} {j / n:.6f} 0\n" for i in range(2 * n + 1))
                for j in range(n + 1):
                    fh.writelines(f"vt {i / (2 * n):.6f} {j / n:.6f}\n" for i in range(2 * n + 1))
                w = 2 * n + 1
                for j in range(n):
                    for i in range(2 * n):
                        a = j * w + i + 1
                        fh.write(f"f {a}/{a} {a + 1}/{a + 1} {a + w + 1}/{a + w + 1} {a + w}/{a + w}\n")
            t = time.perf_counter()
            s = load_scene(p)
            dt = time.perf_counter() - t
        o = s.object("Wall")
        print(f"\n1,000,000-triangle OBJ: {dt:.2f} s, {len(s.geometries['mesh:0'].positions) // 3:,} vertices")
        self.assertEqual(o.triangles, 1_000_000)
        self.assertLess(dt, 30)


if __name__ == "__main__":
    unittest.main()
