"""Content analysis against real ffmpeg output (skipped when ffmpeg is missing).

The clip has known defects at known times, so each detector's parsed result can be
checked against what was put there."""
import copy
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from fractions import Fraction

from nitpix import analysis, checks
from nitpix.probe import ffprobe, find_tool, normalise
from nitpix.store import Project

FFMPEG = find_tool("ffmpeg")


@unittest.skipUnless(FFMPEG and find_tool("ffprobe"), "needs ffmpeg and ffprobe")
class Detectors(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="nitpix analysis ")
        p = Project(os.path.join(cls.tmp, "a.nitpix"))
        cls.settings = copy.deepcopy(p.settings)
        p.close()
        cls.path = os.path.join(cls.tmp, "defects.mov")
        # 10 s at 25 fps: black 3–4 s, frozen 6–7.5 s; tone with a silent gap 4–7 s.
        subprocess.run([FFMPEG, "-v", "error", "-y",
                        "-f", "lavfi", "-i", "testsrc2=s=320x180:r=25:d=10",
                        "-f", "lavfi", "-i", "aevalsrc='0.25*sin(2*PI*440*t)*not(between(t,4,7))':s=48000:d=10",
                        "-filter_complex", "[0:v]drawbox=c=black:t=fill:enable='between(t,3,4)',split[a][b];"
                                           "[a][b]freezeframes=first=150:last=187:replace=150[v]",
                        "-map", "[v]", "-map", "1:a", "-c:v", "libx264", "-qp", "0", "-c:a", "pcm_s16le", "-shortest", cls.path], check=True)
        cls.meta = normalise(ffprobe(cls.path), cls.path)
        cls.res = analysis.analyse(cls.path, cls.meta, cls.settings)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def assertSpan(self, spans, start, end, tol=0.15):
        self.assertTrue(any(abs(s - start) <= tol and e is not None and abs(e - end) <= tol for s, e in spans),
                        f"no span near {start}–{end}s in {spans}")

    def test_silence_gap_is_parsed_not_crashed_on(self):
        # silencedetect's events are echoed by ametadata as lavfi.silence_start=…; that
        # line used to raise IndexError and fail the whole asset.
        self.assertSpan(self.res["silence"], 4.0, 7.0)
        self.assertEqual(len(self.res["silence"]), 1)

    def test_black(self):
        self.assertSpan(self.res["black"], 3.0, 4.0)

    def test_freeze(self):
        self.assertSpan(self.res["freeze"], 6.0, 7.52, tol=0.2)

    def test_audio_windows_and_loudness(self):
        self.assertGreater(len(self.res["audio_windows"]), 15)
        self.assertIsNotNone(self.res["loudness"].get("integrated"))
        self.assertAlmostEqual(self.res["analysed_seconds"], 10.0, delta=0.6)


@unittest.skipUnless(FFMPEG and find_tool("ffprobe"), "needs ffmpeg and ffprobe")
class Clipping(unittest.TestCase):
    """Peak level alone is not clipping; a flat-topped waveform is."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="nitpix clip ")
        p = Project(os.path.join(cls.tmp, "c.nitpix"))
        cls.settings = copy.deepcopy(p.settings)
        p.close()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def clips(self, name, src, codec, af=None):
        path = os.path.join(self.tmp, name)
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", src, *(["-af", af] if af else []),
                        "-c:a", codec, path], check=True)
        meta = normalise(ffprobe(path), path)
        res = analysis.analyse(path, meta, self.settings)
        b = checks.Builder(Fraction(25), 0)
        checks._content_audio(b, res, self.settings["analysis"], meta["audio_streams"][0], meta)
        return [f for f in b.items if f["check_key"] == "content.clipping" and f["auto_result"] == "warning"]

    def test_clean_tone_at_minus_0_1_dbfs_is_not_clipping(self):
        self.assertEqual(self.clips("tone.wav", "aevalsrc='0.9886*sin(2*PI*997*t)|0.9886*sin(2*PI*997*t)':s=48000:d=4",
                                    "pcm_s24le"), [])

    def test_brickwall_limited_master_is_not_clipping(self):
        self.assertEqual(self.clips("limited.wav", "anoisesrc=d=4:c=pink:r=48000:a=1", "pcm_s24le",
                                    "volume=6dB,aformat=channel_layouts=stereo,"
                                    "alimiter=limit=0.9886:level=disabled:attack=0.5:release=40"), [])

    def test_clipped_burst_is_found_on_its_channel_and_time(self):
        found = self.clips("burst.wav", "aevalsrc='if(between(t,2,2.5),4,0.5)*sin(2*PI*440*t)|0.5*sin(2*PI*440*t)'"
                                        ":s=48000:d=4", "pcm_s16le")
        self.assertEqual(len(found), 1, found)
        self.assertIn("ch1", found[0]["title"])
        self.assertLessEqual(abs(found[0]["frame_in"] - 50), 13)


@unittest.skipUnless(FFMPEG and find_tool("ffprobe"), "needs ffmpeg and ffprobe")
class MultipleAudioStreams(unittest.TestCase):
    """Every audio stream is analysed, not just the first."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="nitpix tracks ")
        p = Project(os.path.join(cls.tmp, "t.nitpix"))
        cls.settings = copy.deepcopy(p.settings)
        p.close()
        cls.path = os.path.join(cls.tmp, "tracks.mov")
        # a:0 clean tone · a:1 44.1 kHz with a clipped burst at 2 s and a gap 4–7 s · a:2 silent
        subprocess.run([FFMPEG, "-v", "error", "-y",
                        "-f", "lavfi", "-i", "testsrc2=s=160x90:r=25:d=8",
                        "-f", "lavfi", "-i", "aevalsrc='0.3*sin(2*PI*440*t)':s=48000:d=8",
                        "-f", "lavfi", "-i", "aevalsrc='if(between(t,2,2.5),4,0.5)*sin(2*PI*440*t)*not(between(t,4,7))'"
                                             ":s=44100:d=8",
                        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono:d=8",
                        "-map", "0:v", "-map", "1:a", "-map", "2:a", "-map", "3:a", "-c:v", "libx264", "-qp", "0",
                        "-c:a", "pcm_s16le", "-t", "8", cls.path], check=True)
        cls.meta = normalise(ffprobe(cls.path), cls.path)
        cls.res = analysis.analyse(cls.path, cls.meta, cls.settings)
        cls.findings = checks.media_findings("video", cls.meta, cls.res, {"id": "none"}, cls.settings)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def titles(self, key, result="warning"):
        return [f["title"] for f in self.findings if f["check_key"] == key and f["auto_result"] == result]

    def test_each_stream_gets_its_own_results(self):
        self.assertEqual(self.res["silence"], [])
        self.assertEqual([t["index"] for t in self.res["audio_tracks"]], [2, 3])
        gap, silent = self.res["audio_tracks"]
        self.assertEqual(len(gap["silence"]), 1)
        self.assertAlmostEqual(gap["silence"][0][0], 4.0, delta=0.15)
        self.assertGreater(len(silent["silence"]), 0)
        self.assertNotEqual(self.res["loudness"].get("integrated"), gap["loudness"].get("integrated"))

    def test_findings_name_the_stream(self):
        self.assertEqual(self.titles("content.clipping"), ["Audio #2: Clipping on ch1"])
        self.assertIn("Audio #3: Silent for the whole file", self.titles("content.silence"))
        self.assertTrue(any(t.startswith("Audio #2: Silence") for t in self.titles("content.silence")))
        self.assertFalse([t for t in self.titles("content.silence") + self.titles("content.clipping")
                          if t.startswith("Audio #1:")])
        self.assertEqual(self.titles("audio.sample_rate"), ["Audio #2 sample rate differs from #1"])
        self.assertEqual(self.titles("audio.true_peak"), ["Audio #2: True peak"])

    def test_stored_form_round_trips(self):
        from nitpix import scanner
        raw = json.loads(json.dumps(scanner._compact(self.res)))
        again = checks.media_findings("video", self.meta, scanner._expand(raw), {"id": "none"}, self.settings)
        strip = lambda fs: sorted((f["check_key"], f["title"], f["auto_result"], f["frame_in"]) for f in fs)
        self.assertEqual(strip(again), strip(self.findings))


@unittest.skipUnless(FFMPEG and find_tool("ffprobe"), "needs ffmpeg and ffprobe")
class AlphaPlane(unittest.TestCase):
    """A format with alpha says nothing on its own (NotchLC always decodes with one), so the
    plane is measured over every frame."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="nitpix alpha ")
        p = Project(os.path.join(cls.tmp, "a.nitpix"))
        cls.settings = copy.deepcopy(p.settings)
        p.close()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def run_clip(self, name, alpha_expr, preset_alpha):
        path = os.path.join(self.tmp, name)
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=25:d=2",
                        "-vf", f"format=yuva444p10le,geq=lum='lum(X,Y)':cb='cb(X,Y)':cr='cr(X,Y)':a='{alpha_expr}'",
                        "-c:v", "prores_ks", "-profile:v", "4444", path], check=True)
        meta = normalise(ffprobe(path), path)
        res = analysis.analyse(path, meta, self.settings)
        f = [x for x in checks.media_findings("video", meta, res, {"id": "t", "video": {"alpha": preset_alpha}},
                                              self.settings) if x["check_key"] == "colour.alpha"][0]
        return res["alpha"], f

    def test_opaque_plane(self):
        # ProRes 4444 decodes as 12-bit with a limited-range tag; opaque must still read 4095.
        al, f = self.run_clip("opaque.mov", "1023", "forbidden")
        self.assertEqual((al["min"], al["full"], al["frames"]), (4095, 4095, 50))
        self.assertEqual((f["auto_result"], f["measured"]), ("warning", "plane present, fully opaque in all 50 frames"))

    def test_one_small_transparent_block_is_found(self):
        # a 2x2 hole in one frame only
        al, f = self.run_clip("hole.mov", "if(eq(N,30)*between(X,100,101)*between(Y,50,51),0,1023)", "required")
        self.assertLess(al["min"], al["full"])
        self.assertEqual(f["auto_result"], "pass")
        self.assertTrue(f["measured"].startswith("present, transparent pixels"))


if __name__ == "__main__":
    unittest.main()
