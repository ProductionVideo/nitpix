"""GL for the Mapping view's projector preview (nitpix/projector.py): what the projector can see,
and its frustum drawn as an overlay. The view (nitpix/ui/mapview.py) owns the context and calls
these with it current, on the GUI thread; the playback controller never touches them.

Visibility: the stage is drawn once from the projector into a VISIBILITY_SIZE-wide R32F texture
holding, per projector texel, the distance along the aim to the nearest surface (divided by the far
distance, so it fits 0-1). Every surface occludes, from either side, so a wall blocks what is behind
it. The view's shader then lights a fragment only if it is in the image, in front of the projector,
facing it, and no farther than that nearest distance plus a bias. The bias is about one and a half
texels' width at that distance, more on surfaces seen at a grazing angle, plus 0.01% of the
distance: enough to stop a lit surface shadowing itself ("acne"), small enough that an occluder
standing 1 cm in front of a wall at 10 m still blocks it (a texel there is about 2.6 mm at 30°).
Shadow edges are as fine as one texel; a gap narrower than that may not let light through.

The texture is rebuilt only when the stage's geometry or the projector's pose, field of view or
aspect changes: orbiting, selection and new video frames reuse it.

The overlay (frustum lines to the target distance, the image outline and a tick on its top edge)
is drawn after the stage with depth testing, into the view only: it never occludes or receives.
"""
from __future__ import annotations

import array
import math

from PySide6.QtGui import QMatrix4x4, QVector3D
from PySide6.QtOpenGL import (QOpenGLBuffer, QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat, QOpenGLShader,
                              QOpenGLShaderProgram, QOpenGLVertexArrayObject)

VISIBILITY_SIZE = 2048                    # texels across the image; the height follows the aspect

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


class ProjectionPass:
    def __init__(self, parent):
        self.depth = _program(parent, DEPTH_VERT, DEPTH_FRAG)
        self.lines = _program(parent, LINE_VERT, LINE_FRAG)
        self.fbo = None
        self.key = None                   # what the visibility texture was built for
        self.rebuilds = 0                 # how many times it was (tests and measurements)
        self.size = None                  # (w, h) of the visibility texture
        self.pvp = QMatrix4x4()
        self.near = self.far = 1.0
        self.texel = 0.0                  # width of one texel per metre of distance
        self._vao = self._vbo = None
        self._line_key = None
        self._line_count = 0

    # ---- visibility --------------------------------------------------------------------
    def prepare(self, f, projector, aspect, scene, geo_version, meshes, worlds, draw) -> bool:
        """Make sure the visibility texture matches; True if it had to be rebuilt."""
        key = (geo_version, projector.position, projector.target, projector.h_fov, round(aspect, 6))
        if key == self.key and self.fbo is not None:
            return False
        w = VISIBILITY_SIZE
        h = max(1, round(VISIBILITY_SIZE / aspect))
        if self.fbo is None or self.size != (w, h):
            fmt = QOpenGLFramebufferObjectFormat()
            fmt.setAttachment(QOpenGLFramebufferObject.Attachment.Depth)
            fmt.setInternalTextureFormat(GL_R32F)
            self.fbo = QOpenGLFramebufferObject(w, h, fmt)
            self.size = (w, h)
            f.glBindTexture(GL_TEXTURE_2D, self.fbo.texture())
            for k, v in ((GL_TEXTURE_MIN_FILTER, GL_NEAREST), (GL_TEXTURE_MAG_FILTER, GL_NEAREST),
                         (GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE), (GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)):
                f.glTexParameteri(GL_TEXTURE_2D, k, v)
        self.near, self.far = projector.depth_range(scene.bounds)
        self.pvp = qmatrix(projector.projection(aspect, self.near, self.far)) * qmatrix(projector.view())
        self.texel = 2 * math.tan(math.radians(projector.h_fov) / 2) / w
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
        self.key = key
        self.rebuilds += 1
        return True

    def texture(self) -> int:
        return self.fbo.texture() if self.fbo is not None else 0

    def forget(self):
        """The next prepare() rebuilds (the stage changed)."""
        self.key = None

    # ---- overlay -----------------------------------------------------------------------
    def draw_frustum(self, f, vp, projector, aspect):
        """Lines from the projector to its image at the target's distance, the image's outline and a
        tick on its top edge. GL_LINES with depth testing; nothing else reads them."""
        key = (projector.position, projector.target, projector.h_fov, round(aspect, 6))
        if key != self._line_key:
            p = projector.position
            tl, tr, br, bl = projector.corners(aspect, projector.distance())
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


def look_through(projector, aspect, view_w, view_h, bounds) -> QMatrix4x4:
    """The viewer's matrix when looking through the projector: its pose, with the whole image in view
    (the view is widened in one direction when the window's aspect differs from the image's)."""
    th = math.tan(math.radians(projector.h_fov) / 2)
    tv = max(th / aspect, th / (view_w / max(1, view_h)))
    near, far = projector.depth_range(bounds)
    proj = QMatrix4x4()
    proj.perspective(math.degrees(2 * math.atan(tv)), view_w / max(1, view_h), near, far)
    return proj * qmatrix(projector.view())


def vec3(p) -> QVector3D:
    return QVector3D(*[float(c) for c in p])
