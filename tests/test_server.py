"""HTTP API tests against a real server on a random local port."""
import os as _os, tempfile as _tempfile; _os.environ.setdefault("NITPIX_STATE_DIR", _tempfile.mkdtemp(prefix="nitpix state "))  # keep the real recent list untouched
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

from nitpix import iinfo, server, store
from nitpix.probe import find_tool


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.patches = [mock.patch.object(store, "RECENT_FILE", store.Path(cls.tmp, "recent.json"))]
        for p in cls.patches:
            p.start()
        server.APP.open_project(os.path.join(cls.tmp, "api.nitpix"))
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        server.APP.project.close()
        server.APP.project = server.APP.jobs = server.APP.iina = None
        for p in cls.patches:
            p.stop()
        shutil.rmtree(cls.tmp, True)

    def call(self, method, path, body=None, headers=None):
        c = HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Content-Type": "application/json", **(headers or {})}
        c.request(method, path, json.dumps(body) if body is not None else None, h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, (json.loads(data) if data else None)


class Presets(ServerTest):
    def test_vocab_served(self):
        st, d = self.call("GET", "/api/presets")
        self.assertEqual(st, 200)
        self.assertIn({"value": "prores", "label": "Apple ProRes"}, d["vocab"]["codecs"])
        self.assertIn("prores", d["vocab"]["codec_profiles"])

    def test_typed_values_are_normalised(self):
        st, d = self.call("POST", "/api/presets", {"name": "Typed", "video": {"codecs": ["ProRes", "H.264"],
                          "fps": ["23.98"], "color_primaries": ["Rec.709"]}, "audio": {"sample_rate": "48k"},
                          "sequence": {"formats": ["exr", "bogus"]}})
        self.assertEqual(st, 200)
        self.assertEqual(len(d["warnings"]), 1)
        _, presets = self.call("GET", "/api/presets")
        p = next(x for x in presets["presets"] if x["id"] == d["id"])
        self.assertEqual(p["video"], {"codecs": ["prores", "h264"], "fps": ["24000/1001"], "color_primaries": ["bt709"]})
        self.assertEqual(p["audio"], {"sample_rate": 48000})

    def test_invalid_values_rejected(self):
        st, d = self.call("POST", "/api/presets", {"name": "Bad", "video": {"start_tc": "ten o'clock"}})
        self.assertEqual(st, 400)
        self.assertIn("HH:MM:SS:FF", d["error"])

    def test_old_presets_display_canonically(self):
        server.APP.project.x("INSERT INTO presets(id,name,spec,builtin,updated) VALUES('legacy','Legacy',?,0,0)",
                             (json.dumps({"video": {"codecs": ["ProRes"], "chroma": ["422"]}}),))
        _, d = self.call("GET", "/api/presets")
        p = next(x for x in d["presets"] if x["id"] == "legacy")
        self.assertEqual(p["video"], {"codecs": ["prores"], "chroma": ["4:2:2"]})

    def test_builtins_cannot_be_deleted(self):
        st, _ = self.call("DELETE", "/api/preset/lhs_hd_prores_25")
        self.assertEqual(st, 400)


class SettingsAndGuards(ServerTest):
    def test_null_analysis_value_ignored(self):
        st, s = self.call("POST", "/api/settings", {"analysis": {"black_min_duration": None, "freeze_min_duration": 2}})
        self.assertEqual(st, 200)
        self.assertEqual(s["analysis"]["black_min_duration"], store.DEFAULT_SETTINGS["analysis"]["black_min_duration"])
        self.assertEqual(s["analysis"]["freeze_min_duration"], 2.0)

    def test_cross_origin_write_refused(self):
        st, _ = self.call("POST", "/api/settings", {"client": "x"}, {"Origin": "http://evil.example"})
        self.assertEqual(st, 403)

    def test_iina_unavailable_is_503(self):
        aid = server.APP.project.upsert_asset("/m/a.mov", kind="video", name="a.mov", fps="25/1")
        with mock.patch.object(server.APP.iina, "environment", return_value={"available": False, "reason": "no IINA"}):
            st, d = self.call("POST", "/api/iina/open", {"asset_id": aid, "frame": 0})
        self.assertEqual(st, 503)
        self.assertEqual(d["code"], "iina_unavailable")

    def test_iina_command_without_session(self):
        server.APP.iina.close()
        st, d = self.call("POST", "/api/iina/cmd", {"cmd": "seek", "frame": 3})
        self.assertEqual(st, 503)

    def test_unknown_iina_command(self):
        st, _ = self.call("POST", "/api/iina/cmd", {"cmd": "explode"})
        self.assertEqual(st, 400)


FFMPEG = find_tool("ffmpeg")


@unittest.skipUnless(FFMPEG and find_tool("ffprobe"), "needs ffmpeg and ffprobe")
class ScanEndToEnd(ServerTest):
    """A real scan through the API: probe, ffmpeg analysis, findings and IINfo import."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        data = os.path.join(cls.tmp, "iinfo data")
        os.mkdir(data)
        cls.patches.append(mock.patch.object(iinfo, "DATA_DIR", data))
        cls.patches[-1].start()
        cls.root = os.path.join(cls.tmp, "Delivery 01")
        os.mkdir(cls.root)
        cls.gap = os.path.join(cls.root, "gap in audio.mov")
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=25:d=6",
                        "-f", "lavfi", "-i", "aevalsrc='0.25*sin(2*PI*440*t)*not(between(t,2,5))':s=48000:d=6",
                        "-c:v", "libx264", "-c:a", "pcm_s16le", "-shortest", cls.gap], check=True)
        cls.marked = os.path.join(cls.root, "marked.mov")
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=30000/1001:d=4",
                        "-c:v", "libx264", cls.marked], check=True)
        with open(cls.marked + ".iinfo.json", "w") as f:
            json.dump({"iinfo": "qc-markers", "version": 1, "media": {"size": os.path.getsize(cls.marked)},
                       "events": [{"id": "qc_1", "source": "manual", "tMs": 2002, "severity": "error",
                                   "category": "Video", "note": "tearing"}]}, f)

    def scan(self):
        st, _ = self.call("POST", "/api/scan", {"roots": [self.root]})
        self.assertEqual(st, 200)
        for _ in range(240):
            st, job = self.call("GET", "/api/job")
            if job and job.get("state") in ("done", "error", "cancelled"):
                break
            time.sleep(0.25)
        self.assertEqual((job["state"], job.get("failed")), ("done", 0), job.get("log"))
        return {a["name"]: self.call("GET", f"/api/asset/{a['id']}")[1] for a in self.call("GET", "/api/assets")[1]}

    def test_scan_analyses_and_imports(self):
        assets = self.scan()
        gap, marked = assets["gap in audio.mov"], assets["marked.mov"]
        self.assertEqual((gap["scan_state"], gap["scan_error"]), ("done", None))
        self.assertIn("Silence (3.00s)", [f["title"] for f in gap["findings"] if f["check_key"] == "content.silence"])
        self.assertEqual([(n["frame"], n["severity"], n["text"]) for n in marked["notes"]], [(60, "issue", "[Video] tearing")])
        # rescanning does not import the marker twice
        self.assertEqual(len(self.scan()["marked.mov"]["notes"]), 1)
        st, r = self.call("POST", f"/api/asset/{marked['id']}/iinfo", {})
        self.assertEqual((st, r["imported"], r["events"]), (200, 0, 1))


if __name__ == "__main__":
    unittest.main()
