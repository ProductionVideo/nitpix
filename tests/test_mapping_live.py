"""Mapping against real libmpv in a real window: what is drawn on the generated stage (transforms,
orientation, instances, UV sets, neutral grey outside UV 0-1 and on unassigned objects) from
the metres FBX, the centimetre Z-up FBX and the OBJ; exact frames in the video texture while
the camera moves and the timeline is scrubbed; UVs within UV_EDGE of the image's edge (no grey
fringe) and beyond it (grey), at 25, 24000/1001 and 30000/1001; and switching
between QC and Mapping with a seek pending. Needs libmpv, ffmpeg, assimp-py and a display:
    NITPIX_LIVE_PLAYER=1 .venv/bin/python -m unittest tests.test_mapping_live -v
Pixel readback here is verification only; the view never reads frames back.
"""
import os as _os, tempfile as _tempfile; _os.environ.setdefault("NITPIX_STATE_DIR", _tempfile.mkdtemp(prefix="nitpix state "))  # keep the real recent list untouched
import importlib.util
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures", "scenes")
LIVE = (os.environ.get("NITPIX_LIVE_PLAYER") == "1" and importlib.util.find_spec("PySide6") is not None
        and importlib.util.find_spec("assimp_py") is not None)
if LIVE:
    from PySide6.QtCore import QSettings
    from PySide6.QtGui import QSurfaceFormat, QVector3D
    from PySide6.QtWidgets import QApplication

    from nitpix import libmpv, service, variant
    from nitpix.probe import ffprobe, find_tool, normalise, summary_fields
    from nitpix.scene import load_scene
    from nitpix.store import Project
    from nitpix.ui import theme
    from nitpix.ui.mapview import MappingView
    from nitpix.ui.mpvwidget import gl_format

    FFMPEG = find_tool("ffmpeg")
    LIVE = libmpv.available() and bool(FFMPEG)


def pump(sec):
    end = time.monotonic() + sec
    while time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.002)


def bars(img, y, n=16):
    """Frame number from 16 vertical bars across a QImage row (bar k white when bit k is set)."""
    w = img.width()
    v = [img.pixelColor(int((k + 0.5) * w / n), y).green() for k in range(n)]
    return sum(1 << k for k, a in enumerate(v) if a > 128) if all(a < 60 or a > 195 for a in v) else None


def point_at_uv(scene, key, u, v):
    """The scene-space point of object `key` whose first UV set is (u, v), from its triangles."""
    o = scene.object(key)
    w = o.world
    for gid in o.geometries:
        g = scene.geometries[gid]
        P, I, T = g.positions, g.indices, g.uv_sets[0]
        for t in range(0, len(I), 3):
            a, b, c = I[t], I[t + 1], I[t + 2]
            (ua, va), (ub, vb), (uc, vc) = (T[2 * a], T[2 * a + 1]), (T[2 * b], T[2 * b + 1]), (T[2 * c], T[2 * c + 1])
            d = (vb - vc) * (ua - uc) + (uc - ub) * (va - vc)
            if abs(d) < 1e-12:
                continue
            l1 = ((vb - vc) * (u - uc) + (uc - ub) * (v - vc)) / d
            l2 = ((vc - va) * (u - uc) + (ua - uc) * (v - vc)) / d
            l3 = 1 - l1 - l2
            if min(l1, l2, l3) >= -1e-6:
                x, y, z = (l1 * P[3 * a + i] + l2 * P[3 * b + i] + l3 * P[3 * c + i] for i in range(3))
                return tuple(w[i] * x + w[4 + i] * y + w[8 + i] * z + w[12 + i] for i in range(3))
    raise AssertionError(f"{key} has no point at uv {(u, v)}")


def centroid(scene, key):
    o = scene.object(key)
    g = scene.geometries[o.geometries[0]]
    n = len(g.positions) // 3
    x, y, z = (sum(g.positions[i::3]) / n for i in range(3))
    w = o.world
    return tuple(w[i] * x + w[4 + i] * y + w[8 + i] * z + w[12 + i] for i in range(3))


@unittest.skipUnless(LIVE, "set NITPIX_LIVE_PLAYER=1 with libmpv, ffmpeg, assimp-py and a display")
class Drawn(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="nitpix mapping live ")
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, cls.tmp)
        QSurfaceFormat.setDefaultFormat(gl_format())
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyleSheet(theme.STYLESHEET)
        ff = lambda *a: subprocess.run([FFMPEG, "-v", "error", "-y", *a], check=True)
        cls.corners = os.path.join(cls.tmp, "corners.mov")    # 1280x512 grey: TL red, TR green, BL blue, BR yellow
        ff("-f", "lavfi", "-i", "color=0x808080:s=1280x512:r=25:d=4", "-vf",
           "drawbox=x=0:y=0:w=200:h=150:c=red:t=fill,drawbox=x=1080:y=0:w=200:h=150:c=0x00FF00:t=fill,"
           "drawbox=x=0:y=362:w=200:h=150:c=blue:t=fill,drawbox=x=1080:y=362:w=200:h=150:c=yellow:t=fill",
           "-c:v", "prores_ks", "-profile:v", "3", cls.corners)
        cls.barclips = {}
        for name, rate in (("25", "25"), ("23976", "24000/1001"), ("2997", "30000/1001")):
            p = os.path.join(cls.tmp, f"bars {name}.mp4")
            ff("-f", "lavfi", "-i", f"color=black:s=640x360:r={rate}:d=12,format=gray,"
               "geq=lum='255*mod(floor(N/pow(2\\,floor(X*16/W)))\\,2)'",
               "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0", "-g", "48", "-bf", "2", p)
            cls.barclips[name] = (p, rate)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def make(self):
        v = MappingView()
        v.resize(1200, 700)
        v.show()
        pump(0.2)
        self.addCleanup(self.dispose, v)
        return v

    @staticmethod
    def dispose(v):
        v.shutdown()
        v.close()
        v.deleteLater()
        pump(0.05)

    def load(self, v, path, frame=0):
        v.load(None, {"id": 1, "kind": "video", "name": os.path.basename(path), "path": path,
                      **summary_fields(normalise(ffprobe(path), path))}, frame)
        end = time.monotonic() + 10
        while not v.loaded and time.monotonic() < end:
            pump(0.01)
        self.assertTrue(v.loaded)
        v.seek_frame(frame)
        pump(0.6)

    def pixel(self, v, img, p):
        """Colour drawn at scene point p (r, g, b)."""
        W, H = img.width(), img.height()
        c = v.view_projection(W, H).map(QVector3D(*p))         # with the perspective divide
        x, y = (c.x() + 1) / 2 * W, (1 - c.y()) / 2 * H
        self.assertTrue(0 <= x < W and 0 <= y < H, (p, x, y))
        q = img.pixelColor(int(x), int(y))
        return q.red(), q.green(), q.blue()

    def test_stage_files_draw_what_is_assigned(self):
        RED, GREEN, BLUE, YELLOW = (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)
        like = lambda got, want: all(abs(a - b) <= 60 for a, b in zip(got, want))
        grey = lambda c: max(c) - min(c) <= 12 and 25 <= c[0] <= 150      # the neutral surface, lit or not
        for name in ("stage.fbx", "stage_cm_zup.fbx", "stage.obj"):
            with self.subTest(name):
                sc = load_scene(os.path.join(FIX, name))
                key = lambda tail: next(o.key for o in sc.objects if o.key.split("/")[-1] == tail)
                v = self.make()
                v.set_scene(sc)
                shows = ["Centre", "Left", "Right", "Spill", "Panel_1", "Panel_2", "Mirror"]
                v.set_assignments({key(k): 0 for k in shows})
                self.load(v, self.corners, 10)
                img = v.grabFramebuffer()
                at = lambda k, u, vv: self.pixel(v, img, point_at_uv(sc, key(k), u, vv))
                for corner, (u, vv), want in (("TL", (0.06, 0.9), RED), ("TR", (0.94, 0.9), GREEN),
                                              ("BL", (0.06, 0.1), BLUE), ("BR", (0.94, 0.1), YELLOW)):
                    self.assertTrue(like(at("Centre", u, vv), want), (corner, at("Centre", u, vv)))
                for k in ("Left", "Right", "Panel_1", "Panel_2", "Mirror"):
                    self.assertTrue(like(at(k, 0.06, 0.9), RED), (k, at(k, 0.06, 0.9)))      # instances, mirrored too
                self.assertTrue(like(at("Spill", 0.06, 0.9), RED))                            # inside 0-1 on Spill...
                self.assertTrue(grey(at("Spill", -0.05, 0.9)), at("Spill", -0.05, 0.9))      # ...grey just outside, same triangle
                for k in ("Floor", "Truss"):
                    c = self.pixel(v, img, centroid(sc, key(k)))
                    self.assertTrue(grey(c), (k, c))                                          # unassigned: neutral
                if sc.object(key("Centre")).uv_sets > 1:                                       # the second UV set
                    p = point_at_uv(sc, key("Centre"), 0.2, 0.9)
                    self.assertTrue(grey(self.pixel(v, img, p)))                               # UV 1 there: the grey middle
                    v.set_assignments({**{key(k): 0 for k in shows}, key("Centre"): 1})
                    pump(0.2)
                    self.assertTrue(like(self.pixel(v, v.grabFramebuffer(), p), RED))          # UV 2: PixelMap -> the red corner

    def test_uv_edge_noise_shows_media_and_real_overshoot_stays_neutral(self):
        """uvedge.fbx with the corners clip. Close up (1 cm from the surface, about 10 µm a pixel),
        a UV overshoot within UV_EDGE shows the picture's edge, not a grey fringe; beyond it, grey."""
        RED, GREEN, BLUE = (255, 0, 0), (0, 255, 0), (0, 0, 255)
        like = lambda got, want: all(abs(a - b) <= 60 for a, b in zip(got, want))
        grey = lambda c: max(c) - min(c) <= 12 and 25 <= c[0] <= 150
        sc = load_scene(os.path.join(FIX, "uvedge.fbx"))
        v = self.make()
        v.set_scene(sc)
        v.set_assignments({o.key: 0 for o in sc.objects})
        self.load(v, self.corners, 10)
        img = v.grabFramebuffer()
        at = lambda k, u, vv: self.pixel(v, img, point_at_uv(sc, k, u, vv))
        for k in ("Noise", "Beyond", "Islands"):                               # authored positions unchanged
            self.assertTrue(like(at(k, 0.06, 0.9), RED), (k, at(k, 0.06, 0.9)))
            self.assertTrue(like(at(k, 0.94, 0.9), GREEN), (k, at(k, 0.94, 0.9)))
            self.assertTrue(like(at(k, 0.06, 0.1), BLUE), (k, at(k, 0.06, 0.1)))
        self.assertTrue(grey(at("Islands", 1.45, 0.5)), at("Islands", 1.45, 0.5))         # islands beyond the image
        self.assertTrue(grey(at("Islands", -0.55, 0.5)), at("Islands", -0.55, 0.5))

        def close(k, u, vv):
            p = point_at_uv(sc, k, u, vv)
            v.cam_target, v.yaw, v.pitch, v.dist = QVector3D(*p), 0.0, 0.0, 0.01
            v._camera_changed()
            pump(0.15)
            return self.pixel(v, v.grabFramebuffer(), p)

        for u, vv, want in ((-4e-5, 0.9, RED), (1 + 4e-5, 0.9, GREEN), (0.03, -4e-5, BLUE)):  # Noise: within 8e-5 of the edge
            c = close("Noise", u, vv)
            self.assertTrue(like(c, want), ("Noise", u, vv, c))
        c = close("Beyond", -2.5e-4, 0.9)                                      # Beyond: past UV_EDGE...
        self.assertTrue(grey(c), c)
        c = close("Beyond", 2.5e-4, 0.9)                                       # ...and just inside the image
        self.assertTrue(like(c, RED), c)

    def test_exact_frames_in_the_texture_while_navigating_and_scrubbing(self):
        sc = load_scene(os.path.join(FIX, "stage.fbx"))
        for name, (path, rate) in self.barclips.items():
            with self.subTest(rate=name):
                v = self.make()
                v.set_scene(sc)
                v.set_assignments({"Stage/Screens/Centre": 0})
                self.load(v, path, 0)
                v.set_pause(True)
                done = []
                v.scrub_settled.connect(lambda: done.append(True))
                for target in (40, 200, 7, 251):
                    for f in range(max(0, target - 30), target + 1, 3):
                        v.scrub_to(f)
                        v.orbit(4, 1)
                        pump(0.004)
                    v.scrub_to(target)
                    end = time.monotonic() + 5
                    while (v.scrub_pending() or not done) and time.monotonic() < end:
                        v.orbit(2, 0)
                        pump(0.01)
                    pump(0.2)
                    done.clear()
                    v.makeCurrent()
                    img = v.fbo.toImage()
                    v.doneCurrent()
                    self.assertEqual((bars(img, img.height() // 2), v.frame), (target, target))

    def test_camera_while_paused_never_touches_the_player(self):
        sc = load_scene(os.path.join(FIX, "stage.fbx"))
        v = self.make()
        v.set_scene(sc)
        v.set_assignments({"Stage/Screens/Centre": 0})
        self.load(v, self.corners, 20)
        v.set_pause(True)
        pump(0.3)
        sent = []
        orig = v.m.command, v.m.command_async
        v.m.command = lambda *a, **k: (sent.append(a), orig[0](*a, **k))[1]
        v.m.command_async = lambda *a, **k: (sent.append(a), orig[1](*a, **k))[1]
        import collections
        v.stats = collections.deque(maxlen=1000)               # every paint: (time, seconds, libmpv rendered, camera)
        for i in range(120):
            (v.orbit(3, 1) if i % 3 else v.pan(2, 1))
            if i % 30 == 0:
                v.zoom(1)
            pump(0.008)
        v.m.command, v.m.command_async = orig
        paints = list(v.stats)
        self.assertGreater(len(paints), 60)                     # it redrew for the camera...
        self.assertEqual((sent, [p for p in paints if p[2]]), ([], []))   # ...with no command or libmpv render


@unittest.skipUnless(LIVE, "set NITPIX_LIVE_PLAYER=1 with libmpv, ffmpeg, assimp-py and a display")
class Switching(unittest.TestCase):
    """The app's window, Mapping enabled: QC and Mapping hand over one session at exact frames."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="nitpix mapping switch ")
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, cls.tmp)
        QSurfaceFormat.setDefaultFormat(gl_format())
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyleSheet(theme.STYLESHEET)
        cls.clip = os.path.join(cls.tmp, "bars 2997.mp4")
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "color=black:s=640x360:r=30000/1001:d=14,format=gray,"
                        "geq=lum='255*mod(floor(N/pow(2\\,floor(X*16/W)))\\,2)'", "-pix_fmt", "yuv420p", "-c:v", "libx264",
                        "-preset", "ultrafast", "-qp", "0", "-g", "48", "-bf", "2", cls.clip], check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def test_switching_with_a_seek_pending_lands_on_the_exact_frame(self):
        from nitpix.ui.app import MainWindow
        for name, value in (("IS_EXPERIMENTAL", True), ("VARIANT", "exp"), ("SETTINGS", ("nitpix-exp", "nitpix-exp"))):
            p = mock.patch.object(variant, name, value)
            p.start()
            self.addCleanup(p.stop)
        w = MainWindow(service.Session())
        w.resize(1300, 850)
        w.show()
        w.open_project(os.path.join(self.tmp, "switch.nitpix"))
        p = w.session.project
        st = os.stat(self.clip)
        aid = p.upsert_asset(self.clip, kind="video", name="bars.mp4", size=st.st_size, mtime=st.st_mtime, scan_state="done",
                             **summary_fields(normalise(ffprobe(self.clip), self.clip)))
        w.set_mapping_enabled(True)
        rv, ms = w.review, w.mapping
        one_session = lambda: [x for x in (rv.player.m.path, ms.view.m.path) if x]
        w.show_asset(aid)
        end = time.monotonic() + 10
        while not rv.player.loaded and time.monotonic() < end:
            pump(0.01)
        pump(0.4)
        ms._set_scene(load_scene(os.path.join(FIX, "stage.fbx")), "stage.fbx")
        ms.assign.apply(["Stage/Screens/Centre"])
        ms._changed()

        def texture_frame():
            ms.view.makeCurrent()
            img = ms.view.fbo.toImage()
            ms.view.doneCurrent()
            return bars(img, img.height() // 2)

        def qc_frame():
            img = rv.player.grabFramebuffer()
            iw, ih = img.width(), img.height()
            vw = min(iw, ih * 16 / 9)
            x0 = (iw - vw) / 2
            vals = [img.pixelColor(int(x0 + (k + 0.5) * vw / 16), ih // 2).green() for k in range(16)]
            return sum(1 << k for k, a in enumerate(vals) if a > 128)

        for target_qc, target_map in ((211, 97), (33, 300)):
            for f in range(target_qc - 40, target_qc + 1, 4):     # QC: scrub, still pending at the switch
                rv.scrub(f)
                pump(0.003)
            self.assertTrue(rv.playhead.pending or rv.player.scrub_pending())
            w.show_view("mapping")
            self.assertLessEqual(len(one_session()), 1)
            end = time.monotonic() + 10
            while not (ms.view.loaded and not ms.view.scrub_pending()) and time.monotonic() < end:
                ms.view.orbit(3, 0)
                pump(0.01)
            pump(0.6)
            self.assertEqual((texture_frame(), ms.playhead.frame), (target_qc, target_qc))
            self.assertFalse(rv.player.loaded)
            for f in range(target_map - 40, target_map + 1, 4):   # Mapping: scrub, pending at the switch back
                ms.playhead.scrub(f)
                ms.view.orbit(2, 1)
                pump(0.003)
            w.show_view("review")
            self.assertLessEqual(len(one_session()), 1)
            end = time.monotonic() + 10
            while not (rv.player.loaded and rv.playhead.pending is False and not rv.player.scrub_pending()) and time.monotonic() < end:
                pump(0.01)
            pump(0.6)
            self.assertFalse(ms.view.loaded)
            self.assertEqual((qc_frame(), rv.frame, rv.player.frame), (target_map, target_map, target_map))
        w.show_view("mapping")
        ms.view.set_pause(False)
        pump(0.5)
        t = time.monotonic()
        w.close()                                                  # quit while Mapping plays
        self.assertLess(time.monotonic() - t, 3.0)
        w.deleteLater()
        pump(0.1)


if __name__ == "__main__":
    unittest.main()
