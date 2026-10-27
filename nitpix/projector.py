"""One virtual projector for the Mapping preview: plain state and its geometry. No Qt, no GL: the
Mapping screen edits it, nitpix/ui/projection.py draws with it, and tests check it directly.

A geometric preview, not a projector model: it places the current picture on the stage the way a
pinhole projector at `position`, aimed at `target`, would. No lens shift, brightness, colour or
calibration.

Coordinates are the stage's (nitpix/scene.py): metres, Y up; the audience side of an exported
stage is usually +Z.

- Aim: the projector looks from `position` towards `target`. A target at the position itself
  has no direction; `problem()` says so and nothing is projected.
- Roll is never set by hand: the image's top edge stays towards +Y (world up). Aimed within 0.5°
  of straight down or up, where "up" has no meaning, the image's top points towards -Z (upstage),
  so a floor projection reads the right way up from the audience. The image turns quickly as the
  aim passes near vertical; that is the geometry, not a bug.
- `h_fov` is the full horizontal angle of the image in degrees. The vertical angle follows from
  the image aspect: tan(v/2) = tan(h/2) / aspect. Throw ratio (distance / image width) is
  1 / (2·tan(h/2)), shown, not edited, so the two can never disagree.
- `aspect` None follows the current clip (its display width / height); a number overrides it.

Session only, like the rest of Mapping: nothing here is saved yet, but the state is plain values
so a later save/reopen can store it as is.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

DEFAULT_ASPECT = 16 / 9
VERTICAL = math.cos(math.radians(0.5))      # |forward . Y| above this: aimed (almost) straight up or down
MIN_FOV, MAX_FOV = 1.0, 150.0


@dataclass(frozen=True)
class Projector:
    position: tuple = (0.0, 2.0, 10.0)
    target: tuple = (0.0, 2.0, 0.0)
    h_fov: float = 30.0                     # degrees, the whole image width
    aspect: float | None = None             # None: from the clip
    enabled: bool = True
    show_frustum: bool = True

    def with_(self, **kw) -> "Projector":
        return replace(self, **kw)

    # ---- questions --------------------------------------------------------------------
    def problem(self) -> str | None:
        """Why nothing can be projected, or None."""
        if math.dist(self.position, self.target) < 1e-6:
            return "The target is at the projector's position: move one of them to aim it."
        if not MIN_FOV <= self.h_fov <= MAX_FOV:
            return f"The field of view must be between {MIN_FOV:g}° and {MAX_FOV:g}°."
        return None

    def image_aspect(self, clip_aspect: float | None) -> float:
        a = self.aspect if self.aspect else clip_aspect
        return a if a and a > 0 else DEFAULT_ASPECT

    def throw_ratio(self) -> float:
        return 1.0 / (2.0 * math.tan(math.radians(self.h_fov) / 2))

    def distance(self) -> float:
        return math.dist(self.position, self.target)

    def basis(self):
        """(right, up, forward) unit vectors; forward points from the projector to the target."""
        f = _norm(_sub(self.target, self.position))
        if abs(f[1]) <= VERTICAL:
            world_up = (0.0, 1.0, 0.0)
        else:                               # straight down: top upstage (-Z); straight up: top towards +Z
            world_up = (0.0, 0.0, -1.0) if f[1] < 0 else (0.0, 0.0, 1.0)
        r = _norm(_cross(f, world_up))
        u = _cross(r, f)
        return r, u, f

    def view(self) -> list:
        """4x4 row-major world -> projector space (looking down -Z, like a camera)."""
        r, u, f = self.basis()
        p = self.position
        return [[r[0], r[1], r[2], -_dot(r, p)],
                [u[0], u[1], u[2], -_dot(u, p)],
                [-f[0], -f[1], -f[2], _dot(f, p)],
                [0.0, 0.0, 0.0, 1.0]]

    def projection(self, aspect: float, near: float, far: float) -> list:
        """4x4 row-major perspective of the image (clip w = distance along the aim)."""
        th = math.tan(math.radians(self.h_fov) / 2)
        tv = th / aspect
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

    def corners(self, aspect: float, dist: float) -> list:
        """The image's corners at `dist` along the aim: top-left, top-right, bottom-right, bottom-left."""
        r, u, f = self.basis()
        th = math.tan(math.radians(self.h_fov) / 2)
        w, h = th * dist, th / aspect * dist
        c = _add(self.position, _mul(f, dist))
        return [_add(c, _add(_mul(r, sx * w), _mul(u, sy * h))) for sx, sy in ((-1, 1), (1, 1), (1, -1), (-1, -1))]


def from_view(eye, target, v_fov: float, view_aspect: float, image_aspect: float, base: Projector | None = None) -> Projector:
    """A projector where the orbit camera is, aimed where it looks. Its image is the largest one of
    the image's aspect that fits in the current view: tan(h/2) = tan(v_fov/2) · min(view aspect,
    image aspect). Looking through it then shows the same framing, with the image filling the view
    in at least one direction."""
    th = math.tan(math.radians(v_fov) / 2) * min(view_aspect, image_aspect)
    h = max(MIN_FOV, min(MAX_FOV, math.degrees(2 * math.atan(th))))
    base = base or Projector()
    return base.with_(position=tuple(float(c) for c in eye), target=tuple(float(c) for c in target), h_fov=round(h, 2))


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
