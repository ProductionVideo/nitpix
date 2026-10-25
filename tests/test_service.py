"""The shared service layer, on a temporary project with hand-made assets and findings."""
import os
import shutil
import tempfile
import time
import unittest

from nitpix import service
from nitpix.store import Project


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nitpix svc ")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.p = Project(os.path.join(self.tmp, "s.nitpix"))
        self.addCleanup(self.p.close)
        self.p.update_settings({"reviewer": "Jo"})
        self.a = self.p.upsert_asset("/m/a.mov", kind="video", name="a.mov", fps="25/1", frames=250, duration=10.0, scan_state="done")
        self.b = self.p.upsert_asset("/m/b.mov", kind="video", name="b.mov", fps="25/1", frames=250, duration=10.0, scan_state="done")
        self.p.replace_findings(self.a, [
            {"check_key": "content.black", "category": "content", "kind": "detected", "auto_result": "warning", "confidence": "high",
             "title": "Black segment", "detail": "", "measured": "1.0s", "expected": None, "why": "", "frame_in": 10, "frame_out": 34},
            {"check_key": "video.codec", "category": "format", "kind": "measured", "auto_result": "pass", "confidence": "high",
             "title": "Video codec", "detail": "", "measured": "prores", "expected": "prores", "why": "", "frame_in": None, "frame_out": None}])
        self.p.recompute_result(self.a)

    def test_list_and_detail(self):
        rows = service.list_assets(self.p)
        self.assertEqual([r["name"] for r in rows], ["a.mov", "b.mov"])
        self.assertEqual((rows[0]["warnings"], rows[0]["open_detections"], rows[0]["qc_result"]), (1, 1, "warning"))
        d = service.asset_detail(self.p, self.a)
        self.assertEqual([f["kind"] for f in d["findings"]], ["measured", "detected"])  # measured first
        self.assertFalse(d["exists"])
        self.assertIsNone(service.asset_detail(self.p, 999))
        self.assertEqual(service.list_assets(self.p, check="content.black"), [self.a])

    def test_decide_and_clear(self):
        fid = service.asset_detail(self.p, self.a)["findings"][1]["id"]
        self.assertEqual(service.decide_finding(self.p, fid, status="pass"), "pass")
        f = self.p.q1("SELECT * FROM findings WHERE id=?", (fid,))
        self.assertEqual((f["reviewer_status"], f["reviewed_by"]), ("pass", "Jo"))
        self.assertEqual(service.decide_finding(self.p, fid, note="looked fine"), "pass")  # note alone keeps the decision
        self.assertEqual(service.decide_finding(self.p, fid, status=None), "warning")
        self.assertIsNone(self.p.q1("SELECT reviewed_by FROM findings WHERE id=?", (fid,))["reviewed_by"])
        with self.assertRaises(ValueError):
            service.decide_finding(self.p, fid, status="maybe")
        with self.assertRaises(LookupError):
            service.decide_finding(self.p, 999, status="pass")

    def test_review_status_signs(self):
        service.update_asset(self.p, self.a, {"review_status": "approved", "in_frame": 3, "ignored": 1})
        a = self.p.asset(self.a)
        self.assertEqual((a["review_status"], a["signed_by"], a["in_frame"]), ("approved", "Jo", 3))
        self.assertGreater(a["signed_at"], time.time() - 5)
        service.update_asset(self.p, self.a, {"review_status": "in_review"})
        self.assertIsNone(self.p.asset(self.a)["signed_by"])
        with self.assertRaises(ValueError):
            service.update_asset(self.p, self.a, {"review_status": "shipped"})
        service.bulk_review(self.p, [self.a, self.b], "rejected")
        self.assertEqual({r["review_status"] for r in service.list_assets(self.p)}, {"rejected"})

    def test_notes_affect_result(self):
        nid, res = service.add_note(self.p, self.b, "note", 5, None, "issue", "tearing")
        self.assertEqual(res, "fail")
        self.assertEqual(service.update_note(self.p, nid, severity="info"), "pass")
        service.delete_note(self.p, nid)
        self.assertEqual(self.p.q("SELECT * FROM notes"), [])
        with self.assertRaises(LookupError):
            service.update_note(self.p, nid, text="x")

    def test_dashboard(self):
        d = service.dashboard(self.p)
        self.assertEqual((d["assets"]["n"], d["open_detections"], d["by_result"].get("warning")), (2, 1, 1))
        self.assertEqual([r["id"] for r in d["needs_attention"]], [self.a])
        self.assertEqual(d["top_issues"][0]["check_key"], "content.black")

    def test_presets_and_signoff(self):
        pid, warnings = service.save_preset(self.p, {"name": "My spec", "video": {"codecs": ["ProRes"], "fps": ["25"]}})
        self.assertTrue(pid.startswith("custom_my_spec"))
        self.assertIn(pid, [x["id"] for x in service.presets(self.p)["presets"]])
        with self.assertRaises(ValueError):
            service.delete_preset(self.p, "lhs_hd_prores_25")
        service.update_settings(self.p, {"preset_id": pid})
        service.delete_preset(self.p, pid)
        self.assertEqual(self.p.settings["preset_id"], "none")
        so = service.signoff(self.p, "approved", note="ok")
        self.assertEqual((so["status"], so["by"]), ("approved", "Jo"))
        self.assertIsNone(service.signoff(self.p, "clear"))

    def test_session(self):
        s = service.Session()
        s.open(os.path.join(self.tmp, "other"))
        self.assertTrue(str(s.project.folder).endswith("other.nitpix"))
        self.assertIsNotNone(s.jobs)
        s.close()
        self.assertIsNone(s.project)


if __name__ == "__main__":
    unittest.main()
