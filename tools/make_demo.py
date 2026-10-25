#!/usr/bin/env python3
"""Generate the demo delivery from the command line.

Usage: python3 tools/make_demo.py [output_folder]
Needs ffmpeg. Takes ~30-60 s and ~250 MB of disk. The app does the same from its start screen."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nitpix import demo  # noqa: E402

if __name__ == "__main__":
    try:
        demo.generate(sys.argv[1] if len(sys.argv) > 1 else demo.locations()["delivery"])
    except RuntimeError as e:
        sys.exit(str(e))
