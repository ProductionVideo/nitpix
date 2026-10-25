"""A stand-in for IINA that speaks mpv's JSON IPC and reproduces the IINA 1.5 behaviours
the bridge has to work around (each one was observed against the real app):

  stdin     without --no-stdin, iina-cli relaunches IINA with --stdin and the player
            sits idle with nothing loaded
  unpause   IINA unpauses ~0.4 s after a file loads, whatever --mpv-pause says
  urls      URL arguments are percent-encoded, so mf://@/My Project/list.txt
            arrives as My%20Project and fails to load
  process   every iina-cli launch is a separate IINA process

    python3 fakeiina.py cli <args...>       behaves like iina-cli (logs args, spawns a player)
    python3 fakeiina.py player <json>       the player process itself

Knobs come from the environment: FAKE_IINA_LOG (launch log path), FAKE_IINA_UNPAUSE
(seconds, default 0.2, "off" to disable), FAKE_IINA_IGNORE_START=1 (ignore --mpv-start,
as a resumed file would), FAKE_IINA_LIFETIME (seconds, default 30).
"""
from __future__ import annotations

import json
import math
import os
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import quote

HERE = os.path.abspath(__file__)


def cli(argv):
    log = os.environ.get("FAKE_IINA_LOG")
    if log:
        with open(log, "a") as f:
            f.write(json.dumps(argv) + "\n")
    opts, files = {}, []
    for a in argv:
        if a.startswith("--mpv-"):
            k, _, v = a[len("--mpv-"):].partition("=")
            opts[k] = v
        elif a.startswith("-"):
            opts.setdefault("_flags", []).append(a)
        else:
            files.append(a)
    stdin_mode = "--no-stdin" not in opts.get("_flags", [])
    target = files[-1] if files else None
    if target and "://" in target:
        scheme, rest = target.split("://", 1)
        target = scheme + "://" + quote(rest, safe="/@:")
    cfg = {"opts": opts, "file": None if stdin_mode else target, "stdin": stdin_mode,
           "unpause": os.environ.get("FAKE_IINA_UNPAUSE", "0.2"),
           "ignore_start": os.environ.get("FAKE_IINA_IGNORE_START") == "1",
           "lifetime": float(os.environ.get("FAKE_IINA_LIFETIME", "30"))}
    subprocess.Popen([sys.executable, HERE, "player", json.dumps(cfg)], start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _loadable(target: str) -> bool:
    if target.startswith("mf://@"):
        return os.path.isfile(target[len("mf://@"):])
    return os.path.isfile(target)


def _list_len(target: str) -> int:
    if target.startswith("mf://@"):
        with open(target[len("mf://@"):]) as f:
            return sum(1 for line in f if line.strip())
    return 0


class Player:
    def __init__(self, cfg):
        self.cfg = cfg
        o = cfg["opts"]
        self.props = {"pause": o.get("pause", "no") == "yes", "ab-loop-a": "no", "ab-loop-b": "no",
                      "resume-playback": o.get("resume-playback", "yes") != "no", "pid": os.getpid(),
                      "force-media-title": o.get("force-media-title")}
        self.fps = 25.0
        self.path = None
        self.pos = None
        self.duration = 10.0
        self.playlist = ["-"] if cfg["stdin"] else []
        self.lock = threading.Lock()
        self.alive = True
        self.last_tick = time.time()
        if cfg["file"]:
            self.load(cfg["file"], float(o.get("start", 0) or 0))

    # ---- media model --------------------------------------------------------------
    def load(self, target, start=0.0):
        self.playlist = [target]
        if not _loadable(target):
            self.path = None
            self.pos = None
            return
        self.path = target
        if target.startswith("mf://@"):
            mf = self.cfg["opts"].get("mf-fps") or self.props.get("mf-fps") or "25"
            n, _, d = str(mf).partition("/")
            self.fps = float(n) / float(d or 1)
            self.duration = _list_len(target) / self.fps
        else:
            self.fps = float(os.environ.get("FAKE_IINA_FPS", "25"))
            self.duration = 10.0
        self.pos = 0.0 if self.cfg["ignore_start"] else self._snap(start)
        unpause = self.cfg["unpause"]
        if unpause != "off":
            threading.Timer(float(unpause), self._iina_unpause).start()

    def _iina_unpause(self):
        with self.lock:
            self.props["pause"] = False
            self.last_tick = time.time()

    def _snap(self, t):
        # mpv hr-seek: first frame whose pts >= target (5 ms tolerance)
        n = max(0, math.ceil((t - 0.005) * self.fps))
        return min(n, int(self.duration * self.fps) - 1) / self.fps

    def tick(self):
        now = time.time()
        if self.path and not self.props["pause"]:
            self.pos = min(self.duration - 1 / self.fps, self.pos + (now - self.last_tick))
        self.last_tick = now

    # ---- IPC ----------------------------------------------------------------------
    def handle(self, cmd):
        with self.lock:
            self.tick()
            name, args = cmd[0], cmd[1:]
            if name == "get_property":
                return self.get(args[0])
            if name == "set_property":
                self.props[args[0]] = args[1]
                return None
            if name == "cycle" and args == ["pause"]:
                self.props["pause"] = not self.props["pause"]
                return None
            if name == "loadfile":
                self.load(args[0], 0.0)
                return None
            if name in ("seek", "frame-step", "frame-back-step") and not self.path:
                raise RuntimeError("error running command")
            if name == "seek":
                t = float(args[0])
                mode = args[1] if len(args) > 1 else "relative"
                self.pos = self._snap(t if mode.startswith("absolute") else self.pos + t)
                return None
            if name == "frame-step":
                self.pos = self._snap(self.pos + 1 / self.fps)
                self.props["pause"] = True
                return None
            if name == "frame-back-step":
                self.pos = self._snap(max(0.0, self.pos - 1 / self.fps))
                self.props["pause"] = True
                return None
            if name in ("show-text", "screenshot-to-file", "observe_property"):
                return None
            if name == "stop":  # what IINA does to a player whose window is closed
                self.path = self.pos = None
                self.playlist = []
                return None
            if name == "quit":
                self.alive = False
                return None
            raise RuntimeError("invalid parameter")

    def get(self, prop):
        loaded = self.path is not None
        if prop == "path":
            return self.path
        if prop == "idle-active":
            return not loaded
        if prop == "playlist":
            return [{"filename": f} for f in self.playlist]
        if prop in ("time-pos", "duration", "estimated-frame-number", "container-fps") and not loaded:
            raise RuntimeError("property unavailable")
        if prop == "time-pos":
            return round(self.pos, 6)
        if prop == "duration":
            return self.duration
        if prop == "estimated-frame-number":
            return int(round(self.pos * self.fps))
        if prop == "container-fps":
            return self.fps
        if prop in self.props:
            return self.props[prop]
        raise RuntimeError("property not found")


def player(cfg):
    p = Player(cfg)
    path = cfg["opts"].get("input-ipc-server")
    if not path:
        return
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(8)
    srv.settimeout(0.2)

    def serve(conn):
        buf = b""
        conn.settimeout(0.2)
        while p.alive:
            try:
                chunk = conn.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                req = json.loads(line)
                try:
                    out = {"data": p.handle(req["command"]), "error": "success"}
                except RuntimeError as e:
                    out = {"error": str(e)}
                out["request_id"] = req.get("request_id", 0)
                # mpv interleaves events with replies; the bridge must skip them
                conn.sendall(b'{"event":"property-change","name":"time-pos"}\n' + json.dumps(out).encode() + b"\n")
        conn.close()

    deadline = time.time() + cfg["lifetime"]
    while p.alive and time.time() < deadline:
        try:
            conn, _ = srv.accept()
        except socket.timeout:
            continue
        threading.Thread(target=serve, args=(conn,), daemon=True).start()
    srv.close()
    # Like IINA, leave the socket file behind when the player goes away.


if __name__ == "__main__":
    if sys.argv[1] == "cli":
        cli(sys.argv[2:])
    else:
        player(json.loads(sys.argv[2]))
