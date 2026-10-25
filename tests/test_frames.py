"""Built-in frame viewer accuracy with real ffmpeg (skipped when ffmpeg is missing).

Test clips encode the frame index in their luma, so a grab can be checked against
ffmpeg's own select=eq(n,N) reference."""
import os
import shutil
import subprocess
import tempfile
import unittest

from nitpix import frames
from nitpix.probe import ffprobe, find_tool, normalise, summary_fields
from nitpix.store import Project

FFMPEG = find_tool("ffmpeg")


def _means(args):
    raw = subprocess.run([FFMPEG, "-v", "error", *args, "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    return [sum(raw[i:i + 64]) / 64 for i in range(0, len(raw), 64)]


@unittest.skipUnless(FFMPEG and find_tool("ffprobe"), "needs ffmpeg and ffprobe")
class FrameGrab(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="nitpix frames ")
        cls.p = Project(os.path.join(cls.tmp, "f.nitpix"))
        cls.clips = {}
        for name, rate in (("25", "25"), ("23976", "24000/1001"), ("2997", "30000/1001")):
            path = os.path.join(cls.tmp, f"count {name}.mp4")
            subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
                            f"color=gray:s=64x36:r={rate}:d=3,format=yuv420p,geq=lum='16+N':cb=128:cr=128",
                            "-c:v", "libx264", "-qp", "0", "-g", "12", "-bf", "2", path], check=True)
            # reference: every source frame through the viewer's colour conversion
            ref = _means(["-i", path, "-vf", "format=yuvj444p,scale=8:8:flags=area,format=gray"])
            cls.clips[name] = (path, ref)

    @classmethod
    def tearDownClass(cls):
        cls.p.close()
        shutil.rmtree(cls.tmp, True)

    def test_exact_frames(self):
        for i, (name, (path, ref)) in enumerate(self.clips.items()):
            a = {"id": i + 1, "kind": "video", "path": path, "mtime": 0, **summary_fields(normalise(ffprobe(path), path))}
            for f in (0, 1, 24, 25, 50, 70):
                with self.subTest(clip=name, frame=f):
                    img = frames.grab(self.p, a, f, 64)
                    m = _means(["-i", img, "-vf", "scale=8:8:flags=area,format=gray"])[0]
                    got = min(range(len(ref)), key=lambda n: abs(ref[n] - m))
                    self.assertEqual(got, f)

    def test_deep_into_ntsc_clip(self):
        """23.976 grabs were a frame late after ~40 s when seeking with the rounded fps."""
        path = os.path.join(self.tmp, "long 23976.mp4")
        # 16 bars, bar k white when bit k of the frame index is set
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
                        "color=black:s=128x16:r=24000/1001:d=260,format=gray,"
                        "geq=lum='255*mod(floor(N/pow(2\\,floor(X/8)))\\,2)'",
                        "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0", "-g", "48", path], check=True)
        a = {"id": 99, "kind": "video", "path": path, "mtime": 0, **summary_fields(normalise(ffprobe(path), path))}
        for f in (1000, 3000, 6000):
            with self.subTest(frame=f):
                img = frames.grab(self.p, a, f, 128)
                raw = subprocess.run([FFMPEG, "-v", "error", "-i", img, "-vf", "scale=16:1:flags=area,format=gray",
                                      "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
                self.assertEqual(sum(1 << k for k, v in enumerate(raw[:16]) if v > 128), f)


if __name__ == "__main__":
    unittest.main()
