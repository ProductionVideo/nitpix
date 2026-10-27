"""The projector's geometry (nitpix/projector.py): aim, roll, field of view and aspect, the
conversion from the orbit view, and the cases with no sensible answer. No Qt.
python3 -W ignore -m unittest tests.test_projector"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nitpix.projector import Projector, from_view, mat_mul, transform  # noqa: E402


def ndc(p, aspect, point, near=0.1, far=100.0):
    x, y, z, w = transform(mat_mul(p.projection(aspect, near, far), p.view()), point)
    return x / w, y / w, w


class Geometry(unittest.TestCase):
    def test_aim_up_and_image_corners(self):
        p = Projector(position=(0, 2, 10), target=(0, 2, 0), h_fov=30)
        r, u, f = p.basis()
        self.assertEqual([round(c, 9) for c in r], [1, 0, 0])            # from the audience: image right is +X
        self.assertEqual([round(c, 9) for c in u], [0, 1, 0])
        self.assertEqual([round(c, 9) for c in f], [0, 0, -1])
        x, y, w = ndc(p, 2.5, (0, 2, 0))
        self.assertAlmostEqual(x, 0)
        self.assertAlmostEqual(w, 10)                                     # clip w: distance along the aim
        half_w = 10 * math.tan(math.radians(15))
        for (sx, sy), corner in zip(((-1, 1), (1, 1), (1, -1), (-1, -1)), p.corners(2.5, 10)):
            self.assertAlmostEqual(corner[0], sx * half_w)
            self.assertAlmostEqual(corner[1], 2 + sy * half_w / 2.5)
            cx, cy, _w = ndc(p, 2.5, corner)
            self.assertAlmostEqual(cx, sx)
            self.assertAlmostEqual(cy, sy)
        self.assertAlmostEqual(p.throw_ratio(), 1 / (2 * math.tan(math.radians(15))))

    def test_straight_down_and_up_have_a_fixed_roll(self):
        down = Projector(position=(0, 8, 0), target=(0, 0, 0), h_fov=40)
        r, u, f = down.basis()
        self.assertEqual([round(c, 9) for c in u], [0, 0, -1])           # image top upstage
        self.assertEqual([round(c, 9) for c in r], [1, 0, 0])
        near = Projector(position=(0, 8, 0.01), target=(0, 0, 0), h_fov=40)   # 0.07° off vertical: same rule
        self.assertEqual([round(c, 2) for c in near.basis()[1]], [0, 0, -1])
        up = Projector(position=(0, 0, 0), target=(0, 8, 0), h_fov=40)
        self.assertEqual([round(c, 9) for c in up.basis()[1]], [0, 0, 1])
        for p in (down, near, up):                                        # no singular matrices
            self.assertTrue(all(math.isfinite(v) for row in p.view() for v in row))
            r, u, f = p.basis()
            self.assertAlmostEqual(sum(a * b for a, b in zip(r, u)), 0)

    def test_what_cannot_project(self):
        self.assertRegex(Projector(position=(1, 1, 1), target=(1, 1, 1)).problem(), "at the projector.s position")
        self.assertIn("between", Projector(h_fov=0.2).problem())
        self.assertIsNone(Projector().problem())

    def test_aspect_follows_the_clip_unless_set(self):
        p = Projector()
        self.assertAlmostEqual(p.image_aspect(1280 / 512), 2.5)
        self.assertAlmostEqual(p.image_aspect(None), 16 / 9)
        self.assertAlmostEqual(p.with_(aspect=4 / 3).image_aspect(2.5), 4 / 3)

    def test_from_the_orbit_view(self):
        """The largest image of the clip's aspect that fits the view: same framing when looked through."""
        wide = from_view((0, 2, 10), (0, 2, 0), 40, 2.0, 16 / 9)          # window wider than the clip
        self.assertAlmostEqual(math.tan(math.radians(wide.h_fov / 2)), math.tan(math.radians(20)) * 16 / 9, 3)
        tall = from_view((0, 2, 10), (0, 2, 0), 40, 1.2, 16 / 9)          # narrower: width decides
        self.assertAlmostEqual(math.tan(math.radians(tall.h_fov / 2)), math.tan(math.radians(20)) * 1.2, 3)
        self.assertEqual((tall.position, tall.target), ((0.0, 2.0, 10.0), (0.0, 2.0, 0.0)))

    def test_depth_range_holds_the_stage(self):
        p = Projector(position=(0, 2, 10), target=(0, 2, 0))
        near, far = p.depth_range(((-6, 0, 0), (6, 5, 14)))
        self.assertGreater(far, math.dist((0, 2, 10), (6, 5, 0)))
        self.assertGreaterEqual(near, 1e-3)
        self.assertLess(near, 0.01)


if __name__ == "__main__":
    unittest.main()
