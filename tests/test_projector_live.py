"""The projector preview against real libmpv in a real window (nitpix/ui/projection.py), on the
UV-less stages in tests/fixtures/scenes/projector: the picture lands the right way round and at the
clip's aspect, follows a curved surface whatever the viewpoint, is blocked by nearer surfaces, and
stays off what is behind the projector, outside its image or facing away. Also: no shadow acne or
light leaking at a grazing angle, the visibility texture reused while video plays and the camera
moves, nothing sent to the player for camera or projector edits, and no GL errors while modes,
projector and stage change. Needs libmpv, ffmpeg, assimp-py and a display:
    NITPIX_LIVE_PLAYER=1 .venv/bin/python -m unittest tests.test_projector_live -v
Pixel readback here is verification only; the view never reads frames back.
"""
import os as _os, tempfile as _tempfile; _os.environ.setdefault("NITPIX_STATE_DIR", _tempfile.mkdtemp(prefix="nitpix state "))  # keep the real recent list untouched
import collections
import importlib.util
import math
import os
import shutil
import subprocess
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures", "scenes")
PROJ = os.path.join(FIX, "projector")
LIVE = (os.environ.get("NITPIX_LIVE_PLAYER") == "1" and importlib.util.find_spec("PySide6") is not None
        and importlib.util.find_spec("assimp_py") is not None)
if LIVE:
    from PySide6.QtCore import QSettings
    from PySide6.QtGui import QSurfaceFormat, QVector3D
    from PySide6.QtWidgets import QApplication

    from nitpix import libmpv
    from nitpix.probe import ffprobe, find_tool, normalise, summary_fields
    from nitpix.projector import Projector, mat_mul, raster_for, throw_from_fov, transform
    from nitpix.scene import load_scene
    from nitpix.stage import Stage
    from nitpix.ui.mapview import MappingView
    from nitpix.ui.mpvwidget import gl_format

    FFMPEG = find_tool("ffmpeg")
    LIVE = libmpv.available() and bool(FFMPEG)

RED, GREEN, BLUE, YELLOW, MAGENTA = (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0), (255, 0, 255)
FRONT = dict(position=(0.0, 2.0, 10.0), target=(0.0, 2.0, 0.0), throw=1 / (2 * math.tan(math.radians(15))))
WIDE = dict(FRONT, raster=(1920, 768))                    # 2.5:1, the corners clip's shape


def pump(sec):
    end = time.monotonic() + sec
    while time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.002)


def like(got, want, tol=60):
    return all(abs(a - b) <= tol for a, b in zip(got, want))


def neutral(c):
    """The unlit stage grey (lit by the headlight only), not media."""
    return max(c) - min(c) <= 12 and 25 <= c[0] <= 150


def on_wall(p, aspect, u, v, z=0.0):
    """Where image point (u, v) (v = 1 the top) lands on the plane z, by the projector's own rays."""
    r, up, f = p.basis()
    th = 1 / (2 * p.throw)
    d = [f[i] + (2 * u - 1) * th * r[i] + (2 * v - 1) * th / aspect * up[i] for i in range(3)]
    t = (z - p.position[2]) / d[2]
    return tuple(p.position[i] + t * d[i] for i in range(3))


def image_uv(p, aspect, point):
    x, y, _z, w = transform(mat_mul(p.projection(0.1, 100.0), p.view()), point)
    return (x / w + 1) / 2, (y / w + 1) / 2


@unittest.skipUnless(LIVE, "set NITPIX_LIVE_PLAYER=1 with libmpv, ffmpeg, assimp-py and a display")
class Projected(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="nitpix projector live ")
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, cls.tmp)
        QSurfaceFormat.setDefaultFormat(gl_format())
        cls.app = QApplication.instance() or QApplication([])
        ff = lambda *a: subprocess.run([FFMPEG, "-v", "error", "-y", *a], check=True)
        cls.corners = os.path.join(cls.tmp, "corners.mov")    # 1280x512 (2.5:1) grey: TL red, TR green, BL blue, BR yellow
        ff("-f", "lavfi", "-i", "color=0x808080:s=1280x512:r=25:d=4", "-vf",
           "drawbox=x=0:y=0:w=200:h=150:c=red:t=fill,drawbox=x=1080:y=0:w=200:h=150:c=0x00FF00:t=fill,"
           "drawbox=x=0:y=362:w=200:h=150:c=blue:t=fill,drawbox=x=1080:y=362:w=200:h=150:c=yellow:t=fill",
           "-c:v", "prores_ks", "-profile:v", "3", cls.corners)
        cls.ramp = os.path.join(cls.tmp, "ramp.mov")          # red = u, green = v (1 at the top), 16:9
        ff("-f", "lavfi", "-i", "color=black:s=1024x576:r=25:d=4,format=rgb24,geq=r='255*X/W':g='255*(1-Y/H)':b=0",
           "-c:v", "prores_ks", "-profile:v", "4", "-pix_fmt", "yuv444p10le", cls.ramp)
        cls.magenta = os.path.join(cls.tmp, "magenta.mov")    # one colour: lit or not, nothing else
        ff("-f", "lavfi", "-i", "color=0xFF00FF:s=1280x720:r=25:d=4", "-c:v", "prores_ks", "-profile:v", "3", cls.magenta)
        cls.bars = os.path.join(cls.tmp, "bars.mp4")
        ff("-f", "lavfi", "-i", "color=black:s=640x360:r=25:d=12,format=gray,geq=lum='255*mod(floor(N/pow(2\\,floor(X*16/W)))\\,2)'",
           "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0", cls.bars)
        cls.stage = load_scene(os.path.join(PROJ, "projection.obj"))
        cls.curve = load_scene(os.path.join(PROJ, "curve.obj"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def make(self, scene, clip, frame=10, projector=None, aspect=None):
        v = MappingView()
        v.resize(1200, 700)
        v.show()
        pump(0.2)
        self.addCleanup(self.dispose, v)
        v.set_scene(scene)
        v.load(None, {"id": 1, "kind": "video", "name": os.path.basename(clip), "path": clip,
                      **summary_fields(normalise(ffprobe(clip), clip))}, frame)
        end = time.monotonic() + 10
        while not v.loaded and time.monotonic() < end:
            pump(0.01)
        self.assertTrue(v.loaded)
        v.seek_frame(frame)
        pump(0.5)
        v.set_mode("projectors")
        v.set_projector(projector or Projector(**FRONT), aspect)
        return v

    @staticmethod
    def dispose(v):
        v.shutdown()
        v.close()
        v.deleteLater()
        pump(0.05)

    def camera(self, v, target, yaw, pitch, dist):
        v.cam_target, v.yaw, v.pitch, v.dist = QVector3D(*target), float(yaw), float(pitch), float(dist)
        v._camera_changed()
        pump(0.15)
        return v.grabFramebuffer()

    def pixel(self, v, img, p):
        W, H = img.width(), img.height()
        c = v.view_projection(W, H).map(QVector3D(*p))
        x, y = (c.x() + 1) / 2 * W, (1 - c.y()) / 2 * H
        self.assertTrue(0 <= x < W and 0 <= y < H, (p, x, y))
        q = img.pixelColor(int(x), int(y))
        return q.red(), q.green(), q.blue()

    def test_labelled_clip_lands_the_right_way_round_at_its_aspect(self):
        """No UVs anywhere. Corners in their places, the image's edges where the projector's
        rays put them (2.5:1 from the clip), from two viewpoints."""
        p = Projector(**WIDE)
        v = self.make(self.stage, self.corners, projector=p, aspect=2.5)
        for yaw in (20, 40):                                   # from the right: the occluder never hides a landmark
            img = self.camera(v, (0, 2, 0), yaw, 8, 9)
            at = lambda u, vv: self.pixel(v, img, on_wall(p, 2.5, u, vv))
            for (u, vv), want in (((0.06, 0.9), RED), ((0.94, 0.9), GREEN), ((0.06, 0.1), BLUE), ((0.94, 0.1), YELLOW)):
                self.assertTrue(like(at(u, vv), want), (yaw, u, vv, at(u, vv)))
            self.assertTrue(like(at(0.985, 0.9), GREEN), at(0.985, 0.9))          # just inside the right edge...
            self.assertTrue(neutral(at(1.03, 0.9)), at(1.03, 0.9))              # ...and just outside
            self.assertTrue(like(at(0.06, 0.985), RED), at(0.06, 0.985))        # the top edge: the aspect
            self.assertTrue(neutral(at(0.06, 1.04)), at(0.06, 1.04))
            self.assertTrue(neutral(self.pixel(v, img, (4.5, 2.0, 0.0))))       # outside the image entirely
        self.assertEqual(v.projection.size, (2048, 819))

    def test_a_curved_surface_takes_the_picture_where_the_rays_fall(self):
        """Every point of the half cylinder shows the image position the projector's ray gives it
        (red = u, green = v), and moving the orbit camera does not move it on the surface."""
        p = Projector(position=(0.0, 1.5, 8.0), target=(0.0, 1.5, 0.0), throw=throw_from_fov(30.0))
        v = self.make(self.curve, self.ramp, projector=p, aspect=16 / 9)
        for yaw, pitch in ((0, 5), (35, 15), (-30, -5)):
            img = self.camera(v, (0, 1.5, 0.75), yaw, pitch, 6)
            for deg in (-60, -40, -20, 0, 20, 40, 60):
                a = math.radians(deg)
                for y in (0.8, 1.5, 2.2):
                    pt = (1.5 * math.sin(a) * 0.999, y, 1.5 * math.cos(a) * 0.999)   # just inside the facets' chords
                    if (deg - yaw) > 70 or (deg - yaw) < -70:
                        continue                                                # seen edge-on from this camera
                    u, vv = image_uv(p, 16 / 9, pt)
                    c = self.pixel(v, img, pt)
                    self.assertTrue(abs(c[0] - 255 * u) <= 14 and abs(c[1] - 255 * vv) <= 14 and c[2] <= 14,
                                    (yaw, deg, y, c, round(255 * u), round(255 * vv)))

    def test_nearer_surfaces_block_and_the_rest_stays_neutral(self):
        p = Projector(**FRONT)
        v = self.make(self.stage, self.magenta, aspect=16 / 9)
        img = self.camera(v, (1.2, 1.4, 0.5), 60, 5, 7)                         # from the right: past the occluder
        self.assertTrue(like(self.pixel(v, img, (1.0, 1.5, 2.0)), MAGENTA))     # the occluder itself is lit...
        self.assertTrue(neutral(self.pixel(v, img, (1.25, 1.375, 0.0))))       # ...its shadow on the wall is not
        self.assertTrue(like(self.pixel(v, img, (2.3, 1.375, 0.0)), MAGENTA))   # beside the shadow: lit
        img = self.camera(v, (-1.3, 2.5, 0.5), -60, 5, 7)                       # from the left: past Backface
        self.assertTrue(neutral(self.pixel(v, img, (-1.33, 2.56, 0.0))))       # a surface facing away still blocks
        img = self.camera(v, (-1.2, 2.5, 1.0), 180, 0, 0.45)                    # between wall and Backface, looking at its front
        c = self.pixel(v, img, (-1.2, 2.5, 1.0))
        self.assertTrue(neutral(c) and c[0] > 60, c)                            # its front faces away: lit grey, no media
        img = self.camera(v, (0.0, 2.0, 13.0), 180, 0, 2.5)                     # behind the projector, looking at Rear
        self.assertTrue(neutral(self.pixel(v, img, (0.0, 2.0, 14.0))))          # inside the reversed frustum, never lit
        self.assertTrue(neutral(self.pixel(v, img, (1.0, 1.0, 14.0))))

    def test_grazing_light_neither_speckles_nor_leaks(self):
        """From the side at about 63° to the wall's normal. The wall is lit evenly (no self-shadow
        speckle), and the 5 cm-proud plaque's shadow strip (about 11 cm wide) is dark right up to it."""
        aim = (-0.5, 1.2, 0.0)
        p = Projector(position=(8.0, 2.0, 4.0), target=aim, throw=throw_from_fov(30.0))
        v = self.make(self.stage, self.magenta, projector=p, aspect=16 / 9)
        img = self.camera(v, (-0.5, 1.2, 0.0), 0, 0, 3.5)
        speckles, checked = [], 0
        for i in range(15):
            for j in range(9):
                pt = (-2.4 + i * 0.25, 0.75 + j * 0.11, 0.0)
                if -1.0 <= pt[0] <= -0.15 and 0.85 <= pt[1] <= 1.55:            # the plaque and its shadow
                    continue
                u, vv = image_uv(p, 16 / 9, pt)
                if not (0.03 < u < 0.97 and 0.03 < vv < 0.97):
                    continue
                checked += 1
                c = self.pixel(v, img, pt)
                if not like(c, MAGENTA):
                    speckles.append((pt, c))
        self.assertGreater(checked, 60)
        self.assertEqual(speckles, [])
        self.assertTrue(neutral(self.pixel(v, img, (-0.80, 1.2, 0.0))))         # 5 cm beside the plaque: in its shadow
        self.assertTrue(like(self.pixel(v, img, (-0.98, 1.2, 0.0)), MAGENTA))   # past the strip: lit
        self.assertTrue(like(self.pixel(v, img, (-0.38, 1.3, 0.05)), MAGENTA))  # the plaque (beside the target marker)

    def test_visibility_is_reused_while_playing_and_orbiting_and_edits_never_reach_the_player(self):
        v = self.make(self.stage, self.bars, frame=0, aspect=16 / 9)
        pump(0.3)
        self.assertEqual(v.projection.rebuilds, 1)
        v.stats = collections.deque(maxlen=2000)
        v.set_pause(False)
        for _ in range(80):
            v.orbit(3, 0.4)
            pump(0.01)
        self.assertGreater(sum(1 for s in v.stats if s[2]), 10)                 # new video frames were drawn...
        self.assertEqual(v.projection.rebuilds, 1)                              # ...with the same visibility
        v.set_projector(v.projector.with_(target=(0.5, 2.0, 0.0)), 16 / 9)       # an edit while playing
        pump(0.1)
        self.assertEqual(v.projection.rebuilds, 2)
        self.assertFalse(v.paused)
        v.set_pause(True)
        pump(0.3)
        sent = []
        orig = v.m.command, v.m.command_async
        v.m.command = lambda *a, **k: (sent.append(a), orig[0](*a, **k))[1]
        v.m.command_async = lambda *a, **k: (sent.append(a), orig[1](*a, **k))[1]
        for i in range(40):
            v.orbit(2, 0)
            if i % 10 == 0:
                v.set_projector(v.projector.with_(throw=1.8 + i / 100), 16 / 9)
            if i == 20:
                v.look_through(True)
            pump(0.01)
        v.m.command, v.m.command_async = orig
        self.assertEqual(sent, [])
        self.assertFalse(v.looking_through)                                     # orbiting returned to the orbit camera
        self.assertEqual(v.projection.rebuilds, 6)

    def test_modes_composed_stage_and_changes_without_gl_errors(self):
        """A composed stage with a UV screen: Surfaces shows its UV picture, Projectors the projected
        one (and nothing UV-mapped underneath), and back. Adding and removing files while projecting
        rebuilds visibility; glGetError after every paint stays clear."""
        st = Stage()
        screen = st.add(load_scene(os.path.join(FIX, "parts", "centre.obj")))
        st.add(load_scene(os.path.join(PROJ, "curve.obj")))   # in front of the screen's middle, clear of its corners
        key = screen.objects[0].key
        v = self.make(st, self.corners, projector=Projector(**WIDE), aspect=2.5)
        gl, paint = [], v.paintGL
        v.paintGL = lambda: (paint(), gl.append(v.context().functions().glGetError()))
        v.set_assignments({key: 0})
        o = st.object(key)                                     # the centre screen: z 0, x -3.2..3.2, y 0.5..4.1
        uv_tl = (-3.2 + 0.06 * 6.4, 0.5 + 0.9 * 3.6, 0.0)       # UV (0.06, 0.9): red by its UVs
        v.set_mode("surfaces")
        img = self.camera(v, (0, 2, 0), 10, 5, 12)
        self.assertTrue(like(self.pixel(v, img, uv_tl), RED), self.pixel(v, img, uv_tl))
        v.set_mode("projectors")
        img = self.camera(v, (0, 2, 0), 10, 5, 12)
        self.assertTrue(neutral(self.pixel(v, img, uv_tl)), self.pixel(v, img, uv_tl))   # outside the projected image: no UV picture
        p = Projector(**WIDE)
        tl = on_wall(p, 2.5, 0.06, 0.9, z=0.0)
        self.assertTrue(like(self.pixel(v, img, (tl[0], tl[1], 0.0)), RED))      # the projected corner, on the screen
        v.set_mode("surfaces")
        img = self.camera(v, (0, 2, 0), 10, 5, 12)
        self.assertTrue(like(self.pixel(v, img, uv_tl), RED))
        self.assertEqual(v.assigned, {key: 0})
        v.set_mode("projectors")
        pump(0.1)
        n = v.projection.rebuilds
        st.add(load_scene(os.path.join(PROJ, "projection.obj")))
        v.set_scene(st, frame=False)
        pump(0.2)
        self.assertEqual(v.projection.rebuilds, n + 1)
        st.remove(screen.id)
        v.set_scene(st, frame=False)
        pump(0.2)
        self.assertEqual(v.projection.rebuilds, n + 2)
        v.set_scene(None)
        pump(0.2)
        v.set_projector(None)
        pump(0.1)
        self.assertGreater(len(gl), 10)
        self.assertEqual(sorted(set(gl)), [0])
        self.assertIsNotNone(o)

    # ---- the setup workflow's GL parts -----------------------------------------------------------
    def test_a_clip_of_another_shape_is_fitted_and_its_bars_light_nothing(self):
        """A 2.5:1 clip in a 16:9 raster: the beam keeps the raster's shape; the clip sits in the middle,
        bars above and below project nothing. Stretch fills the raster instead."""
        p = Projector(**FRONT)                                               # 1920 x 1080
        v = self.make(self.stage, self.corners, projector=p, aspect=2.5)
        img = self.camera(v, (0, 2, 0), 40, 8, 9)
        bar = (16 / 9) / 2.5                                                  # the clip's height share of the image
        at = lambda u, vv: self.pixel(v, img, on_wall(p, 16 / 9, u, vv))
        self.assertTrue(like(at(0.06, 0.5 + bar / 2 - 0.04), RED), at(0.06, 0.5 + bar / 2 - 0.04))   # the clip's top-left
        self.assertTrue(neutral(at(0.06, 0.5 + bar / 2 + 0.06)), at(0.06, 0.5 + bar / 2 + 0.06))   # the bar above it
        self.assertTrue(neutral(at(0.5, 0.03)))                                                      # the bar below
        self.assertEqual(v.projection.size, (2048, 1152))                     # the raster's shape, not the clip's
        v.set_projector(p.with_(fit="stretch"), 2.5)
        img = self.camera(v, (0, 2, 0), 40, 8, 9)
        self.assertTrue(like(self.pixel(v, img, on_wall(p, 16 / 9, 0.06, 0.95)), RED))
        v.grid = True                                                         # the framing grid: drawn, no GL error
        img = self.camera(v, (0, 2, 0), 40, 8, 9)
        self.assertEqual(v.context().functions().glGetError(), 0)

    def test_picking_finds_the_nearest_surface_in_any_view(self):
        """GPU ray queries: the nearest of overlapping surfaces, a miss, transformed geometry, and
        the point under the cursor in perspective and orthographic views at this display's scale."""
        v = self.make(self.stage, self.magenta, aspect=16 / 9)
        hit = v.query_ray((1.0, 1.5, 10.0), (0.0, 0.0, -1.0))                 # the occluder stands in front of the wall
        self.assertEqual(hit[1], "Occluder")
        self.assertAlmostEqual(hit[0][2], 2.0, 3)
        hit = v.query_ray((4.0, 2.0, 10.0), (0.0, 0.0, -1.0))
        self.assertEqual(hit[1], "Wall")
        self.assertAlmostEqual(hit[0][2], 0.0, 3)
        self.assertIsNone(v.query_ray((0.0, 2.0, 10.0), (0.0, 1.0, 0.0)))     # nothing above
        v.gizmo = True                                                        # helpers drawn at the projector: never hit
        self.assertEqual(v.query_ray((0.0, 2.0, 12.0), (0.0, 0.0, -1.0))[1], "Wall")
        self.camera(v, (0, 2, 0), 20, 8, 9)                                  # in front of Rear (z 14), which faces the wall
        for kind, points in (("perspective", ((3.0, 1.0, 0.0), (-4.0, 3.5, 0.0), (1.0, 1.5, 2.0))),
                             ("front", ((4.5, 1.0, 0.0), (-4.5, 4.5, 0.0)))):            # beside Rear: it hides the rest
            v.set_view(kind)
            pump(0.1)
            for point in points:
                o, d = v.ray_at(v.to_screen(point))
                hit = v.query_ray(o, d)
                self.assertIsNotNone(hit, (kind, point))
                self.assertLess(math.dist(hit[0], point), 0.02, (kind, point, hit, v.devicePixelRatioF()))
        st = load_scene(os.path.join(FIX, "stage_cm_zup.fbx"))               # centimetres, Z up: transformed on import
        v.set_scene(st)
        pump(0.2)
        lo, hi = st.bounds
        c = [(a + b) / 2 for a, b in zip(lo, hi)]
        hit = v.query_ray((c[0], c[1], hi[2] + 5), (0.0, 0.0, -1.0))
        self.assertIsNotNone(hit)
        self.assertTrue(all(lo[i] - 1e-3 <= hit[0][i] <= hi[i] + 1e-3 for i in range(3)), (hit, st.bounds))

    def test_visibility_is_bounded_both_ways_for_any_raster(self):
        v = self.make(self.stage, self.magenta, aspect=16 / 9)
        gl, paint = [], v.paintGL
        v.paintGL = lambda: (paint(), gl.append(v.context().functions().glGetError()))
        for raster, size in (((1080, 1920), (1152, 2048)), ((16, 16384), (2, 2048)), ((16384, 16), (2048, 2))):
            v.set_projector(Projector(**FRONT, raster=raster), 16 / 9)
            pump(0.15)
            self.assertEqual(v.projection.size, size)
        self.assertEqual(sorted(set(gl)), [0])
        self.assertEqual(v.vis_problem, "")


if __name__ == "__main__":
    unittest.main()
