#!/bin/bash
# Build the macOS app: PySide6 + libmpv + ffmpeg/ffprobe inside, ad-hoc signed so it runs locally.
# Needs: uv sync (PySide6, python-mpv), brew install mpv ffmpeg-full, and pyinstaller in .venv.
#
#   tools/build_mac.sh                                    release: dist/nitpix.app, dist/nitpix-<version>.dmg
#   NITPIX_VARIANT=exp tools/build_mac.sh                 experimental: dist-exp/nitpix-exp.app, no installer
#   NITPIX_VARIANT=exp NITPIX_DMG=1 tools/build_mac.sh    ...and dist-exp/nitpix-exp-<version>-<commit>.dmg
#   NITPIX_DIST=<folder>                                  put the app (and installer) there instead
#
# An experimental build keeps its own preferences and recent list and does not update itself
# (nitpix/variant.py). It never writes to dist/ or build/pyi, so the release app, installer and
# build folder are left alone.
set -euo pipefail
cd "$(dirname "$0")/.."
VARIANT="${NITPIX_VARIANT:-release}"
case "$VARIANT" in
  release) NAME=nitpix;     DIST="${NITPIX_DIST:-dist}";     WORK=build/pyi;     DMG_WANTED="${NITPIX_DMG:-1}" ;;
  exp)     NAME=nitpix-exp; DIST="${NITPIX_DIST:-dist-exp}"; WORK=build/pyi-exp; DMG_WANTED="${NITPIX_DMG:-0}" ;;
  *) echo "NITPIX_VARIANT must be release or exp, not '$VARIANT'" >&2; exit 2 ;;
esac
APP="$DIST/$NAME.app"
[ -x .venv/bin/pyinstaller ] || uv pip install -q --python .venv/bin/python pyinstaller
.venv/bin/python tools/make_icon.py build/nitpix.icns
NITPIX_VARIANT="$VARIANT" .venv/bin/pyinstaller --noconfirm --clean --distpath "$DIST" --workpath "$WORK" --log-level WARN build/nitpix.spec
[ "$VARIANT" = release ] || rm -rf "${DIST:?}/$NAME"   # the loose folder PyInstaller leaves beside the .app
# Ad-hoc signature: required on Apple Silicon for the app to launch at all. A Developer ID
# signature + notarisation is needed before handing the app to other machines.
codesign --force --deep --sign - "$APP"
echo "built: $APP ($(du -sh "$APP" | cut -f1))"
# Checked with nothing in the environment, as when opened from Finder: the bundle itself must
# say which build it is.
CHECK=$(env -u NITPIX_VARIANT -u NITPIX_STATE_DIR "$APP/Contents/MacOS/nitpix" --selfcheck)
echo "$CHECK"
GOT=$(printf '%s' "$CHECK" | /usr/bin/python3 -c 'import json, sys; print(json.load(sys.stdin)["build"]["variant"])')
[ "$GOT" = "$VARIANT" ] || { echo "the bundle reports itself as '$GOT', expected '$VARIANT'" >&2; exit 1; }
[ "$DMG_WANTED" = 1 ] || exit 0
# Installer: a drag-to-Applications disk image. Everything the app needs is inside the bundle.
VER=$(sed -n 's/^__version__ = "\([^"]*\)".*/\1/p' nitpix/__init__.py)
if [ "$VARIANT" = release ]; then
  DMG="$DIST/nitpix-${VER}.dmg"; VOL="nitpix ${VER}"
else
  DMG="$DIST/nitpix-exp-${VER}-$(git rev-parse --short HEAD).dmg"; VOL="nitpix experimental ${VER}"
fi
STAGE=$(mktemp -d)
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
rm -f "$DMG"
hdiutil create -quiet -volname "$VOL" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
rm -rf "$STAGE"
echo "installer: $DMG ($(du -sh "$DMG" | cut -f1))"
