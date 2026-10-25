"""Background scan jobs with pause / resume / cancel."""
from __future__ import annotations

import json
import os
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

from . import checks, discovery
from .analysis import Cancelled, ProcControl, analyse
from .imageheaders import read_header
from .presets import builtin_by_id
from .probe import ProbeError, ffprobe, normalise, summary_fields
from .sequences import scan_frames
from .store import Project
from .timecode import parse_rate


def all_presets(project: Project) -> list[dict]:
    out = {p["id"]: {**p, "builtin": True} for p in builtin_by_id().values()}
    for r in project.q("SELECT * FROM presets"):
        spec = json.loads(r["spec"])
        out[r["id"]] = {**spec, "id": r["id"], "name": r["name"], "builtin": False}
    return list(out.values())


def active_preset(project: Project, preset_id: str | None = None) -> dict:
    pid = preset_id or project.settings.get("preset_id") or "none"
    for p in all_presets(project):
        if p["id"] == pid:
            return p
    return builtin_by_id()["none"]


class Job:
    def __init__(self, kind: str, label: str):
        self.id = f"job{int(time.time() * 1000)}"
        self.kind = kind
        self.label = label
        self.state = "running"  # running | paused | cancelling | cancelled | done | error
        self.phase = "discovering"
        self.total = 0
        self.done = 0
        self.failed = 0
        self.skipped = 0
        self.files_seen = 0
        self.current: dict[int, dict] = {}
        self.log: list[str] = []
        self.started = time.time()
        self.finished = None
        self.run_event = threading.Event()
        self.run_event.set()
        self.cancel_flag = False
        self.ctl = ProcControl()
        self.lock = threading.Lock()
        self.summary: dict = {}

    def note(self, msg):
        with self.lock:
            self.log.append(f"{time.strftime('%H:%M:%S')}  {msg}")
            self.log = self.log[-300:]

    def wait_if_paused(self):
        while not self.run_event.wait(0.25):
            if self.cancel_flag:
                break

    def as_dict(self):
        with self.lock:
            partial = sum(c.get("progress", 0) for c in self.current.values())
            frac = (self.done + partial) / self.total if self.total else 0.0
            elapsed = (self.finished or time.time()) - self.started
            eta = (elapsed / frac - elapsed) if frac > 0.02 and self.state == "running" else None
            return {
                "id": self.id, "kind": self.kind, "label": self.label, "state": self.state,
                "phase": self.phase, "total": self.total, "done": self.done, "failed": self.failed,
                "skipped": self.skipped, "files_seen": self.files_seen, "progress": frac,
                "current": list(self.current.values()), "log": self.log[-40:],
                "elapsed": elapsed, "eta": eta, "summary": self.summary, "finished": self.finished,
            }


class JobManager:
    def __init__(self, project: Project):
        self.project = project
        self.jobs: list[Job] = []
        self.lock = threading.Lock()

    @property
    def active(self) -> Job | None:
        for j in reversed(self.jobs):
            if j.state in ("running", "paused", "cancelling"):
                return j
        return None

    def latest(self) -> Job | None:
        return self.jobs[-1] if self.jobs else None

    # ---- control ----------------------------------------------------------
    def pause(self):
        j = self.active
        if j and j.state == "running":
            j.state = "paused"
            j.run_event.clear()
            j.ctl.pause()
            j.note("Paused")

    def resume(self):
        j = self.active
        if j and j.state == "paused":
            j.state = "running"
            j.ctl.resume()
            j.run_event.set()
            j.note("Resumed")

    def cancel(self):
        j = self.active
        if j:
            j.state = "cancelling"
            j.cancel_flag = True
            j.ctl.cancel()
            j.run_event.set()
            j.note("Cancelling…")

    # ---- entry points -------------------------------------------------------
    def scan_roots(self, roots: list[str], force=False) -> Job:
        if self.active:
            raise RuntimeError("A batch is already running. Pause or cancel it first, or wait for it to finish.")
        roots = [os.path.abspath(os.path.expanduser(r)) for r in roots if r]
        missing = [r for r in roots if not os.path.exists(r)]
        if missing:
            raise FileNotFoundError("Not found: " + ", ".join(missing))
        label = roots[0] if len(roots) == 1 else f"{len(roots)} locations"
        job = Job("scan", label)
        self.jobs.append(job)
        for r in roots:
            self.project.x("INSERT OR IGNORE INTO roots(path, added) VALUES(?,?)", (r, time.time()))
        threading.Thread(target=self._run_scan, args=(job, roots, force), daemon=True).start()
        return job

    def recheck(self, asset_ids: list[int], reanalyse: bool = True) -> Job:
        """reanalyse=False re-evaluates stored measurements against the current preset
        (fast, no decode); True re-reads and re-decodes every file."""
        if self.active:
            raise RuntimeError("A batch is already running.")
        label = f"{'Re-scan' if reanalyse else 'Re-evaluate'} {len(asset_ids)} asset(s)"
        job = Job("recheck" if reanalyse else "reevaluate", label)
        job.reanalyse = reanalyse
        self.jobs.append(job)
        threading.Thread(target=self._run_assets, args=(job, asset_ids), daemon=True).start()
        return job

    # ---- workers ------------------------------------------------------------
    def _run_scan(self, job: Job, roots, force):
        try:
            job.note(f"Scanning {', '.join(roots)}")

            def prog(n, d):
                job.files_seen = n

            found = discovery.walk(roots, cancel=lambda: job.cancel_flag, progress=prog)
            if job.cancel_flag:
                return self._finish(job, "cancelled")
            for e in found.errors[:20]:
                job.note(f"Folder error: {e}")
            job.phase = "registering"
            ids = []
            seen_paths = set()
            for kind, path, size, mtime in found.files:
                seen_paths.add(path)
                row = self.project.q1("SELECT id,size,mtime,scan_state FROM assets WHERE path=?", (path,))
                if row and not force and row["size"] == size and abs((row["mtime"] or 0) - mtime) < 1 and row["scan_state"] == "done":
                    job.skipped += 1
                    continue
                aid = self.project.upsert_asset(
                    path, kind=kind, name=os.path.basename(path), dir=os.path.dirname(path),
                    ext=os.path.splitext(path)[1].lstrip(".").lower(), size=size, mtime=mtime,
                    scan_state="pending", scan_error=None)
                ids.append(aid)
            for seq in found.sequences:
                path = os.path.join(seq["dir"], seq["pattern"])
                seen_paths.add(path)
                row = self.project.q1("SELECT id,size,mtime,scan_state,frames FROM assets WHERE path=?", (path,))
                if row and not force and row["size"] == seq["total_size"] and row["frames"] == seq["count"] \
                        and abs((row["mtime"] or 0) - seq["mtime"]) < 1 and row["scan_state"] == "done":
                    job.skipped += 1
                    continue
                aid = self.project.upsert_asset(
                    path, kind="sequence", name=seq["pattern"], dir=seq["dir"], ext=seq["ext"],
                    size=seq["total_size"], mtime=seq["mtime"], frames=seq["count"],
                    seq=json.dumps(seq), scan_state="pending", scan_error=None)
                ids.append(aid)
            # Zero-byte non-media files are reported as their own assets so they are not missed.
            for p in found.zero_other:
                seen_paths.add(p)
                aid = self.project.upsert_asset(p, kind="other", name=os.path.basename(p), dir=os.path.dirname(p),
                                                ext=os.path.splitext(p)[1].lstrip(".").lower(), size=0,
                                                mtime=os.path.getmtime(p), scan_state="pending")
                ids.append(aid)
            # Assets previously scanned under these roots that are gone now.
            for r in roots:
                like = r.rstrip("/") + "/%"
                for a in self.project.q("SELECT id,path FROM assets WHERE path LIKE ? OR path=?", (like, r)):
                    if a["path"] not in seen_paths and not os.path.exists(a["path"]):
                        self.project.update_asset(a["id"], scan_state="error", scan_error="File no longer found on disk")
                        self.project.recompute_result(a["id"])
            job.summary = {"media_files": len(found.files), "sequences": len(found.sequences),
                           "ignored_files": len(found.ignored), "zero_byte_other": len(found.zero_other),
                           "unchanged_skipped": job.skipped}
            job.note(f"Found {len(found.files)} files, {len(found.sequences)} sequences, "
                     f"{len(found.ignored)} non-media files ignored, {job.skipped} unchanged")
            self._run_assets(job, ids)
        except Exception as e:
            job.note(f"Scan failed: {e}")
            traceback.print_exc()
            self._finish(job, "error")

    def _run_assets(self, job: Job, ids: list[int]):
        job.phase = "checking"
        job.total = len(ids)
        workers = max(1, int(self.project.settings["analysis"].get("workers", 2)))
        # Sequences and small files first so the list fills quickly.
        order = sorted(ids, key=lambda i: (self.project.q1("SELECT size FROM assets WHERE id=?", (i,)) or {"size": 0})["size"] or 0)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for aid in order:
                ex.submit(self._one, job, aid)
        if job.cancel_flag:
            for aid in ids:
                self.project.x("UPDATE assets SET scan_state='pending' WHERE id=? AND scan_state IN ('probing','analysing')", (aid,))
            return self._finish(job, "cancelled")
        self._finish(job, "done")

    def _finish(self, job, state):
        job.state = state
        job.phase = state
        job.finished = time.time()
        job.current.clear()
        job.note({"done": "Batch complete", "cancelled": "Batch cancelled", "error": "Batch stopped with an error"}[state])

    def _one(self, job: Job, aid: int):
        job.wait_if_paused()
        if job.cancel_flag:
            return
        a = self.project.asset(aid)
        if not a:
            return
        cur = {"id": aid, "name": a["name"], "phase": "probing", "progress": 0.0}
        with job.lock:
            job.current[aid] = cur
        try:
            self.process(a, job, cur)
            try:
                added = self.project.import_iinfo(aid)
                if added:
                    job.note(f"{a['name']}: {added} IINfo marker{'s' if added != 1 else ''} added as notes")
            except Exception as e:  # marker files are optional; never fail the asset over one
                job.note(f"{a['name']}: IINfo markers not read ({e})")
        except (Cancelled, InterruptedError):
            self.project.update_asset(aid, scan_state="pending")
        except Exception as e:
            traceback.print_exc()
            self.project.update_asset(aid, scan_state="error", scan_error=f"Internal error: {e}")
            self.project.replace_findings(aid, checks.failure_findings(a["kind"], f"Internal error: {e}", False))
            job.failed += 1
            job.note(f"Error on {a['name']}: {e}")
        finally:
            with job.lock:
                job.current.pop(aid, None)
                if not job.cancel_flag:
                    job.done += 1

    def process(self, a: dict, job: Job, cur: dict):
        p = self.project
        settings = p.settings
        preset = active_preset(p)
        aid = a["id"]
        if not getattr(job, "reanalyse", True) and self._reevaluate(a, preset, settings):
            return
        p.update_asset(aid, scan_state="probing")
        if a["kind"] == "sequence":
            seq = a["seq"]
            cur["phase"] = "reading frames"
            seq = scan_frames(seq, settings, should_stop=lambda: job.cancel_flag, wait_if_paused=job.wait_if_paused,
                              on_progress=lambda f: cur.__setitem__("progress", f * 0.95))
            first_meta = None
            good = next((r for r in seq["frames"] if r[3] in ("ok", "rep", "odd")), None)
            if good:
                try:
                    first_meta = normalise(ffprobe(os.path.join(seq["dir"], good[1])), good[1])
                except ProbeError:
                    first_meta = None
            fr = seq["frame_results"]
            dims = fr.get("dims_majority") or [None, None]
            hdr = fr.get("header") or {}
            # 23.976 must become 24000/1001, not "23.976/1" (which parses as nothing).
            rate = parse_rate(settings["iina"].get("sequence_fps")) or parse_rate(25)
            fields = {
                "width": dims[0], "height": dims[1], "fps": f"{rate.numerator}/{rate.denominator}",
                "fps_float": round(float(rate), 3),
                "frames": seq["count"], "duration": (seq["last"] - seq["first"] + 1) / float(rate),
                "bit_depth": hdr.get("bit_depth"), "alpha": 1 if hdr.get("alpha") else 0,
                "container": seq["ext"], "vcodec": seq["ext"],
            }
            if first_meta and first_meta.get("video_streams"):
                v = first_meta["video_streams"][0]
                fields.update(pix_fmt=v.get("pix_fmt"), chroma=v.get("chroma"),
                              color_primaries=v.get("color_primaries"), color_trc=v.get("color_trc"),
                              color_space=v.get("color_space"))
                fields["bit_depth"] = fields["bit_depth"] or v.get("bit_depth")
            findings = checks.sequence_findings(seq, first_meta, preset, settings)
            p.update_asset(aid, seq=seq, metadata={"first_frame": first_meta, "header": hdr}, scan_state="done",
                           scan_error=None, **fields)
            p.replace_findings(aid, findings)
            return
        path = a["path"]
        try:
            size = os.path.getsize(path)
        except OSError as e:
            p.update_asset(aid, scan_state="error", scan_error=f"Cannot access file: {e.strerror}")
            p.replace_findings(aid, checks.failure_findings(a["kind"], f"Cannot access file: {e.strerror}", False))
            job.failed += 1
            return
        if size == 0:
            p.update_asset(aid, scan_state="error", scan_error="Zero-byte file", size=0)
            p.replace_findings(aid, checks.failure_findings(a["kind"], "File is empty (0 bytes).", True))
            job.failed += 1
            return
        if a["kind"] == "image":
            hdr = read_header(path)
            meta = None
            try:
                meta = normalise(ffprobe(path), path)
            except ProbeError as e:
                if hdr.get("ok") is not True:
                    p.update_asset(aid, scan_state="error", scan_error=str(e))
                    p.replace_findings(aid, checks.failure_findings("image", str(e), False))
                    job.failed += 1
                    return
            fields = summary_fields(meta) if meta else {}
            fields.update({k: v for k, v in (("width", hdr.get("width")), ("height", hdr.get("height"))) if v})
            fields.pop("fps", None), fields.pop("fps_float", None), fields.pop("duration", None)
            p.update_asset(aid, metadata={**(meta or {}), "header": hdr}, scan_state="done", scan_error=None, **fields)
            p.replace_findings(aid, checks.still_findings(hdr, meta, preset, settings))
            return
        try:
            meta = normalise(ffprobe(path), path)
        except ProbeError as e:
            p.update_asset(aid, scan_state="error", scan_error=str(e))
            p.replace_findings(aid, checks.failure_findings(a["kind"], str(e), False))
            job.failed += 1
            return
        meta["size"] = meta.get("size") or size
        p.update_asset(aid, metadata=meta, scan_state="analysing", **summary_fields(meta))
        cur["phase"] = "analysing"
        ana = analyse(path, meta, settings, ctl=job.ctl, on_progress=lambda f: cur.__setitem__("progress", f))
        p.update_asset(aid, analysis=_compact(ana))
        meta["analysis"] = {k: ana.get(k) for k in ("loudness", "decode_error_count", "analysed_seconds", "command")}
        meta["analysis"]["yavg"] = _downsample([v for _, v in ana.get("yavg", [])], 1200)
        meta["analysis"]["audio_rms"] = _audio_curve(ana.get("audio_windows", []))
        findings = checks.media_findings(a["kind"], meta, ana, preset, settings)
        p.update_asset(aid, metadata=meta, scan_state="done", scan_error=None)
        p.replace_findings(aid, findings)


    def _reevaluate(self, a, preset, settings) -> bool:
        """Rebuild findings from stored measurements. Returns False if a full scan is needed."""
        p = self.project
        if a["scan_state"] != "done":
            return False
        if a["kind"] == "sequence" and a.get("seq", {}).get("frame_results"):
            first_meta = (a.get("metadata") or {}).get("first_frame")
            p.replace_findings(a["id"], checks.sequence_findings(a["seq"], first_meta, preset, settings))
            return True
        if a["kind"] in ("video", "audio"):
            raw = p.analysis(a["id"])
            meta = a.get("metadata") or {}
            if raw is None or meta.get("stream_count") is None or raw.get("v", 1) < ANALYSIS_VERSION:
                return False
            p.replace_findings(a["id"], checks.media_findings(a["kind"], meta, _expand(raw), preset, settings))
            return True
        if a["kind"] == "image":
            meta = a.get("metadata") or {}
            p.replace_findings(a["id"], checks.still_findings(meta.get("header") or {}, meta if meta.get("video_streams") else None, preset, settings))
            return True
        return a["scan_state"] == "done" and a["kind"] == "other"


ANALYSIS_VERSION = 2  # 2: per-window Flat_factor (clipping) and every audio stream


def _compact_windows(wins):
    return [[w["t"], {str(c): [st.get("Peak_level"), st.get("RMS_level"), st.get("Peak_count"), st.get("Flat_factor")]
                      for c, st in w["ch"].items()}] for w in wins]


def _expand_windows(rows):
    keys = ("Peak_level", "RMS_level", "Peak_count", "Flat_factor")
    return [{"t": t, "ch": {int(c): dict(zip(keys, v)) for c, v in ch.items()}} for t, ch in rows]


def _compact(ana: dict) -> dict:
    """Raw detections in a compact JSON-friendly shape."""
    out = {k: ana.get(k) for k in ("black", "freeze", "silence", "loudness", "decode_errors",
                                  "decode_error_count", "analysed_seconds", "returncode", "error", "alpha")}
    out["v"] = ANALYSIS_VERSION
    out["yavg"] = [v for _, v in ana.get("yavg", [])]
    out["aw"] = _compact_windows(ana.get("audio_windows", []))
    out["tracks"] = [{"index": t["index"], "silence": t["silence"], "loudness": t["loudness"],
                      "aw": _compact_windows(t["audio_windows"])} for t in ana.get("audio_tracks") or []]
    return out


def _expand(raw: dict) -> dict:
    ana = dict(raw)
    ana["yavg"] = [[None, v] for v in raw.get("yavg", [])]
    ana["audio_windows"] = _expand_windows(raw.get("aw", []))
    ana["audio_tracks"] = [{"index": t["index"], "silence": t["silence"], "loudness": t["loudness"],
                            "audio_windows": _expand_windows(t["aw"])} for t in raw.get("tracks") or []]
    return ana


def _downsample(vals, n):
    if len(vals) <= n:
        return vals
    step = len(vals) / n
    return [vals[int(i * step)] for i in range(n)]


def _audio_curve(wins):
    """Per-channel RMS (dB) per window for the asset view's audio lane."""
    out = {}
    for w in wins:
        for c, st in w["ch"].items():
            r = st.get("RMS_level")
            out.setdefault(str(c), []).append(None if r is None or r == float("-inf") else round(r, 1))
    return {"window": (wins[1]["t"] - wins[0]["t"]) if len(wins) > 1 and wins[0]["t"] is not None and wins[1]["t"] is not None else 0.5,
            "channels": {k: _downsample(v, 1200) for k, v in out.items()}}
