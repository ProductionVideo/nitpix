# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the nitpix desktop app.

Bundles the Python app with PySide6, libmpv (and its codec libraries) and the ffmpeg and
ffprobe binaries, so the .app needs nothing installed. Build with tools/build_mac.sh.
Library sources can be overridden: NITPIX_LIBMPV, NITPIX_FFMPEG_BIN (a directory).
NITPIX_VARIANT=exp builds nitpix-exp.app for trying a development branch beside the release: its
own name and bundle identifier, no document types (so it never becomes the app that opens
projects or media), and a BUILD_VARIANT file that makes it keep its own preferences and recent
list and leave updates alone however it is launched (nitpix/variant.py)."""
import os
import sys

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
sys.path.insert(0, ROOT)
from PyInstaller.utils.hooks import collect_submodules  # noqa: E402

LIBMPV = os.environ.get("NITPIX_LIBMPV") or next(p for p in ("/opt/homebrew/lib/libmpv.2.dylib", "/usr/local/lib/libmpv.2.dylib", "/usr/lib/libmpv.so.2") if os.path.exists(p))
FFBIN = os.environ.get("NITPIX_FFMPEG_BIN") or next(d for d in ("/opt/homebrew/opt/ffmpeg-full/bin", "/opt/homebrew/opt/ffmpeg/bin", "/usr/local/bin", "/usr/bin") if os.path.exists(os.path.join(d, "ffmpeg")))
EXT = ".exe" if sys.platform == "win32" else ""
from nitpix import __version__ as VERSION  # noqa: E402

VARIANT = os.environ.get("NITPIX_VARIANT") or "release"
if VARIANT not in ("release", "exp"):
    raise SystemExit(f"NITPIX_VARIANT must be release or exp, not {VARIANT!r}")
EXP = VARIANT == "exp"
NAME = "nitpix-exp" if EXP else "nitpix"

binaries = [(LIBMPV, "."), (os.path.join(FFBIN, "ffmpeg" + EXT), "."), (os.path.join(FFBIN, "ffprobe" + EXT), ".")]
datas = [(os.path.join(ROOT, "nitpix", "web"), os.path.join("nitpix", "web"))]
if EXP:  # read by nitpix/variant.py at start-up; never written into the source tree
    import tempfile
    marker = os.path.join(tempfile.mkdtemp(prefix="nitpix-variant-"), "BUILD_VARIANT")
    with open(marker, "w") as fh:
        fh.write("exp\n")
    datas.append((marker, "nitpix"))

a = Analysis(
    [os.path.join(ROOT, "nitpix", "__main__.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=collect_submodules("nitpix") + ["mpv", "reportlab", "assimp_py"],
    excludes=["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtQml",
              "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtMultimedia",
              "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtPdf", "PySide6.QtLocation", "PySide6.QtBluetooth",
              "tkinter", "unittest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, exclude_binaries=True, name="nitpix", console=False, icon=os.path.join(SPECPATH, "nitpix.icns"),
          target_arch=None, codesign_identity=None, entitlements_file=None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=NAME)
app = BUNDLE(
    coll,
    name=NAME + ".app",
    icon=os.path.join(SPECPATH, "nitpix.icns"),
    bundle_identifier="com.jakestebbings.nitpix" + (".exp" if EXP else ""),
    info_plist={
        "CFBundleName": NAME,
        "CFBundleDisplayName": "nitpix (experimental)" if EXP else "nitpix",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSMinimumSystemVersion": "12.0",
        "NSHighResolutionCapable": True,
        "NSPrincipalClass": "NSApplication",
        "LSApplicationCategoryType": "public.app-category.video",
        "NSHumanReadableCopyright": "© Jake Stebbings",
        "CFBundleDocumentTypes": [] if EXP else [
            {"CFBundleTypeName": "nitpix project", "CFBundleTypeRole": "Editor", "LSHandlerRank": "Owner",
             "LSItemContentTypes": ["com.jakestebbings.nitpix.project"]},
            # media and folders can be dropped on the Dock icon or opened with nitpix; "Alternate" never
            # makes nitpix the default app for them
            {"CFBundleTypeName": "Media for QC", "CFBundleTypeRole": "Viewer", "LSHandlerRank": "Alternate",
             "LSItemContentTypes": ["public.folder", "public.movie", "public.audio", "public.image",
                                    "com.apple.quicktime-movie", "public.mpeg-4", "org.openexr.exr", "public.data"]},
        ],
    },
)
