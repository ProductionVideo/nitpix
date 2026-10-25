"""Core logic tests: python3 -m unittest discover tests"""
import os
import tempfile
import unittest
from fractions import Fraction

from nitpix.discovery import group_sequences, sequence_siblings, walk
from nitpix.imageheaders import read_header
from nitpix.timecode import frames_to_tc, tc_to_frames, TimecodeContext


class Timecode(unittest.TestCase):
    def test_ndf_roundtrip(self):
        r = Fraction(25)
        for f in (0, 1, 24, 25, 90000, 2159999):
            self.assertEqual(tc_to_frames(frames_to_tc(f, r), r), f)
        self.assertEqual(frames_to_tc(90000, r), "01:00:00:00")

    def test_drop_frame(self):
        r = Fraction(30000, 1001)
        self.assertEqual(frames_to_tc(1800, r, drop=True), "00:01:00;02")
        self.assertEqual(frames_to_tc(17982, r, drop=True), "00:10:00;00")
        for f in (0, 1799, 1800, 17981, 17982, 107892):
            self.assertEqual(tc_to_frames(frames_to_tc(f, r, True), r, True), f)

    def test_context_offsets(self):
        c = TimecodeContext(Fraction(25), "10:00:00:00")
        self.assertEqual(c.tc(0), "10:00:00:00")
        self.assertEqual(c.tc(53), "10:00:02:03")
        self.assertEqual(c.frame_from_tc("10:00:02:03"), 53)


class Sequences(unittest.TestCase):
    def test_gaps_dupes_padding(self):
        items = [(f"shot.{n:04d}.exr", 10, 0) for n in range(1001, 1011) if n not in (1004, 1005)]
        items.append(("shot.01007.exr", 10, 0))
        singles, seqs = group_sequences("/x", items)
        self.assertEqual(len(seqs), 1)
        s = seqs[0]
        self.assertEqual((s["first"], s["last"], s["padding"]), (1001, 1010, 4))
        self.assertEqual(s["missing"], [[1004, 1005]])
        self.assertEqual(s["duplicate_numbers"][0]["frame"], 1007)
        self.assertEqual(s["inconsistent_padding"], ["shot.01007.exr"])

    def test_one_frame_stands_for_its_sequence(self):
        import tempfile
        d = tempfile.mkdtemp()
        for i in (1001, 1002, 1003, 1005):
            open(os.path.join(d, f"plate.{i}.png"), "wb").write(b"x")
        open(os.path.join(d, "poster.png"), "wb").write(b"x")
        open(os.path.join(d, "other.1001.png"), "wb").write(b"x")
        self.assertEqual(sorted(sequence_siblings(os.path.join(d, "plate.1002.png"))),
                         ["plate.1001.png", "plate.1002.png", "plate.1003.png", "plate.1005.png"])
        self.assertEqual(sequence_siblings(os.path.join(d, "poster.png")), ["poster.png"])
        found = walk([os.path.join(d, "plate.1002.png"), d])  # the frame and its folder: no duplicates
        self.assertEqual([s["pattern"] for s in found.sequences], ["plate.####.png"])
        self.assertEqual(sorted(os.path.basename(f[1]) for f in found.files), ["other.1001.png", "poster.png"])

    def test_singles_not_grouped(self):
        singles, seqs = group_sequences("/x", [("poster_v01.png", 1, 0), ("poster_v02.png", 1, 0)])
        self.assertEqual(len(seqs), 0)
        self.assertEqual(len(singles), 2)


class Headers(unittest.TestCase):
    def test_zero_and_truncated_png(self):
        d = tempfile.mkdtemp()
        z = os.path.join(d, "z.png")
        open(z, "wb").close()
        self.assertTrue(read_header(z)["zero"])
        import struct, zlib
        def chunk(t, data):
            return struct.pack(">I", len(data)) + t + data + struct.pack(">I", zlib.crc32(t + data))
        png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 3, 8, 6, 0, 0, 0)) + \
            chunk(b"IDAT", zlib.compress(b"\x00" * 13 * 3)) + chunk(b"IEND", b"")
        ok = os.path.join(d, "ok.png")
        open(ok, "wb").write(png)
        h = read_header(ok)
        self.assertTrue(h["ok"])
        self.assertEqual((h["width"], h["height"], h["alpha"]), (4, 3, True))
        bad = os.path.join(d, "bad.png")
        open(bad, "wb").write(png[:-12])
        self.assertFalse(read_header(bad)["ok"])


if __name__ == "__main__":
    unittest.main()
