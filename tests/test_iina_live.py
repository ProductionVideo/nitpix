"""Acceptance test against the real IINA. Opens IINA windows, so it only runs when asked:

    NITPIX_LIVE_IINA=1 python3 -m unittest tests.test_iina_live -v

Each test clip draws its frame index as 8 black/white bars (bit k = bar k), which survives
IINA's colour handling. After every seek or step, the test takes an mpv screenshot and
checks both what is on screen and what the app reads back. Media goes in a folder with
spaces in its name, since that is what broke sequence playback before. Only the IINA
processes this test started are closed afterwards.
"""
import os
import platform
import shutil
import subprocess
import tempfile
import time
import unittest

from nitpix.iina import IINABridge
from nitpix.probe import ffprobe, find_tool, normalise, summary_fields
from nitpix.store import Project

FFMPEG = find_tool("ffmpeg")
CLI = "/Applications/IINA.app/Contents/MacOS/iina-cli"
BARS = "geq=lum='255*mod(floor(N/pow(2\\,floor(X*8/W)))\\,2)'"


def bars_of(img):
    d = subprocess.run([FFMPEG, "-v", "error", "-i", img, "-vf", "crop=iw:ih/5:0:0,scale=8:1:flags=area,format=gray",
                        "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    return sum(1 << k for k, v in enumerate(d[:8]) if v > 40)  # IINA can render white as low as ~80


@unittest.skipUnless(os.environ.get("NITPIX_LIVE_IINA") == "1" and platform.system() == "Darwin"
                     and os.path.exists(CLI) and FFMPEG, "set NITPIX_LIVE_IINA=1 on a Mac with IINA and ffmpeg")
class LiveIINA(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="nitpix live ")
        cls.media = os.path.join(cls.tmp, "My Clips")
        os.makedirs(cls.media)
        cls.clips = {}
        for name, rate in (("25", "25"), ("23976", "24000/1001"), ("2997", "30000/1001")):
            path = os.path.join(cls.media, f"bars {name}.mp4")
            subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=black:s=320x180:r={rate}:d=9,format=gray,{BARS}",
                            "-pix_fmt", "yuv420p", "-c:v", "libx264", "-qp", "0", "-g", "12", "-bf", "2", path], check=True)
            cls.clips[name] = path
        cls.seqdir = os.path.join(cls.tmp, "plate dir")
        os.makedirs(cls.seqdir)
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=black:s=160x90:r=25:d=4,format=gray,{BARS}",
                        "-pix_fmt", "rgb24", "-start_number", "1001", os.path.join(cls.seqdir, "shot.%04d.png")], check=True)
        cls.project = Project(os.path.join(cls.tmp, "Live Test.nitpix"))
        cls.bridge = IINABridge(cls.project)
        cls.sockets = set()

    @classmethod
    def tearDownClass(cls):
        cls.bridge.close()
        for s in cls.sockets:
            subprocess.run(["pkill", "-f", f"input-ipc-server={s}"])
        cls.project.close()
        shutil.rmtree(cls.tmp, True)

    def open(self, asset, frame):
        st = self.bridge.open(asset, frame)
        self.sockets.add(st["session"]["socket"])
        self.assertTrue(st["connected"], st)
        self.assertIsNone(st["error"])
        return st

    def on_screen(self):
        time.sleep(0.4)  # let the seek finish before the screenshot
        out = os.path.join(self.tmp, f"shot{time.time():.3f}.png")
        self.bridge.ipc.command("screenshot-to-file", out, "video", timeout=3)
        for _ in range(30):
            if os.path.exists(out) and os.path.getsize(out):
                break
            time.sleep(0.1)
        return bars_of(out), self.bridge.state()["frame"]

    def expect(self, frame):
        self.assertEqual(self.on_screen(), (frame, frame))

    def asset(self, i, path):
        return {"id": i, "kind": "video", "path": path, "name": os.path.basename(path),
                **summary_fields(normalise(ffprobe(path), path))}

    def test_reopen_after_window_closed(self):
        # Closing an IINA window stops its player (mpv `stop`). Depending on IINA's
        # settings the process then quits or idles windowless; either way the next open
        # must show a window on the requested frame.
        self.open(self.asset(20, self.clips["25"]), 10)
        self.bridge.ipc.command("stop")
        time.sleep(1.0)
        st = self.open(self.asset(21, self.clips["2997"]), 60)
        self.assertEqual(st["path"], self.clips["2997"])
        time.sleep(1.0)
        self.expect(60)

    def test_video_clips(self):
        before = set(self.sockets)
        for i, (name, path) in enumerate(self.clips.items()):
            with self.subTest(clip=name):
                a = {"id": i + 1, "kind": "video", "path": path, "name": os.path.basename(path),
                     **summary_fields(normalise(ffprobe(path), path))}
                self.open(a, 100)
                time.sleep(1.0)  # still paused after IINA's own post-load unpause
                self.expect(100)
                self.bridge.seek_frame(37)
                self.expect(37)
                self.bridge.step(1)
                self.expect(38)
                self.bridge.step(-1)
                self.expect(37)
                self.bridge.step(10)
                self.expect(47)
                self.bridge.seek_frame(200)
                self.expect(200)
        self.assertLessEqual(len(self.sockets - before), 1, "each open should reuse the same IINA window")

    def test_sequence_in_folder_with_spaces(self):
        names = sorted(os.listdir(self.seqdir))
        seq = {"dir": self.seqdir, "first": 1001, "last": 1000 + len(names),
               "frames": [[1001 + i, n, 0, "ok"] for i, n in enumerate(names)]}
        self.open({"id": 9, "kind": "sequence", "name": "shot.####.png", "fps": "25/1", "seq": seq}, 30)
        self.expect(30)
        self.bridge.seek_frame(70)
        self.expect(70)
        self.bridge.step(1)
        self.expect(71)


if __name__ == "__main__":
    unittest.main()
