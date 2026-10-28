"""GL for the Mapping view's projector preview (nitpix/projector.py): what the projector can see,
and its frustum drawn as an overlay. The view (nitpix/ui/mapview.py) owns the context and calls
these with it current, on the GUI thread; the playback controller never touches them.

Visibility: the stage is drawn once from the projector into an R32F texture VISIBILITY_SIZE
texels along its longer side (the other follows the raster's shape; both within the GPU's limit)
holding, per projector texel, the distance along the aim to the nearest surface (divided by the far
distance, so it fits 0-1). Every surface occludes, from either side, so a wall blocks what is behind
it. The view's shader then lights a fragment only if it is in the image, in front of the projector,
facing it, and no farther than that nearest distance plus a bias. The bias is about one and a half
texels' width at that distance, more on surfaces seen at a grazing angle, plus 0.01% of the
distance: enough to stop a lit surface shadowing itself ("acne"), small enough that an occluder
standing 1 cm in front of a wall at 10 m still blocks it (a texel there is about 2.6 mm at 30°).
Shadow edges are as fine as one texel; a gap narrower than that may not let light through.

The texture is rebuilt only when the stage's geometry or the projector's pose, roll, throw or
raster changes: orbiting, selection, the clip and new video frames reuse it. If the GPU cannot
make it, prepare() raises VisibilityError once for that size and the view says so (no repeated GL
errors); nothing is projected until the projector changes.

Ray queries (RayQuery): the stage drawn into one float texel through a very narrow lens along a
ray, keeping the nearest surface's world position and which object it is. Used for picking a
target under the cursor (a few times a second at most, only while picking), for whether that point
is blocked from the projector, and for where the centre of the beam lands. Helpers never take part.

The overlay (frustum lines to the target distance, the image outline and a tick on its top edge)
is drawn after the stage with depth testing, into the view only: it never occludes or receives.
"""
from __future__ import annotations

import array
import math

from PySide6.QtGui import QMatrix4x4, QVector3D
from PySide6.QtOpenGL import (QOpenGLBuffer, QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat, QOpenGLShader,
                              QOpenGLShaderProgram, QOpenGLVertexArrayObject)

VISIBILITY_SIZE = 2048                    # texels along the image's longer side
GL_MAX_TEXTURE_SIZE, GL_RGBA, GL_RGBA32F = 0x0D33, 0x1908, 0x8814

GL_TRIANGLES, GL_LINES, GL_FLOAT, GL_TEXTURE_2D, GL_R32F = 0x0004, 0x0001, 0x1406, 0x0DE1, 0x822E
GL_TEXTURE_MIN_FILTER, GL_TEXTURE_MAG_FILTER, GL_NEAREST = 0x2801, 0x2800, 0x2600
GL_TEXTURE_WRAP_S, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE = 0x2802, 0x2803, 0x812F
GL_DEPTH_TEST, GL_CULL_FACE, GL_COLOR_BUFFER_BIT, GL_DEPTH_BUFFER_BIT = 0x0B71, 0x0B44, 0x4000, 0x0100

DEPTH_VERT = """#version 330 core
layout(location = 0) in vec3 pos;
uniform mat4 pvp;
uniform mat4 model;
out float v_d;
void main() {
    vec4 c = pvp * (model * vec4(pos, 1.0));
    v_d = c.w;                               // distance along the aim
    gl_Position = c;
}
"""
DEPTH_FRAG = """#version 330 core
in float v_d;
uniform float pfar;
out vec4 frag;
void main() { frag = vec4(v_d / pfar, 0.0, 0.0, 1.0); }
"""
RAY_VERT = """#version 330 core
layout(location = 0) in vec3 pos;
uniform mat4 rvp;
uniform mat4 model;
out vec3 v_world;
void main() {
    v_world = (model * vec4(pos, 1.0)).xyz;
    gl_Position = rvp * vec4(v_world, 1.0);
}
"""
RAY_FRAG = """#version 330 core
in vec3 v_world;
uniform float obj;        // the object's index + 1 (0: nothing hit)
out vec4 frag;
void main() { frag = vec4(v_world, obj); }
"""
LINE_VERT = """#version 330 core
layout(location = 0) in vec3 pos;
uniform mat4 mvp;
void main() { gl_Position = mvp * vec4(pos, 1.0); }
"""
LINE_FRAG = """#version 330 core
uniform vec4 colour;
out vec4 frag;
void main() { frag = colour; }
"""


def qmatrix(rows) -> QMatrix4x4:
    return QMatrix4x4(*[float(rows[r][c]) for r in range(4) for c in range(4)])


def _program(parent, vert, frag):
    p = QOpenGLShaderProgram(parent)
    if not (p.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, vert)
            and p.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, frag) and p.link()):
        raise RuntimeError(p.log() or "projector shader did not compile")
    return p


class VisibilityError(RuntimeError):
    pass


def visibility_size(aspect: float, limit: int) -> tuple[int, int]:
    """(w, h) of the visibility texture: VISIBILITY_SIZE (or the GPU's limit) along the longer side."""
    n = max(16, min(VISIBILITY_SIZE, limit))
    if aspect >= 1:
        return n, max(1, min(n, round(n / aspect)))
    return max(1, min(n, round(n * aspect))), n


def max_texture(f) -> int:
    try:
        return int(f.glGetIntegerv(GL_MAX_TEXTURE_SIZE)) or 4096
    except Exception:                         # an unusual binding: a size every GL 3.3 GPU has
        return 4096


class ProjectionPass:
    def __init__(self, parent):
        self.depth = _program(parent, DEPTH_VERT, DEPTH_FRAG)
        self.lines = _program(parent, LINE_VERT, LINE_FRAG)
        self.fbo = None
        self.key = None                   # what the visibility texture was built for
        self.rebuilds = 0                 # how many times it was (tests and measurements)
        self.size = None                  # (w, h) of the visibility texture
        self.failed = None                # (key, size) the GPU could not make: not tried again
        self.limit = None                 # the GPU's largest texture
        self.pvp = QMatrix4x4()
        self.near = self.far = 1.0
        self.texel = 0.0                  # width of one texel per metre of distance
        self._vao = self._vbo = None
        self._line_key = None
        self._line_count = 0

    # ---- visibility --------------------------------------------------------------------
    def prepare(self, f, projector, scene, geo_version, meshes, worlds, draw) -> bool:
        """Make sure the visibility texture matches; True if it had to be rebuilt. Raises
        VisibilityError if the GPU cannot make it (once per projector state)."""
        key = (geo_version, projector.position, projector.target, projector.roll, projector.throw, projector.raster)
        if key == self.key and self.fbo is not None:
            return False
        if self.failed and self.failed[0] == key:
            raise VisibilityError(self.failed[1])
        if self.limit is None:
            self.limit = max_texture(f)
        w, h = visibility_size(projector.aspect, self.limit)
        if self.fbo is None or self.size != (w, h):
            fmt = QOpenGLFramebufferObjectFormat()
            fmt.setAttachment(QOpenGLFramebufferObject.Attachment.Depth)
            fmt.setInternalTextureFormat(GL_R32F)
            self.fbo = QOpenGLFramebufferObject(w, h, fmt)
            self.size = (w, h)
            if not self.fbo.isValid():
                self.fbo, self.size, self.key = None, None, None
                self.failed = (key, f"the graphics card could not make the {w}×{h} visibility texture")
                raise VisibilityError(self.failed[1])
            f.glBindTexture(GL_TEXTURE_2D, self.fbo.texture())
            for k, v in ((GL_TEXTURE_MIN_FILTER, GL_NEAREST), (GL_TEXTURE_MAG_FILTER, GL_NEAREST),
                         (GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE), (GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)):
                f.glTexParameteri(GL_TEXTURE_2D, k, v)
        self.near, self.far = projector.depth_range(scene.bounds)
        self.pvp = qmatrix(projector.projection(self.near, self.far)) * qmatrix(projector.view())
        self.texel = (1 / projector.throw) / w
        self.fbo.bind()
        f.glViewport(0, 0, w, h)
        f.glDisable(GL_CULL_FACE)                     # both sides of every surface occlude
        f.glEnable(GL_DEPTH_TEST)
        f.glClearColor(1.0, 0.0, 0.0, 1.0)            # nothing nearer than the far distance
        f.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        self.depth.bind()
        self.depth.setUniformValue("pvp", self.pvp)
        self.depth.setUniformValue1f("pfar", self.far)   # 1f: the Python-float overload sets an int
        for o in scene.objects:
            model = worlds[o.key][0]
            self.depth.setUniformValue("model", model)
            for gid in o.geometries:
                m = meshes.get(gid)
                if m is None:
                    continue
                m[1].vao.bind()
                draw(GL_TRIANGLES, m[1].count)
                m[1].vao.release()
        self.fbo.release()
        self.key, self.failed = key, None
        self.rebuilds += 1
        return True

    def texture(self) -> int:
        return self.fbo.texture() if self.fbo is not None else 0

    def forget(self):
        """The next prepare() rebuilds (the stage changed)."""
        self.key = None

    # ---- overlay -----------------------------------------------------------------------
    def draw_frustum(self, f, vp, projector):
        """Lines from the projector to its image at the target's distance, the image's outline and a
        tick on its top edge. GL_LINES with depth testing; nothing else reads them."""
        key = (projector.position, projector.target, projector.roll, projector.throw, projector.raster)
        if key != self._line_key:
            p = projector.position
            tl, tr, br, bl = projector.corners(projector.distance())
            top = [(a + b) / 2 for a, b in zip(tl, tr)]
            r, u, _f = projector.basis()
            tick = [top[i] + u[i] * 0.08 * math.dist(tl, tr) for i in range(3)]
            segs = [p, tl, p, tr, p, br, p, bl, tl, tr, tr, br, br, bl, bl, tl, top, tick]
            buf = array.array("f", [float(c) for pt in segs for c in pt]).tobytes()
            if self._vao is None:
                self._vao = QOpenGLVertexArrayObject()
                self._vao.create()
                self._vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
                self._vbo.create()
            self._vao.bind()
            self._vbo.bind()
            self._vbo.allocate(buf, len(buf))
            self.lines.bind()
            self.lines.enableAttributeArray(0)
            self.lines.setAttributeBuffer(0, GL_FLOAT, 0, 3, 12)
            self._vao.release()
            self._line_key, self._line_count = key, len(segs)
        self.lines.bind()
        self.lines.setUniformValue("mvp", vp)
        self.lines.setUniformValue("colour", 1.0, 0.72, 0.2, 1.0)
        self._vao.bind()
        f.glDrawArrays(GL_LINES, 0, self._line_count)
        self._vao.release()

    def destroy(self):
        """With the context current."""
        self.fbo = None
        if self._vbo is not None:
            self._vbo.destroy()
            self._vao.destroy()
            self._vbo = self._vao = None
        for p in (self.depth, self.lines):
            p.removeAllShaders()
        self.key = self._line_key = None


class RayQuery:
    """The nearest surface along a ray, drawn on the GPU into one float texel (see the module notes)."""

    def __init__(self, parent):
        self.prog = _program(parent, RAY_VERT, RAY_FRAG)
        self.fbo = None
        self.count = 0                     # queries made (tests and measurements)

    def run(self, f, origin, direction, far, scene, meshes, worlds, draw):
        """(world point, object index) of the first surface along the ray, or None. With the
        context current; leaves the framebuffer unbound."""
        if self.fbo is None:
            fmt = QOpenGLFramebufferObjectFormat()
            fmt.setAttachment(QOpenGLFramebufferObject.Attachment.Depth)
            fmt.setInternalTextureFormat(GL_RGBA32F)
            self.fbo = QOpenGLFramebufferObject(1, 1, fmt)
            if not self.fbo.isValid():
                self.fbo = None
                return None
        o, d = vec3(origin), vec3(direction).normalized()
        up = QVector3D(0, 1, 0) if abs(d.y()) < 0.99 else QVector3D(0, 0, -1)
        far = max(far, 1e-2)
        proj, view = QMatrix4x4(), QMatrix4x4()
        proj.perspective(0.01, 1.0, max(far * 1e-5, 1e-4), far)
        view.lookAt(o, o + d, up)
        rvp = proj * view
        self.fbo.bind()
        f.glViewport(0, 0, 1, 1)
        f.glDisable(GL_CULL_FACE)                     # either side of a surface stops the ray
        f.glEnable(GL_DEPTH_TEST)
        f.glClearColor(0.0, 0.0, 0.0, 0.0)
        f.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        self.prog.bind()
        self.prog.setUniformValue("rvp", rvp)
        for i, ob in enumerate(scene.objects):
            self.prog.setUniformValue("model", worlds[ob.key][0])
            self.prog.setUniformValue1f("obj", float(i + 1))
            for gid in ob.geometries:
                m = meshes.get(gid)
                if m is None:
                    continue
                m[1].vao.bind()
                draw(GL_TRIANGLES, m[1].count)
                m[1].vao.release()
        buf = bytearray(16)
        f.glReadPixels(0, 0, 1, 1, GL_RGBA, GL_FLOAT, buf)
        self.fbo.release()
        self.count += 1
        x, y, z, obj = array.array("f", bytes(buf))
        if obj < 0.5:
            return None
        return (x, y, z), int(round(obj)) - 1

    def destroy(self):
        self.fbo = None
        self.prog.removeAllShaders()


def look_through(projector, view_w, view_h, bounds) -> QMatrix4x4:
    """The viewer's matrix when looking through the projector: its pose, with the whole image in view
    (the view is widened in one direction when the window's aspect differs from the image's)."""
    th = 1 / (2 * projector.throw)
    tv = max(th / projector.aspect, th / (view_w / max(1, view_h)))
    near, far = projector.depth_range(bounds)
    proj = QMatrix4x4()
    proj.perspective(math.degrees(2 * math.atan(tv)), view_w / max(1, view_h), near, far)
    return proj * qmatrix(projector.view())


def vec3(p) -> QVector3D:
    return QVector3D(*[float(c) for c in p])
