"""Locate libmpv and import python-mpv against it.

python-mpv finds the library with ctypes.util.find_library("mpv"), which on macOS only
searches dyld's default paths. Pointing DYLD_LIBRARY_PATH at Homebrew works but redirects
every other library too and crashes libmpv under ctypes, so instead the exact file is
chosen here and find_library is answered with it.

Search order: NITPIX_LIBMPV, the app bundle (next to the executable, or Frameworks/),
then the usual install locations for the platform.
"""
from __future__ import annotations

import ctypes.util
import os
import platform
import sys

_SYSTEM = platform.system()
_NAMES = {"Darwin": ["libmpv.2.dylib", "libmpv.dylib"],
          "Windows": ["libmpv-2.dll", "mpv-2.dll", "mpv-1.dll"],
          "Linux": ["libmpv.so.2", "libmpv.so"]}.get(_SYSTEM, ["libmpv.so"])


def _bundle_dirs() -> list[str]:
    here = os.path.dirname(os.path.abspath(sys.executable))
    dirs = [here, os.path.join(here, "..", "Frameworks"), os.path.join(here, "lib")]
    if getattr(sys, "_MEIPASS", None):  # PyInstaller
        dirs.insert(0, sys._MEIPASS)
    return dirs


def _system_dirs() -> list[str]:
    if _SYSTEM == "Darwin":
        return ["/opt/homebrew/lib", "/usr/local/lib", "/opt/local/lib"]
    if _SYSTEM == "Windows":
        env = os.environ
        return [os.path.join(env.get("ProgramFiles", r"C:\Program Files"), "mpv"),
                os.path.join(env.get("LOCALAPPDATA", ""), "Programs", "mpv"),
                os.path.expanduser(r"~\scoop\apps\mpv\current")]
    return ["/usr/lib", "/usr/local/lib", "/usr/lib/x86_64-linux-gnu", "/usr/lib/aarch64-linux-gnu"]


def find_libmpv() -> str | None:
    override = os.environ.get("NITPIX_LIBMPV")
    if override and os.path.isfile(override):
        return override
    for d in _bundle_dirs() + _system_dirs():
        for n in _NAMES:
            p = os.path.join(d, n)
            if os.path.isfile(p):
                return os.path.normpath(p)
    return ctypes.util.find_library("mpv")


LIBMPV = find_libmpv()

# The bundled libmpv links a Vulkan loader. Left alone, it loads whatever Vulkan driver the host
# has installed (Homebrew's MoltenVK on a dev Mac, nothing on a clean one), so the packaged app
# behaved differently per machine and crashed inside MoltenVK. The player renders through OpenGL
# and decodes through VideoToolbox, so it needs no Vulkan driver: point the loader at none.
if _SYSTEM == "Darwin" and getattr(sys, "frozen", False) and not os.environ.get("NITPIX_ALLOW_VULKAN"):
    for _var in ("VK_DRIVER_FILES", "VK_ICD_FILENAMES"):
        os.environ[_var] = os.path.join(getattr(sys, "_MEIPASS", ""), "no-vulkan-drivers.json")
    os.environ["VK_LOADER_LAYERS_DISABLE"] = "~all~"
_find = ctypes.util.find_library


def _patched(name):
    if name == "mpv" and LIBMPV:
        return LIBMPV
    return _find(name)


ctypes.util.find_library = _patched
if _SYSTEM == "Windows" and LIBMPV:
    os.add_dll_directory(os.path.dirname(LIBMPV))
    os.environ["PATH"] = os.path.dirname(LIBMPV) + os.pathsep + os.environ.get("PATH", "")

try:
    import mpv  # noqa: E402  (python-mpv)
    MPV_ERROR = None
except OSError as e:  # no libmpv: the app still runs, with the ffmpeg frame viewer
    mpv = None
    MPV_ERROR = str(e)


def available() -> bool:
    return mpv is not None


def reset():
    """Look for libmpv again (after an install) and import python-mpv if it is now there."""
    global LIBMPV, mpv, MPV_ERROR
    if mpv is not None:
        return
    LIBMPV = find_libmpv()
    if not LIBMPV:
        return
    try:
        import mpv as _mpv
        mpv, MPV_ERROR = _mpv, None
    except OSError as e:
        MPV_ERROR = str(e)


def version() -> str | None:
    if not mpv:
        return None
    v = mpv.backend.mpv_client_api_version()
    return f"client API {v >> 16}.{v & 0xFFFF}"
