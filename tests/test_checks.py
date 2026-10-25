"""Rules engine: preset comparisons on synthetic ffprobe metadata (no media needed)."""
import copy
import unittest

from nitpix.checks import media_findings, sequence_findings
from nitpix.presets import builtin_by_id
from nitpix.store import DEFAULT_SETTINGS

PRESETS = builtin_by_id()


def meta(**over):
    v = {"index": 0, "codec": "prores", "profile": "HQ", "width": 1920, "height": 1080, "display_width": 1920,
         "display_height": 1080, "rotation": 0, "orientation": "landscape", "sar": "1:1", "dar": "16:9",
         "fps": "25/1", "avg_fps": "25/1", "vfr": False, "field_order": "progressive", "pix_fmt": "yuv422p10le",
         "bit_depth": 10, "chroma": "4:2:2", "alpha": False, "color_range": "tv", "color_primaries": "bt709",
         "color_trc": "bt709", "color_space": "bt709", "duration": 10.0, "frames": 250, "hdr_side_data": []}
    a = {"index": 1, "codec": "pcm_s24le", "sample_rate": 48000, "channels": 2, "layout": "stereo",
         "sample_fmt": "s32", "bit_depth": 24, "duration": 10.0}
    m = {"container": "mov,mp4,m4a,3gp,3g2,mj2", "container_long": "QuickTime / MOV", "duration": 10.0,
         "start_time": 0.0, "size": 1000, "format_tags": {"major_brand": "qt  "}, "start_tc": "10:00:00:00",
         "video_streams": [v], "audio_streams": [a]}
    for k, val in over.items():
        if k.startswith("v_"):
            v[k[2:]] = val
        elif k.startswith("a_"):
            a[k[2:]] = val
        else:
            m[k] = val
    return m


def results(m, preset, kind="video"):
    out = {}
    for f in media_findings(kind, m, None, preset, DEFAULT_SETTINGS):
        out.setdefault(f["check_key"], f)
    return out


def custom(**sections):
    return {"id": "t", "name": "test", **sections}


class PresetComparison(unittest.TestCase):
    def test_reference_file_passes_its_preset(self):
        r = results(meta(), PRESETS["lhs_hd_prores_25"])
        for k in ("format.container", "format.codec", "format.resolution", "format.fps", "format.aspect",
                  "colour.tags", "colour.chroma", "colour.bit_depth", "audio.codec", "audio.sample_rate", "audio.channels"):
            with self.subTest(check=k):
                self.assertEqual(r[k]["auto_result"], "pass", r[k])

    def test_mp4_does_not_pass_a_mov_spec(self):
        r = results(meta(format_tags={"major_brand": "isom"}), PRESETS["lhs_hd_prores_25"])
        self.assertEqual(r["format.container"]["auto_result"], "fail")
        self.assertIn("MP4", r["format.container"]["measured"])

    def test_mov_does_not_pass_an_mp4_only_spec(self):
        r = results(meta(), custom(video={"containers": ["mp4"]}))
        self.assertEqual(r["format.container"]["auto_result"], "fail")

    def test_profiles_only_constrain_their_codec(self):
        p = custom(video={"codecs": ["prores", "h264"], "profiles": ["HQ"]})
        self.assertEqual(results(meta(v_codec="h264", v_profile="High"), p)["format.codec"]["auto_result"], "pass")
        self.assertEqual(results(meta(v_profile="LT"), p)["format.codec"]["auto_result"], "fail")
        self.assertEqual(results(meta(), p)["format.codec"]["expected"], "prores HQ or h264")

    def test_hand_typed_preset_values(self):
        p = custom(video={"containers": ["QuickTime"], "codecs": ["Apple ProRes"], "profiles": ["422 HQ"],
                          "fps": ["25p"], "chroma": ["422"], "color_primaries": ["Rec.709"], "color_trc": ["Rec 709"],
                          "color_space": ["BT.709"], "par": "square", "start_tc": "10:00:00:00"},
                   audio={"codecs": ["PCM"], "sample_rate": "48k"})
        r = results(meta(), p)
        for k in ("format.container", "format.codec", "format.fps", "colour.chroma", "colour.tags",
                  "format.aspect", "format.timecode", "audio.codec", "audio.sample_rate"):
            with self.subTest(check=k):
                self.assertEqual(r[k]["auto_result"], "pass", r[k])

    def test_ntsc_rates(self):
        p = custom(video={"fps": ["23.976"]})
        self.assertEqual(results(meta(v_fps="24000/1001"), p)["format.fps"]["auto_result"], "pass")
        self.assertEqual(results(meta(v_fps="24/1"), p)["format.fps"]["auto_result"], "fail")

    def test_timecode_is_informational_unless_asked(self):
        self.assertEqual(results(meta(start_tc=None), custom())["format.timecode"]["auto_result"], "info")
        self.assertEqual(results(meta(start_tc=None), custom(video={"timecode": True}))["format.timecode"]["auto_result"], "warning")
        self.assertEqual(results(meta(start_tc="01:00:00:00"), custom(video={"timecode": True}))["format.timecode"]["auto_result"], "pass")

    def test_notchlc_profiles_and_colour_tags(self):
        p = custom(video={"codecs": ["prores", "notchlc"], "profiles": ["4444", "4444 XQ"], "chroma": ["4:4:4"], "color_trc": ["bt709"]})
        r = results(meta(v_codec="notchlc", v_profile=None, v_chroma="4:4:4", v_color_trc="iec61966-2-1", v_color_space="gbr"), p)
        self.assertEqual(r["format.codec"]["auto_result"], "pass", r["format.codec"])   # ProRes profiles don't apply to NotchLC
        self.assertEqual(r["colour.chroma"]["auto_result"], "pass")
        self.assertEqual(r["colour.tags"]["auto_result"], "info")                        # the decoder's fixed tags are not a spec failure

    def test_default_led_spec(self):
        from nitpix.checks import still_findings
        spec = PRESETS["led_notchlc_1080p25"]
        good = meta(v_codec="notchlc", v_profile=None, v_pix_fmt="yuva444p12le", v_bit_depth=12, v_chroma="4:4:4", v_alpha=True,
                    v_color_space="gbr", v_color_trc="iec61966-2-1", start_tc=None, a_codec="pcm_s16le")
        r = results(good, spec)
        for k in ("format.container", "format.codec", "format.resolution", "format.fps", "colour.bit_depth",
                  "colour.alpha", "audio.codec", "audio.sample_rate"):
            with self.subTest(check=k):
                self.assertEqual(r[k]["auto_result"], "pass", r[k])
        self.assertEqual(r["format.timecode"]["auto_result"], "info")
        self.assertEqual(results(meta(v_alpha=False), spec)["colour.alpha"]["auto_result"], "fail")
        self.assertEqual(results(meta(), spec)["format.codec"]["auto_result"], "fail")  # ProRes is not the spec
        st = {f["check_key"]: f["auto_result"] for f in still_findings({"ok": True, "format": "png", "width": 1920, "height": 1080, "alpha": True, "size": 10}, None, spec, {})}
        self.assertEqual((st["format.container"], st["format.resolution"], st["colour.alpha"]), ("pass", "pass", "pass"))
        st = {f["check_key"]: f["auto_result"] for f in still_findings({"ok": True, "format": "jpeg", "width": 1280, "height": 720, "alpha": False, "size": 10}, None, spec, {})}
        self.assertEqual((st["format.container"], st["format.resolution"], st["colour.alpha"]), ("fail", "fail", "fail"))

    def test_drop_frame_start_tc(self):
        p = custom(video={"start_tc": "01:00:00:00"})
        r = results(meta(v_fps="30000/1001", start_tc="01:00:00;00"), p)
        self.assertEqual(r["format.timecode"]["auto_result"], "pass")

    def test_hdr_tags(self):
        p = custom(video={"color_primaries": ["Rec.2020"], "color_trc": ["PQ"], "color_space": ["bt2020"]})
        r = results(meta(v_color_primaries="bt2020", v_color_trc="smpte2084", v_color_space="bt2020nc"), p)
        self.assertEqual(r["colour.tags"]["auto_result"], "pass")

    def test_wrong_values_still_fail(self):
        r = results(meta(v_codec="h264", v_profile="High", v_fps="30/1", v_chroma="4:2:0", a_sample_rate=44100),
                    PRESETS["lhs_hd_prores_25"])
        for k in ("format.codec", "format.fps", "colour.chroma", "audio.sample_rate"):
            with self.subTest(check=k):
                self.assertEqual(r[k]["auto_result"], "fail")

    def test_no_preset_is_informational(self):
        r = results(meta(), PRESETS["none"])
        self.assertEqual(r["format.codec"]["auto_result"], "info")
        self.assertNotIn("spec.preset", r)


class Sequences(unittest.TestCase):
    SEQ = {"dir": "/x", "prefix": "shot.", "pattern": "shot.####.exr", "ext": "exr", "padding": 4, "first": 1001,
           "last": 1010, "count": 10, "expected": 10, "missing": [], "missing_count": 0, "duplicate_numbers": [],
           "inconsistent_padding": [], "inconsistent_padding_count": 0,
           "frame_results": {"header": {"bit_depth": 16, "float": True, "alpha": False}, "dims_majority": [1920, 1080]}}

    def run_seq(self, preset):
        out = {}
        for f in sequence_findings(copy.deepcopy(self.SEQ), None, preset, DEFAULT_SETTINGS):
            out.setdefault(f["check_key"], f)
        return out

    def test_vfx_preset_passes(self):
        r = self.run_seq(PRESETS["vfx_exr_1001"])
        for k in ("seq.naming", "seq.range", "seq.format", "colour.bit_depth"):
            with self.subTest(check=k):
                self.assertEqual(r[k]["auto_result"], "pass", r[k])

    def test_typed_formats_and_depths(self):
        r = self.run_seq(custom(sequence={"formats": ["OpenEXR"], "bit_depth": ["half"]}))
        self.assertEqual(r["seq.format"]["auto_result"], "pass")
        self.assertEqual(r["colour.bit_depth"]["auto_result"], "pass")

    def test_invalid_regex_is_a_finding_not_a_crash(self):
        r = self.run_seq(custom(sequence={"naming_regex": "([a-z"}))
        self.assertEqual(r["seq.naming"]["auto_result"], "fail")
        self.assertIn("not a valid regex", r["seq.naming"]["detail"])


if __name__ == "__main__":
    unittest.main()
