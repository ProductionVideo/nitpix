# nitpix

Delivery QC for video, audio, stills and image sequences, reviewed frame by frame on the source media in a desktop app (macOS now; 64-bit Windows and Linux are kept possible).

Scan a delivery, compare it against a delivery spec, then confirm or dismiss every automated finding frame-accurately. Playback is embedded libmpv showing the source file itself (ProRes, DNxHR, NotchLC, HAP, EXR/DPX sequences): no proxies, no transcode. Optionally hand an asset to IINA on a Mac. At the end, export a signed-off report.

Source media is only ever **read**. Everything the app writes goes inside the project folder.

---

## Install (macOS)

Paste this into Terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/ProductionVideo/nitpix/main/install.sh | bash
```

It downloads the latest release, puts nitpix in Applications and opens it. Installed this way, macOS does not block the first launch: the "unidentified developer" check only applies to files a browser downloaded, and the in-app updater installs the same way. Run the same line again to update.

Or download `nitpix-<version>.dmg` from the latest GitHub release and drag nitpix to Applications. That copy is flagged by the browser, so the first launch needs **System Settings ▸ Privacy & Security ▸ Open Anyway** (older macOS: right-click ▸ Open). The proper fix is an Apple Developer ID signature and notarisation, which nitpix does not have yet.

Either way the app is self-contained (ffmpeg, ffprobe, libmpv and Qt are inside) and built for Apple Silicon.

## Getting media in

Drop files or folders anywhere on the window, or on the nitpix icon in the Dock, and they scan straight away; with no project open, nitpix starts one named after what you dropped. Drops made during a scan queue and start when it finishes. A dropped `.nitpix` folder opens that project. **Add media** still offers the full dialog (re-check unchanged files, pick an image sequence by one frame).

Press **?** (or "? Shortcuts" in the sidebar) on any screen for the keys and power moves that apply there.

### Updates

nitpix checks GitHub for a newer release when it starts (at most once a day, quietly when offline) and offers to install it in place and relaunch (the packaged app) or download it; Help ▸ Check for Updates… and Settings ▸ Updates do the same on demand, and the automatic check can be switched off there. It only reads the public releases list, and nothing is installed until you choose Install.

## Running

```bash
uv sync                       # Python 3.14 + PySide6 + python-mpv + reportlab into .venv
brew install ffmpeg mpv       # ffmpeg/ffprobe for scanning, libmpv for playback
.venv/bin/nitpix              # or double-click "nitpix.command"; `python -m nitpix` also works
.venv/bin/nitpix --web        # the legacy browser UI
.venv/bin/nitpix --selfcheck  # what this installation found: ffmpeg, ffprobe, libmpv, and a decode test
```

### Packaged app (macOS)

```bash
tools/build_mac.sh            # -> dist/nitpix.app and dist/nitpix-<version>.dmg (drag-to-Applications installer)
```

The app carries PySide6, libmpv with its codecs, ffmpeg and ffprobe, so the installer has no dependencies to resolve. Running from source, the app checks for ffmpeg, ffprobe and libmpv at start-up and offers to install the missing ones with Homebrew (also under Settings ▸ Tools).

The bundle needs nothing installed on the target Mac. It is ad-hoc signed, which is enough to run on the machine that built it; before handing it to other machines it needs a Developer ID signature and notarisation, or recipients must right-click ▸ Open once. `dist/nitpix.app/Contents/MacOS/nitpix --selfcheck` reports what the bundle contains. Library sources for the build can be overridden with `NITPIX_LIBMPV` and `NITPIX_FFMPEG_BIN`.

### Renamed from LHS-QC Review

Projects are `.nitpix` folders now; older `.lhsqc` folders still open. The recent-projects list is carried over on first launch. Environment variables are `NITPIX_*` (were `LHSQC_*`). IINfo sidecars written by the old name are still recognised. The built-in spec ids (e.g. `lhs_hd_prores_25`) are unchanged so saved projects keep their spec. Delivery specs were called presets before 0.5.5; only the name changed.

The desktop app is `nitpix/ui/`: a PySide6 window whose review screen renders the source media on libmpv (`nitpix/ui/mpvwidget.py`). Frame maths are shared with the rest of the app: frame *n* starts at *n* / rate, exact seeks aim a quarter-frame early, positions are read back as round(time × rate). Verified frame-exact on bar-coded clips at 25, 23.976 and 29.97 from both the decoded frame and the pixels drawn. libmpv is found by `nitpix/libmpv.py` (set `NITPIX_LIBMPV` to a specific library); without it the review screen falls back to ffmpeg frame grabs.

The sidebar shows icons with labels and collapses to an icon strip (the chevron at its top; remembered between runs). Keyboard: Space and the arrow keys drive the player from anywhere on the review screen (a just-clicked button, the tabs, the metadata tree); only text fields keep them, and the go-to field hands them back while it is empty. Space on a finished clip plays it again from the start. Dragging on the timeline shows exact frames as fast as the decoder delivers them; while the picture is catching up with the pointer, the timecode is dimmed and already shows the frame you are going to (notes, bookmarks and in/out points use that frame). Dragging during playback keeps playing. New projects default to the built-in show-content spec: NotchLC 1080p25 in MOV, Rec.709 / gamma 2.4, alpha required (it exposes IMAG), stereo 48 kHz PCM, PNG stills with alpha at 1920×1080. ‹ Markers › (or [ and ]) jumps between findings and notes on the timeline. Start timecode is reported but only checked when a spec asks for it ("Timecode track: Required", or a specific start timecode). Profiles only apply to codecs that have them, so a ProRes 4444 profile in a spec does not fail NotchLC; NotchLC's colour tags are reported as information, since the decoder's RGB/sRGB tags are fixed defaults rather than metadata. Image sequences come in through Add media: choose the folder that holds the frames, or any one frame of it ("Choose image sequence…"); frames named `name.####.ext` become one asset. The window sizes itself to the screen and remembers its last position; long names and paths elide and the transport controls wrap, so the layout holds down to about 1040 × 640. Specs are edited natively (pickers for codecs, colour, rates and limits; typed values are matched through the vocabulary). Still to come: the Windows build.

## Setup (macOS)

1. **Python 3.9+.** macOS ships `python3` with the Xcode command-line tools: `xcode-select --install`.
2. **FFmpeg** (required, provides `ffprobe` and `ffmpeg`): `brew install ffmpeg`
3. **IINA** (recommended): <https://iina.io> or `brew install --cask iina`. Launch it once after installing.
4. **PDF export** needs ReportLab: `python3 -m pip install --user -r requirements.txt`
5. **Run the app**, by either:
   ```bash
   python3 -m nitpix                       # opens http://127.0.0.1:8765 in your browser
   python3 -m nitpix ~/QC/ClientX.nitpix    # open or create a project directly
   ```
   or double-clicking **`nitpix.command`**.
6. **Load the demo.** On the start screen, click **Load demo delivery**. It generates about 400 MB of test media with planted faults (around 2 minutes) and scans it.

The server listens on `127.0.0.1` only. Stop it with Ctrl+C in the terminal.

## How the app talks to IINA

**What IINA supports, and how each item is used:**

| Need | IINA support | How it's used |
|---|---|---|
| Open a file from another app | Yes: `iina-cli` in `IINA.app/Contents/MacOS` | Launched with the file, or an `mf://` list for sequences |
| Pass player options | Yes: any mpv option as `--mpv-<name>=<value>` | `start`, `keep-open`, `hr-seek`, `pause`, `mf-fps`, `input-ipc-server` |
| Control and read back position | Yes, via mpv's JSON IPC socket (`input-ipc-server`) | `time-pos`, `pause`, `duration`, `estimated-frame-number`, `seek … absolute+exact`, `frame-step`, `frame-back-step`, A-B loop, OSD text |
| Timecode | **No**: IINA has no source-timecode concept | The app computes all timecode from ffprobe metadata (NDF and DF) |
| `iina://` URL scheme | Restricted in IINA 1.4.3 and 1.4.4 (security fixes) | Not used |
| EXR/DPX sequence playback | Depends on IINA's bundled FFmpeg | Best effort. The built-in viewer is the fallback |

**Two connection modes** (Settings ▸ IINA):

- **Per session** (default). Each "Open in IINA" passes a fresh `--mpv-input-ipc-server=/tmp/nitpix-….sock`.
- **Fixed socket**. Use this if the app reports that IINA opened the file but no control socket appeared. In IINA ▸ Settings ▸ Advanced, enable advanced settings and add the mpv option `input-ipc-server` = `/tmp/iina-qc.sock`. Restart IINA, then pick *Fixed path* in this app. The app then loads files into IINA through the socket.

**Browser player.** Press <kbd>p</kbd> (or *Player* on the viewer) to play a video or image sequence in the app itself. Browsers can't decode ProRes (outside Safari), DNxHR, NotchLC, HAP or EXR/DPX, so the first time you do this ffmpeg makes an H.264 proxy in the project cache: about 5–6× realtime on Apple Silicon, so a 10-minute UHD file takes around 2 minutes. The proxy keeps the asset's exact frame rate with frame *n* at *n* / rate, and holds the previous frame over sequence gaps, so the playhead, timecode, stepping, notes and the in/out loop all line up with the source. It is a viewing copy: 1920 px wide at most, 4:2:0, no alpha, first audio track only. Judge colour and detail in the frame viewer, and measurements always come from the source.

**Built-in frame viewer.** This is always available. Every frame or timecode is rendered exactly by ffmpeg into the project cache. You can review, take notes and decide on findings without IINA. The viewer is also used on non-Mac machines.

## Which checks need external tools

| Check | Tool | Kind |
|---|---|---|
| Zero-byte, readability, container, codec/profile, resolution, fps/VFR, duration, PAR/DAR, rotation/orientation, field order, timecode, colour primaries/transfer/matrix/range, bit depth, chroma, alpha, audio codec/rate/channels/layout | `ffprobe` | measured |
| Full-decode integrity (decoder errors, truncation) | `ffmpeg` | measured |
| Integrated loudness, LRA, true peak (EBU R128 / BS.1770) | `ffmpeg ebur128` | measured |
| Sample peak, clipping, dead or dropped-out channels, on every audio stream | `ffmpeg astats` (0.5 s windows) | detected |
| Silence, including a track that is silent throughout | `ffmpeg silencedetect` | detected |
| Black, frozen picture | `ffmpeg blackdetect`, `freezedetect` | detected |
| Flash or stray frames | `ffmpeg signalstats` mean luma per frame | detected |
| Sequence grouping, gaps, duplicate numbers, padding | none (filename parsing) | measured |
| Per-frame header, truncation, dimensions (PNG, DPX, EXR, TIFF, JPEG) | none (built-in readers) | measured |
| Repeated (identical) sequence frames | none (sampled BLAKE2 hash) | detected |
| Delivery-spec comparison | none (rules engine + spec) | measured |

Every result is shown as one of three kinds:

- **Measured:** read from the file.
- **Detected:** a heuristic, shown with a confidence level and kept as "awaiting decision" until a reviewer marks it Pass, Issue, Warning or N/A.
- **Observed:** a reviewer's own note or bookmark.

Reports keep the three apart.

## Workflow

1. **Add media.** Use *Add media*, then *Choose folders…* (native macOS picker), *Browse here*, or paste paths.
   - Folders are scanned recursively.
   - Sequences are grouped by `name.####.ext`.
   - Re-scanning skips files that haven't changed.
2. **Watch the batch.** The progress bar shows the current files, ETA, and Pause / Resume / Cancel.
   - Pause freezes running ffmpeg processes.
   - Cancel kills them and returns their files to *pending*.
   - Re-scan later to pick up where you left off.
   - Markers made in IINA with the [IINfo](https://github.com/ProductionVideo/IINfo) plugin are added to each asset as notes (see below).
3. **Triage.** Use the Dashboard and the Media list's filters for result, type, format, resolution, fps, duration, review status and "has unconfirmed detections". Search `check:content.black` lists assets with that problem.
4. **Review an asset.** Press <kbd>e</kbd> to open it in IINA at the current frame. The playhead, timecode, in/out points (sent to IINA as an A-B loop), notes and bookmarks all stay tied to that asset.
5. **Decide.** Mark each finding Pass, Issue, Warning or N/A (<kbd>1</kbd>–<kbd>4</kbd>) and add notes. Then set the asset to Approved, Approved with notes, or Rejected.
6. **Report.** Under Reports, export PDF, CSV or JSON for all assets, the current filter, or the selection. Record a batch sign-off.

Press <kbd>?</kbd> in the app for all keyboard shortcuts.

### IINfo markers

[IINfo](https://github.com/ProductionVideo/IINfo) is an IINA plugin for marking QC issues while you watch (<kbd>⌥⇧M</kbd>). Each scan reads its markers for every video and audio file, from `<file>.iinfo.json` beside the media and from IINfo's own data folder, and adds new ones as notes credited to "IINfo". Markers saved for a different render of the file (a different size) are skipped. A marker you delete here is not imported again.

**Sync with IINfo** on an asset's Notes tab goes the other way. It writes `<file>.iinfo.json` beside the media, so IINfo shows your nitpix notes on its timeline in IINA. The file keeps every IINfo marker, including those in IINfo's data folder, which IINfo stops showing once a sidecar exists. Edits to imported notes are written back to the original marker. Nothing is written beside media until you press the button.

## Specs and settings

- **Spec fields** are pickers built from the values ffprobe reports, with an "+ other" box for anything not listed. Typed values are matched loosely ("ProRes 422 HQ", "Rec.709", "23.98", "48k", "PQ"), stored in canonical form, and flagged if not recognised. Invalid numbers, timecodes and regexes are rejected when you save.
- **Delivery specs** cover video, audio and image-sequence rules. Six are built in, including HD ProRes 25p EBU R128, UHD ProRes 4444 for LED or playback, Web H.264 at −14 LUFS, social vertical, and VFX EXR starting at frame 1001. Duplicate a built-in to customise it.
- **Switching specs** re-evaluates stored measurements instantly, with no re-decode.
- **Analysis thresholds** (black, freeze, flash, silence, clipping, dead channel, sequence outliers), a decode time limit and parallel workers are set per project.

## Project data

```
ClientX.nitpix/
  project.db   SQLite: assets, metadata, raw detections, findings, decisions, notes, presets, settings
  cache/       frame grabs, browser proxies, IINA launch log, sequence lists (safe to delete)
  exports/     CSV / JSON / PDF reports
```

Reopen a project from the start screen, or with `python3 -m nitpix path/to/project.nitpix`.

## Known limitations

- **IINA was tested against IINA 1.5.0** (`tests/test_iina_live.py`), covering open-at-frame, exact seek, frame stepping and image sequences at 25, 23.976 and 29.97 fps. The bridge works around four IINA behaviours: iina-cli treats a redirected stdin as piped media (so the app passes `--no-stdin`); IINA unpauses about 0.4 s after a file loads; IINA percent-encodes `mf://` paths that contain spaces; and every iina-cli launch is a new IINA process, so the app reuses the open window. If that window has been closed but IINA left its player running (idle, with no window), the app quits that player and opens a new window. Older IINA versions have not been checked. *Fixed socket* mode and `Extra iina-cli arguments` remain as fallbacks.
- **Gaps in sequences played in IINA** are filled by holding the previous frame, so frame indices stay aligned with the app.
- **EXR in the frame viewer** gets a simple sRGB transfer. There is no OCIO or ACES view transform. HDR (PQ/HLG) frames are not tone-mapped.
- **NotchLC** is decoded by ffmpeg (and plays in IINA). Three things are specific to it:
  - **Colour.** ffmpeg tags NotchLC with an RGB matrix, but the decoded planes are YCbCr. Converted as BT.601 full range, frames match disguise's own NotchLC decoder to within one code value; BT.709 is off by up to 8. The frame viewer and browser proxies use BT.601. IINA (mpv) reads it as BT.709 and converts sRGB to display gamma, so use the frame viewer, not IINA, to judge NotchLC colour.
  - **Alpha.** The decoder always outputs an alpha plane, even for NotchLC RGB. The scan measures the plane over every frame, and reports "fully opaque" when it carries no transparency. This applies to any format with alpha.
  - **Timecode.** NotchLC exports usually have none; the timecode check warns and 00:00:00:00 is assumed.
- **Only the first video stream is analysed.** Every audio stream gets silence, clipping, drop-out and loudness checks, and findings name the stream (`Audio #2: …`) when there is more than one. The loudness and true-peak rules in a spec apply to the first audio stream; the others are reported, and true peak above 0 dBTP (or the spec's limit) is flagged on any stream. Multi-mono MXF tracks are not merged into one programme for loudness.
- **Black, freeze, flash, clipping and drop-out detection are heuristics.** Hard cuts can look like flashes. Static graphics look like freezes. Clipping is only claimed when a full-scale window is flat-topped (astats `Flat_factor` ≥ 1, i.e. runs of identical samples at the peak); a clean tone or a brick-wall limited master at −0.1 dBFS reads exactly 0 and is not flagged. Audio decoded from lossy sources often has short flat-topped runs that may be inaudible. That is why every detection needs a reviewer decision.
- **Sequences get no content analysis.** Checks are header, filename and sampled-hash based. Image sequences have no frame rate, so the playback fps comes from Settings.
- **Interlace detection** relies on the file's field-order flag. There is no `idet` analysis.
- **Drop-frame timecode** is supported for 29.97 and 59.94. 23.976 uses 24-frame counting, as is standard.
- **Format support** is whatever your FFmpeg build decodes. ProRes RAW, BRAW and R3D are not supported.
- **One reviewer per project at a time.** A project on a shared drive is fine, but not edited simultaneously.
- **The interface runs in your browser** at `127.0.0.1`. It is not yet wrapped as a native app.

## Developing

```bash
python3 -m unittest discover tests          # full suite, about 30 s; frame-viewer tests need ffmpeg
NITPIX_LIVE_IINA=1 python3 -m unittest tests.test_iina_live -v   # real IINA on a Mac (opens windows)
python3 tools/make_demo.py /tmp/demo        # regenerate demo media
NITPIX_ALLOW_NON_MAC=1 python3 -m nitpix     # test the IINA bridge on Linux with tools/dev/fake-iina-cli + mpv
```

Code map:

- `nitpix/discovery.py`: walking folders and grouping sequences
- `nitpix/probe.py`: ffprobe
- `nitpix/analysis.py`: ffmpeg detectors
- `nitpix/sequences.py`: per-frame checks
- `nitpix/checks.py`: rules, reasons, presets
- `nitpix/vocab.py`: preset vocabulary (options, aliases, matching, preset validation)
- `nitpix/scanner.py`: jobs, pause/resume
- `nitpix/iina.py`: IINA bridge (optional extra)
- `nitpix/libmpv.py`, `nitpix/playlist.py`: finding libmpv; playable targets (mf:// lists for sequences)
- `nitpix/service.py`: operations shared by the desktop app and the web server (lists, decisions, notes, dashboard, presets, sign-off)
- `nitpix/ui/`: desktop app (app, review, media, presets, timeline, mpvwidget, widgets, theme)
- `nitpix/iinfo.py`: IINfo marker import and sidecar export
- `nitpix/proxy.py`: browser playback proxies
- `nitpix/frames.py`: frame viewer
- `nitpix/reports.py`: reports
- `nitpix/server.py`: API
- `nitpix/web/`: UI

Tests:

- `tests/test_iina.py` drives the bridge against `tests/fakeiina.py`, a stand-in for iina-cli and IINA that speaks mpv IPC and reproduces the IINA behaviours listed under Known limitations. It runs on any OS.
- `tests/test_iina_live.py` runs the same checks against real IINA. Each clip encodes its frame number as bars, so every seek and step is verified from a screenshot.
- `tests/test_frames.py` checks that built-in viewer grabs are frame-exact, including deep into 23.976 clips.
- `tests/test_analysis.py` runs the real ffmpeg detectors on clips with defects at known times: black, freeze, a silent gap, multiple audio streams, and clipping (a clean −0.1 dBFS tone and a limited master must not be flagged; a clipped burst must be, on the right channel).
- `tests/test_proxy.py` builds proxies from clips whose frames encode their own number (ProRes 4444 with alpha at 29.97, a PNG sequence with a missing frame) and checks every proxy frame, plus the HTTP Range requests the player makes.
- `nitpix/demo.py`: the demo delivery generator (also `tools/make_demo.py`); `nitpix/selfcheck.py`: `--selfcheck`
- `build/nitpix.spec`, `tools/build_mac.sh`, `tools/make_icon.py`: packaging
- `tests/test_service.py` covers the shared service layer; `tests/test_ui.py` builds the desktop screens offscreen without libmpv and drives the review flow by keyboard (run with `.venv/bin/python`).
- `tests/test_iinfo.py` covers IINfo's file fingerprint (checked against IINfo's own JavaScript), frame mapping at NTSC rates, merge rules and round trips.
- `tests/test_vocab.py`, `test_checks.py`, `test_store.py`, `test_server.py` and `test_web.py` cover preset matching, rules, settings, the API (including a real scan end to end) and the UI/vocabulary contract.

Fonts are Inter (the desktop app), Inter Tight (PDF reports and the legacy web UI) and IBM Plex Mono, all under the SIL Open Font License; the licences are in `nitpix/web/fonts`.

## Licence

nitpix is free software under the MIT licence (see `LICENSE`). It is built by people who QC media for a living and shaped by what breaks in real deliveries; issues with sample files and pull requests are welcome, see `CONTRIBUTING.md`.
