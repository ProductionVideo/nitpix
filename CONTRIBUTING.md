# Contributing to nitpix

nitpix is a delivery-QC tool made by people who review media for a living. Bug reports,
test files that break it, and pull requests are all welcome. There is no CLA; contributions
are accepted under the MIT licence in `LICENSE`.

## Reporting a problem

Open an issue with:

- what you expected and what happened,
- the output of `nitpix --selfcheck` (or `dist/nitpix.app/Contents/MacOS/nitpix --selfcheck`; it says whether the app is a release or an experimental build),
- for a media problem, `ffprobe -v error -show_streams -show_format <file>` and, if you can share
  it, a short clip that reproduces it. Codec and colour edge cases (NotchLC, HAP, EXR, drop-frame
  timecode) are exactly the reports we want.

## Working on the code

```bash
uv sync                                   # Python 3.14 + PySide6 + python-mpv + reportlab
brew install ffmpeg mpv                   # macOS; see README for other platforms
.venv/bin/nitpix                          # run the app
python3 -W ignore -m unittest discover tests      # backend tests (no Qt needed)
.venv/bin/python -m unittest tests.test_ui        # desktop UI tests, offscreen
```

A few conventions, mostly learned the hard way:

- Frame maths lives in `nitpix/timecode.py` and `nitpix/ui/mpvwidget.py`; frame *n* starts at
  *n* / rate. Anything that touches it needs a test against a bar-coded clip or the existing
  timecode tests.
- Rules go in `nitpix/checks.py`, vocabulary (aliases, matching) in `nitpix/vocab.py`. Each
  finding carries a `why`, written for a reviewer, not a developer.
- UI text of unbounded length uses `ElideLabel`; toolbars use `FlowLayout`. The review screen
  must keep working at 1040 px wide (`tests/test_ui.py` checks this).
- The web UI in `nitpix/web/` is legacy; new features go in the desktop app.

Pull requests should come with a test, and `README.md` updated if behaviour changes.
