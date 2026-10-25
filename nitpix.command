#!/bin/bash
# Double-click to start nitpix (macOS). Close this Terminal window to stop it.
cd "$(dirname "$0")"
if ! command -v ffprobe >/dev/null 2>&1 && [ ! -x /opt/homebrew/bin/ffprobe ] && [ ! -x /usr/local/bin/ffprobe ]; then
  echo "FFmpeg not found. Install it with:  brew install ffmpeg"
fi
if [ ! -f /opt/homebrew/lib/libmpv.dylib ] && [ -z "$NITPIX_LIBMPV" ]; then
  echo "libmpv not found; the player falls back to the frame viewer. Install it with:  brew install mpv"
fi
if [ ! -x .venv/bin/nitpix ]; then
  if command -v uv >/dev/null 2>&1; then uv sync -q; else echo "uv not found: install from https://docs.astral.sh/uv/"; exit 1; fi
fi
exec .venv/bin/nitpix "$@"
