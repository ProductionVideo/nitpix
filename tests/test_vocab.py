"""Preset vocabulary: what people type vs what ffprobe reports."""
import unittest
from fractions import Fraction

from nitpix import vocab
from nitpix.timecode import parse_rate


class Normalise(unittest.TestCase):
    def check(self, field, cases):
        for typed, want in cases:
            with self.subTest(field=field, typed=typed):
                self.assertEqual(vocab.normalise(field, typed), want)

    def test_codecs(self):
        self.check("codecs", [("ProRes", "prores"), ("Apple ProRes 422", "prores"), ("H.264", "h264"),
                              ("h264", "h264"), ("AVC", "h264"), ("x265", "hevc"), ("H.265", "hevc"),
                              ("DNxHR", "dnxhd"), ("MPEG-2", "mpeg2video"), ("J2K", "jpeg2000"),
                              ("CineForm", "cfhd"), ("ProRes 4444 XQ", "prores"), ("DNxHR HQX", "dnxhd"), ("nonsense", None)])

    def test_profiles(self):
        self.check("profiles", [("HQ", "HQ"), ("hq", "HQ"), ("422 HQ", "HQ"), ("ProRes 422 HQ", "HQ"),
                                ("ProRes 4444 XQ", "4444 XQ"), ("4444xq", "4444 XQ"), ("422", "Standard"),
                                ("high", "High"), ("High 4:2:2", "High 4:2:2"), ("main10", "Main 10"),
                                ("Some New Profile", "Some New Profile")])

    def test_containers(self):
        self.check("containers", [("MOV", "mov"), (".mov", "mov"), ("QuickTime", "mov"), ("MPEG-4", "mp4"),
                                  ("Matroska", "mkv"), ("mxf", "mxf"), ("TS", "mpegts")])

    def test_fps(self):
        self.check("fps", [("25", "25/1"), ("25p", "25/1"), ("25 fps", "25/1"), ("23.976", "24000/1001"),
                           ("23.98", "24000/1001"), ("29.97", "30000/1001"), ("59.94", "60000/1001"),
                           ("24000/1001", "24000/1001"), ("50i", "50/1"), ("12.5", "25/2"), ("fast", None)])

    def test_colour(self):
        self.check("color_primaries", [("Rec.709", "bt709"), ("BT.709", "bt709"), ("709", "bt709"),
                                       ("Rec 2020", "bt2020"), ("P3", "smpte432"), ("DCI-P3", "smpte431")])
        self.check("color_trc", [("PQ", "smpte2084"), ("ST 2084", "smpte2084"), ("HLG", "arib-std-b67"),
                                 ("sRGB", "iec61966-2-1"), ("rec709", "bt709")])
        # The same word means different tags per field: bt2020 matrix is bt2020nc
        self.check("color_space", [("bt2020", "bt2020nc"), ("Rec.2020 CL", "bt2020c"), ("709", "bt709"), ("RGB", "gbr")])

    def test_chroma(self):
        self.check("chroma", [("422", "4:2:2"), ("4:2:2", "4:2:2"), ("yuv420", "4:2:0"), ("rgb", "RGB")])

    def test_audio(self):
        self.check("audio_codecs", [("AAC", "aac"), ("AAC-LC", "aac"), ("Dolby Digital", "ac3"), ("E-AC-3", "eac3"),
                                    ("pcm_s24le", "pcm_s24le"), ("PCM", "pcm")])
        self.check("sample_rate", [("48000", 48000), ("48k", 48000), ("48 kHz", 48000), ("48", 48000),
                                   ("44.1", 44100), (96000, 96000), ("lots", None)])

    def test_sequence(self):
        self.check("formats", [("EXR", "exr"), (".exr", "exr"), ("OpenEXR", "exr"), ("tif", "tiff"), ("JPEG", "jpg")])
        self.check("bit_depth", [("16", 16), ("16-bit", 16), ("half", 16), ("float", 32), (10, 10)])

    def test_par(self):
        self.check("par", [("1:1", "1:1"), ("1", "1:1"), ("1.0", "1:1"), ("square", "1:1"), ("64/45", "64:45"),
                           ("2:1", "2:1"), ("wide", None)])


class Matching(unittest.TestCase):
    def test_measured_vs_typed(self):
        self.assertTrue(vocab.matches("codecs", "prores", ["ProRes"]))
        self.assertTrue(vocab.matches("codecs", "h264", ["H.264", "hevc"]))
        self.assertFalse(vocab.matches("codecs", "hevc", ["h264"]))
        self.assertTrue(vocab.matches("color_primaries", "bt709", ["Rec.709"]))
        self.assertTrue(vocab.matches("chroma", "4:2:2", ["422"]))
        self.assertTrue(vocab.matches("sample_rate", 48000, "48k"))
        self.assertFalse(vocab.matches("sample_rate", 44100, 48000))

    def test_fps_compares_rates_not_strings(self):
        self.assertTrue(vocab.matches("fps", "24000/1001", ["23.976"]))
        self.assertTrue(vocab.matches("fps", "25/1", ["25"]))
        self.assertFalse(vocab.matches("fps", "24/1", ["23.976"]))
        self.assertFalse(vocab.matches("fps", None, ["25"]))

    def test_profiles(self):
        self.assertTrue(vocab.matches("profiles", "HQ", ["422 HQ"]))
        self.assertTrue(vocab.matches("profiles", "DNXHR HQ", ["HQ"]))
        self.assertFalse(vocab.matches("profiles", "LT", ["HQ", "4444"]))

    def test_pcm_family(self):
        self.assertTrue(vocab.matches("audio_codecs", "pcm_s24le", ["PCM"]))
        self.assertTrue(vocab.matches("audio_codecs", "pcm_s24be", ["24-bit PCM"]))
        self.assertFalse(vocab.matches("audio_codecs", "pcm_s16le", ["pcm24"]))
        self.assertFalse(vocab.matches("audio_codecs", "aac", ["PCM"]))

    def test_timecode_ignores_separators(self):
        self.assertTrue(vocab.matches("start_tc", "10:00:00;00", ["10:00:00:00"]))
        self.assertFalse(vocab.matches("start_tc", "09:59:50:00", ["10:00:00:00"]))

    def test_unknown_values_compare_literally(self):
        self.assertTrue(vocab.matches("codecs", "weirdcodec", ["WeirdCodec"]))

    def test_profiles_for_codec(self):
        self.assertEqual(vocab.profiles_for("prores", ["HQ", "High"]), ["HQ"])
        self.assertEqual(vocab.profiles_for("h264", ["HQ", "4444"]), [])
        self.assertEqual(vocab.profiles_for("dnxhd", ["HQ"]), ["HQ"])
        self.assertEqual(vocab.profiles_for("unknowncodec", ["X"]), ["X"])
        self.assertEqual(vocab.profiles_for("notchlc", ["4444", "4444 XQ", "X"]), ["X"])  # ProRes profiles don't apply


class Container(unittest.TestCase):
    MOV_FAMILY = "mov,mp4,m4a,3gp,3g2,mj2"

    def test_mov_and_mp4_told_apart_by_brand(self):
        self.assertEqual(vocab.container_of({"container": self.MOV_FAMILY, "format_tags": {"major_brand": "qt  "}}), "mov")
        self.assertEqual(vocab.container_of({"container": self.MOV_FAMILY, "format_tags": {"major_brand": "isom"}}), "mp4")
        self.assertEqual(vocab.container_of({"container": self.MOV_FAMILY, "format_tags": {"major_brand": "mp42"}}), "mp4")

    def test_falls_back_to_extension(self):
        self.assertEqual(vocab.container_of({"container": self.MOV_FAMILY}, "/a/b.mov"), "mov")
        self.assertEqual(vocab.container_of({"container": self.MOV_FAMILY}, "/a/b.mp4"), "mp4")

    def test_others(self):
        self.assertEqual(vocab.container_of({"container": "mxf"}), "mxf")
        self.assertEqual(vocab.container_of({"container": "matroska,webm"}, "x.mkv"), "mkv")
        self.assertEqual(vocab.container_of({"container": "matroska,webm"}, "x.webm"), "webm")
        self.assertIsNone(vocab.container_of({}))


class CleanPreset(unittest.TestCase):
    def test_canonicalises_and_warns(self):
        spec, warnings = vocab.clean_preset({
            "description": "d",
            "video": {"codecs": ["ProRes", "prores"], "fps": ["23.98"], "par": "square", "width": "1,920",
                      "color_primaries": ["Rec.709"], "containers": ["MOV", "betamax"]},
            "audio": {"codecs": ["PCM"], "sample_rate": "48k", "loudness_target": "−23 LUFS", "channels": 2},
            "sequence": {"formats": [".EXR"], "bit_depth": ["half", "float"], "start_frame": "1001"},
        })
        self.assertEqual(spec["description"], "d")
        self.assertEqual(spec["video"]["codecs"], ["prores"])  # de-duplicated
        self.assertEqual(spec["video"]["fps"], ["24000/1001"])
        self.assertEqual(spec["video"]["par"], "1:1")
        self.assertEqual(spec["video"]["width"], 1920)
        self.assertEqual(spec["video"]["containers"], ["mov", "betamax"])
        self.assertEqual(spec["audio"], {"codecs": ["pcm"], "sample_rate": 48000, "loudness_target": -23.0, "channels": 2})
        self.assertEqual(spec["sequence"], {"formats": ["exr"], "bit_depth": [16, 32], "start_frame": 1001})
        self.assertEqual(len(warnings), 1)
        self.assertIn("betamax", warnings[0])

    def test_rejects_unusable_values(self):
        for bad, msg in (({"video": {"start_tc": "10:00"}}, "timecode"),
                         ({"video": {"width": "wide"}}, "not a number"),
                         ({"video": {"par": "wide"}}, "Pixel aspect"),
                         ({"video": {"min_duration": 30, "max_duration": 10}}, "Min duration"),
                         ({"sequence": {"naming_regex": "([a-z"}}, "regex")):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, msg):
                vocab.clean_preset(bad)

    def test_empty_values_are_dropped(self):
        spec, _ = vocab.clean_preset({"video": {"codecs": [], "width": None, "start_tc": ""}})
        self.assertNotIn("video", spec)

    def test_builtins_are_already_clean(self):
        from nitpix.presets import BUILTIN_PRESETS
        for p in BUILTIN_PRESETS:
            spec = {k: v for k, v in p.items() if k in ("video", "audio", "sequence")}
            with self.subTest(preset=p["id"]):
                cleaned, warnings = vocab.clean_preset(spec)
                self.assertEqual(warnings, [])
                self.assertEqual(cleaned, spec)

    def test_display_preset(self):
        p = vocab.display_preset({"id": "x", "video": {"codecs": ["ProRes"], "width": 1920}})
        self.assertEqual(p["video"], {"codecs": ["prores"], "width": 1920})

    def test_every_option_round_trips(self):
        for field, opts in vocab.OPTIONS.items():
            for value, label in opts:
                with self.subTest(field=field, value=value):
                    self.assertEqual(vocab.normalise(field, value), value)


class Rates(unittest.TestCase):
    def test_parse_rate_ntsc_decimals(self):
        self.assertEqual(parse_rate(23.976), Fraction(24000, 1001))
        self.assertEqual(parse_rate("29.97"), Fraction(30000, 1001))
        self.assertEqual(parse_rate("59.94"), Fraction(60000, 1001))
        self.assertEqual(parse_rate(119.88), Fraction(120000, 1001))
        self.assertEqual(parse_rate(25), Fraction(25))
        self.assertEqual(parse_rate("12.5"), Fraction(25, 2))
        self.assertIsNone(parse_rate(True))
        self.assertIsNone(parse_rate("0/0"))


if __name__ == "__main__":
    unittest.main()
