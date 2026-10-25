"""IINfo interop: QC markers made inside IINA with the IINfo plugin <-> nitpix notes.

IINfo (github.com/ProductionVideo/IINfo) keeps one marker list per media file, in one of
two places:

  * `<media>.iinfo.json` beside the file, if the reviewer turned that on or exported one;
  * otherwise `qc-<fingerprint>.json` in the plugin data folder, where the fingerprint is
    djb2 over "<filename>|<size>" (JavaScript string, so UTF-16 code units).

IINfo reads the sidecar whenever one exists, so a sidecar hides the data-folder list. That
is why export merges both lists into the sidecar instead of writing only nitpix's notes.

Times come from mpv's float container-fps, so frames are recomputed here from tMs on the
asset's exact rational rate. IINfo places a frame at its centre, (n + 0.5) / fps, and
reads it back with floor(t * fps); export does the same so markers land on the same frame
in both apps.
"""
from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime, timezone
from fractions import Fraction

from .timecode import frames_to_tc, is_drop_frame_rate

DATA_DIR = os.path.expanduser(
    "~/Library/Application Support/com.colliderli.iina/plugins/.data/com.jakestebbings.iinfo")
SOURCE = "nitpix"  # events this app writes; never imported back as notes
OUR_SOURCES = {SOURCE, "lhs-qc"}  # the old name, from sidecars written before the rename
SEVERITY_IN = {"info": "info", "warning": "warning", "error": "issue"}
SEVERITY_OUT = {"info": "info", "warning": "warning", "issue": "error"}
CATEGORIES = ("Video", "Audio", "Sync", "Colour", "Performance", "Content", "Other")


def fingerprint(filename: str, size: int | None) -> str:
    h = 5381
    data = f"{filename or '?'}|{size if size is not None else '?'}".encode("utf-16-le")
    for i in range(0, len(data), 2):
        h = ((h << 5) + h + int.from_bytes(data[i:i + 2], "little")) & 0xFFFFFFFF
    return f"{h:08x}"


def sidecar_path(media_path: str) -> str:
    return media_path + ".iinfo.json"


def data_path(media_path: str, size: int | None, data_dir: str = DATA_DIR) -> str:
    return os.path.join(data_dir, f"qc-{fingerprint(os.path.basename(media_path), size)}.json")


def _read(path: str, size: int | None) -> list[dict] | None:
    """Events in one IINfo file, or None if absent, unreadable or for a different render."""
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return None
    if isinstance(doc, list):
        events, media = doc, {}
    elif isinstance(doc, dict) and isinstance(doc.get("events"), list):
        events, media = doc["events"], doc.get("media") or {}
    else:
        return None
    if size is not None and isinstance(media.get("size"), (int, float)) and int(media["size"]) != size:
        return None
    return [e for e in events if isinstance(e, dict) and isinstance(e.get("tMs"), (int, float))]


def load(asset: dict, data_dir: str = DATA_DIR) -> list[dict]:
    """Every IINfo event for this asset, sidecar first, de-duplicated by id."""
    if asset.get("kind") not in ("video", "audio") or not asset.get("path"):
        return []
    out, seen = [], set()
    for path in (sidecar_path(asset["path"]), data_path(asset["path"], asset.get("size"), data_dir)):
        for e in _read(path, asset.get("size")) or []:
            key = e.get("id") or (e.get("tMs"), e.get("note"))
            if key not in seen:
                seen.add(key)
                out.append(e)
    return out


def frame_of(t_ms: float, rate: Fraction) -> int:
    # tMs is time-pos rounded to the millisecond, so a frame's start can read up to 0.5 ms
    # early; add that back before flooring.
    return max(0, math.floor((Fraction(round(t_ms)) + Fraction(1, 2)) / 1000 * rate))


def to_note(e: dict, rate: Fraction) -> dict:
    frame = frame_of(e["tMs"], rate)
    dur = e.get("durMs")
    frame_out = frame_of(e["tMs"] + dur, rate) if isinstance(dur, (int, float)) and dur > 0 else None
    cat = e.get("category") if e.get("category") in CATEGORIES else "Other"
    text = (e.get("note") or "").strip() or "IINfo marker"
    if cat != "Other":
        text = f"[{cat}] {text}"
    if e.get("resolved"):
        text += " (resolved in IINfo)"
    return {"ext_id": str(e.get("id")) if e.get("id") else None, "kind": "note", "frame": frame,
            "frame_out": frame_out if frame_out and frame_out > frame else None,
            "severity": SEVERITY_IN.get(e.get("severity"), "warning"), "text": text, "author": "IINfo"}


def import_events(asset: dict, events: list[dict], rate: Fraction, known: set, dismissed: set) -> list[dict]:
    """Notes for events not yet in the project. `known`/`dismissed` are ext_ids."""
    notes = []
    for e in events:
        if e.get("source") in OUR_SOURCES:
            continue
        n = to_note(e, rate)
        if n["ext_id"] and (n["ext_id"] in known or n["ext_id"] in dismissed):
            continue
        notes.append(n)
    return notes


def _strip_category(text: str) -> tuple[str, str | None]:
    if text.startswith("[") and "] " in text:
        cat, rest = text[1:].split("] ", 1)
        if cat in CATEGORIES:
            return rest, cat
    return text, None


def to_event(note: dict, rate: Fraction, base: dict | None = None) -> dict:
    """An IINfo event for a note. `base` is the original IINfo event of an imported note."""
    frame = int(note["frame"] or 0)
    t_ms = round(float((frame + Fraction(1, 2)) / rate) * 1000)
    text, cat = _strip_category(note.get("text") or "")
    resolved = text.endswith(" (resolved in IINfo)")
    if resolved:
        text = text[: -len(" (resolved in IINfo)")]
    ev = dict(base or {})
    ev.update({
        "tMs": t_ms, "frame": frame, "fps": float(rate),
        "tc": frames_to_tc(frame, rate, is_drop_frame_rate(rate)),
        "durMs": (round(float((int(note["frame_out"]) - frame + 1) / rate) * 1000)
                  if note.get("frame_out") is not None and note["frame_out"] > frame else None),
        "severity": SEVERITY_OUT.get(note.get("severity"), "warning"),
        "note": "" if text == "IINfo marker" else text,
    })
    if base is None:
        ev.update({"id": f"nitpix_{note['id']}", "source": SOURCE, "type": "marker", "category": cat or "Other",
                   "resolved": False, "ts": int((note.get("created") or time.time()) * 1000), "ref": None,
                   "meta": {"author": note.get("author")}})
    else:
        ev["category"] = cat or base.get("category") or "Other"
        ev["resolved"] = resolved or bool(base.get("resolved"))
    return ev


def export(asset: dict, notes: list[dict], rate: Fraction, dismissed: set, data_dir: str = DATA_DIR) -> dict:
    """Write the merged marker list to the asset's sidecar. Returns {path, events}."""
    if asset.get("kind") not in ("video", "audio") or not asset.get("path"):
        raise ValueError("IINfo markers are only kept for video and audio files")
    theirs = {str(e.get("id")): e for e in load(asset, data_dir) if e.get("source") not in OUR_SOURCES}
    by_ext = {n["ext_id"]: n for n in notes if n.get("ext_id")}
    events = []
    for eid, e in theirs.items():
        if eid in by_ext:
            events.append(to_event(by_ext[eid], rate, base=e))
        elif eid not in dismissed:
            events.append(e)
    events += [to_event(n, rate) for n in notes if not n.get("ext_id")]
    events.sort(key=lambda e: (e["tMs"], str(e.get("id"))))
    doc = {"iinfo": "qc-markers", "version": 1,
           "media": {"path": asset["path"], "filename": os.path.basename(asset["path"]), "size": asset.get("size"),
                     "fps": float(rate), "durationMs": round(asset["duration"] * 1000) if asset.get("duration") else None},
           "saved": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
           "events": events}
    path = sidecar_path(asset["path"])
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
    os.replace(tmp, path)
    return {"path": path, "events": len(events)}
