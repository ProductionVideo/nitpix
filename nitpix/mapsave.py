"""The saved Mapping setup: one small, versioned JSON document in the project's database (meta key
KEY), plain values only. No Qt, no GL, no importer objects: everything else (geometry, GPU
buffers, the visibility texture) is rebuilt from the managed model files when the project reopens.

Version 3 (versions 1 and 2, from candidate builds before it, are read and migrated; see below):
  resources    {id: {path, name, sha256, size, origin, sidecars, missing}}  model files kept in the
               project (nitpix/models.py); `path` is relative to the project, `origin` where it came from
  sources      [{id, resource, origin}]  the stage's files in order; id is the stage's own ("m3"), so the
               saved assignments ("m3:Stage/Screen") point at the same objects after restore; origin
               (optional) is where this placement's file came from, as several can share one kept copy
  next_id      the next source number, so a removed file's id is never reused
  assignments  {stage key: UV set}
  clip         the mapped clip's asset id in this project, or None
  tab          "surfaces" or "projectors"
  camera       {yaw, pitch, dist, target: [x, y, z], view}  view: "perspective", "front", "side" or "top"
  projectors   [{id, position, target, roll, throw_ratio, raster: [w, h], raster_note, fit, enabled,
               show_frustum}]  at most one for now; a list (with ids) so several can follow

Version 1 stored a projector's horizontal field of view and an aspect (None: follow the clip). It
is migrated so the saved picture looks the same: throw ratio from the angle; the raster 1920 wide
of that aspect (the saved clip's own, for None), marked "inferred" (it was never chosen as real
hardware); an explicit aspect keeps the clip stretched across the image, as version 1 drew it. The
next save writes version 3; an older nitpix then sets the document aside rather than misread it.

Version 3 changed only what roll means for an aim within 0.5° of straight up or down: before, the
image's top was fixed there (-Z down, +Z up); now roll counts from the level reference, which is
continuous up to the exact vertical (nitpix/projector.py). Versions 1 and 2 get the roll that
draws the picture they had (legacy_roll); every other projector is unchanged.

`validate()` refuses anything else with a reason; the caller then keeps the stored text untouched.
"""
from __future__ import annotations

import math

from .projector import FITS, MAX_RASTER, MAX_THROW, MIN_RASTER, MIN_THROW, Projector, legacy_roll, raster_for, throw_from_fov

VERSION = 3
READS = (1, 2, 3)
KEY = "mapping.setup"
TABS = ("surfaces", "projectors")
VIEWS = ("perspective", "front", "side", "top")


class SetupError(Exception):
    pass


def projector_to(p: Projector, pid: str = "p1") -> dict:
    return {"id": pid, "position": list(p.position), "target": list(p.target), "roll": p.roll, "throw_ratio": p.throw,
            "raster": list(p.raster), "raster_note": p.raster_note, "fit": p.fit, "enabled": p.enabled,
            "show_frustum": p.show_frustum}


def projector_from(d: dict) -> Projector:
    return Projector(position=tuple(d["position"]), target=tuple(d["target"]), roll=d["roll"], throw=d["throw_ratio"],
                     raster=tuple(d["raster"]), raster_note=d["raster_note"], fit=d["fit"], enabled=d["enabled"],
                     show_frustum=d["show_frustum"])


def validate(raw, clip_aspect: float | None = None) -> dict:
    """A checked copy of a stored document (as version 2), or SetupError saying what is wrong with
    it. `clip_aspect`: the saved clip's display width / height, for migrating a version 1 projector
    that followed the clip."""
    if not isinstance(raw, dict):
        raise SetupError("not a setup document")
    v = raw.get("version")
    if v not in READS:
        raise SetupError(f"saved by a different version of nitpix (format {v!r}; this one reads {', '.join(map(str, READS))})")
    out = {"version": VERSION, "migrated_from": v if v != VERSION else None}
    res = _type(raw.get("resources", {}), dict, "resources")
    out["resources"] = {}
    for rid, r in res.items():
        _type(r, dict, f"resource {rid}")
        rec = {"path": _type(r.get("path"), str, "resource path"), "name": _type(r.get("name"), str, "resource name"),
               "sha256": _type(r.get("sha256"), str, "resource checksum"), "size": _int(r.get("size"), "resource size"),
               "origin": _type(r.get("origin", ""), str, "resource origin"),
               "sidecars": [_type(x, str, "sidecar") for x in _type(r.get("sidecars", []), list, "sidecars")],
               "missing": [_type(x, str, "missing sidecar") for x in _type(r.get("missing", []), list, "missing")]}
        if len(rec["sha256"]) != 64:
            raise SetupError(f"resource {rid} has no proper checksum")
        out["resources"][_type(rid, str, "resource id")] = rec
    out["sources"], seen = [], set()
    for s in _type(raw.get("sources", []), list, "sources"):
        _type(s, dict, "source")
        sid, rid = _type(s.get("id"), str, "source id"), _type(s.get("resource"), str, "source resource")
        if not (sid.startswith("m") and sid[1:].isdigit()) or sid in seen:
            raise SetupError(f"bad or repeated source id {sid!r}")
        if rid not in out["resources"]:
            raise SetupError(f"source {sid} names a resource that is not listed")
        seen.add(sid)
        out["sources"].append({"id": sid, "resource": rid, "origin": _type(s.get("origin", ""), str, "source origin")})
    out["next_id"] = max([_int(raw.get("next_id", 1), "next_id")] + [int(s["id"][1:]) + 1 for s in out["sources"]])
    out["assignments"] = {}
    for k, uv in _type(raw.get("assignments", {}), dict, "assignments").items():
        if _type(k, str, "assignment key").partition(":")[0] not in seen:
            raise SetupError(f"assignment {k!r} names no saved source")
        out["assignments"][k] = _int(uv, "UV set")
    clip = raw.get("clip")
    out["clip"] = None if clip is None else _int(clip, "clip")
    out["tab"] = raw.get("tab", "surfaces")
    if out["tab"] not in TABS:
        raise SetupError(f"unknown tab {out['tab']!r}")
    cam = raw.get("camera")
    if cam is not None:
        _type(cam, dict, "camera")
        cam = {"yaw": _num(cam.get("yaw"), "camera"), "pitch": _num(cam.get("pitch"), "camera"),
               "dist": _num(cam.get("dist"), "camera"), "target": _vec(cam.get("target"), "camera target"),
               "view": cam.get("view", "perspective")}
        if cam["dist"] <= 0:
            raise SetupError("camera distance must be positive")
        if cam["view"] not in VIEWS:
            raise SetupError(f"unknown camera view {cam['view']!r}")
    out["camera"] = cam
    out["projectors"] = []
    for p in _type(raw.get("projectors", []), list, "projectors"):
        _type(p, dict, "projector")
        rec = {"id": _type(p.get("id", "p1"), str, "projector id"), "position": _vec(p.get("position"), "projector position"),
               "target": _vec(p.get("target"), "projector target"),
               "enabled": _type(p.get("enabled", True), bool, "enabled"), "show_frustum": _type(p.get("show_frustum", True), bool, "frustum")}
        if v == 1:
            rec.update(_from_v1(p, clip_aspect))
        else:
            raster = p.get("raster")
            if not isinstance(raster, list) or len(raster) != 2:
                raise SetupError("projector raster is not two whole numbers")
            rec.update({"roll": _num(p.get("roll", 0.0), "roll"), "throw_ratio": _num(p.get("throw_ratio"), "throw ratio"),
                        "raster": [_int(n, "raster") for n in raster], "raster_note": _type(p.get("raster_note", ""), str, "raster note"),
                        "fit": p.get("fit", "fit")})
        if not MIN_THROW <= rec["throw_ratio"] <= MAX_THROW or not all(MIN_RASTER <= n <= MAX_RASTER for n in rec["raster"]):
            raise SetupError("projector throw ratio or raster out of range")
        if rec["fit"] not in FITS or not -360 <= rec["roll"] <= 360:
            raise SetupError("projector content fit or roll not understood")
        if v < 3:
            rec["roll"] = legacy_roll(projector_from(rec))
        out["projectors"].append(rec)
    if len(out["projectors"]) > 1:
        raise SetupError("more than one projector (this version shows one)")
    return out


def _from_v1(p, clip_aspect):
    """A version 1 projector's lens and image, as version 2 fields drawing the same picture."""
    fov = _num(p.get("h_fov"), "field of view")
    if not 1.0 <= fov <= 150.0:
        raise SetupError("projector field of view out of range")
    aspect = p.get("aspect")
    if aspect is not None:
        aspect = _num(aspect, "aspect")
        if not 0.1 <= aspect <= 10:
            raise SetupError("projector aspect out of range")
    shape = aspect if aspect is not None else (clip_aspect if clip_aspect and clip_aspect > 0 else 16 / 9)
    return {"roll": 0.0, "throw_ratio": throw_from_fov(fov), "raster": list(raster_for(shape)), "raster_note": "inferred",
            "fit": "stretch" if aspect is not None else "fit"}


def _type(v, t, what):
    if not isinstance(v, t) or (t is not bool and isinstance(v, bool) and t in (int, float)):
        raise SetupError(f"{what} is not a {t.__name__}")
    return v


def _int(v, what):
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        raise SetupError(f"{what} is not a whole number")
    return v


def _num(v, what):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise SetupError(f"{what} is not a number")
    return float(v)


def _vec(v, what):
    if not isinstance(v, list) or len(v) != 3:
        raise SetupError(f"{what} is not three numbers")
    return [_num(c, what) for c in v]
