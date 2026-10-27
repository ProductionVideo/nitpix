"""Which objects of an imported stage show the current media, and through which of their own UV
sets. One shared source for all of them. The stage is a NormalizedScene or a composed Stage
(nitpix/stage.py); keys are whatever it calls its objects. Pure data, no Qt: the Mapping screen edits it and the
view draws from it. Session only for now: nothing here is saved with the project.

Rules:
- Assignment is per object, with all of its material slots; there are no slot-level overrides.
- An object needs a UV set present on every slot to be assigned one; objects without UVs stay
  neutral and are never given generated coordinates.
- UVs are used exactly as authored. Wherever they fall outside 0-1 (by more than UV_EDGE, export
  noise) the surface shows neutral grey (the view decides that per pixel); here it is only
  reported, for the UV set in use, from the importer's per-triangle ranges. Only assigned objects
  get a warning: an unassigned object's ranges are details, not a problem.
"""
from __future__ import annotations

from .scene import UV_EDGE, NormalizedScene, UVRange

MIXED = object()   # the selection disagrees (assigned / not, or UV sets)


class Assignments:
    def __init__(self, scene: NormalizedScene):
        self.scene = scene
        self.uv = {}                      # object key -> UV set index, for assigned objects

    # ---- questions ---------------------------------------------------------------------
    def object(self, key):
        return self.scene.object(key)

    def eligible(self, key) -> bool:
        o = self.object(key)
        return bool(o) and o.uv_sets > 0

    def uv_choices(self, keys) -> list[int]:
        """UV sets every eligible object in `keys` has (index order)."""
        counts = [self.object(k).uv_sets for k in keys if self.eligible(k)]
        return list(range(min(counts))) if counts else []

    def assigned_state(self, keys):
        """True / False for the whole selection, or MIXED."""
        states = {k in self.uv for k in keys}
        return MIXED if len(states) > 1 else (states.pop() if states else False)

    def uv_state(self, keys):
        """The UV set the assigned objects in `keys` use, MIXED if they differ, None if none assigned."""
        sets = {self.uv[k] for k in keys if k in self.uv}
        return MIXED if len(sets) > 1 else (sets.pop() if sets else None)

    def coverage(self, key, uv_set: int | None = None) -> UVRange | None:
        """Where this object's triangles fall in `uv_set` (default: its assigned set, else the first)."""
        o = self.object(key)
        if not o or o.uv_sets == 0:
            return None
        s = self.uv.get(key, 0) if uv_set is None else uv_set
        if s >= o.uv_sets:
            return None
        return UVRange.join(self.scene.geometries[g].uv_ranges[s] for g in o.geometries
                            if s < len(self.scene.geometries[g].uv_ranges))

    def outside(self, key, uv_set: int | None = None) -> bool:
        """Whether part of this object shows neutral in `uv_set` because its UVs leave 0-1."""
        r = self.coverage(key, uv_set)
        return bool(r and r.outside)

    def warning(self, key) -> str | None:
        """Short note for an assigned object whose media is partly replaced by neutral."""
        if key not in self.uv or not self.outside(key):
            return None
        return f"partly outside image ({share(self.coverage(key).neutral_share)} neutral)"

    def details(self, key, uv_set: int | None = None) -> str:
        """The exact numbers behind warning(), for a tooltip; any object, assigned or not."""
        o = self.object(key)
        if not o:
            return ""
        if o.uv_sets == 0:
            return "No UVs: this object stays neutral and cannot show media."
        s = self.uv.get(key, 0) if uv_set is None else uv_set
        r = self.coverage(key, s)
        if r is None:
            return f"Has no UV set {s + 1}."
        text = f"UV {s + 1}: U {r.u[0]:.4g} to {r.u[1]:.4g}, V {r.v[0]:.4g} to {r.v[1]:.4g}"
        if not r.outside:
            return text + " — all on the image."
        return (f"{text}, up to {r.overshoot:.4g} beyond the image.\nAbout {share(r.neutral_share)} of the surface "
                f"({r.outside:,} of {r.triangles:,} triangles touched) falls outside 0–1 and shows neutral grey there. "
                f"UVs are used as authored (within {UV_EDGE:g} of the edge counts as on it).")

    # ---- changes -----------------------------------------------------------------------
    def apply(self, keys, uv_set: int | None = None) -> dict:
        """Show the media on `keys`. `uv_set` None keeps each object's current set (first for new).
        Returns {key: reason} for objects left out."""
        skipped = {}
        for k in keys:
            o = self.object(k)
            if not o:
                skipped[k] = "not in this scene"
            elif o.uv_sets == 0:
                skipped[k] = "no UVs"
            else:
                s = self.uv.get(k, 0) if uv_set is None else uv_set
                if s >= o.uv_sets:
                    skipped[k] = f"has no UV set {s + 1}"
                else:
                    self.uv[k] = s
        return skipped

    def apply_all(self, uv_set: int | None = None) -> dict:
        return self.apply([o.key for o in self.scene.objects if o.uv_sets > 0], uv_set)

    def clear(self, keys):
        for k in keys:
            self.uv.pop(k, None)

    def prune(self):
        """Forget objects the stage no longer has (a model file taken off it)."""
        self.uv = {k: s for k, s in self.uv.items() if self.object(k) is not None}


def share(f: float) -> str:
    """A 0-1 fraction as '12%', with '<1%' and '>99%' rather than a misleading '0%' or '100%'."""
    p = 100 * f
    return "<1%" if 0 < p < 1 else ">99%" if 99 < p < 100 else f"{p:.0f}%"
