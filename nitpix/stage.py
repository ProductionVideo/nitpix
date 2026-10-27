"""One Mapping stage made of several imported model files (sources). Pure data, no Qt: the
Mapping screen adds and removes sources, nitpix/mapping.py assigns its objects and the view
draws it, through the same questions a single NormalizedScene answers (objects, object(key),
geometries, bounds).

- Every import is a Source with its own session id ("m1", "m2", ... never reused: a stage that
  replaces another continues its numbering, Stage(next_id=old.next_id)), so the same file name, path or object key in two sources cannot collide.
- Stage keys are "<source id>:<object key in its file>"; geometry ids are prefixed the same way.
  Neither display names nor list positions are identities.
- Objects keep their imported coordinates (metres, Y up, the file's own placement): nothing is
  centred, fitted, rescaled or packed per file, so separately exported screens line up as authored.
- Adding or removing a source composes only that source's objects; the geometry arrays are shared
  with the imported scene, never copied.

Session only: nothing here is saved. The ids and each source's recorded path and checksum are
what a later save/reopen needs.
"""
from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass

from .scene import NormalizedScene, SceneObject

SEP = ":"


def stage_key(sid: str, key: str) -> str:
    return f"{sid}{SEP}{key}"


def split_key(key: str) -> tuple[str, str]:
    """(source id, object key in its file). Source ids never contain SEP; object keys may."""
    sid, _, rest = key.partition(SEP)
    return sid, rest


@dataclass(frozen=True)
class Source:
    id: str                       # "m1": this import, in this stage
    scene: NormalizedScene
    objects: tuple                # the scene's objects with stage keys and geometry ids

    @property
    def path(self) -> str:
        return self.scene.source["path"]

    @property
    def origin(self) -> str:
        """Where the file came from (for showing and telling files apart); `path` is what was read,
        which for a model kept in the project is its copy there."""
        return self.scene.source.get("origin") or self.path

    @property
    def name(self) -> str:
        return os.path.basename(self.origin)


class Stage:
    def __init__(self, next_id: int = 1):
        self.sources: list[Source] = []
        self.objects: tuple = ()
        self.geometries: dict = {}
        self._by_key: dict = {}
        self.next_id = next_id

    def __bool__(self):
        return bool(self.sources)

    # ---- questions (as NormalizedScene) -------------------------------------------------
    def object(self, key: str) -> SceneObject | None:
        return self._by_key.get(key)

    @property
    def bounds(self) -> tuple:
        """Around every object of every source, in the shared scene space."""
        if not self.sources:
            return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
        bs = [s.scene.bounds for s in self.sources]
        return (tuple(min(b[0][a] for b in bs) for a in range(3)), tuple(max(b[1][a] for b in bs) for a in range(3)))

    @property
    def warnings(self) -> tuple:
        return tuple(f"{self.label(s)}: {w}" for s in self.sources for w in s.scene.warnings)

    def source(self, sid: str) -> Source | None:
        return next((s for s in self.sources if s.id == sid), None)

    def source_of(self, key: str) -> Source | None:
        return self.source(split_key(key)[0])

    def has_path(self, path: str) -> Source | None:
        """The source already read from this file (the same file by any spelling of its path)."""
        real = os.path.realpath(path)
        return next((s for s in self.sources if os.path.realpath(s.origin) == real), None)

    def label(self, src: Source) -> str:
        """The file name, with as many parent folders as it takes to tell apart sources with the
        same name: "centre.obj", or "venue A/screen.obj" and "venue B/screen.obj"."""
        parts = _parts(src.origin)
        others = [_parts(s.origin) for s in self.sources if s is not src]
        n = 1
        while n < len(parts) and any(o[-n:] == parts[-n:] for o in others):
            n += 1
        return "/".join(parts[-n:])

    # ---- changes ------------------------------------------------------------------------
    def add(self, scene: NormalizedScene, sid: str | None = None) -> Source:
        """A new source; `sid` restores a saved one (later new ids continue after it)."""
        if sid is None:
            sid = f"m{self.next_id}"
            self.next_id += 1
        else:
            if self.source(sid) is not None:
                raise ValueError(f"source {sid} is already on the stage")
            if sid[1:].isdigit():
                self.next_id = max(self.next_id, int(sid[1:]) + 1)
        objs = tuple(dataclasses.replace(o, key=stage_key(sid, o.key), geometries=tuple(stage_key(sid, g) for g in o.geometries))
                     for o in scene.objects)
        src = Source(sid, scene, objs)
        self.sources.append(src)
        self.objects = self.objects + objs
        self.geometries.update((stage_key(sid, gid), g) for gid, g in scene.geometries.items())
        self._by_key.update((o.key, o) for o in objs)
        return src

    def remove(self, sid: str) -> Source | None:
        """Take a source and its objects off the stage. The file itself is never touched."""
        src = self.source(sid)
        if src is None:
            return None
        self.sources.remove(src)
        prefix = sid + SEP
        self.objects = tuple(o for o in self.objects if not o.key.startswith(prefix))
        for gid in src.scene.geometries:
            self.geometries.pop(stage_key(sid, gid), None)
        for o in src.objects:
            self._by_key.pop(o.key, None)
        return src


def _parts(path: str) -> list[str]:
    return [p for p in os.path.normpath(path).split(os.sep) if p]
