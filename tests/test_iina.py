"""IINA bridge tests against tests/fakeiina.py, which mimics the IINA quirks the bridge
works around. Runs on any OS: python3 -m unittest tests.test_iina"""
import json
import os
import shutil
import socket
import stat
import sys
import tempfile
import threading
import time
import unittest
from fractions import Fraction
from unittest import mock

from nitpix import iina
from nitpix.iina import IINABridge, IINAUnavailable, MPVIPC, asset_rate
from nitpix.store import Project

HERE = os.path.dirname(os.path.abspath(__file__))


def _quit(path):
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(0.5)
        s.connect(path)
        s.sendall(b'{"command":["quit"],"request_id":1}\n')
        s.close()
    except OSError:
        pass


class FakeIINATest(unittest.TestCase):
    unpause = "0.2"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nitpix iina ")  # space on purpose: IINA mangles it in URLs
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cli = os.path.join(self.tmp, "iina-cli")
        with open(self.cli, "w") as f:
            f.write(f'#!/bin/sh\nexec "{sys.executable}" "{os.path.join(HERE, "fakeiina.py")}" cli "$@"\n')
        os.chmod(self.cli, os.stat(self.cli).st_mode | stat.S_IEXEC)
        self.log = os.path.join(self.tmp, "launches.jsonl")
        env = {"NITPIX_ALLOW_NON_MAC": "1", "FAKE_IINA_LOG": self.log, "FAKE_IINA_UNPAUSE": self.unpause,
               "FAKE_IINA_LIFETIME": "20"}
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        for name, val in (("PAUSE_SETTLE", 0.6), ("LAUNCH_TIMEOUT", 5.0)):
            p = mock.patch.object(iina, name, val)
            p.start()
            self.addCleanup(p.stop)
        self.project = Project(os.path.join(self.tmp, "p.nitpix"))
        self.addCleanup(self.project.close)
        self.project.update_settings({"iina": {"cli_path": self.cli}})
        self.bridge = IINABridge(self.project)
        self.addCleanup(self._stop_players)
        self.media = os.path.join(self.tmp, "clip one.mov")
        open(self.media, "wb").write(b"\0" * 16)

    def _stop_players(self):
        if self.bridge.session:
            _quit(self.bridge.session["socket"])
        for args in self.launches():
            for a in args:
                if a.startswith("--mpv-input-ipc-server="):
                    _quit(a.split("=", 1)[1])
                    try:
                        os.unlink(a.split("=", 1)[1])
                    except OSError:
                        pass

    def launches(self):
        if not os.path.exists(self.log):
            return []
        return [json.loads(line) for line in open(self.log)]

    def video(self, fps="25/1", aid=1, path=None):
        return {"id": aid, "kind": "video", "path": path or self.media, "name": os.path.basename(path or self.media),
                "fps": fps, "fps_float": round(float(Fraction(fps)), 3)}


class Launch(FakeIINATest):
    def test_launch_args(self):
        args = self.bridge.launch_args("/x/iina-cli", self.video("24000/1001"), "/m.mov", 240, "/tmp/s.sock")
        self.assertEqual(args[:2], ["/x/iina-cli", "--no-stdin"])
        self.assertIn("--mpv-resume-playback=no", args)
        self.assertIn("--mpv-input-ipc-server=/tmp/s.sock", args)
        # exact rational, not 240 / 23.976
        self.assertIn(f"--mpv-start={240 * 1001 / 24000:.6f}", args)
        self.assertEqual(args[-1], "/m.mov")

    def test_fixed_mode_has_no_socket_arg(self):
        args = self.bridge.launch_args("/x/iina-cli", self.video(), "/m.mov", 0, None)
        self.assertFalse(any(a.startswith("--mpv-input-ipc-server") for a in args))

    def test_sequence_args_use_exact_rate(self):
        a = {"id": 2, "kind": "sequence", "name": "shot.####.exr", "fps": "24000/1001"}
        args = self.bridge.launch_args("/x/iina-cli", a, "mf://@/l.txt", 0, "/tmp/s.sock")
        self.assertIn("--mpv-mf-fps=24000/1001", args)

    def test_extra_args_follow_no_stdin(self):
        self.project.update_settings({"iina": {"extra_args": ["--separate-windows"]}})
        args = self.bridge.launch_args("/x/iina-cli", self.video(), "/m.mov", 0, None)
        self.assertEqual(args[1:3], ["--no-stdin", "--separate-windows"])

    def test_unavailable_off_mac(self):
        with mock.patch.dict(os.environ, {"NITPIX_ALLOW_NON_MAC": "0"}), mock.patch("platform.system", return_value="Linux"):
            with self.assertRaises(IINAUnavailable):
                self.bridge.open(self.video(), 0)


class OpenAndControl(FakeIINATest):
    def test_open_lands_paused_on_requested_frame(self):
        # The fake unpauses 0.2 s after load, like IINA; the bridge must win that race.
        st = self.bridge.open(self.video(), 100)
        self.assertTrue(st["connected"], st)
        self.assertIsNone(st["error"])
        time.sleep(0.4)
        st = self.bridge.state()
        self.assertIs(st["paused"], True)
        self.assertEqual(st["frame"], 100)

    def test_open_seeks_even_if_start_ignored(self):
        with mock.patch.dict(os.environ, {"FAKE_IINA_IGNORE_START": "1"}):
            st = self.bridge.open(self.video(), 77)
        self.assertEqual(st["frame"], 77)

    def test_seek_step_and_readback(self):
        self.bridge.open(self.video(), 0)
        self.bridge.seek_frame(37)
        self.assertEqual(self.bridge.state()["frame"], 37)
        self.bridge.step(1)
        self.assertEqual(self.bridge.state()["frame"], 38)
        self.bridge.step(-1)
        self.assertEqual(self.bridge.state()["frame"], 37)
        self.bridge.step(10)
        self.assertEqual(self.bridge.state()["frame"], 47)
        self.bridge.seek_frame(0)
        self.assertEqual(self.bridge.state()["frame"], 0)

    def test_loop_points(self):
        self.bridge.open(self.video(), 0)
        self.bridge.set_loop(25, 49)
        st = self.bridge.state()
        self.assertAlmostEqual(st["loop_a"], 1.0)
        self.assertAlmostEqual(st["loop_b"], 2.0)  # out point is inclusive, so loop ends after frame 49
        self.bridge.set_loop(None, None)
        self.assertEqual(self.bridge.state()["loop_a"], "no")

    def test_pause_toggle(self):
        self.bridge.open(self.video(), 0)
        self.bridge.set_pause(False)
        self.assertIs(self.bridge.state()["paused"], False)
        self.bridge.set_pause(None)
        self.assertIs(self.bridge.state()["paused"], True)

    def test_controls_need_a_session(self):
        with self.assertRaises(IINAUnavailable):
            self.bridge.seek_frame(1)

    def test_lost_connection_is_reported(self):
        self.bridge.open(self.video(), 0)
        _quit(self.bridge.session["socket"])
        time.sleep(0.5)
        st = self.bridge.state()
        self.assertFalse(st["connected"])
        self.assertIn("Lost connection", st["error"])

    def test_state_polling_during_open(self):
        """The UI polls /api/iina/state while /api/iina/open is still waiting."""
        errors = []
        stop = threading.Event()

        def poll():
            while not stop.is_set():
                try:
                    if self.bridge.session:
                        self.bridge.state()
                except Exception as e:  # anything but a clean state dict is a bug
                    errors.append(e)
                time.sleep(0.02)
        t = threading.Thread(target=poll)
        t.start()
        try:
            st = self.bridge.open(self.video(), 12)
        finally:
            stop.set()
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(st["frame"], 12)


class Quirks(FakeIINATest):
    def test_sequence_in_path_with_spaces_loads(self):
        d = os.path.join(self.tmp, "plates dir")
        os.makedirs(d)
        names = []
        for n in range(1001, 1051):
            names.append(f"shot.{n:04d}.png")
            open(os.path.join(d, names[-1]), "wb").write(b"x")
        seq = {"dir": d, "first": 1001, "last": 1050, "frames": [[1001 + i, nm, 1, "ok"] for i, nm in enumerate(names)]}
        a = {"id": 3, "kind": "sequence", "name": "shot.####.png", "fps": "25/1", "seq": seq}
        st = self.bridge.open(a, 30)
        self.assertIsNone(st["error"])
        self.assertTrue(st["path"].startswith("mf://@"))
        self.assertIn(" ", st["path"], "raw path should have been re-sent through the socket")
        self.assertEqual(st["frame"], 30)

    def test_sequence_gaps_hold_previous_frame(self):
        d = os.path.join(self.tmp, "seq")
        os.makedirs(d)
        seq = {"dir": d, "first": 1, "last": 5, "frames": [[1, "a.1.png", 1, "ok"], [2, "a.2.png", 1, "ok"], [5, "a.5.png", 1, "ok"]]}
        target, _ = self.bridge._target({"id": 4, "kind": "sequence", "seq": seq})
        lines = open(target[len("mf://@"):]).read().splitlines()
        self.assertEqual([os.path.basename(x) for x in lines], ["a.1.png", "a.2.png", "a.2.png", "a.2.png", "a.5.png"])

    def test_second_open_reuses_the_window(self):
        self.bridge.open(self.video(aid=1), 10)
        other = os.path.join(self.tmp, "clip two.mov")
        open(other, "wb").write(b"\0")
        st = self.bridge.open(self.video(aid=2, path=other), 20)
        self.assertEqual(len(self.launches()), 1, "a second IINA process was launched")
        self.assertEqual(st["path"], other)
        self.assertEqual(st["frame"], 20)
        self.assertEqual(self.bridge.ipc.get("force-media-title"), "clip two.mov")

    def test_new_window_after_iina_closed(self):
        self.bridge.open(self.video(), 0)
        _quit(self.bridge.session["socket"])
        time.sleep(0.5)
        st = self.bridge.open(self.video(), 5)
        self.assertEqual(len(self.launches()), 2)
        self.assertEqual(st["frame"], 5)

    def test_new_window_after_window_closed_but_iina_still_running(self):
        self.bridge.open(self.video(), 0)
        old = self.bridge.session["socket"]
        self.bridge.ipc.command("stop")  # window closed; the player stays up with nothing loaded
        st = self.bridge.open(self.video(), 5)
        self.assertEqual(len(self.launches()), 2, "loaded into a player with no window")
        self.assertNotEqual(st["session"]["socket"], old)
        self.assertEqual(st["frame"], 5)
        time.sleep(0.4)
        self.assertFalse(iina._socket_alive(old), "the idle player was left running")

    def test_fixed_socket_with_running_player_loads_through_socket(self):
        first = self.bridge.open(self.video(), 0)
        self.project.update_settings({"iina": {"socket_mode": "fixed", "fixed_socket": first["session"]["socket"]}})
        other = os.path.join(self.tmp, "clip two.mov")
        open(other, "wb").write(b"\0")
        st = self.bridge.open(self.video(aid=2, path=other), 9)
        self.assertEqual(len(self.launches()), 1)
        self.assertEqual((st["path"], st["frame"]), (other, 9))

    def test_stale_socket_file_is_not_a_player(self):
        stale = os.path.join(tempfile.gettempdir(), f"nitpix-test-stale-{os.getpid()}.sock")
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(stale)
        s.close()  # file exists, nobody listening: what IINA leaves behind
        self.addCleanup(lambda: os.path.exists(stale) and os.unlink(stale))
        self.project.update_settings({"iina": {"socket_mode": "fixed", "fixed_socket": stale}})
        st = self.bridge.open(self.video(), 0)
        self.assertEqual(len(self.launches()), 1, "should launch IINA rather than talk to a dead socket")
        self.assertFalse(st["connected"])  # the fake (like IINA without the setting) won't create it
        self.assertIn("fixed socket", st["error"])

    def test_close_removes_dead_session_socket(self):
        st = self.bridge.open(self.video(), 0)
        sock = st["session"]["socket"]
        _quit(sock)
        time.sleep(0.4)
        self.bridge.close()
        self.assertFalse(os.path.exists(sock))


class StdinQuirk(FakeIINATest):
    def test_without_no_stdin_the_player_idles(self):
        """Documents the IINA behaviour behind the original bug, using the fake directly."""
        sock = os.path.join(tempfile.gettempdir(), f"nitpix-test-{os.getpid()}.sock")
        self.addCleanup(lambda: (_quit(sock), os.path.exists(sock) and os.unlink(sock)))
        os.system(f'"{self.cli}" --mpv-input-ipc-server={sock} "{self.media}"')
        for _ in range(40):
            if os.path.exists(sock):
                break
            time.sleep(0.05)
        time.sleep(0.2)
        ipc = MPVIPC(sock)
        self.assertIsNone(ipc.get("path"))
        self.assertTrue(ipc.get("idle-active"))


class Rates(unittest.TestCase):
    def test_asset_rate_prefers_rational(self):
        self.assertEqual(asset_rate({"fps": "24000/1001", "fps_float": 23.976}), Fraction(24000, 1001))
        self.assertEqual(asset_rate({"fps_float": 23.976}), Fraction(24000, 1001))
        self.assertEqual(asset_rate({}), Fraction(25))

    def test_seek_target_is_a_quarter_frame_early(self):
        b = IINABridge(None)
        b.ipc, b.session = mock.Mock(), {"fps": 23.976, "rate": "24000/1001"}
        b.seek_frame(172_000)  # two hours in
        t = b.ipc.command.call_args[0][1]
        self.assertAlmostEqual(t, float((172_000 - Fraction(1, 4)) * Fraction(1001, 24000)), places=9)


class NTSCPlayback(FakeIINATest):
    def test_23976_readback(self):
        with mock.patch.dict(os.environ, {"FAKE_IINA_FPS": str(24000 / 1001)}):
            self.bridge.open(self.video("24000/1001"), 0)
            for f in (1, 24, 100, 230):
                self.bridge.seek_frame(f)
                self.assertEqual(self.bridge.state()["frame"], f)


if __name__ == "__main__":
    unittest.main()
