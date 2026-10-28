"""One virtual projector for the Mapping preview: plain state and its geometry. No Qt, no GL: the
Mapping screen edits it (nitpix/ui/projedit.py), nitpix/ui/projection.py draws with it, and tests
check it directly.

A geometric preview, not a projector model: it places the current picture on the stage the way a
pinhole projector at `position`, aimed at `target`, would. No lens shift, brightness, colour, focus
or calibration.

Coordinates are the stage's (nitpix/scene.py): metres, Y up; the audience side of an exported
stage is usually +Z.

- Aim: the projector looks from `position` towards `target`. A target at the position itself
  has no direction; `problem()` says so and nothing is projected. Aiming (picking a target)
  keeps the position and the roll.
- Orientation: with roll 0 the image's top edge stays towards +Y (world up), so the horizon is
  level whatever the pan and tilt. Aimed within 0.5° of straight down or up, where "up" has no
  meaning, the top points towards -Z (upstage) for down and +Z for up, so a floor projection reads
  the right way up from the audience. Near vertical a small change of aim can turn the image
  quickly; that is the geometry of pan and tilt, and `roll` sets it by hand.
- `roll` turns the image about the aim, in degrees; positive turns its top towards the right as
  seen from behind the projector.
- `throw` is the width-based throw ratio: distance / image width, so at distance D the image is
  D / throw wide. It is the only stored lens value: the horizontal angle `h_fov` follows from it,
  tan(h/2) = 1 / (2 throw), so the two can never disagree.
- `raster` is the projector's native image in pixels (width, height): the beam's shape. It never
  follows the clip. `fit` says how the clip sits in it: "fit" keeps the clip's shape, centred, and
  the bars beside or above it project nothing; "stretch" fills the raster.
- `raster_note` "inferred" marks a raster worked out from an earlier preview's aspect (a saved
  setup from before rasters existed), not one chosen for real hardware.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

VERTICAL = math.cos(math.radians(0.5))      # |forward . Y| above this: aimed (almost) straight up or down
MIN_THROW, MAX_THROW = 0.13, 60.0            # about 150° to 1° wide
MIN_RASTER, MAX_RASTER = 16, 16384
FITS = ("fit", "stretch")
RASTERS = ((1920, 1080), (1920, 1200), (3840, 2160), (4096, 2160), (2560, 1600), (1400, 1050))


@dataclass(frozen=True)
class Projector:
    position: tuple = (0.0, 2.0, 10.0)
    target: tuple = (0.0, 2.0, 0.0)
    roll: float = 0.0                       # degrees about the aim
    throw: float = 1.5                      # distance / image width
    raster: tuple = (1920, 1080)            # native pixels: the beam's shape
    fit: str = "fit"                        # how the clip sits in the raster
    raster_note: str = ""                   # "inferred": from an earlier preview's aspect
    enabled: bool = True
    show_frustum: bool = True

    def with_(self, **kw) -> "Projector":
        return replace(self, **kw)

    # ---- questions --------------------------------------------------------------------
    def problem(self) -> str | None:
        """Why nothing can be projected, or None."""
        if math.dist(self.position, self.target) < 1e-6:
            return "The target is at the projector's position: move one of them to aim it."
        if not MIN_THROW <= self.throw <= MAX_THROW:
            return f"The throw ratio must be between {MIN_THROW:g} and {MAX_THROW:g}."
        if not all(MIN_RASTER <= n <= MAX_RASTER for n in self.raster):
            return f"The native raster must be between {MIN_RASTER} and {MAX_RASTER} pixels each way."
        return None

    @property
    def h_fov(self) -> float:
        """The full horizontal angle of the image, in degrees (from the throw ratio)."""
        return math.degrees(2 * math.atan(1 / (2 * self.throw)))

    @property
    def aspect(self) -> float:
        return self.raster[0] / self.raster[1]

    def image_aspect(self, clip_aspect: float | None = None) -> float:
        """The beam's width / height: the raster's, whatever the clip."""
        return self.aspect

    def distance(self) -> float:
        return math.dist(self.position, self.target)

    def image_size(self, dist: float | None = None) -> tuple[float, float]:
        """The ideal image (width, height) in metres on a plane square to the aim at `dist` (the
        target's distance by default): not the size on a tilted or curved surface."""
        d = self.distance() if dist is None else dist
        w = d / self.throw
        return w, w / self.aspect

    def content_rect(self, clip_aspect: float | None) -> tuple[float, float, float, float]:
        """Where the clip sits in the raster, as (left, bottom, width, height) in 0-1 image units."""
        if self.fit == "stretch" or not clip_aspect or clip_aspect <= 0:
            return 0.0, 0.0, 1.0, 1.0
        r = clip_aspect / self.aspect
        if r >= 1:                          # wider than the raster: bars above and below
            return 0.0, (1 - 1 / r) / 2, 1.0, 1 / r
        return (1 - r) / 2, 0.0, r, 1.0     # narrower: bars at the sides

    def basis(self):
        """(right, up, forward) unit vectors; forward points from the projector to the target."""
        f = _norm(_sub(self.target, self.position))
        if abs(f[1]) <= VERTICAL:
            world_up = (0.0, 1.0, 0.0)
        else:                               # straight down: top upstage (-Z); straight up: top towards +Z
            world_up = (0.0, 0.0, -1.0) if f[1] < 0 else (0.0, 0.0, 1.0)
        r = _norm(_cross(f, world_up))
        u = _cross(r, f)
        if self.roll:
            a = math.radians(self.roll)
            c, s = math.cos(a), math.sin(a)
            r, u = _add(_mul(r, c), _mul(u, -s)), _add(_mul(u, c), _mul(r, s))
        return r, u, f

    def view(self) -> list:
        """4x4 row-major world -> projector space (looking down -Z, like a camera)."""
        r, u, f = self.basis()
        p = self.position
        return [[r[0], r[1], r[2], -_dot(r, p)],
                [u[0], u[1], u[2], -_dot(u, p)],
                [-f[0], -f[1], -f[2], _dot(f, p)],
                [0.0, 0.0, 0.0, 1.0]]

    def projection(self, near: float, far: float) -> list:
        """4x4 row-major perspective of the image (clip w = distance along the aim)."""
        th = 1 / (2 * self.throw)
        tv = th / self.aspect
        return [[1 / th, 0.0, 0.0, 0.0],
                [0.0, 1 / tv, 0.0, 0.0],
                [0.0, 0.0, -(far + near) / (far - near), -2 * far * near / (far - near)],
                [0.0, 0.0, -1.0, 0.0]]

    def depth_range(self, bounds) -> tuple[float, float]:
        """(near, far) along the aim that hold the whole stage: far just beyond its farthest corner,
        near a ten-thousandth of that (and at least 1 mm). Surfaces nearer than `near` are not lit."""
        lo, hi = bounds
        far = max(math.dist(self.position, (x, y, z)) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2]))
        far = max(far * 1.01, 0.01)
        return max(far * 1e-4, 1e-3), far

    def corners(self, dist: float) -> list:
        """The image's corners at `dist` along the aim: top-left, top-right, bottom-right, bottom-left."""
        r, u, f = self.basis()
        w, h = self.image_size(dist)
        c = _add(self.position, _mul(f, dist))
        return [_add(c, _add(_mul(r, sx * w / 2), _mul(u, sy * h / 2))) for sx, sy in ((-1, 1), (1, 1), (1, -1), (-1, -1))]

    # ---- edits (each returns a new Projector) -------------------------------------------
    def aimed_at(self, point) -> "Projector":
        """Aimed at `point`: the position and roll stay."""
        return self.with_(target=tuple(float(c) for c in point))

    def moved(self, delta, keep_aim: bool = False) -> "Projector":
        """The body moved by `delta`. It keeps its orientation (the target moves with it) unless
        `keep_aim`, when the target stays and the projector turns to it."""
        pos = _add(self.position, delta)
        return self.with_(position=pos) if keep_aim else self.with_(position=pos, target=_add(self.target, delta))

    def placed(self, eye, look_at) -> "Projector":
        """Moved to `eye`, aimed at `look_at`: the pose only; throw, raster and roll stay."""
        return self.with_(position=tuple(float(c) for c in eye), target=tuple(float(c) for c in look_at))


def from_view(eye, target, v_fov: float, view_aspect: float, base: Projector | None = None) -> Projector:
    """Match view: a projector where the orbit camera is, aimed where it looks, with the throw ratio
    that gives the largest image of its raster's shape that fits in the view:
    tan(h/2) = tan(v_fov/2) · min(view aspect, raster aspect). Looking through it then shows the
    same framing, with the image filling the view in at least one direction."""
    base = base or Projector()
    th = math.tan(math.radians(v_fov) / 2) * min(view_aspect, base.aspect)
    throw = max(MIN_THROW, min(MAX_THROW, 1 / (2 * th)))
    return base.placed(eye, target).with_(throw=round(throw, 3))


def throw_from_fov(h_fov: float) -> float:
    return 1 / (2 * math.tan(math.radians(h_fov) / 2))


def raster_for(aspect: float) -> tuple[int, int]:
    """A raster 1920 wide of this aspect (for a preview saved with an aspect only)."""
    return 1920, max(MIN_RASTER, min(MAX_RASTER, round(1920 / aspect)))


def mat_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def transform(m, p):
    """(x, y, z, w) of point p through the 4x4 row-major m."""
    v = (p[0], p[1], p[2], 1.0)
    return tuple(sum(m[i][k] * v[k] for k in range(4)) for i in range(4))


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _mul(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(a):
    n = math.sqrt(_dot(a, a))
    return (a[0] / n, a[1] / n, a[2] / n) if n > 0 else (0.0, 0.0, -1.0)
