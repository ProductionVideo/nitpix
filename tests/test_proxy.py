"""Browser proxies with real ffmpeg (skipped when ffmpeg is missing).

Each clip draws its frame index as 8 black/white bars, so a proxy frame can be read
back and checked against the frame it should be, including at 29.97 and across the
gaps of an image sequence."""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from unittest import mock

from nitpix import server, store
from nitpix.probe import ffprobe, find_tool, normalise, summary_fields
from nitpix.proxy import ProxyManager, command
from nitpix.store import Project

FFMPEG = find_tool("ffmpeg")
BARS = "geq=lum='255*mod(floor(N/pow(2\\,floor(X*8/W)))\\,2)'"


def bars_at(path, n):
    """Frame index drawn in frame n of `path`, decoding exactly frame n."""
    d = subprocess.run([FFMPEG, "-v", "error", "-i", path, "-vf", f"select=eq(n\\,{n}),crop=iw:ih/3:0:0,"
                        "scale=8:1:flags=area,format=gray", "-frames:v", "1", "-f", "rawvideo", "-"],
                       capture_output=True, check=True).stdout
    return sum(1 << k for k, v in enumerate(d[:8]) if v > 128)


def count(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                          "stream=nb_read_frames,r_frame_rate", "-of", "json", path], capture_output=True, text=True).stdout
    s = json.loads(out)["streams"][0]
    return int(s["nb_read_frames"]), s["r_frame_rate"]


@unittest.skipUnless(FFMPEG and find_tool("ffprobe"), "needs ffmpeg and ffprobe")
class Proxies(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="nitpix proxy ")
        cls.p = Project(os.path.join(cls.tmp, "p.nitpix"))
        cls.pm = ProxyManager(cls.p)
        cls.media = os.path.join(cls.tmp, "My Clips")
        os.makedirs(cls.media)

    @classmethod
    def tearDownClass(cls):
        cls.pm.close()
        cls.p.close()
        shutil.rmtree(cls.tmp, True)

    def build(self, asset, timeout=120):
        self.pm.request(asset)
        deadline = time.time() + timeout
        while self.pm.state(asset)["state"] in ("queued", "building") and time.time() < deadline:
            time.sleep(0.2)
        st = self.pm.state(asset)
        self.assertEqual(st["state"], "ready", st)
        return str(self.pm.path(asset))

    def video(self, aid, name, rate, args):
        path = os.path.join(self.media, name)
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=black:s=320x180:r={rate}:d=4,format=gray,{BARS}",
                        "-f", "lavfi", "-i", "sine=f=440:d=4", *args, "-shortest", path], check=True)
        meta = normalise(ffprobe(path), path)
        st = os.stat(path)
        return {"id": aid, "kind": "video", "path": path, "name": name, "size": st.st_size, "mtime": st.st_mtime,
                "metadata": meta, **summary_fields(meta)}

    def test_prores_4444_with_alpha_at_2997_is_frame_exact(self):
        a = self.video(1, "bars 2997.mov", "30000/1001",
                       ["-pix_fmt", "yuva444p10le", "-c:v", "prores_ks", "-profile:v", "4444", "-c:a", "pcm_s24le"])
        out = self.build(a)
        self.assertEqual(count(out), (a["frames"], "30000/1001"))
        for n in (0, 1, 2, 57, 58, a["frames"] - 1):
            self.assertEqual(bars_at(out, n), n)
        info = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,pix_fmt",
                                          "-of", "json", out], capture_output=True, text=True).stdout)["streams"]
        self.assertEqual([(s["codec_name"], s.get("pix_fmt")) for s in info], [("h264", "yuv420p"), ("aac", None)])

    def test_sequence_with_gaps_holds_frames(self):
        d = os.path.join(self.media, "plate dir")
        os.makedirs(d)
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=black:s=160x90:r=25:d=0.4,format=gray,{BARS}",
                        "-start_number", "1001", os.path.join(d, "shot.%04d.png")], check=True)
        os.remove(os.path.join(d, "shot.1004.png"))
        names = sorted(os.listdir(d))
        frames = [[int(n.split(".")[1]), n, 1, "ok"] for n in names]
        a = {"id": 2, "kind": "sequence", "path": os.path.join(d, "shot.####.png"), "name": "shot.####.png",
             "size": 1, "mtime": 1, "fps": "25/1", "metadata": {},
             "seq": {"dir": d, "first": 1001, "last": 1010, "count": len(names), "ext": "png", "frames": frames}}
        out = self.build(a)
        self.assertEqual(count(out)[0], 10)
        self.assertEqual([bars_at(out, n) for n in range(10)], [0, 1, 2, 2, 4, 5, 6, 7, 8, 9])

    def test_notchlc_reads_as_bt601_full_range(self):
        meta = {"video_streams": [{"codec": "notchlc", "color_space": "gbr", "height": 1080}]}
        a = {"id": 3, "kind": "video", "path": "/x/n.mov", "fps": "50/1", "metadata": meta}
        vf = command(self.p, a, "/tmp/o.mp4")
        vf = vf[vf.index("-vf") + 1]
        self.assertTrue(vf.startswith("setparams=colorspace=bt470bg:range=pc,"), vf)
        a["metadata"] = {"video_streams": [{"codec": "h264", "color_space": None, "height": 1080}]}
        vf = command(self.p, a, "/tmp/o.mp4")
        self.assertTrue(vf[vf.index("-vf") + 1].startswith("setparams=colorspace=bt709,"))

    def test_audio_and_other_assets_have_no_proxy(self):
        with self.assertRaises(ValueError):
            self.pm.request({"id": 4, "kind": "audio", "path": "/x.wav"})


@unittest.skipUnless(FFMPEG and find_tool("ffprobe"), "needs ffmpeg and ffprobe")
class ProxyHTTP(unittest.TestCase):
    """The proxy endpoints, including the Range requests a <video> element makes."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="nitpix proxyhttp ")
        cls.patch = mock.patch.object(store, "RECENT_FILE", store.Path(cls.tmp, "recent.json"))
        cls.patch.start()
        server.APP.open_project(os.path.join(cls.tmp, "h.nitpix"))
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        path = os.path.join(cls.tmp, "clip.mov")
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=25:d=2",
                        "-c:v", "prores_ks", path], check=True)
        meta = normalise(ffprobe(path), path)
        cls.aid = server.APP.project.upsert_asset(path, kind="video", name="clip.mov", size=os.path.getsize(path),
                                                  mtime=os.path.getmtime(path), metadata=json.dumps(meta),
                                                  **summary_fields(meta))

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        server.APP.proxies.close()
        server.APP.project.close()
        server.APP.project = server.APP.jobs = server.APP.iina = server.APP.proxies = None
        cls.patch.stop()
        shutil.rmtree(cls.tmp, True)

    def call(self, method, path, headers=None, raw=False):
        c = HTTPConnection("127.0.0.1", self.port, timeout=30)
        c.request(method, path, None, {"Content-Type": "application/json", **(headers or {})})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, dict(r.getheaders()), (data if raw else json.loads(data) if data else None)

    def test_make_poll_and_stream_with_ranges(self):
        st, _, d = self.call("GET", f"/api/proxy/{self.aid}")
        self.assertEqual((st, d["state"], d["rate"]), (200, "none", "25/1"))
        st, _, d = self.call("POST", f"/api/proxy/{self.aid}")
        self.assertIn(d["state"], ("queued", "building", "ready"))
        for _ in range(200):
            st, _, d = self.call("GET", f"/api/proxy/{self.aid}")
            if d["state"] not in ("queued", "building"):
                break
            time.sleep(0.1)
        self.assertEqual(d["state"], "ready", d)
        st, h, whole = self.call("GET", d["url"], raw=True)
        self.assertEqual((st, h["Accept-Ranges"], h["Content-Type"]), (200, "bytes", "video/mp4"))
        self.assertEqual(int(h["Content-Length"]), len(whole))
        st, h, part = self.call("GET", d["url"], {"Range": "bytes=100-199"}, raw=True)
        self.assertEqual((st, h["Content-Range"], part), (206, f"bytes 100-199/{len(whole)}", whole[100:200]))
        st, h, part = self.call("GET", d["url"], {"Range": "bytes=-50"}, raw=True)
        self.assertEqual((st, part), (206, whole[-50:]))
        st, h, _ = self.call("GET", d["url"], {"Range": f"bytes={len(whole)}-"}, raw=True)
        self.assertEqual(st, 416)
        st, _, _ = self.call("GET", f"/api/proxy/{self.aid}/a{self.aid}_000000000000.mp4", raw=True)
        self.assertEqual(st, 404)


if __name__ == "__main__":
    unittest.main()
