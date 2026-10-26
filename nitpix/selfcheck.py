"""`nitpix --selfcheck`: what this installation found, as JSON. Used to verify a packaged
build and as the first thing to ask for when the app misbehaves on someone's machine."""
from __future__ import annotations

import os
import platform
import sys
import threading

from . import libmpv, variant
from .probe import find_tool, tool_versions


def run(media: str | None = None) -> dict:
    out = {"nitpix": _version(), "build": variant.describe(), "python": sys.version.split()[0], "platform": f"{platform.system()} {platform.release()} {platform.machine()}",
           "frozen": bool(getattr(sys, "frozen", False)), "executable": sys.executable,
           "bundle_root": getattr(sys, "_MEIPASS", None), "tools": tool_versions(),
           "ffmpeg_path": find_tool("ffmpeg"), "ffprobe_path": find_tool("ffprobe"),
           "libmpv": {"found": libmpv.LIBMPV, "loaded": libmpv.available(), "api": libmpv.version(), "error": libmpv.MPV_ERROR}}
    try:
        import PySide6
        out["pyside6"] = PySide6.__version__
    except ImportError as e:
        out["pyside6"] = f"missing: {e}"
    if libmpv.available():
        out["libmpv"]["decode"] = _decode_check(media)
    from . import scene
    out["scene_importer"] = scene.selfcheck()
    return out


def _version() -> str:
    from . import __version__
    return __version__


def _decode_check(media: str | None = None) -> str:
    """Decode `media` (or a generated test pattern) with no window: proves libmpv and its
    codecs work. Polls rather than python-mpv's wait_for_property, which did not wake here."""
    import time
    result = {"msg": "no result"}
    src = media or "av://lavfi:testsrc2=size=64x36:rate=25:duration=0.4"

    def work():
        m = None
        try:
            m = libmpv.mpv.MPV(vo="null", ao="null", pause=True, idle="yes", hwdec="no", loglevel="error",
                               log_handler=lambda lvl, pfx, msg: result.setdefault("log", []).append(msg.strip()))
            m.loadfile(src)
            t0 = time.time()
            while time.time() - t0 < 15:
                codec, pos = m.video_codec, m.time_pos
                if codec and pos is not None:
                    vp = m.video_params or {}
                    result["msg"] = (f"ok (codec {codec}, {vp.get('w')}x{vp.get('h')} {vp.get('pixelformat')}, "
                                     f"{m.container_fps or m.estimated_vf_fps or '?'} fps, {time.time() - t0:.1f}s to first frame)")
                    break
                if m.idle_active:  # load failed and mpv went back to idle
                    result["msg"] = f"failed: file did not load {result.get('log', [])[-2:]}"
                    break
                time.sleep(0.05)
            else:
                result["msg"] = f"failed: no frame within 15 s {result.get('log', [])[-2:]}"
        except Exception as e:
            result["msg"] = f"failed: {type(e).__name__}: {e} {result.get('log', [])[-2:]}"
        finally:
            if m is not None:
                try:
                    m.terminate()
                except Exception:
                    pass

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(20)
    return result["msg"] if not t.is_alive() else "failed: timed out"
