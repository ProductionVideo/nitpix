"""The saved Mapping setup: one small, versioned JSON document in the project's database (meta key
KEY), plain values only. No Qt, no GL, no importer objects: everything else (geometry, GPU
buffers, the visibility texture) is rebuilt from the managed model files when the project reopens.

Version 1:
  resources    {id: {path, name, sha256, size, origin, sidecars, missing}}  model files kept in the
               project (nitpix/models.py); `path` is relative to the project, `origin` where it came from
  sources      [{id, resource}]  the stage's files in order; id is the stage's own ("m3"), so the
               saved assignments ("m3:Stage/Screen") point at the same objects after restore
  next_id      the next source number, so a removed file's id is never reused
  assignments  {stage key: UV set}
  clip         the mapped clip's asset id in this project, or None
  tab          "surfaces" or "projectors"
  camera       {yaw, pitch, dist, target: [x, y, z]}
  projectors   [{id, position, target, h_fov, aspect, enabled, show_frustum}]  at most one for now; a
               list (with ids) so several can follow without a new version

`validate()` refuses anything else with a reason; the caller then keeps the stored text untouched.
"""
from __future__ import annotations

import math

from .projector import MAX_FOV, MIN_FOV, Projector

VERSION = 1
KEY = "mapping.setup"
TABS = ("surfaces", "projectors")


class SetupError(Exception):
    pass


def projector_to(p: Projector, pid: str = "p1") -> dict:
    return {"id": pid, "position": list(p.position), "target": list(p.target), "h_fov": p.h_fov, "aspect": p.aspect,
            "enabled": p.enabled, "show_frustum": p.show_frustum}


def projector_from(d: dict) -> Projector:
    return Projector(position=tuple(d["position"]), target=tuple(d["target"]), h_fov=d["h_fov"], aspect=d["aspect"],
                     enabled=d["enabled"], show_frustum=d["show_frustum"])


def validate(raw) -> dict:
    """A checked copy of a stored document, or SetupError saying what is wrong with it."""
    if not isinstance(raw, dict):
        raise SetupError("not a setup document")
    v = raw.get("version")
    if v != VERSION:
        raise SetupError(f"saved by a different version of nitpix (format {v!r}; this one reads {VERSION})")
    out = {"version": VERSION}
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
        out["sources"].append({"id": sid, "resource": rid})
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
               "dist": _num(cam.get("dist"), "camera"), "target": _vec(cam.get("target"), "camera target")}
        if cam["dist"] <= 0:
            raise SetupError("camera distance must be positive")
    out["camera"] = cam
    out["projectors"] = []
    for p in _type(raw.get("projectors", []), list, "projectors"):
        _type(p, dict, "projector")
        aspect = p.get("aspect")
        rec = {"id": _type(p.get("id", "p1"), str, "projector id"), "position": _vec(p.get("position"), "projector position"),
               "target": _vec(p.get("target"), "projector target"), "h_fov": _num(p.get("h_fov"), "field of view"),
               "aspect": None if aspect is None else _num(aspect, "aspect"),
               "enabled": _type(p.get("enabled", True), bool, "enabled"), "show_frustum": _type(p.get("show_frustum", True), bool, "frustum")}
        if not MIN_FOV <= rec["h_fov"] <= MAX_FOV or (rec["aspect"] is not None and not 0.1 <= rec["aspect"] <= 10):
            raise SetupError("projector field of view or aspect out of range")
        out["projectors"].append(rec)
    if len(out["projectors"]) > 1:
        raise SetupError("more than one projector (this version shows one)")
    return out


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
