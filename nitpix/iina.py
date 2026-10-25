"""IINA integration (macOS, optional).

The app's own player is the embedded libmpv viewer. "Open in IINA" hands the asset to IINA
as an extra, mainly so IINfo's live scopes and meters can be used on it.

How it works
------------
IINA ships a command-line launcher, `iina-cli`, inside the app bundle
(/Applications/IINA.app/Contents/MacOS/iina-cli). It forwards any mpv option
given as `--mpv-<option>=<value>` to the player core. We use that to:

  * open the file (or an mf:// list for image sequences),
  * start at the reviewer's current frame (`--mpv-start`),
  * keep the window open at the end (`--mpv-keep-open`),
  * enable mpv's JSON IPC server on a Unix socket (`--mpv-input-ipc-server`).

Over the socket we can then read `time-pos`, `pause`, `duration`,
`estimated-frame-number`, `container-fps`, and send `seek … absolute+exact`,
`frame-step`, `frame-back-step`, `set pause`, A-B loop points and OSD text.

IINA 1.5 behaviours worked around (each verified against the app):

  * iina-cli guesses whether media is being piped in. Launched from a server with
    stdin redirected it guesses "yes", relaunches IINA with --stdin, and the player
    sits idle with nothing loaded. We always pass --no-stdin.
  * IINA resumes files at their last position by default, which overrides
    --mpv-start. We pass resume-playback=no and, once the file has loaded, do an
    exact seek to the requested frame anyway.
  * IINA unpauses ~0.4 s after a file loads whatever --mpv-pause says, so pause is
    held for a moment after load.
  * IINA percent-encodes URL arguments, so an mf:// list in a folder with spaces
    fails to load; the raw target is re-sent over the socket.
  * Every iina-cli launch is a new IINA process, so the open window is reused over
    its socket. A player whose window was closed but which kept running is idle
    with no window; it is quit and a new window opened.

If the per-session socket does not appear (some IINA builds may not apply CLI
mpv options to an already-running player), the user can instead set a fixed
socket path in IINA ▸ Settings ▸ Advanced ▸ "Additional mpv options"
(input-ipc-server = /tmp/iina-qc.sock) and switch the app to "fixed" mode.

What IINA cannot do for us
--------------------------
  * It has no concept of source timecode; all TC maths happens in this app.
  * The iina:// URL scheme was locked down in IINA 1.4.3 / 1.4.4 (command-injection
    fixes), so it is only usable for "open file" and is not used here.
  * NotchLC colour: IINA reads it as BT.709 and lifts the shadows; the frame viewer
    (BT.601 full range, matched against disguise) is the reference.
"""
from __future__ import annotations

import itertools
import json
import os
import platform
import shutil
import socket
import subprocess
import threading
import time
import uuid
from fractions import Fraction

from . import playlist
from .timecode import asset_rate, parse_rate

LAUNCH_TIMEOUT = 10.0  # seconds to wait for the socket and for the file to load
PAUSE_SETTLE = 1.2     # seconds after load during which IINA may still unpause on its own


class IINAUnavailable(Exception):
    pass


class MPVIPC:
    def __init__(self, path: str):
        self.path = path
        self.sock: socket.socket | None = None
        self.buf = b""
        self.lock = threading.Lock()
        self.ids = itertools.count(1)

    def _connect(self, timeout):
        self._close()
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            s.connect(self.path)
        except OSError:
            s.close()
            raise
        self.sock = s
        self.buf = b""

    def connect(self, timeout=1.0):
        with self.lock:
            self._connect(timeout)

    def _close(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None

    def close(self):
        with self.lock:
            self._close()

    def command(self, *args, timeout=1.0):
        with self.lock:
            if not self.sock:
                self._connect(timeout)
            rid = next(self.ids)
            msg = json.dumps({"command": list(args), "request_id": rid}).encode() + b"\n"
            try:
                self.sock.sendall(msg)
                deadline = time.time() + timeout
                while time.time() < deadline:
                    while b"\n" in self.buf:
                        line, self.buf = self.buf.split(b"\n", 1)
                        try:
                            obj = json.loads(line)
                        except ValueError:
                            continue
                        if obj.get("request_id") == rid:  # skip events mpv interleaves
                            if obj.get("error") not in (None, "success"):
                                raise RuntimeError(obj["error"])
                            return obj.get("data")
                    chunk = self.sock.recv(65536)
                    if not chunk:
                        raise ConnectionError("IINA closed the connection")
                    self.buf += chunk
                raise TimeoutError("No reply from IINA")
            except (OSError, ConnectionError):
                self._close()
                raise

    def get(self, prop, default=None):
        try:
            return self.command("get_property", prop)
        except RuntimeError:  # property unavailable (e.g. nothing loaded)
            return default


class IINABridge:
    def __init__(self, project):
        self.project = project
        self.ipc: MPVIPC | None = None
        self.session = None  # {asset_id, kind, fps, rate, socket, launched, list_map, loaded}
        self.last_error = None
        self.lock = threading.Lock()

    # ---- environment --------------------------------------------------------
    def cfg(self):
        return self.project.settings["iina"]

    def cli_path(self) -> str | None:
        p = self.cfg().get("cli_path")
        if p and os.path.isfile(p) and os.access(p, os.X_OK):
            return p
        for c in ("/Applications/IINA.app/Contents/MacOS/iina-cli",
                  os.path.expanduser("~/Applications/IINA.app/Contents/MacOS/iina-cli")):
            if os.path.isfile(c):
                return c
        return shutil.which("iina")

    def environment(self) -> dict:
        # NITPIX_ALLOW_NON_MAC lets developers test the bridge with a stand-in launcher that runs mpv.
        is_mac = platform.system() == "Darwin" or os.environ.get("NITPIX_ALLOW_NON_MAC") == "1"
        cli = self.cli_path()
        version = None
        if cli:
            plist = os.path.join(os.path.dirname(os.path.dirname(cli)), "Info.plist")
            try:
                import plistlib
                with open(plist, "rb") as f:
                    version = plistlib.load(f).get("CFBundleShortVersionString")
            except Exception:
                version = None
        reason = None
        if not is_mac:
            reason = "IINA is a macOS app. This machine is not running macOS, so the built-in player is used instead."
        elif not cli:
            reason = "IINA was not found. Install it from iina.io (or `brew install --cask iina`), or set the iina-cli path in Settings."
        return {"platform": platform.system(), "available": bool(is_mac and cli), "cli": cli,
                "version": version, "reason": reason, "mode": self.cfg().get("socket_mode")}

    # ---- launching ------------------------------------------------------------
    def launch_args(self, cli: str, asset: dict, target: str, frame: int, sock: str | None) -> list[str]:
        """iina-cli command line. `sock` is None when the socket comes from IINA's own settings."""
        rate = asset_rate(asset)
        start = max(0.0, float(frame / rate))
        args = [cli, "--no-stdin"]
        args += self.cfg().get("extra_args") or []
        if sock:
            args.append(f"--mpv-input-ipc-server={sock}")
        args += [f"--mpv-start={start:.6f}", "--mpv-keep-open=yes", "--mpv-hr-seek=yes", "--mpv-pause=yes",
                 "--mpv-resume-playback=no", f"--mpv-force-media-title={asset['name']}"]
        if asset["kind"] == "sequence":
            args += [f"--mpv-mf-fps={rate.numerator}/{rate.denominator}"]
        args.append(target)
        return args

    def open(self, asset: dict, frame: int = 0) -> dict:
        env = self.environment()
        if not env["available"]:
            raise IINAUnavailable(env["reason"])
        cfg = self.cfg()
        rate = asset_rate(asset)
        target, list_map = self._target(asset)
        fixed = cfg.get("socket_mode") == "fixed"
        prev = self.session.get("socket") if self.session else None
        if fixed:
            sock = cfg.get("fixed_socket") or "/tmp/iina-qc.sock"
        elif prev and _socket_alive(prev) and not _retire_if_idle(prev):
            sock = prev  # every iina-cli launch is a new IINA process; keep using the open window
        else:
            sock = f"/tmp/nitpix-{uuid.uuid4().hex[:12]}.sock"
        with self.lock:
            self._drop_session()
            self.ipc = MPVIPC(sock)
            self.session = {"asset_id": asset["id"], "kind": asset["kind"], "fps": float(rate),
                            "rate": f"{rate.numerator}/{rate.denominator}", "socket": sock,
                            "launched": time.time(), "list_map": list_map, "name": asset["name"],
                            "connected": False, "loaded": False, "owns_socket": not fixed}
            self.last_error = None
        # IINA already listening on this socket: load through it instead of launching
        # another IINA process.
        reuse = self._ping()
        if reuse:
            for prop, val in (("pause", True), ("hr-seek", "yes"), ("keep-open", "yes"), ("resume-playback", False),
                              ("force-media-title", asset["name"]), ("ab-loop-a", "no"), ("ab-loop-b", "no")):
                self.ipc.command("set_property", prop, val)
            if asset["kind"] == "sequence":
                self.ipc.command("set_property", "mf-fps", float(rate))
            self.ipc.command("loadfile", target, "replace")
        else:
            args = self.launch_args(env["cli"], asset, target, frame, None if fixed else sock)
            log = self.project.safe_path("cache", "iina-launch.log")
            with open(log, "a") as lf:
                lf.write(f"\n{time.ctime()}  {' '.join(args)}\n")
                subprocess.Popen(args, stdout=lf, stderr=lf, stdin=subprocess.DEVNULL, start_new_session=True)
        deadline = time.time() + LAUNCH_TIMEOUT
        while not reuse and time.time() < deadline and not self._ping():
            time.sleep(0.25)
        if not self.session["connected"]:
            self.last_error = ("IINA opened the file but the control socket did not appear, so playback position "
                               "cannot be read back. In IINA ▸ Settings ▸ Advanced, enable advanced settings and add "
                               f"input-ipc-server = {cfg.get('fixed_socket')}, then switch to 'fixed socket' mode in "
                               "this app's Settings. Until then, use the built-in player for frame-accurate notes.")
        elif not self._wait_loaded(deadline, target):
            self.last_error = ("IINA's player started but did not load the file. Check that IINA can open it "
                               f"directly; the launch command is logged in {self.project.safe_path('cache', 'iina-launch.log')}.")
        else:
            self.session["loaded"] = True
            # IINA unpauses ~0.4 s after load whatever --mpv-pause says, and may ignore
            # --mpv-start (resume, reused player window), so hold pause, then seek.
            self._hold_pause(PAUSE_SETTLE)
            self.seek_frame(frame)
            self.osd(f"QC: {asset['name']}")
        return self.state()

    def _ping(self) -> bool:
        """Connect to the socket if it is live. Stale socket files (IINA quit) refuse."""
        if not os.path.exists(self.ipc.path):
            return False
        try:
            self.ipc.connect()
            self.ipc.command("get_property", "pid")
        except (OSError, RuntimeError, TimeoutError):
            return False
        self.session["connected"] = True
        return True

    def _hold_pause(self, seconds):
        """Keep the player paused while IINA finishes its own post-load setup."""
        end = time.time() + seconds
        while time.time() < end:
            if self.ipc.get("pause") is not True:
                self.ipc.command("set_property", "pause", True)
            time.sleep(0.05)

    def _wait_loaded(self, deadline, target) -> bool:
        retry_at = time.time() + 1.5
        while time.time() < deadline:
            try:
                if self.ipc.get("path") and self.ipc.get("time-pos") is not None:
                    return True
                # IINA percent-encodes URL arguments, so mf://@/My Project/... arrives as
                # My%20Project and the player sits idle. Hand mpv the raw target instead.
                if retry_at and time.time() > retry_at and self.ipc.get("idle-active"):
                    self.ipc.command("loadfile", target, "replace")
                    retry_at = None
            except (OSError, RuntimeError, TimeoutError):
                pass
            time.sleep(0.15)
        return False

    def _target(self, asset):
        return playlist.target(self.project, asset), (asset["kind"] == "sequence") or None

    # ---- control ----------------------------------------------------------------
    def _need(self):
        if not self.ipc or not self.session:
            raise IINAUnavailable("No IINA review session. Open the asset in IINA first.")

    def _rate(self) -> Fraction:
        return parse_rate(self.session.get("rate")) or Fraction(self.session["fps"]).limit_denominator(1001)

    def state(self) -> dict:
        env = self.environment()
        if not self.session:
            return {"env": env, "session": None, "connected": False, "error": self.last_error}
        s = dict(self.session)
        s.pop("list_map", None)
        out = {"env": env, "session": s, "connected": False, "error": self.last_error}
        try:
            rate = self._rate()
            pos = self.ipc.get("time-pos")
            out.update(connected=True, paused=self.ipc.get("pause"), time_pos=pos,
                       duration=self.ipc.get("duration"), path=self.ipc.get("path"),
                       container_fps=self.ipc.get("container-fps"),
                       mpv_frame=self.ipc.get("estimated-frame-number"),
                       loop_a=self.ipc.get("ab-loop-a"), loop_b=self.ipc.get("ab-loop-b"))
            out["frame"] = int(round(pos * float(rate))) if isinstance(pos, (int, float)) else out.get("mpv_frame")
            self.session["connected"] = True
            if out["path"]:
                self.session["loaded"] = True
                if self.last_error and "did not load" in self.last_error:
                    self.last_error = out["error"] = None
        except (OSError, TimeoutError, ConnectionError) as e:
            out["connected"] = False
            out["error"] = self.last_error or f"Lost connection to IINA ({e}). The window may have been closed."
        return out

    def seek_frame(self, frame: int):
        self._need()
        # mpv's exact seek shows the first frame whose pts >= target, so aim a quarter-frame
        # early: float rounding can then never push us onto the following frame.
        t = max(0.0, float((frame - Fraction(1, 4)) / self._rate()))
        self.ipc.command("seek", t, "absolute+exact")

    def step(self, n: int):
        self._need()
        if abs(n) == 1:
            self.ipc.command("frame-step" if n > 0 else "frame-back-step")
        else:
            self.ipc.command("seek", float(n / self._rate()), "relative+exact")

    def set_pause(self, paused: bool | None):
        self._need()
        if paused is None:
            self.ipc.command("cycle", "pause")
        else:
            self.ipc.command("set_property", "pause", bool(paused))

    def set_loop(self, a_frame, b_frame):
        self._need()
        rate = self._rate()
        self.ipc.command("set_property", "ab-loop-a", "no" if a_frame is None else float(a_frame / rate))
        self.ipc.command("set_property", "ab-loop-b", "no" if b_frame is None else float((b_frame + 1) / rate))

    def osd(self, text: str, ms: int = 1500):
        if self.ipc:
            try:
                self.ipc.command("show-text", text, ms)
            except Exception:
                pass

    def _drop_session(self):
        if self.ipc:
            self.ipc.close()
        if self.session and self.session.get("owns_socket"):
            # Our per-session socket file; IINA does not remove it when the window closes.
            try:
                if not os.path.exists(self.session["socket"]) or not _socket_alive(self.session["socket"]):
                    os.unlink(self.session["socket"])
            except OSError:
                pass
        self.ipc = None
        self.session = None

    def close(self):
        with self.lock:
            self._drop_session()


def _retire_if_idle(path: str) -> bool:
    """True if the player on `path` has nothing loaded, after asking it to quit.

    Closing an IINA window stops its player; whether the process then exits depends on
    IINA's settings. A player left running is idle with no window, so loading into it
    would be invisible. It holds no file, so quitting it loses nothing.
    """
    ipc = MPVIPC(path)
    try:
        if ipc.get("idle-active") is not True:
            return False
        ipc.command("quit")
    except (OSError, RuntimeError, TimeoutError):
        pass
    finally:
        ipc.close()
    return True


def _socket_alive(path: str) -> bool:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(0.3)
    try:
        s.connect(path)
        return True
    except OSError:
        return False
    finally:
        s.close()
