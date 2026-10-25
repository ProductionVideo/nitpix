#!/bin/bash
# Install (or update) nitpix on macOS from the latest GitHub release.
#
#   curl -fsSL https://raw.githubusercontent.com/ProductionVideo/nitpix/main/install.sh | bash
#
# Downloads the DMG with curl, copies nitpix.app into /Applications (or ~/Applications when
# /Applications is not writable) and opens it. Files fetched by curl carry no quarantine flag,
# so macOS does not stop the first launch the way it does for a browser download.
# Options (environment): NITPIX_INSTALL_DIR=<folder>  NITPIX_VERSION=v0.5.3  NITPIX_NO_OPEN=1
set -euo pipefail

REPO="ProductionVideo/nitpix"
say() { printf '\033[1mnitpix:\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mnitpix:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || die "this installer is for macOS."
[ "$(uname -m)" = "arm64" ] || say "warning: this build is for Apple Silicon; on an Intel Mac it will not start."

if [ -n "${NITPIX_VERSION:-}" ]; then api="https://api.github.com/repos/$REPO/releases/tags/$NITPIX_VERSION"
else api="https://api.github.com/repos/$REPO/releases/latest"; fi
json=$(curl -fsSL -H "Accept: application/vnd.github+json" "$api") || die "could not reach GitHub."
url=$(printf '%s' "$json" | grep -o '"browser_download_url": *"[^"]*\.dmg"' | head -1 | sed 's/.*"\(https[^"]*\)"$/\1/')
tag=$(printf '%s' "$json" | grep -o '"tag_name": *"[^"]*"' | head -1 | sed 's/.*"\([^"]*\)"$/\1/')
[ -n "$url" ] || die "no .dmg found in release ${tag:-latest}."

dest="${NITPIX_INSTALL_DIR:-/Applications}"
if [ -z "${NITPIX_INSTALL_DIR:-}" ] && [ ! -w "$dest" ]; then dest="$HOME/Applications"; fi
mkdir -p "$dest"

tmp=$(mktemp -d)
mnt="$tmp/mnt"
cleanup() { hdiutil detach -quiet "$mnt" 2>/dev/null || true; rm -rf "$tmp"; }
trap cleanup EXIT

say "downloading $tag"
curl -fL --progress-bar -o "$tmp/nitpix.dmg" "$url"
mkdir -p "$mnt"
hdiutil attach -nobrowse -readonly -quiet -mountpoint "$mnt" "$tmp/nitpix.dmg"
[ -d "$mnt/nitpix.app" ] || die "the disk image has no nitpix.app."

# Only a copy running from the folder being replaced is asked to quit; other copies are left alone.
if pgrep -qf "$dest/nitpix.app/Contents/MacOS/nitpix"; then
  say "quitting the nitpix running from $dest"
  osascript -e "tell application \"$dest/nitpix.app\" to quit" >/dev/null 2>&1 || true
  for _ in 1 2 3 4 5 6 7 8 9 10; do pgrep -qf "$dest/nitpix.app/Contents/MacOS/nitpix" || break; sleep 0.5; done
fi
rm -rf "$dest/nitpix.app.new"
ditto "$mnt/nitpix.app" "$dest/nitpix.app.new"
xattr -dr com.apple.quarantine "$dest/nitpix.app.new" 2>/dev/null || true
rm -rf "$dest/nitpix.app"
mv "$dest/nitpix.app.new" "$dest/nitpix.app"

say "installed $tag in $dest/nitpix.app"
[ -n "${NITPIX_NO_OPEN:-}" ] || open "$dest/nitpix.app"
