"""IINfo marker interop: fingerprints, frame mapping, import, export and round trips."""
import json
import os
import shutil
import tempfile
import time
import unittest
from fractions import Fraction
from unittest import mock

from nitpix import iinfo, store
from nitpix.store import Project

NTSC = Fraction(30000, 1001)


def event(eid, t_ms, **kw):
    return {"id": eid, "source": "manual", "type": "marker", "tMs": t_ms, "frame": None, "fps": 29.969999313354492,
            "tc": None, "durMs": None, "category": "Other", "severity": "warning", "note": "", "resolved": False,
            "ts": 1, "ref": None, "meta": {}, **kw}


def write(path, events, size=None):
    with open(path, "w") as f:
        json.dump({"iinfo": "qc-markers", "version": 1, "media": {"size": size}, "events": events}, f)


class Fingerprint(unittest.TestCase):
    def test_matches_iinfo_djb2(self):
        # Reference values from IINfo's own djb2Hex run under node; the first is a real data file.
        for name, size, want in (("JAY Z LONDON NIGHT 1 .mov", 5851844109, "4c639261"),
                                 ("Café Ü 試写.mov", 12345, "b0f5100e"),
                                 ("reel 🎬 v2.mov", 99, "624155eb"),
                                 ("x.mov", None, "8c4cb7f8")):
            self.assertEqual(iinfo.fingerprint(name, size), want, name)


class Frames(unittest.TestCase):
    def test_export_then_import_lands_on_the_same_frame(self):
        for rate in (NTSC, Fraction(24000, 1001), Fraction(25), Fraction(60000, 1001)):
            for frame in (0, 1, 29, 1798, 58676, 215784):
                ev = iinfo.to_event({"id": 1, "frame": frame, "frame_out": None, "severity": "info", "text": "x"}, rate)
                self.assertEqual(iinfo.frame_of(ev["tMs"], rate), frame, (rate, frame))
                # IINfo reads it back with floor(t * container-fps), a float32 from mpv
                self.assertEqual(int(ev["tMs"] / 1000 * float(rate)), frame, (rate, frame))

    def test_frame_start_times_from_iinfo(self):
        # A marker captured with time-pos at a frame's start, rounded to the millisecond.
        for frame in (1, 7, 58676):
            t_ms = round(frame / float(NTSC) * 1000)
            self.assertEqual(iinfo.frame_of(t_ms, NTSC), frame)

    def test_real_marker(self):
        # From a real IINfo sidecar: tMs 1957824 at 29.97 is frame 58676, 00:32:37;24.
        n = iinfo.to_note(event("a", 1957824), NTSC)
        self.assertEqual(n["frame"], 58676)
        ev = iinfo.to_event({"id": 1, "frame": 58676, "frame_out": None, "severity": "warning", "text": "x"}, NTSC)
        self.assertEqual(ev["tc"], "00:32:37;24")


class Files(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nitpix iinfo ")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.data = os.path.join(self.tmp, "data")
        os.mkdir(self.data)
        self.media = os.path.join(self.tmp, "reel 1 v003.mov")
        open(self.media, "wb").write(b"\0" * 100)
        self.asset = {"id": 1, "kind": "video", "path": self.media, "size": 100, "fps": "30000/1001", "duration": 60.0}

    def test_load_merges_sidecar_and_data_folder(self):
        write(iinfo.sidecar_path(self.media), [event("s1", 1000)], size=100)
        write(iinfo.data_path(self.media, 100, self.data), [event("d1", 2000), event("s1", 1000)], size=100)
        self.assertEqual([e["id"] for e in iinfo.load(self.asset, self.data)], ["s1", "d1"])

    def test_markers_for_a_different_render_are_ignored(self):
        write(iinfo.sidecar_path(self.media), [event("old", 1000)], size=999)
        self.assertEqual(iinfo.load(self.asset, self.data), [])

    def test_unreadable_files_are_ignored(self):
        open(iinfo.sidecar_path(self.media), "w").write("{not json")
        self.assertEqual(iinfo.load(self.asset, self.data), [])
        self.assertEqual(iinfo.load({**self.asset, "kind": "sequence"}, self.data), [])

    def test_note_mapping(self):
        n = iinfo.to_note(event("x", 1000, durMs=500, severity="error", category="Audio", note="pop"), NTSC)
        self.assertEqual((n["frame"], n["frame_out"], n["severity"], n["text"], n["author"]),
                         (29, 44, "issue", "[Audio] pop", "IINfo"))
        self.assertEqual(iinfo.to_note(event("y", 0), NTSC)["text"], "IINfo marker")

    def test_export_keeps_iinfo_markers_and_adds_notes(self):
        write(iinfo.data_path(self.media, 100, self.data),
              [event("d1", 2000, note="hidden by a sidecar"), event("d2", 3000), event("gone", 4000)], size=100)
        notes = [{"id": 7, "ext_id": None, "frame": 100, "frame_out": 110, "severity": "issue", "text": "[Video] hit",
                  "created": time.time(), "author": "Jo"},
                 {"id": 8, "ext_id": "d2", "frame": 89, "frame_out": None, "severity": "info",
                  "text": "edited in nitpix", "created": 1, "author": "IINfo"}]
        out = iinfo.export(self.asset, notes, NTSC, {"gone"}, self.data)
        doc = json.load(open(out["path"]))
        self.assertEqual(out["path"], self.media + ".iinfo.json")
        self.assertEqual((doc["iinfo"], doc["version"], doc["media"]["size"]), ("qc-markers", 1, 100))
        by_id = {e["id"]: e for e in doc["events"]}
        self.assertEqual(set(by_id), {"d1", "d2", "nitpix_7"})
        self.assertEqual(by_id["d1"]["note"], "hidden by a sidecar")
        self.assertEqual((by_id["d2"]["note"], by_id["d2"]["severity"], by_id["d2"]["source"]),
                         ("edited in nitpix", "info", "manual"))
        ours = by_id["nitpix_7"]
        self.assertEqual((ours["source"], ours["category"], ours["note"], ours["severity"], ours["frame"]),
                         ("nitpix", "Video", "hit", "error", 100))
        self.assertEqual(iinfo.frame_of(ours["tMs"] + ours["durMs"], NTSC), 111)
        # what we wrote is never imported back as a duplicate note
        self.assertEqual([n["ext_id"] for n in iinfo.import_events(self.asset, doc["events"], NTSC, {"d1", "d2"}, set())], [])
        self.assertFalse([f for f in os.listdir(self.tmp) if f.endswith(".tmp")])


class ProjectSync(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nitpix iinfo ")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.data = os.path.join(self.tmp, "data")
        os.mkdir(self.data)
        p = mock.patch.object(iinfo, "DATA_DIR", self.data)
        p.start()
        self.addCleanup(p.stop)
        self.p = Project(os.path.join(self.tmp, "p.nitpix"))
        self.addCleanup(self.p.close)
        self.media = os.path.join(self.tmp, "clip.mov")
        open(self.media, "wb").write(b"\0" * 10)
        self.aid = self.p.upsert_asset(self.media, kind="video", name="clip.mov", size=10, fps="25/1", duration=10.0)
        write(iinfo.sidecar_path(self.media), [event("m1", 2000, severity="error", note="glitch")], size=10)

    def test_import_once_and_respect_deletions(self):
        self.assertEqual(self.p.import_iinfo(self.aid), 1)
        self.assertEqual(self.p.import_iinfo(self.aid), 0)
        n = self.p.q1("SELECT * FROM notes WHERE asset_id=?", (self.aid,))
        self.assertEqual((n["frame"], n["severity"], n["text"], n["ext_id"]), (50, "issue", "glitch", "m1"))
        self.assertEqual(self.p.q1("SELECT qc_result FROM assets WHERE id=?", (self.aid,))["qc_result"], "fail")
        self.p.x("DELETE FROM notes WHERE id=?", (n["id"],))
        self.p.dismiss_iinfo("m1")
        self.assertEqual(self.p.import_iinfo(self.aid), 0)
        out = self.p.export_iinfo(self.aid)
        self.assertEqual(out["events"], 0)

    def test_old_project_gains_the_column(self):
        self.p.close()
        import sqlite3
        db = sqlite3.connect(os.path.join(self.tmp, "p.nitpix", "project.db"))
        db.executescript("CREATE TABLE n2 AS SELECT id, asset_id, kind, frame, frame_out, severity, text, author, "
                         "created, updated FROM notes; DROP TABLE notes; ALTER TABLE n2 RENAME TO notes;")
        db.commit()
        db.close()
        self.p = Project(os.path.join(self.tmp, "p.nitpix"))
        self.assertEqual(self.p.import_iinfo(self.aid), 1)


if __name__ == "__main__":
    unittest.main()
