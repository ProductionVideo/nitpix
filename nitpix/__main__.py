#!/usr/bin/env python3
"""nitpix — delivery QC with frame-accurate review of the source media.

    nitpix                          # desktop app; choose/create a project in it
    nitpix ~/QC/Client_X.nitpix     # open (or create) a project
    nitpix --web                    # the browser UI instead (preset editor lives here for now)
    nitpix --web --port 8800 --no-browser

`nitpix` is .venv/bin/nitpix after `uv sync`; `python -m nitpix` also works.
"""
import argparse
import sys

if sys.version_info < (3, 13):
    sys.exit("nitpix needs Python 3.13 or newer.")


def main():
    ap = argparse.ArgumentParser(prog="nitpix", description="nitpix — delivery QC")
    ap.add_argument("project", nargs="?", help="Project folder (.nitpix). Created if it does not exist.")
    ap.add_argument("--web", action="store_true", help="Run the browser UI (local server) instead of the desktop app")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true", help="With --web: do not open a browser window")
    ap.add_argument("--selfcheck", nargs="?", const=True, metavar="MEDIA",
                    help="Report the tools this installation found (JSON) and exit; with a media file, try to decode it")
    ap.add_argument("--scene-import", nargs=2, metavar=("SCENE", "OUT"), help=argparse.SUPPRESS)  # the Mapping importer's helper process
    a = ap.parse_args()
    if a.scene_import:
        from nitpix.sceneimport import run_helper
        sys.exit(run_helper(*a.scene_import))
    if a.selfcheck:
        import json
        from nitpix.selfcheck import run as selfcheck
        print(json.dumps(selfcheck(None if a.selfcheck is True else a.selfcheck), indent=1))
        return
    if a.web:
        from nitpix.server import serve
        serve(port=a.port, project=a.project, open_browser=not a.no_browser)
        return
    try:
        from nitpix.ui.app import run
    except ImportError as e:
        sys.exit(f"The desktop app needs PySide6 ({e}). Install with: uv sync   or run the browser UI with --web")
    sys.exit(run(a.project))


if __name__ == "__main__":
    main()
