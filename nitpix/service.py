"""Operations on a project that both UIs share: asset lists and detail, review decisions,
notes, dashboard figures, presets and sign-off. The HTTP server and the desktop app are
thin layers over these.

`Session` holds the open project with its job manager and IINA bridge."""
from __future__ import annotations

import json
import os
import re
import threading
import time

from . import vocab
from .iina import IINABridge
from .presets import builtin_by_id
from .scanner import JobManager, active_preset, all_presets
from .store import DEFAULT_SETTINGS, Project, remember_project

LIST_COLS = """a.id, a.kind, a.name, a.dir, a.path, a.ext, a.size, a.container, a.vcodec, a.acodec, a.width, a.height,
 a.fps, a.fps_float, a.duration, a.frames, a.start_tc, a.audio_channels, a.sample_rate, a.bit_depth, a.chroma,
 a.alpha, a.color_primaries, a.color_trc, a.pix_fmt, a.scan_state, a.scan_error, a.qc_result, a.review_status,
 a.signed_by, a.signed_at, a.updated,
 (SELECT COUNT(*) FROM findings f WHERE f.asset_id=a.id AND COALESCE(f.reviewer_status, f.auto_result) IN ('fail','issue')) AS fails,
 (SELECT COUNT(*) FROM findings f WHERE f.asset_id=a.id AND COALESCE(f.reviewer_status, f.auto_result)='warning') AS warnings,
 (SELECT COUNT(*) FROM findings f WHERE f.asset_id=a.id AND f.kind='detected' AND f.auto_result IN ('warning','fail') AND f.reviewer_status IS NULL) AS open_detections,
 (SELECT COUNT(*) FROM notes n WHERE n.asset_id=a.id) AS notes"""

REVIEW_STATES = ("unreviewed", "in_review", "approved", "approved_notes", "rejected")
SIGNED_STATES = ("approved", "approved_notes", "rejected")
DECISIONS = (None, "pass", "issue", "warning", "na")
ASSET_EDITABLE = {"review_status", "in_frame", "out_frame", "last_frame"}
_UNSET = object()


def reviewer(p: Project) -> str:
    return p.settings.get("reviewer") or "Reviewer"


# ---- assets ---------------------------------------------------------------------------
def list_assets(p: Project, check: str | None = None):
    if check:
        rows = p.q("SELECT DISTINCT asset_id FROM findings WHERE check_key=? AND COALESCE(reviewer_status, auto_result) "
                   "IN ('fail','issue','warning')", (check,))
        return [r["asset_id"] for r in rows]
    return p.q(f"SELECT {LIST_COLS} FROM assets a ORDER BY a.dir, a.name")


def asset_detail(p: Project, aid: int) -> dict | None:
    a = p.asset(int(aid))
    if not a:
        return None
    a["findings"] = p.q("SELECT * FROM findings WHERE asset_id=? ORDER BY CASE kind WHEN 'measured' THEN 0 ELSE 1 END, "
                        "CASE category WHEN 'integrity' THEN 0 WHEN 'format' THEN 1 WHEN 'colour' THEN 2 WHEN 'audio' THEN 3 "
                        "WHEN 'content' THEN 4 WHEN 'sequence' THEN 5 ELSE 6 END, id", (a["id"],))
    a["notes"] = p.q("SELECT * FROM notes WHERE asset_id=? ORDER BY frame, id", (a["id"],))
    a["exists"] = os.path.exists(a["path"]) if a["kind"] != "sequence" else os.path.isdir(a["dir"])
    return a


def update_asset(p: Project, aid: int, fields: dict, by: str | None = None):
    """Review status and positions. Signing fields follow the review status."""
    fields = {k: v for k, v in fields.items() if k in ASSET_EDITABLE}
    if "review_status" in fields:
        if fields["review_status"] not in REVIEW_STATES:
            raise ValueError("Unknown review status")
        if fields["review_status"] in SIGNED_STATES:
            fields["signed_by"] = by or reviewer(p)
            fields["signed_at"] = time.time()
        else:
            fields["signed_by"] = None
            fields["signed_at"] = None
    if fields:
        p.update_asset(int(aid), **fields)


def bulk_review(p: Project, ids, status: str):
    if status not in REVIEW_STATES:
        raise ValueError("Unknown review status")
    for aid in ids:
        update_asset(p, int(aid), {"review_status": status})


def remove_assets(p: Project, ids):
    for aid in ids:
        p.x("DELETE FROM assets WHERE id=?", (int(aid),))


# ---- findings and notes ---------------------------------------------------------------
def decide_finding(p: Project, fid: int, status=_UNSET, note=_UNSET) -> str:
    """Set the reviewer's decision and/or note on a finding. Returns the asset's QC result."""
    f = p.q1("SELECT * FROM findings WHERE id=?", (int(fid),))
    if not f:
        raise LookupError("Finding not found")
    st = f["reviewer_status"] if status is _UNSET else status
    if st not in DECISIONS:
        raise ValueError("Unknown decision")
    nt = f["reviewer_note"] if note is _UNSET else note
    p.x("UPDATE findings SET reviewer_status=?, reviewer_note=?, reviewed_by=?, reviewed_at=? WHERE id=?",
        (st, nt, reviewer(p) if st else None, time.time() if st else None, int(fid)))
    return p.recompute_result(f["asset_id"])


def add_note(p: Project, asset_id: int, kind="note", frame=None, frame_out=None, severity="info", text="") -> tuple[int, str]:
    now = time.time()
    nid = p.x("INSERT INTO notes(asset_id, kind, frame, frame_out, severity, text, author, created, updated) VALUES(?,?,?,?,?,?,?,?,?)",
              (int(asset_id), kind, frame, frame_out, severity, text, reviewer(p), now, now))
    return nid, p.recompute_result(int(asset_id))


def update_note(p: Project, nid: int, **vals) -> str:
    n = p.q1("SELECT * FROM notes WHERE id=?", (int(nid),))
    if not n:
        raise LookupError("Note not found")
    v = {k: vals.get(k, n[k]) for k in ("text", "severity", "frame", "frame_out", "kind")}
    p.x("UPDATE notes SET text=?, severity=?, frame=?, frame_out=?, kind=?, updated=? WHERE id=?",
        (v["text"], v["severity"], v["frame"], v["frame_out"], v["kind"], time.time(), int(nid)))
    return p.recompute_result(n["asset_id"])


def delete_note(p: Project, nid: int):
    n = p.q1("SELECT * FROM notes WHERE id=?", (int(nid),))
    if n:
        p.x("DELETE FROM notes WHERE id=?", (int(nid),))
        if n.get("ext_id"):
            p.dismiss_iinfo(n["ext_id"])  # so the next scan does not import it again
        p.recompute_result(n["asset_id"])


def iinfo_sync(p: Project, aid: int) -> dict:
    """Import new IINfo markers, then write the merged list to the sidecar beside the media."""
    added = p.import_iinfo(int(aid))
    out = p.export_iinfo(int(aid))
    return {"imported": added, **out, "qc_result": p.q1("SELECT qc_result FROM assets WHERE id=?", (int(aid),))["qc_result"]}


# ---- dashboard --------------------------------------------------------------------------
def dashboard(p: Project) -> dict:
    eff = "COALESCE(reviewer_status, auto_result)"
    out = {}
    out["assets"] = p.q1("SELECT COUNT(*) n, SUM(size) bytes, SUM(CASE WHEN kind='sequence' THEN frames ELSE 0 END) seq_frames FROM assets")
    out["by_result"] = {r["k"] or "pending": r["n"] for r in p.q("SELECT qc_result k, COUNT(*) n FROM assets WHERE scan_state IN ('done','error') GROUP BY qc_result")}
    out["by_review"] = {r["k"]: r["n"] for r in p.q("SELECT review_status k, COUNT(*) n FROM assets GROUP BY review_status")}
    out["by_state"] = {r["k"]: r["n"] for r in p.q("SELECT scan_state k, COUNT(*) n FROM assets GROUP BY scan_state")}
    out["by_kind"] = {r["k"]: r["n"] for r in p.q("SELECT kind k, COUNT(*) n FROM assets GROUP BY kind")}
    out["checks"] = {r["k"]: r["n"] for r in p.q(
        f"SELECT CASE WHEN {eff} IN ('fail','issue') THEN 'fail' WHEN {eff}='warning' THEN 'warning' "
        f"WHEN {eff} IN ('pass') THEN 'pass' ELSE 'info' END k, COUNT(*) n FROM findings GROUP BY k")}
    out["open_detections"] = p.q1("SELECT COUNT(*) n FROM findings WHERE kind='detected' AND auto_result IN ('warning','fail') AND reviewer_status IS NULL")["n"]
    out["top_issues"] = p.q(
        f"SELECT check_key, category, COUNT(*) n, COUNT(DISTINCT asset_id) assets, "
        f"SUM(CASE WHEN {eff} IN ('fail','issue') THEN 1 ELSE 0 END) fails "
        f"FROM findings WHERE {eff} IN ('fail','issue','warning') GROUP BY check_key ORDER BY fails DESC, n DESC LIMIT 12")
    out["by_category"] = p.q(
        f"SELECT category, SUM(CASE WHEN {eff} IN ('fail','issue') THEN 1 ELSE 0 END) fail, "
        f"SUM(CASE WHEN {eff}='warning' THEN 1 ELSE 0 END) warning, "
        f"SUM(CASE WHEN {eff}='pass' THEN 1 ELSE 0 END) pass FROM findings GROUP BY category")
    out["needs_attention"] = p.q(
        f"SELECT {LIST_COLS} FROM assets a WHERE a.qc_result='fail' OR a.scan_state='error' "
        "OR (SELECT COUNT(*) FROM findings f WHERE f.asset_id=a.id AND f.kind='detected' AND f.auto_result IN ('warning','fail') AND f.reviewer_status IS NULL) > 0 "
        "ORDER BY CASE a.qc_result WHEN 'fail' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, a.name LIMIT 12")
    return out


# ---- presets, settings, sign-off ----------------------------------------------------------
def presets(p: Project) -> dict:
    items = [x if x.get("builtin") else vocab.display_preset(x) for x in all_presets(p)]
    return {"presets": items, "active": p.settings.get("preset_id"), "vocab": vocab.options_payload()}


def save_preset(p: Project, body: dict) -> tuple[str, list[str]]:
    pid = body.get("id") or ""
    if not pid or pid in builtin_by_id():
        pid = "custom_" + re.sub(r"[^a-z0-9]+", "_", (body.get("name") or "preset").lower()).strip("_")[:40] + f"_{int(time.time()) % 100000}"
    spec, warnings = vocab.clean_preset({k: v for k, v in body.items() if k in ("description", "video", "audio", "sequence", "still")})
    p.x("INSERT INTO presets(id,name,spec,builtin,updated) VALUES(?,?,?,0,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, spec=excluded.spec, updated=excluded.updated",
        (pid, body.get("name") or pid, json.dumps(spec), time.time()))
    return pid, warnings


def default_spec_ids() -> tuple[str, str]:
    """The factory default spec new projects start with, and the id of a project's own edited
    version of it."""
    src = DEFAULT_SETTINGS["preset_id"]
    return src, f"project_{src}"


def save_default_spec(p: Project, body: dict) -> tuple[str, list[str], bool]:
    """Save edits made to the factory default spec as this project's own version: created on the
    first save, updated in place after that, the factory definition untouched. If the factory
    default is the active spec it hands over to the saved version. Invalid fields raise before
    anything is written. Returns (id, warnings, made_active)."""
    src, own = default_spec_ids()
    pid, warnings = save_preset(p, {**body, "id": own})
    made_active = p.settings.get("preset_id") == src
    if made_active:
        update_settings(p, {"preset_id": pid})
    return pid, warnings, made_active


def delete_preset(p: Project, pid: str):
    if pid in builtin_by_id():
        raise ValueError("Built-in specs cannot be deleted; duplicate one to customise it.")
    p.x("DELETE FROM presets WHERE id=?", (pid,))
    if p.settings.get("preset_id") == pid:
        p.update_settings({"preset_id": "none"})


def update_settings(p: Project, patch: dict) -> dict:
    s = p.update_settings(patch)
    remember_project(p.folder, s["project_name"])
    return s


def signoff(p: Project, status: str | None, by: str | None = None, note: str = ""):
    if status in (None, "", "clear"):
        p.set_meta("signoff", None)
    else:
        p.set_meta("signoff", {"status": status, "by": by or reviewer(p), "note": note, "at": time.time()})
    return p.get_meta("signoff")


# ---- session ------------------------------------------------------------------------------
class Session:
    """The open project plus the long-lived helpers that belong to it."""

    def __init__(self):
        self.project: Project | None = None
        self.jobs: JobManager | None = None
        self.iina: IINABridge | None = None
        self.lock = threading.Lock()

    def open(self, folder: str, name: str | None = None) -> Project:
        folder = os.path.expanduser(folder)
        if not folder.endswith((".nitpix", ".lhsqc")):  # .lhsqc: projects from before the rename
            folder += ".nitpix"
        with self.lock:
            if self.jobs and self.jobs.active:
                raise RuntimeError("Finish or cancel the running batch before switching projects.")
            self._close_locked()
            p = Project(folder)
            if name:
                p.update_settings({"project_name": name})
            self.project = p
            self.jobs = JobManager(p)
            self.iina = IINABridge(p)
            remember_project(p.folder, p.settings["project_name"])
            # Assets left mid-scan by a crash/quit go back to pending.
            p.x("UPDATE assets SET scan_state='pending' WHERE scan_state IN ('probing','analysing')")
        return p

    def _close_locked(self):
        if self.iina:
            self.iina.close()
        if self.project:
            self.project.close()
        self.project = self.jobs = self.iina = None

    def close(self):
        with self.lock:
            if self.jobs and self.jobs.active:
                raise RuntimeError("Finish or cancel the running batch before closing the project.")
            self._close_locked()

    def preset(self) -> dict:
        return active_preset(self.project) if self.project else {}
