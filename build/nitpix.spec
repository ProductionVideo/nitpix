# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the nitpix desktop app.

Bundles the Python app with PySide6, libmpv (and its codec libraries) and the ffmpeg and
ffprobe binaries, so the .app needs nothing installed. Build with tools/build_mac.sh.
Library sources can be overridden: NITPIX_LIBMPV, NITPIX_FFMPEG_BIN (a directory)."""
import os
import sys

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
sys.path.insert(0, ROOT)
from PyInstaller.utils.hooks import collect_submodules  # noqa: E402

LIBMPV = os.environ.get("NITPIX_LIBMPV") or next(p for p in ("/opt/homebrew/lib/libmpv.2.dylib", "/usr/local/lib/libmpv.2.dylib", "/usr/lib/libmpv.so.2") if os.path.exists(p))
FFBIN = os.environ.get("NITPIX_FFMPEG_BIN") or next(d for d in ("/opt/homebrew/opt/ffmpeg-full/bin", "/opt/homebrew/opt/ffmpeg/bin", "/usr/local/bin", "/usr/bin") if os.path.exists(os.path.join(d, "ffmpeg")))
EXT = ".exe" if sys.platform == "win32" else ""

binaries = [(LIBMPV, "."), (os.path.join(FFBIN, "ffmpeg" + EXT), "."), (os.path.join(FFBIN, "ffprobe" + EXT), ".")]
datas = [(os.path.join(ROOT, "nitpix", "web"), os.path.join("nitpix", "web"))]

a = Analysis(
    [os.path.join(ROOT, "nitpix", "__main__.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=collect_submodules("nitpix") + ["mpv", "reportlab"],
    excludes=["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtQml",
              "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtMultimedia",
              "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtPdf", "PySide6.QtLocation", "PySide6.QtBluetooth",
              "tkinter", "unittest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, exclude_binaries=True, name="nitpix", console=False, icon=os.path.join(SPECPATH, "nitpix.icns"),
          target_arch=None, codesign_identity=None, entitlements_file=None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="nitpix")
app = BUNDLE(
    coll,
    name="nitpix.app",
    icon=os.path.join(SPECPATH, "nitpix.icns"),
    bundle_identifier="com.jakestebbings.nitpix",
    info_plist={
        "CFBundleName": "nitpix",
        "CFBundleDisplayName": "nitpix",
        "CFBundleShortVersionString": "0.5.1",
        "CFBundleVersion": "0.5.1",
        "LSMinimumSystemVersion": "12.0",
        "NSHighResolutionCapable": True,
        "NSPrincipalClass": "NSApplication",
        "LSApplicationCategoryType": "public.app-category.video",
        "NSHumanReadableCopyright": "© Jake Stebbings",
        "CFBundleDocumentTypes": [{"CFBundleTypeName": "nitpix project", "CFBundleTypeRole": "Editor", "LSHandlerRank": "Owner",
                                   "LSItemContentTypes": ["com.jakestebbings.nitpix.project"]}],
    },
)
