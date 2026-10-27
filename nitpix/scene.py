"""Stage models for the Mapping workspace: OBJ / FBX read through Assimp (assimp-py) into plain,
importer-independent data. No Qt, no playback, no database: callers get a NormalizedScene and
decide what to do with it, so this can run on a worker thread or in another process unchanged.

What is kept exactly as authored: UV coordinates (no flipping, generation or normalising), the
node hierarchy, and which nodes share a mesh (instances). What Assimp is asked to do: split
polygons into triangles, split meshes per primitive type and per material, merge identical
vertices (no values change), and apply the file's units and up axis, so every scene arrives in
metres, Y up. The steps that would change coordinates or node identity (FlipUVs, GenUVCoords,
PreTransformVertices, OptimizeGraph, OptimizeMeshes, FindInvalidData...) are never used.

Objects are the unit of assignment: one node of the file, with every material slot of its
mesh(es). Keys are node paths below the file's root, not display names, so two objects called
"Logo" stay apart ("Stage/Logo" and "Stage/Logo#2"). Assimp's FBX pivot helper nodes
("…_$AssimpFbx$_Translation") are folded into the node they belong to.

Known importer limits (assimp-py 1.2): a file with any line or point geometry (splines, guides)
is refused as a whole, because the binding rejects non-triangle faces before nitpix sees them;
UV set names and the file's declared units are not exposed (UV sets are numbered; units are
applied, not reported); OBJ objects that share a name are merged into one by Assimp's OBJ reader.
"""
from __future__ import annotations

import hashlib
import os
import threading
from array import array
from dataclasses import dataclass, field
from importlib import metadata
from types import SimpleNamespace

FORMATS = (".obj", ".fbx")
_HELPER = "_$AssimpFbx$_"
# How far beyond 0-1 a UV may fall and still count as the image's edge: export and float noise, not
# authored placement. Within it the picture's edge is sampled; beyond it the surface is neutral.
# Half a texel of the largest preview texture (3840 px, nitpix/ui/mapview.py) is 1.3e-4, so the
# shift is never visible; exporters that round UVs to four decimals are off by up to 5e-5. One
# value for the import diagnostics, nitpix/mapping.py and the view's shader.
UV_EDGE = 1e-4
LINES_MESSAGE = ("This file has lines or points (splines, guides) as well as surfaces, and the importer "
                 "cannot skip them yet. Export the surfaces only and import again.")


class SceneImportError(Exception):
    """A scene that cannot be used; the message is written for the person importing it."""


@dataclass(frozen=True)
class UVRange:
    """Where one UV set falls on the triangles of a mesh (or several). Vertices no triangle uses
    are left out: they cover no surface."""
    u: tuple                      # (min, max)
    v: tuple
    outside: int                  # triangles with a corner beyond 0-1 by more than UV_EDGE
    triangles: int
    area: float = 0.0             # surface area of the triangles, in the mesh's own units
    neutral: float = 0.0          # the part of it whose UVs fall beyond 0-1: shown neutral

    @property
    def overshoot(self) -> float:
        """How far the farthest corner lies beyond 0-1 (0 when all are on the image)."""
        return max(0.0, -self.u[0], self.u[1] - 1, -self.v[0], self.v[1] - 1)

    @property
    def neutral_share(self) -> float:
        return self.neutral / self.area if self.area > 0 else (1.0 if self.outside else 0.0)

    @staticmethod
    def join(ranges) -> "UVRange | None":
        ranges = [r for r in ranges if r.triangles]
        if not ranges:
            return None
        return UVRange((min(r.u[0] for r in ranges), max(r.u[1] for r in ranges)),
                       (min(r.v[0] for r in ranges), max(r.v[1] for r in ranges)),
                       sum(r.outside for r in ranges), sum(r.triangles for r in ranges),
                       sum(r.area for r in ranges), sum(r.neutral for r in ranges))


def uv_range(uv: array, indices: array, positions: array | None = None) -> UVRange:
    """UVRange of one UV set. With positions, also how much surface shows neutral: within a
    triangle UVs are linear over the surface, so its share inside the image is the share of its
    UV-space area that lies inside 0-1 (clipped exactly)."""
    us, vs = uv[0::2], uv[1::2]
    if not indices:
        return UVRange((0.0, 0.0), (0.0, 0.0), 0, 0)
    used = set(indices)
    ru, rv = (us, vs) if len(used) == len(us) else ([us[i] for i in used], [vs[i] for i in used])
    lo, hi = -UV_EDGE, 1 + UV_EDGE
    off = [not (lo <= a <= hi and lo <= b <= hi) for a, b in zip(us, vs)]
    outside, area, neutral = 0, 0.0, 0.0
    flagged = any(off)
    for t in range(0, len(indices), 3):
        a, b, c = indices[t], indices[t + 1], indices[t + 2]
        if positions is not None:
            ta = _area3(positions, a, b, c)
            area += ta
        if flagged and (off[a] or off[b] or off[c]):
            outside += 1
            if positions is not None:
                neutral += ta * (1.0 - _inside_share(((us[a], vs[a]), (us[b], vs[b]), (us[c], vs[c])), lo, hi))
    return UVRange((min(ru), max(ru)), (min(rv), max(rv)), outside, len(indices) // 3, area, neutral)


def _area3(p, a, b, c) -> float:
    ax, ay, az = p[3 * a], p[3 * a + 1], p[3 * a + 2]
    ux, uy, uz = p[3 * b] - ax, p[3 * b + 1] - ay, p[3 * b + 2] - az
    vx, vy, vz = p[3 * c] - ax, p[3 * c + 1] - ay, p[3 * c + 2] - az
    return 0.5 * ((uy * vz - uz * vy) ** 2 + (uz * vx - ux * vz) ** 2 + (ux * vy - uy * vx) ** 2) ** 0.5


def _area2(poly) -> float:
    return abs(sum(poly[i][0] * poly[i - 1][1] - poly[i - 1][0] * poly[i][1] for i in range(len(poly)))) / 2


def _inside_share(tri, lo, hi) -> float:
    """The share of a UV triangle's area inside [lo, hi]², by clipping it to that square."""
    whole = _area2(tri)
    if whole <= 1e-18:                    # degenerate in UV: inside if its middle is
        mu, mv = sum(p[0] for p in tri) / 3, sum(p[1] for p in tri) / 3
        return 1.0 if lo <= mu <= hi and lo <= mv <= hi else 0.0
    poly = list(tri)
    for axis, bound, keep_above in ((0, lo, True), (0, hi, False), (1, lo, True), (1, hi, False)):
        out = []
        for i, cur in enumerate(poly):
            prev = poly[i - 1]
            cin = (cur[axis] >= bound) if keep_above else (cur[axis] <= bound)
            pin = (prev[axis] >= bound) if keep_above else (prev[axis] <= bound)
            if cin != pin:
                k = (bound - prev[axis]) / (cur[axis] - prev[axis])
                out.append((prev[0] + k * (cur[0] - prev[0]), prev[1] + k * (cur[1] - prev[1])))
            if cin:
                out.append(cur)
        poly = out
        if len(poly) < 3:
            return 0.0
    return min(1.0, _area2(poly) / whole)


@dataclass(frozen=True)
class Geometry:
    """One mesh of the file for one material slot. Instances share it."""
    id: str                       # "mesh:3", stable within one import
    positions: array              # 'f', x y z per vertex, in the node's own space
    indices: array                # 'I', three per triangle
    uv_sets: tuple                # of array('f'), u v per vertex, exactly as authored
    material: str
    fingerprint: str              # positions, triangles and every UV set
    uv_ranges: tuple = ()         # UVRange per UV set

    @property
    def triangles(self) -> int:
        return len(self.indices) // 3


@dataclass(frozen=True)
class SceneObject:
    key: str                      # "Stage/Screens/Centre"; a repeated name gets "#2", "#3"...
    path: tuple                   # node names from below the root, for showing the hierarchy
    display: str                  # "Centre", or "Logo (2)" for the second of that name
    geometries: tuple             # Geometry ids, one per material slot
    slots: tuple                  # material names, in the same order
    world: tuple                  # 16 floats, column-major: object space -> scene (metres, Y up)
    uv_sets: int                  # UV sets present on every slot (0: cannot show media)
    triangles: int
    outside_0_1: bool             # some triangles reach outside the image in the first UV set
    issue: str | None             # why it cannot show media, if it cannot
    fingerprint: str              # geometry and UVs only: moving an object keeps its mapping

    @property
    def assignable(self) -> bool:
        return self.issue is None


@dataclass(frozen=True)
class NormalizedScene:
    source: dict                  # path, size, mtime, sha256
    importer: str
    objects: tuple                # SceneObject, in file order
    geometries: dict              # id -> Geometry
    bounds: tuple                 # (min xyz, max xyz) in metres, around every object
    warnings: tuple = field(default=())

    def object(self, key: str) -> SceneObject | None:
        return next((o for o in self.objects if o.key == key), None)


def importer_version() -> str:
    try:
        return f"assimp-py {metadata.version('assimp-py')}"
    except metadata.PackageNotFoundError:
        return "assimp-py (not installed)"


def selfcheck() -> str:
    """Import a one-triangle OBJ: proves the native importer loads in this installation."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="nitpix scene ") as d:
        p = os.path.join(d, "check.obj")
        with open(p, "w") as fh:
            fh.write("o Check\nv 0 0 0\nv 1 0 0\nv 0 1 0\nvt 0 0\nvt 1 0\nvt 0 1\nf 1/1 2/2 3/3\n")
        try:
            s = load_scene(p)
        except SceneImportError as e:
            return f"failed: {e}"
    o = s.objects[0]
    return f"ok ({s.importer}, {o.triangles} triangle, {o.uv_sets} UV set)"


def load_scene(path: str, cancel: threading.Event | None = None) -> NormalizedScene:
    """Read an OBJ or FBX stage. Raises SceneImportError with a message for the user."""
    if os.path.splitext(path)[1].lower() not in FORMATS:
        raise SceneImportError("Mapping reads OBJ and FBX stage models.")
    try:
        st = os.stat(path)
    except OSError:
        raise SceneImportError(f"File not found: {path}") from None
    try:
        import assimp_py as ai
    except ImportError:
        raise SceneImportError("The scene importer (assimp-py) is not installed.") from None
    flags = (ai.Process_Triangulate | ai.Process_SortByPType | ai.Process_JoinIdenticalVertices
             | ai.Process_GlobalScale | ai.Process_ValidateDataStructure)
    try:
        raw = ai.import_file(path, flags)
    except FileNotFoundError:
        raise SceneImportError(f"File not found: {path}") from None
    except ValueError as e:     # assimp-py 1.2 refuses a whole file if any mesh is lines or points
        if "triangulated" in str(e):
            raise SceneImportError(LINES_MESSAGE) from None
        raise SceneImportError(f"Could not read this scene: {e}") from None
    except Exception as e:      # Assimp reports damaged or unsupported files as RuntimeError
        raise SceneImportError(f"Could not read this scene: {_assimp_message(e, path)}") from None
    _check(cancel)
    warnings = []
    geometries, skipped = _geometries(raw, cancel)
    if skipped:
        warnings.append(f"{skipped} point or line mesh{'es' if skipped > 1 else ''} ignored (only surfaces can show media).")
    objects = _objects(raw, geometries)
    if not objects:
        raise SceneImportError("This file has no surfaces to show media on.")
    no_uv = sum(1 for o in objects if o.uv_sets == 0)
    if no_uv:
        warnings.append(f"{no_uv} object{'s' if no_uv > 1 else ''} without UVs (shown neutral).")
    outside = sum(1 for o in objects if o.outside_0_1)
    if outside:
        warnings.append(f"{outside} object{'s' if outside > 1 else ''} with UVs partly outside the image (UV 1): "
                        "where assigned, those parts show neutral.")
    return NormalizedScene(source={"path": os.path.abspath(path), "size": st.st_size, "mtime": st.st_mtime, "sha256": _sha256(path)},
                           importer=importer_version(), objects=tuple(objects), geometries=geometries,
                           bounds=_bounds(objects, geometries), warnings=tuple(warnings))


# ---- internals -------------------------------------------------------------------------------
def _check(cancel):
    if cancel is not None and cancel.is_set():
        raise SceneImportError("Import cancelled.")


def _assimp_message(e: Exception, path: str) -> str:
    msg = str(e)
    prefix = f"Assimp error loading '{path}': "
    return msg[len(prefix):] if msg.startswith(prefix) else msg


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _uv(mv, components: int, vertices: int) -> array:
    """u, v per vertex from Assimp's set, which may carry a third (w) component."""
    a = array("f")
    a.frombytes(mv.tobytes())
    if components == 2:
        return a
    out = array("f", bytes(8 * vertices))
    out[0::2] = a[0::components]
    out[1::2] = a[1::components]
    return out


def _geometries(raw, cancel) -> tuple[dict, int]:
    out, skipped = {}, 0
    names = [m.get("NAME") or f"Material {i + 1}" for i, m in enumerate(raw.materials)]
    for i, m in enumerate(raw.meshes):
        _check(cancel)
        if m.num_faces == 0 or m.num_indices != 3 * m.num_faces:   # points or lines after SortByPType
            skipped += 1
            continue
        pos = array("f")
        pos.frombytes(m.vertices.tobytes())
        idx = array("I")
        idx.frombytes(m.indices.tobytes())
        comps = list(m.num_uv_components or [])
        sets = tuple(_uv(tc, comps[k] if k < len(comps) else 2, m.num_vertices) for k, tc in enumerate(m.texcoords or []))
        h = hashlib.sha256(pos.tobytes())
        h.update(idx.tobytes())
        for s in sets:
            h.update(b"uv")
            h.update(s.tobytes())
        gid = f"mesh:{i}"
        out[gid] = Geometry(gid, pos, idx, sets, names[m.material_index] if m.material_index < len(names) else "", h.hexdigest(),
                            tuple(uv_range(s, idx, pos) for s in sets))
    return out, skipped


def _mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


_IDENTITY = [[float(i == j) for j in range(4)] for i in range(4)]


def _escape(name: str) -> str:
    return name.replace("%", "%25").replace("/", "%2F").replace("#", "%23")


def _objects(raw, geometries) -> list[SceneObject]:
    out = []

    def walk(node, parent_m, key_parts, path, seen):
        m = _mul(parent_m, [list(r) for r in node.transformation])
        if _HELPER in node.name:     # an FBX pivot helper: its transform belongs to the node below
            for c in node.children:
                walk(c, m, key_parts, path, seen)
            return
        n = seen.get(node.name, 0) + 1
        seen[node.name] = n
        seg = _escape(node.name) + (f"#{n}" if n > 1 else "")
        key_parts = key_parts + [seg]
        path = path + (node.name,)
        geos = [geometries[f"mesh:{i}"] for i in node.mesh_indices if f"mesh:{i}" in geometries]
        if geos:
            sets = min(len(g.uv_sets) for g in geos)
            tris = sum(g.triangles for g in geos)
            issue = None if sets else "no UVs"
            outside = any(r.outside for g in geos for r in g.uv_ranges[:1])
            h = hashlib.sha256()
            for g in geos:
                h.update(g.fingerprint.encode())
            out.append(SceneObject(key="/".join(key_parts), path=path, display=node.name + (f" ({n})" if n > 1 else ""),
                                   geometries=tuple(g.id for g in geos), slots=tuple(g.material for g in geos),
                                   world=tuple(m[r][c] for c in range(4) for r in range(4)), uv_sets=sets, triangles=tris,
                                   outside_0_1=outside, issue=issue, fingerprint=h.hexdigest()))
        child_seen = {}
        for c in node.children:
            walk(c, m, key_parts, path, child_seen)

    # The root stands for the file itself (Assimp names it after the file, which changes on
    # relink), so keys start below it. Its transform carries the file's unit scale.
    root = raw.root_node
    root_m = [list(r) for r in root.transformation]
    seen = {}
    if root.mesh_indices:                 # meshes on the root itself (rare)
        walk(SimpleNamespace(name="(root)", transformation=_IDENTITY, mesh_indices=root.mesh_indices, children=[]), root_m, [], (), seen)
    for c in root.children:
        walk(c, root_m, [], (), seen)
    return out



def _bounds(objects, geometries):
    lo, hi = [float("inf")] * 3, [float("-inf")] * 3
    for o in objects:
        w = o.world
        for gid in o.geometries:
            p = geometries[gid].positions
            if not p:
                continue
            box = [(min(p[a::3]), max(p[a::3])) for a in range(3)]
            for x in box[0]:
                for y in box[1]:
                    for z in box[2]:
                        for a in range(3):
                            v = w[a] * x + w[4 + a] * y + w[8 + a] * z + w[12 + a]
                            lo[a], hi[a] = min(lo[a], v), max(hi[a], v)
    if lo[0] == float("inf"):
        return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    return (tuple(lo), tuple(hi))
