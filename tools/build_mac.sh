#!/bin/bash
# Build dist/nitpix.app: PySide6 + libmpv + ffmpeg/ffprobe inside, ad-hoc signed so it runs locally.
# Needs: uv sync (PySide6, python-mpv), brew install mpv ffmpeg-full, and pyinstaller in .venv.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -x .venv/bin/pyinstaller ] || uv pip install -q --python .venv/bin/python pyinstaller
.venv/bin/python tools/make_icon.py build/nitpix.icns
.venv/bin/pyinstaller --noconfirm --clean --distpath dist --workpath build/pyi --log-level WARN build/nitpix.spec
# Ad-hoc signature: required on Apple Silicon for the app to launch at all. A Developer ID
# signature + notarisation is needed before handing the app to other machines.
codesign --force --deep --sign - dist/nitpix.app
echo "built: dist/nitpix.app ($(du -sh dist/nitpix.app | cut -f1))"
dist/nitpix.app/Contents/MacOS/nitpix --selfcheck
# Installer: a drag-to-Applications disk image. Everything the app needs is inside the bundle.
VER=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)
DMG="dist/nitpix-${VER}.dmg"
STAGE=$(mktemp -d)
cp -R dist/nitpix.app "$STAGE/"
ln -s /Applications "$STAGE/Applications"
rm -f "$DMG"
hdiutil create -quiet -volname "nitpix ${VER}" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
rm -rf "$STAGE"
echo "installer: $DMG ($(du -sh "$DMG" | cut -f1))"
