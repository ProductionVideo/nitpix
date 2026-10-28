"""Editing helpers drawn over the Mapping view (nitpix/ui/mapview.py): the projector's body, its
target, the move arrows and the pick marker. They are the view's own drawing: never part of the
stage, never lit by the projector, never in its visibility texture or in ray queries, never saved.

Each helper is a small mesh built here once, drawn at a constant size on screen (the caller gives
the scale for its place), flat-shaded so its shape reads, over the stage: the depth buffer is
cleared first, so a helper behind a wall is still seen and clicked, and helpers sort among
themselves. Drawn with the view's context current, after the stage and the frustum lines.
"""
from __future__ import annotations

import array
import math

from PySide6.QtGui import QMatrix4x4
from PySide6.QtOpenGL import QOpenGLBuffer, QOpenGLVertexArrayObject

from .projection import _program, vec3

GL_TRIANGLES, GL_FLOAT, GL_DEPTH_TEST, GL_DEPTH_BUFFER_BIT, GL_BLEND = 0x0004, 0x1406, 0x0B71, 0x0100, 0x0BE2

VERT = """#version 330 core
layout(location = 0) in vec3 pos;
uniform mat4 mvp;
uniform mat4 model;
out vec3 v_world;
void main() {
    v_world = (model * vec4(pos, 1.0)).xyz;
    gl_Position = mvp * vec4(pos, 1.0);
}
"""
FRAG = """#version 330 core
in vec3 v_world;
uniform vec4 colour;
uniform vec3 eye;
out vec4 frag;
void main() {
    vec3 n = normalize(cross(dFdx(v_world), dFdy(v_world)));
    float light = 0.62 + 0.38 * abs(dot(n, normalize(eye - v_world)));
    frag = vec4(colour.rgb * light, colour.a);
}
"""

# sizes on screen, in logical pixels
BODY_PX, ARROW_PX, MARK_PX, TARGET_PX = 30.0, 92.0, 10.0, 7.0
AXES = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}
COLOURS = {"x": (0.93, 0.33, 0.30), "y": (0.45, 0.82, 0.36), "z": (0.36, 0.56, 0.98)}
ACTIVE = (1.0, 0.86, 0.25)
BODY, TARGET, MARK = (1.0, 0.72, 0.2), (1.0, 0.9, 0.7), (1.0, 0.25, 0.85)
ARROW_START = 0.22                        # of the arrow's length: it starts clear of the body


def _box(out, c, h):
    x0, y0, z0 = (c[i] - h[i] for i in range(3))
    x1, y1, z1 = (c[i] + h[i] for i in range(3))
    v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    for a, b, cc, d in ((0, 1, 2, 3), (5, 4, 7, 6), (4, 0, 3, 7), (1, 5, 6, 2), (3, 2, 6, 7), (4, 5, 1, 0)):
        out.extend((*v[a], *v[b], *v[cc], *v[a], *v[cc], *v[d]))


def _cone_x(out, x0, x1, r0, r1, n=14):
    """A (truncated) cone along +X from x0 (radius r0) to x1 (radius r1), capped."""
    ring = [(math.cos(2 * math.pi * i / n), math.sin(2 * math.pi * i / n)) for i in range(n)]
    for i in range(n):
        (c0, s0), (c1, s1) = ring[i], ring[(i + 1) % n]
        a, b = (x0, c0 * r0, s0 * r0), (x0, c1 * r0, s1 * r0)
        c, d = (x1, c1 * r1, s1 * r1), (x1, c0 * r1, s0 * r1)
        out.extend((*a, *b, *c, *a, *c, *d))
        out.extend((x0, 0.0, 0.0, *b, *a))
        if r1 > 0:
            out.extend((x1, 0.0, 0.0, *d, *c))


def arrow_mesh():
    out = array.array("f")
    _box(out, ((ARROW_START + 0.78) / 2, 0, 0), ((0.78 - ARROW_START) / 2, 0.022, 0.022))
    _cone_x(out, 0.76, 1.0, 0.075, 0.0)
    return out


def octahedron_mesh():
    out = array.array("f")
    p = [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)]
    for a, b, c in ((0, 2, 4), (2, 1, 4), (1, 3, 4), (3, 0, 4), (2, 0, 5), (1, 2, 5), (3, 1, 5), (0, 3, 5)):
        out.extend((*p[a], *p[b], *p[c]))
    return out


def body_mesh():
    """A projector in its own space (x right, y up, looking down -z), lens at the origin."""
    out = array.array("f")
    _box(out, (0, 0, 0.42), (0.5, 0.24, 0.4))                 # the chassis, behind the lens
    lens = array.array("f")
    _cone_x(lens, 0.0, 0.2, 0.2, 0.24)                          # the lens, along -z
    for i in range(0, len(lens), 3):
        x, y, z = lens[i:i + 3]
        out.extend((y, z, -x))
    _box(out, (0, 0.3, 0.2), (0.12, 0.06, 0.08))               # a bump on top: which way is up
    return out


class _Shape:
    def __init__(self, data, prog):
        self.count = len(data) // 3
        self.vao = QOpenGLVertexArrayObject()
        self.vao.create()
        self.vao.bind()
        self.vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.vbo.create()
        self.vbo.bind()
        raw = data.tobytes()
        self.vbo.allocate(raw, len(raw))
        prog.enableAttributeArray(0)
        prog.setAttributeBuffer(0, GL_FLOAT, 0, 3, 12)
        self.vao.release()

    def destroy(self):
        self.vbo.destroy()
        self.vao.destroy()


def axis_frame(axis) -> QMatrix4x4:
    """Rotation taking +X onto the axis."""
    m = QMatrix4x4()
    if axis == "y":
        m.rotate(90, 0, 0, 1)
    elif axis == "z":
        m.rotate(-90, 0, 1, 0)
    return m


class HelperPass:
    def __init__(self, parent):
        self.prog = _program(parent, VERT, FRAG)
        self.prog.bind()
        self.shapes = {"arrow": _Shape(arrow_mesh(), self.prog), "diamond": _Shape(octahedron_mesh(), self.prog),
                       "body": _Shape(body_mesh(), self.prog)}

    def draw(self, f, vp, eye, items):
        """items: (shape, model matrix, rgb). Over the stage (its depth is cleared first)."""
        if not items:
            return
        f.glClear(GL_DEPTH_BUFFER_BIT)
        f.glEnable(GL_DEPTH_TEST)
        f.glDisable(GL_BLEND)
        self.prog.bind()
        self.prog.setUniformValue("eye", eye)
        for shape, model, rgb in items:
            s = self.shapes[shape]
            self.prog.setUniformValue("mvp", vp * model)
            self.prog.setUniformValue("model", model)
            self.prog.setUniformValue("colour", rgb[0], rgb[1], rgb[2], 1.0)
            s.vao.bind()
            f.glDrawArrays(GL_TRIANGLES, 0, s.count)
            s.vao.release()

    def destroy(self):
        for s in self.shapes.values():
            s.destroy()
        self.shapes = {}
        self.prog.removeAllShaders()


def at(point, scale, rotation: QMatrix4x4 | None = None) -> QMatrix4x4:
    m = QMatrix4x4()
    m.translate(vec3(point))
    if rotation is not None:
        m = m * rotation
    m.scale(scale)
    return m
