"""One virtual projector for the Mapping preview: plain state and its geometry. No Qt, no GL: the
Mapping screen edits it (nitpix/ui/projedit.py), nitpix/ui/projection.py draws with it, and tests
check it directly.

A geometric preview, not a projector model: it places the current picture on the stage the way a
pinhole projector at `position`, aimed at `target`, would. No lens shift, brightness, colour, focus
or calibration.

Coordinates are the stage's (nitpix/scene.py): metres, Y up; the audience side of an exported
stage is usually +Z.

- Aim: the projector looks from `position` towards `target`. A target at the position itself
  has no direction; `problem()` says so and nothing is projected.
- Orientation: `roll` turns the image about the aim, in degrees, from a level reference: with roll
  0 the image's top edge is towards +Y (world up), so the horizon is level whatever the pan and
  tilt. That reference is continuous for every aim except exactly straight down or up, where it
  is defined as top towards -Z (upstage) aimed down, so a floor projection reads the right way up
  from the audience, and +Z aimed up. Positive roll turns the top towards the right as seen from
  behind the projector.
- Re-aiming (reaim(): picking a target, moving with the aim kept, typing a target): away from
  vertical the roll is kept as it is. When the old or the new aim is within NEAR_VERTICAL of
  straight up or down, where the level reference turns quickly with the aim (by half a turn as an
  aim passes beneath the projector), the roll is set instead so that the image keeps the
  orientation it had, as near as the new aim allows. So a small aim edit never flips the image;
  a roll gained that way stays until changed by hand.
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

NEAR_VERTICAL = 20.0                         # degrees from straight up or down: re-aiming keeps the image's orientation
LEGACY_VERTICAL = math.cos(math.radians(0.5))   # setups saved before version 3 used a fixed top within 0.5° of vertical
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
        r, u = _level(f)
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
    def reaim(self, position, target) -> "Projector":
        """A new position and/or target, with the roll kept (away from vertical) or set so the image
        keeps its orientation (near vertical): see the module notes."""
        new = self.with_(position=tuple(float(c) for c in position), target=tuple(float(c) for c in target))
        if new.problem() or self.problem() or (_off_vertical(self) > NEAR_VERTICAL and _off_vertical(new) > NEAR_VERTICAL):
            return new
        _r, up, f0 = self.basis()
        f1 = new.basis()[2]
        roll = _roll_for(_turned(up, f0, f1), f1)               # the image top turned with the aim, by the shortest turn
        return new if roll is None else new.with_(roll=roll)

    def aimed_at(self, point) -> "Projector":
        """Aimed at `point`: the position stays; the image keeps its orientation (reaim())."""
        return self.reaim(self.position, point)

    def moved(self, delta, keep_aim: bool = False) -> "Projector":
        """The body moved by `delta`. It keeps its orientation (the target moves with it) unless
        `keep_aim`, when the target stays and the projector turns to it."""
        pos = _add(self.position, delta)
        return self.reaim(pos, self.target) if keep_aim else self.with_(position=pos, target=_add(self.target, delta))

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


def legacy_roll(p: Projector) -> float:
    """The roll that gives a projector saved before version 3 the picture it had then: those
    setups used a fixed top (-Z down, +Z up) within 0.5° of vertical rather than the level
    reference. Elsewhere the roll is unchanged."""
    f = _norm(_sub(p.target, p.position))
    if abs(f[1]) <= LEGACY_VERTICAL or p.problem():
        return p.roll
    up = (0.0, 0.0, -1.0) if f[1] < 0 else (0.0, 0.0, 1.0)
    r0 = _norm(_cross(f, up))
    u0 = _cross(r0, f)
    a = math.radians(p.roll)
    old_u = _add(_mul(u0, math.cos(a)), _mul(r0, math.sin(a)))
    roll = _roll_for(old_u, f)
    return p.roll if roll is None else roll


def _level(f):
    """(right, up) of the level reference for aim f (roll 0): continuous except exactly vertical."""
    if math.hypot(f[0], f[2]) < 1e-9:      # exactly straight down: top upstage (-Z); straight up: +Z
        world_up = (0.0, 0.0, -1.0) if f[1] < 0 else (0.0, 0.0, 1.0)
    else:
        world_up = (0.0, 1.0, 0.0)
    r = _norm(_cross(f, world_up))
    return r, _cross(r, f)


def _off_vertical(p: Projector) -> float:
    """Degrees between the aim and straight up or down."""
    f = _norm(_sub(p.target, p.position))
    return math.degrees(math.acos(min(1.0, abs(f[1]))))


def _turned(v, f0, f1):
    """v rotated by the shortest rotation taking direction f0 to f1 (Rodrigues); v itself when f0 and
    f1 are opposite, where the shortest rotation is not defined."""
    k = _cross(f0, f1)
    s, c = math.sqrt(_dot(k, k)), _dot(f0, f1)
    if s < 1e-12:
        return v
    k = _mul(k, 1 / s)
    return _add(_add(_mul(v, c), _mul(_cross(k, v), s)), _mul(k, _dot(k, v) * (1 - c)))


def _roll_for(top, f) -> float | None:
    """The roll (degrees, -180..180) whose image top for aim f is nearest the direction `top`; None
    when `top` lies along f (no nearest)."""
    t = _sub(top, _mul(f, _dot(top, f)))
    if math.sqrt(_dot(t, t)) < 1e-6:
        return None
    r0, u0 = _level(f)
    return round(math.degrees(math.atan2(_dot(t, r0), _dot(t, u0))), 9)


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
