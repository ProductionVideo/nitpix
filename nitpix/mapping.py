"""Which objects of an imported stage show the current media, and through which of their own UV
sets. One shared source for all of them. Pure data, no Qt: the Mapping screen edits it and the
view draws from it. Session only in this alpha: nothing here is saved with the project.

Rules (agreed for the alpha):
- Assignment is per object, with all of its material slots; there are no slot-level overrides.
- An object needs a UV set present on every slot to be assigned one; objects without UVs stay
  neutral and are never given generated coordinates.
- UVs are used exactly as authored. Wherever they fall outside 0-1 the surface shows neutral
  grey (the view decides that per pixel); here it is only reported, for the chosen UV set.
"""
from __future__ import annotations

from .scene import NormalizedScene

MIXED = object()   # the selection disagrees (assigned / not, or UV sets)


class Assignments:
    def __init__(self, scene: NormalizedScene):
        self.scene = scene
        self.uv = {}                      # object key -> UV set index, for assigned objects
        self._outside = {}                # (geometry id, uv set) -> bool

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

    def outside(self, key, uv_set: int | None = None) -> bool:
        """Whether this object's UVs (in `uv_set`, default its assigned or first set) leave 0-1."""
        o = self.object(key)
        if not o or o.uv_sets == 0:
            return False
        s = self.uv.get(key, 0) if uv_set is None else uv_set
        if s >= o.uv_sets:
            return False
        for gid in o.geometries:
            if (gid, s) not in self._outside:
                uv = self.scene.geometries[gid].uv_sets[s]
                self._outside[gid, s] = bool(uv) and (min(uv) < -1e-6 or max(uv) > 1 + 1e-6)
            if self._outside[gid, s]:
                return True
        return False

    def warning(self, key) -> str | None:
        o = self.object(key)
        if not o:
            return None
        if o.uv_sets == 0:
            return "no UVs: stays neutral"
        if self.outside(key):
            return "UVs outside the image: neutral there"
        return None

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
