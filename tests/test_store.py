"""Project storage: settings sanitising, reviewer decisions, path guards."""
import os as _os, tempfile as _tempfile; _os.environ.setdefault("NITPIX_STATE_DIR", _tempfile.mkdtemp(prefix="nitpix state "))  # keep the real recent list untouched
import os
import shutil
import tempfile
import unittest

from nitpix.checks import media_findings
from nitpix.store import DEFAULT_SETTINGS, Project


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.p = Project(os.path.join(self.tmp, "t.nitpix"))
        self.addCleanup(self.p.close)


class Settings(StoreTest):
    def test_emptied_number_keeps_previous_value(self):
        # The Settings form used to send parseFloat("") = NaN, which JSON-encodes as null.
        s = self.p.update_settings({"analysis": {"black_pixel_threshold": None, "freeze_noise": "abc",
                                                 "silence_threshold_db": -50, "flash_luma_jump": "70"}})
        an = s["analysis"]
        self.assertEqual(an["black_pixel_threshold"], DEFAULT_SETTINGS["analysis"]["black_pixel_threshold"])
        self.assertEqual(an["freeze_noise"], DEFAULT_SETTINGS["analysis"]["freeze_noise"])
        self.assertEqual(an["silence_threshold_db"], -50.0)  # negative thresholds are legitimate
        self.assertEqual(an["flash_luma_jump"], 70.0)

    def test_sanitised_settings_dont_break_checks(self):
        s = self.p.update_settings({"analysis": {"black_pixel_threshold": None, "black_min_duration": None}})
        meta = {"container": "mxf", "duration": 1.0, "video_streams": [{"codec": "prores", "fps": "25/1", "width": 1, "height": 1}],
                "audio_streams": []}
        ana = {"black": [], "freeze": [], "yavg": [], "loudness": {}}
        media_findings("video", meta, ana, {"id": "none"}, s)  # used to raise TypeError formatting None

    def test_workers_clamped(self):
        self.assertEqual(self.p.update_settings({"analysis": {"workers": 50}})["analysis"]["workers"], 8)
        self.assertEqual(self.p.update_settings({"analysis": {"workers": 0}})["analysis"]["workers"], 1)

    def test_booleans(self):
        self.assertIs(self.p.update_settings({"analysis": {"loudness": 0}})["analysis"]["loudness"], False)

    def test_iina_settings(self):
        s = self.p.update_settings({"iina": {"sequence_fps": "23.976", "socket_mode": "per_session"}})
        self.assertEqual(s["iina"]["sequence_fps"], "23.976")
        s = self.p.update_settings({"iina": {"sequence_fps": "fast", "socket_mode": "bogus", "extra_args": "-w --pip"}})
        self.assertEqual(s["iina"]["sequence_fps"], "23.976")
        self.assertEqual(s["iina"]["socket_mode"], "per_session")
        self.assertEqual(s["iina"]["extra_args"], ["-w", "--pip"])

    def test_other_settings_pass_through(self):
        s = self.p.update_settings({"reviewer": "Sam", "client": "X"})
        self.assertEqual((s["reviewer"], s["client"]), ("Sam", "X"))


class Findings(StoreTest):
    def asset(self):
        return self.p.upsert_asset("/m/a.mov", kind="video", name="a.mov", scan_state="done")

    def finding(self, key="content.black", frame=10, result="warning"):
        return {"check_key": key, "category": "content", "kind": "detected", "auto_result": result,
                "confidence": "high", "title": "t", "frame_in": frame, "frame_out": frame}

    def test_rescan_keeps_decisions_at_same_place(self):
        aid = self.asset()
        self.p.replace_findings(aid, [self.finding(frame=10), self.finding(frame=50)])
        fid = self.p.q1("SELECT id FROM findings WHERE frame_in=10")["id"]
        self.p.x("UPDATE findings SET reviewer_status='pass', reviewer_note='intentional' WHERE id=?", (fid,))
        self.p.replace_findings(aid, [self.finding(frame=10), self.finding(frame=50)])
        rows = {r["frame_in"]: r for r in self.p.q("SELECT * FROM findings WHERE asset_id=?", (aid,))}
        self.assertEqual((rows[10]["reviewer_status"], rows[10]["reviewer_note"]), ("pass", "intentional"))
        self.assertIsNone(rows[50]["reviewer_status"])

    def test_result_follows_reviewer(self):
        aid = self.asset()
        self.p.replace_findings(aid, [self.finding(result="warning")])
        self.assertEqual(self.p.recompute_result(aid), "warning")
        self.p.x("UPDATE findings SET reviewer_status='pass'")
        self.assertEqual(self.p.recompute_result(aid), "pass")
        self.p.x("INSERT INTO notes(asset_id, kind, severity, text) VALUES(?, 'note', 'issue', 'x')", (aid,))
        self.assertEqual(self.p.recompute_result(aid), "fail")

    def test_safe_path(self):
        self.assertTrue(str(self.p.safe_path("cache", "a.jpg")).startswith(str(self.p.folder)))
        with self.assertRaises(PermissionError):
            self.p.safe_path("..", "escape.txt")


if __name__ == "__main__":
    unittest.main()
