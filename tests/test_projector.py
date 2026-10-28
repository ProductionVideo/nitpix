"""The projector's geometry (nitpix/projector.py): aim, roll, throw ratio and raster, content fit,
the edits (aim at a point, move, place, match the view), the saved form and its migration from
version 1, and the cases with no sensible answer. No Qt.
python3 -W ignore -m unittest tests.test_projector"""
import copy
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nitpix import mapsave  # noqa: E402
from nitpix.projector import Projector, from_view, mat_mul, raster_for, throw_from_fov, transform  # noqa: E402


def ndc(p, point, near=0.1, far=100.0):
    x, y, z, w = transform(mat_mul(p.projection(near, far), p.view()), point)
    return x / w, y / w, w


def r9(v):
    return [round(c, 9) + 0.0 for c in v]


class Geometry(unittest.TestCase):
    def test_throw_ratio_sizes_the_image(self):
        """The design case: a 4 m × 2.25 m screen 6 m away, TR 1.5, 1920 × 1080 native: exactly filled."""
        p = Projector(position=(0, 1.125, 6), target=(0, 1.125, 0), throw=1.5, raster=(1920, 1080))
        w, h = p.image_size()
        self.assertAlmostEqual(w, 4.0)
        self.assertAlmostEqual(h, 2.25)
        tl, tr, br, bl = p.corners(6.0)
        self.assertEqual(r9(tl), [-2.0, 2.25, 0.0])
        self.assertEqual(r9(br), [2.0, 0.0, 0.0])
        for (sx, sy), corner in zip(((-1, 1), (1, 1), (1, -1), (-1, -1)), (tl, tr, br, bl)):
            cx, cy, cw = ndc(p, corner)
            self.assertAlmostEqual(cx, sx)
            self.assertAlmostEqual(cy, sy)
            self.assertAlmostEqual(cw, 6.0)                                # clip w: distance along the aim
        self.assertAlmostEqual(p.h_fov, math.degrees(2 * math.atan(1 / 3)))  # derived, never stored
        self.assertAlmostEqual(throw_from_fov(p.h_fov), 1.5)
        self.assertEqual(p.with_(raster=(1920, 1200)).image_size(), (4.0, 2.5))   # width from TR, height from the raster

    def test_the_clip_never_moves_the_beam(self):
        p = Projector(raster=(1920, 1080))
        self.assertEqual(p.image_aspect(2.5), p.image_aspect(4 / 3))
        self.assertEqual(p.content_rect(16 / 9), (0.0, 0.0, 1.0, 1.0))
        left, bottom, w, h = p.content_rect(2.5)                           # wider clip: bars above and below
        self.assertEqual((left, w), (0.0, 1.0))
        self.assertAlmostEqual(h, (16 / 9) / 2.5)
        self.assertAlmostEqual(bottom, (1 - h) / 2)
        left, bottom, w, h = p.content_rect(4 / 3)                         # narrower: bars at the sides
        self.assertAlmostEqual(w, 0.75)
        self.assertAlmostEqual(left, 0.125)
        self.assertEqual(p.with_(fit="stretch").content_rect(2.5), (0.0, 0.0, 1.0, 1.0))

    def test_aim_level_and_roll(self):
        p = Projector(position=(0, 2, 10), target=(0, 2, 0))
        r, u, f = p.basis()
        self.assertEqual(r9(r), [1, 0, 0])                                 # from the audience: image right is +X
        self.assertEqual(r9(u), [0, 1, 0])
        self.assertEqual(r9(f), [0, 0, -1])
        tilted = p.aimed_at((3, 0, 0))                                     # pan and tilt: the horizon stays level
        self.assertAlmostEqual(tilted.basis()[0][1], 0.0)
        rolled = p.with_(roll=90)                                          # top turned to the right
        self.assertEqual(r9(rolled.basis()[1]), [1, 0, 0])
        self.assertEqual(r9(rolled.basis()[0]), [0, -1, 0])
        self.assertEqual(r9(rolled.aimed_at((0, 2, -5)).basis()[1]), [1, 0, 0])   # aiming keeps the roll

    def test_near_vertical_is_predictable(self):
        down = Projector(position=(0, 8, 0), target=(0, 0, 0))
        self.assertEqual(r9(down.basis()[1]), [0, 0, -1])                  # image top upstage
        self.assertEqual(r9(down.basis()[0]), [1, 0, 0])
        near = down.aimed_at((0, 0, 0.01))                                 # 0.07° off vertical: same rule
        self.assertEqual([round(c, 2) + 0.0 for c in near.basis()[1]], [0, 0, -1])
        from_front = Projector(position=(0, 8, 0.5), target=(0, 0, 0))     # 3.6° off, from downstage: no flip
        self.assertGreater(-from_front.basis()[1][2], 0.99)
        up = Projector(position=(0, 0, 0), target=(0, 8, 0))
        self.assertEqual(r9(up.basis()[1]), [0, 0, 1])
        self.assertEqual(r9(down.with_(roll=180).basis()[1]), [0, 0, 1])   # roll still applies
        for q in (down, near, up, from_front):                             # no singular matrices
            self.assertTrue(all(math.isfinite(v) for row in q.view() for v in row))
            r, u, f = q.basis()
            self.assertAlmostEqual(sum(a * b for a, b in zip(r, u)), 0)

    def test_edits(self):
        p = Projector(position=(0, 2, 10), target=(0, 2, 0), roll=5, throw=2.0)
        aimed = p.aimed_at((1.5, 1.0, 0.5))
        self.assertEqual((aimed.position, aimed.roll, aimed.throw), (p.position, 5, 2.0))
        moved = p.moved((1, 0, 0))                                         # orientation kept: the target moves too
        self.assertEqual((moved.position, moved.target), ((1, 2, 10), (1, 2, 0)))
        self.assertEqual(moved.basis()[2], p.basis()[2])
        locked = p.moved((1, 0, 0), keep_aim=True)                         # aim kept: the target stays
        self.assertEqual((locked.position, locked.target), ((1, 2, 10), (0, 2, 0)))
        placed = p.placed((4, 3, 8), (0, 1, 0))                            # the pose only
        self.assertEqual((placed.throw, placed.roll, placed.raster), (2.0, 5, p.raster))

    def test_what_cannot_project(self):
        self.assertRegex(Projector(position=(1, 1, 1), target=(1, 1, 1)).problem(), "at the projector.s position")
        self.assertIn("throw ratio", Projector(throw=0.05).problem())
        self.assertIn("raster", Projector(raster=(8, 1080)).problem())
        self.assertIsNone(Projector().problem())

    def test_match_view(self):
        """The largest image of the raster's shape that fits the view: same framing when looked through."""
        wide = from_view((0, 2, 10), (0, 2, 0), 40, 2.0)                  # window wider than the raster
        self.assertAlmostEqual(1 / (2 * wide.throw), math.tan(math.radians(20)) * 16 / 9, 3)
        tall = from_view((0, 2, 10), (0, 2, 0), 40, 1.2)                  # narrower: width decides
        self.assertAlmostEqual(1 / (2 * tall.throw), math.tan(math.radians(20)) * 1.2, 3)
        self.assertEqual((tall.position, tall.target), ((0.0, 2.0, 10.0), (0.0, 2.0, 0.0)))

    def test_depth_range_holds_the_stage(self):
        p = Projector(position=(0, 2, 10), target=(0, 2, 0))
        near, far = p.depth_range(((-6, 0, 0), (6, 5, 14)))
        self.assertGreater(far, math.dist((0, 2, 10), (6, 5, 0)))
        self.assertGreaterEqual(near, 1e-3)
        self.assertLess(near, 0.01)


V1 = {"version": 1, "resources": {}, "sources": [], "next_id": 1, "assignments": {}, "clip": 3, "tab": "projectors",
      "camera": {"yaw": 10, "pitch": 5, "dist": 9, "target": [0, 1, 0]},
      "projectors": [{"id": "p1", "position": [0, 2, 10], "target": [0, 2, 0], "h_fov": 30, "aspect": None,
                      "enabled": True, "show_frustum": False}]}


class Saved(unittest.TestCase):
    def test_round_trip(self):
        p = Projector(position=(1, 2, 3), target=(0, 1, 0), roll=-12.5, throw=1.37, raster=(3840, 2160), fit="stretch")
        doc = mapsave.validate({**copy.deepcopy(V1), "version": 2, "projectors": [mapsave.projector_to(p)],
                                "camera": {**V1["camera"], "view": "top"}})
        self.assertEqual(mapsave.projector_from(doc["projectors"][0]), p)
        self.assertEqual(doc["camera"]["view"], "top")

    def test_version_1_keeps_its_picture(self):
        """Throw from the angle; the raster from the clip it followed, marked inferred; an explicit aspect stays stretched."""
        doc = mapsave.validate(copy.deepcopy(V1), clip_aspect=1280 / 512)
        p = mapsave.projector_from(doc["projectors"][0])
        self.assertAlmostEqual(p.h_fov, 30.0)
        self.assertEqual((p.raster, p.raster_note, p.fit, p.show_frustum), ((1920, 768), "inferred", "fit", False))
        self.assertEqual(p.content_rect(1280 / 512), (0.0, 0.0, 1.0, 1.0))  # the whole clip, as before
        self.assertEqual(doc["camera"]["view"], "perspective")
        self.assertEqual(doc["migrated_from"], 1)
        fixed = copy.deepcopy(V1)
        fixed["projectors"][0]["aspect"] = 4 / 3
        q = mapsave.projector_from(mapsave.validate(fixed, clip_aspect=2.5)["projectors"][0])
        self.assertEqual((q.raster, q.fit), (raster_for(4 / 3), "stretch"))  # clip stretched to it, as version 1 drew it
        unknown = mapsave.projector_from(mapsave.validate(copy.deepcopy(V1))["projectors"][0])
        self.assertEqual(unknown.raster, (1920, 1080))

    def test_what_is_refused(self):
        good = {**copy.deepcopy(V1), "version": 2, "projectors": [mapsave.projector_to(Projector())]}
        for name, change in (("throw", {"throw_ratio": 0}), ("raster", {"raster": [1920]}), ("fit", {"fit": "zoom"}),
                             ("raster size", {"raster": [1920, 99999]})):
            with self.subTest(name):
                bad = copy.deepcopy(good)
                bad["projectors"][0].update(change)
                with self.assertRaises(mapsave.SetupError):
                    mapsave.validate(bad)
        with self.assertRaises(mapsave.SetupError):
            mapsave.validate({**good, "version": 3})                       # a newer nitpix's: set aside, not misread
        with self.assertRaises(mapsave.SetupError):
            mapsave.validate({**good, "camera": {**V1["camera"], "view": "iso"}})


if __name__ == "__main__":
    unittest.main()
