"""Project storage.

A project is a folder ending in `.nitpix` containing:
  project.db   SQLite database (assets, findings, notes, presets, settings)
  cache/       frame grabs and analysis side files (safe to delete)
  exports/     CSV / JSON / PDF reports

Source media is never written to. Every path the app writes to is checked to sit
inside the project folder (see `Project.safe_path`).
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

from . import variant

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS roots (id INTEGER PRIMARY KEY, path TEXT UNIQUE, added REAL);
CREATE TABLE IF NOT EXISTS presets (
  id TEXT PRIMARY KEY, name TEXT, spec TEXT, builtin INTEGER DEFAULT 0, updated REAL
);
CREATE TABLE IF NOT EXISTS assets (
  id INTEGER PRIMARY KEY,
  kind TEXT,                -- video | audio | image | sequence | other
  path TEXT UNIQUE,         -- file path, or sequence pattern path (dir/name.####.ext)
  name TEXT, dir TEXT, ext TEXT,
  size INTEGER, mtime REAL,
  container TEXT, vcodec TEXT, acodec TEXT,
  width INTEGER, height INTEGER, fps TEXT, fps_float REAL,
  duration REAL, frames INTEGER, start_tc TEXT,
  audio_channels INTEGER, sample_rate INTEGER,
  pix_fmt TEXT, bit_depth INTEGER, chroma TEXT, alpha INTEGER,
  color_primaries TEXT, color_trc TEXT, color_space TEXT,
  metadata TEXT,            -- normalised metadata JSON
  seq TEXT,                 -- sequence detail JSON (ranges, gaps, duplicates...)
  analysis TEXT,            -- raw ffmpeg detection results (kept so presets can be re-evaluated)
  scan_state TEXT DEFAULT 'pending', -- pending | probing | analysing | done | error | skipped
  scan_error TEXT,
  qc_result TEXT,           -- pass | warning | fail | error (computed from findings)
  review_status TEXT DEFAULT 'unreviewed', -- unreviewed | in_review | approved | approved_notes | rejected
  signed_by TEXT, signed_at REAL,
  in_frame INTEGER, out_frame INTEGER, last_frame INTEGER,
  added REAL, updated REAL
);
CREATE INDEX IF NOT EXISTS idx_assets_state ON assets(scan_state);
CREATE TABLE IF NOT EXISTS findings (
  id INTEGER PRIMARY KEY,
  asset_id INTEGER REFERENCES assets(id) ON DELETE CASCADE,
  check_key TEXT, category TEXT,
  kind TEXT,                -- measured | detected
  auto_result TEXT,         -- pass | warning | fail | info
  confidence TEXT,          -- high | medium | low
  title TEXT, detail TEXT, measured TEXT, expected TEXT, why TEXT,
  frame_in INTEGER, frame_out INTEGER,
  reviewer_status TEXT,     -- NULL (unreviewed) | pass | issue | warning | na
  reviewer_note TEXT, reviewed_by TEXT, reviewed_at REAL,
  created REAL
);
CREATE INDEX IF NOT EXISTS idx_findings_asset ON findings(asset_id);
CREATE TABLE IF NOT EXISTS notes (
  id INTEGER PRIMARY KEY,
  asset_id INTEGER REFERENCES assets(id) ON DELETE CASCADE,
  kind TEXT,                -- note | bookmark
  frame INTEGER, frame_out INTEGER,
  severity TEXT,            -- info | warning | issue
  text TEXT, author TEXT, created REAL, updated REAL
);
CREATE INDEX IF NOT EXISTS idx_notes_asset ON notes(asset_id);
"""

DEFAULT_SETTINGS = {
    "project_name": "Untitled delivery",
    "client": "",
    "reviewer": "",
    "preset_id": "led_notchlc_1080p25",
    "analysis": {
        "content_checks": True,       # black / freeze / flash / silence
        "loudness": True,
        "max_analysis_seconds": 0,    # 0 = whole file
        "black_min_duration": 0.20,   # seconds
        "black_pixel_threshold": 0.10,
        "freeze_min_duration": 1.0,
        "freeze_noise": 0.001,
        "flash_luma_jump": 60.0,      # YAVG delta (0-255) vs both neighbours
        "silence_threshold_db": -60.0,
        "silence_min_duration": 2.0,
        "clip_threshold_dbfs": -0.1,
        "dead_channel_db": -70.0,
        "seq_size_outlier_ratio": 0.35,  # frame < 35% of median size => suspicious
        "seq_hash_duplicates": True,
        "workers": 2,
    },
    "iina": {
        "cli_path": "/Applications/IINA.app/Contents/MacOS/iina-cli",
        "socket_mode": "per_session",  # per_session | fixed
        "fixed_socket": "/tmp/iina-qc.sock",
        "sequence_fps": 25,
    },
}


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _sanitise_settings(patch: dict) -> dict:
    """Drop settings values that would break scans (an emptied number box arrives as null),
    so the previous value is kept instead."""
    out = dict(patch or {})
    an = out.get("analysis")
    if isinstance(an, dict):
        clean = {}
        for k, v in an.items():
            default = DEFAULT_SETTINGS["analysis"].get(k)
            if isinstance(default, bool):
                clean[k] = bool(v)
            elif isinstance(default, (int, float)):
                try:
                    n = float(v)
                except (TypeError, ValueError):
                    continue
                if n != n or n in (float("inf"), float("-inf")):
                    continue
                clean[k] = int(n) if isinstance(default, int) else n
            else:
                clean[k] = v
        if "workers" in clean:
            clean["workers"] = max(1, min(8, clean["workers"]))
        out["analysis"] = clean
    ii = out.get("iina")
    if isinstance(ii, dict):
        ii = dict(ii)
        from .timecode import parse_rate
        if "sequence_fps" in ii and not parse_rate(ii["sequence_fps"]):
            ii.pop("sequence_fps")
        if ii.get("socket_mode") not in (None, "per_session", "fixed"):
            ii.pop("socket_mode")
        if "extra_args" in ii and not isinstance(ii["extra_args"], list):
            ii["extra_args"] = str(ii["extra_args"]).split()
        out["iina"] = ii
    return out


class Project:
    def __init__(self, folder: str | os.PathLike):
        self.folder = Path(folder).expanduser().resolve()
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / "cache").mkdir(exist_ok=True)
        (self.folder / "exports").mkdir(exist_ok=True)
        self.db_path = self.folder / "project.db"
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(assets)")}
        if "analysis" not in cols:
            self.conn.execute("ALTER TABLE assets ADD COLUMN analysis TEXT")
        if "ext_id" not in {r[1] for r in self.conn.execute("PRAGMA table_info(notes)")}:
            self.conn.execute("ALTER TABLE notes ADD COLUMN ext_id TEXT")  # IINfo marker id
        self.conn.commit()
        self.created = self.get_meta("settings") is None      # made just now, not opened (nitpix/defaults.py seeds it)
        if self.created:
            self.set_meta("settings", DEFAULT_SETTINGS)
            self.set_meta("created", time.time())

    # ---- guards -----------------------------------------------------------
    def safe_path(self, *parts: str) -> Path:
        """Return a path inside the project folder; refuse anything outside it."""
        p = (self.folder.joinpath(*parts)).resolve()
        if self.folder not in p.parents and p != self.folder:
            raise PermissionError(f"Refusing to write outside project: {p}")
        return p

    # ---- meta / settings --------------------------------------------------
    def get_meta(self, key, default=None):
        with self.lock:
            r = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r else default

    def set_meta(self, key, value):
        with self.lock:
            self.conn.execute(
                "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )
            self.conn.commit()

    @property
    def settings(self) -> dict:
        return _deep_merge(DEFAULT_SETTINGS, self.get_meta("settings", {}))

    def update_settings(self, patch: dict) -> dict:
        s = _deep_merge(self.settings, _sanitise_settings(patch))
        self.set_meta("settings", s)
        return s

    # ---- generic helpers --------------------------------------------------
    def q(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def q1(self, sql, args=()):
        with self.lock:
            r = self.conn.execute(sql, args).fetchone()
        return dict(r) if r else None

    def x(self, sql, args=()):
        with self.lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur.lastrowid

    # ---- assets -----------------------------------------------------------
    def upsert_asset(self, path: str, **fields) -> int:
        now = time.time()
        with self.lock:
            row = self.conn.execute("SELECT id FROM assets WHERE path=?", (path,)).fetchone()
            if row:
                if fields:
                    cols = ", ".join(f"{k}=?" for k in fields)
                    self.conn.execute(
                        f"UPDATE assets SET {cols}, updated=? WHERE id=?",
                        (*fields.values(), now, row[0]),
                    )
                self.conn.commit()
                return row[0]
            cols = ["path", "added", "updated", *fields.keys()]
            cur = self.conn.execute(
                f"INSERT INTO assets({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})",
                (path, now, now, *fields.values()),
            )
            self.conn.commit()
            return cur.lastrowid

    def update_asset(self, asset_id: int, **fields):
        if not fields:
            return
        for k in ("metadata", "seq", "analysis"):
            if k in fields and not isinstance(fields[k], (str, type(None))):
                fields[k] = json.dumps(fields[k])
        cols = ", ".join(f"{k}=?" for k in fields)
        self.x(f"UPDATE assets SET {cols}, updated=? WHERE id=?", (*fields.values(), time.time(), asset_id))

    def asset(self, asset_id: int) -> dict | None:
        a = self.q1("SELECT * FROM assets WHERE id=?", (asset_id,))
        if a:
            a["metadata"] = json.loads(a["metadata"]) if a["metadata"] else {}
            a["seq"] = json.loads(a["seq"]) if a["seq"] else None
            a.pop("analysis", None)
        return a

    def analysis(self, asset_id: int) -> dict | None:
        r = self.q1("SELECT analysis FROM assets WHERE id=?", (asset_id,))
        return json.loads(r["analysis"]) if r and r["analysis"] else None

    def replace_findings(self, asset_id: int, findings: list[dict]):
        """Replace automated findings for an asset, keeping reviewer decisions
        where the same check fires again at the same place."""
        with self.lock:
            old = {
                (r["check_key"], r["frame_in"]): r
                for r in self.q("SELECT * FROM findings WHERE asset_id=?", (asset_id,))
            }
            self.conn.execute("DELETE FROM findings WHERE asset_id=?", (asset_id,))
            now = time.time()
            for f in findings:
                prev = old.get((f.get("check_key"), f.get("frame_in")))
                keep = {k: prev[k] for k in ("reviewer_status", "reviewer_note", "reviewed_by", "reviewed_at")} if prev else {}
                row = {**f, **keep, "asset_id": asset_id, "created": now}
                cols = list(row.keys())
                self.conn.execute(
                    f"INSERT INTO findings({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})",
                    [row[c] for c in cols],
                )
            self.conn.commit()
        self.recompute_result(asset_id)

    def recompute_result(self, asset_id: int):
        """Asset QC result: reviewer decisions override automated results."""
        rows = self.q("SELECT auto_result, reviewer_status FROM findings WHERE asset_id=?", (asset_id,))
        a = self.q1("SELECT scan_state FROM assets WHERE id=?", (asset_id,))
        if a and a["scan_state"] == "error":
            result = "fail"
        else:
            worst = 0
            rank = {"pass": 0, "info": 0, "na": 0, "warning": 1, "issue": 2, "fail": 2}
            for r in rows:
                eff = r["reviewer_status"] or r["auto_result"]
                worst = max(worst, rank.get(eff, 0))
            notes = self.q("SELECT severity FROM notes WHERE asset_id=? AND kind='note'", (asset_id,))
            for n in notes:
                worst = max(worst, rank.get(n["severity"] or "info", 0))
            result = ["pass", "warning", "fail"][worst]
        self.x("UPDATE assets SET qc_result=? WHERE id=?", (result, asset_id))
        return result

    # ---- IINfo markers ------------------------------------------------------
    def iinfo_dismissed(self) -> set:
        return set(self.get_meta("iinfo_dismissed", []))

    def dismiss_iinfo(self, ext_id: str):
        self.set_meta("iinfo_dismissed", sorted(self.iinfo_dismissed() | {ext_id}))

    def import_iinfo(self, asset_id: int, data_dir: str | None = None) -> int:
        """Add IINfo markers for this asset as notes. Returns how many were added."""
        from . import iinfo
        from .timecode import asset_rate
        a = self.asset(asset_id)
        if not a:
            return 0
        events = iinfo.load(a, data_dir or iinfo.DATA_DIR)
        if not events:
            return 0
        known = {r["ext_id"] for r in self.q("SELECT ext_id FROM notes WHERE asset_id=? AND ext_id IS NOT NULL",
                                              (asset_id,))}
        now = time.time()
        new = iinfo.import_events(a, events, asset_rate(a), known, self.iinfo_dismissed())
        for n in new:
            self.x("INSERT INTO notes(asset_id, kind, frame, frame_out, severity, text, author, created, updated, ext_id)"
                   " VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (asset_id, n["kind"], n["frame"], n["frame_out"], n["severity"], n["text"], n["author"], now, now,
                    n["ext_id"]))
        if new:
            self.recompute_result(asset_id)
        return len(new)

    def export_iinfo(self, asset_id: int, data_dir: str | None = None) -> dict:
        from . import iinfo
        from .timecode import asset_rate
        a = self.asset(asset_id)
        if not a:
            raise ValueError("Asset not found")
        notes = self.q("SELECT * FROM notes WHERE asset_id=? ORDER BY frame, id", (asset_id,))
        return iinfo.export(a, notes, asset_rate(a), self.iinfo_dismissed(), data_dir or iinfo.DATA_DIR)

    def close(self):
        with self.lock:
            self.conn.close()


# The app's own state (the recent-projects list) lives in ~/.nitpix, or ~/.nitpix-exp for an
# experimental build (nitpix/variant.py). NITPIX_STATE_DIR moves it; the tests point it at a
# temporary folder so they never touch the user's list.
def _recent_file() -> Path:  # looked up on each use, so the override works whenever it is set
    return variant.state_dir() / "recent.json"


RECENT_FILE = _recent_file()
_OLD_RECENT = Path.home() / ".lhs-qc-review" / "recent.json"  # before the rename to nitpix
# The release takes over the list from before the rename; an experimental build never reads it.
if not variant.IS_EXPERIMENTAL and not os.environ.get("NITPIX_STATE_DIR") and not RECENT_FILE.exists() and _OLD_RECENT.exists():
    try:
        RECENT_FILE.parent.mkdir(parents=True, exist_ok=True)
        RECENT_FILE.write_text(_OLD_RECENT.read_text())
    except OSError:
        pass


def remember_project(folder: Path, name: str):
    f = _recent_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    try:
        items = json.loads(f.read_text())
    except Exception:
        items = []
    items = [i for i in items if i.get("path") != str(folder)]
    items.insert(0, {"path": str(folder), "name": name, "opened": time.time()})
    f.write_text(json.dumps(items[:12], indent=1))


def recent_projects() -> list[dict]:
    try:
        items = json.loads(_recent_file().read_text())
    except Exception:
        return []
    return [i for i in items if Path(i["path"], "project.db").exists()]
