"""The Mapping view: the current media as a texture on an imported stage, with a free camera,
while playback continues. It is the review player (MpvWidget: loading, the async scrub lane,
generations, decoder safeguards, rational frame maths, shutdown) with only the drawing replaced.

- libmpv draws each new source frame once into a texture the size of the video, long side capped
  at MAX_TEXTURE (larger media is a labelled, downscaled spatial preview; QC keeps full quality).
  This player's own core runs keepaspect=no (no bars baked in) and background=none (alpha kept).
- Frames are rendered without blocking and the repaint is scheduled for each frame's display time
  (nitpix/ui/mpvrender.py), so camera input never waits on the video clock. Where that timing is
  not available the view renders the way the QC player does and says why.
- Camera movement only repaints: no seek, decode, libmpv render or geometry upload. Geometry is
  uploaded once, one set of buffers per mesh, shared by its instances. A stage that changes
  (a model file added or removed, nitpix/stage.py) uploads only the new meshes and releases only
  the ones that left; the rest stay on the GPU, and playback is not touched.
- Unlit: a media surface shows the texture's values. Where the chosen UVs fall outside 0-1 (by
  more than UV_EDGE, nitpix/scene.py: within it the image's edge is sampled), and on objects
  with no media, the surface is neutral grey with a simple headlight so the shape reads.
  Faces seen from behind are darker and never show media. Alpha is premultiplied and shown over
  the surface's grey. Mipmapped, anisotropic sampling for surfaces seen at an angle.
- Two ways to show the media, never both at once: mode "surfaces" uses each assigned object's own
  UVs (above); mode "projectors" ignores UVs and assignments and casts the picture from one
  virtual projector (nitpix/projector.py) onto every surface, with occlusion
  (nitpix/ui/projection.py). A surface is lit on its front side (by winding) only, when that side
  faces the projector: single-sided, like the media in surfaces mode. Switching mode only repaints.

GL objects are created and destroyed with this widget's context current, on the GUI thread;
shutdown() releases them before the render context and the core.
"""
from __future__ import annotations

import math
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QMatrix4x4, QVector3D
from PySide6.QtOpenGL import (QOpenGLBuffer, QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat, QOpenGLShader,
                              QOpenGLShaderProgram, QOpenGLTexture, QOpenGLVertexArrayObject)

from ..scene import UV_EDGE
from .mpvrender import FrameClock, IndexedDraw
from .mpvwidget import MpvWidget
from .projection import ProjectionPass, look_through, vec3

MAX_TEXTURE = 3840

GL_TRIANGLES, GL_FLOAT, GL_TEXTURE_2D, GL_TEXTURE0, GL_RGBA8 = 0x0004, 0x1406, 0x0DE1, 0x84C0, 0x8058
GL_TEXTURE_MIN_FILTER, GL_TEXTURE_MAG_FILTER, GL_LINEAR, GL_LINEAR_MIPMAP_LINEAR = 0x2801, 0x2800, 0x2601, 0x2703
GL_TEXTURE_WRAP_S, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE, GL_TEXTURE_MAX_ANISOTROPY = 0x2802, 0x2803, 0x812F, 0x84FE
GL_DEPTH_TEST, GL_BLEND, GL_CULL_FACE, GL_SCISSOR_TEST = 0x0B71, 0x0BE2, 0x0B44, 0x0C11
GL_COLOR_BUFFER_BIT, GL_DEPTH_BUFFER_BIT, GL_FRAMEBUFFER, GL_CW, GL_CCW = 0x4000, 0x0100, 0x8D40, 0x0900, 0x0901
GL_TEXTURE1 = 0x84C1

VERT = """#version 330 core
layout(location = 0) in vec3 pos;
layout(location = 1) in vec2 uv;
uniform mat4 mvp;
uniform mat4 model;
out vec2 v_uv;
out vec3 v_world;
void main() {
    v_uv = uv;
    v_world = (model * vec4(pos, 1.0)).xyz;
    gl_Position = mvp * vec4(pos, 1.0);
}
"""
FRAG = """#version 330 core
in vec2 v_uv;
in vec3 v_world;
uniform sampler2D tex;
uniform int media;        // 1: this object shows the media
uniform int video;        // 1: a frame has been drawn into the texture
uniform vec3 eye;
uniform float selected;
uniform float edge;       // UV_EDGE: export noise beyond 0-1 still samples the image's edge
uniform int mode;         // 0: each object's UVs; 1: the projector
uniform int lit;          // mode 1: the projector can project
uniform mat4 pvp;         // world -> projector clip space
uniform vec3 ppos;
uniform sampler2D pdepth; // nearest surface per projector texel, as distance / pfar
uniform float pnear;
uniform float pfar;
uniform float ptexel;     // one visibility texel's width per metre of distance
out vec4 frag;
void main() {
    vec3 n = normalize(cross(dFdx(v_world), dFdy(v_world)));   // the face's normal, towards the camera
    float light = 0.55 + 0.45 * abs(dot(n, normalize(eye - v_world)));
    vec3 grey = vec3(0.42) * light;
    vec3 c = grey;
    if (!gl_FrontFacing) {
        c = grey * 0.55;                                   // seen from behind: never media
    } else if (mode == 0) {
        if (media == 1 && video == 1
            && all(greaterThanEqual(v_uv, vec2(-edge))) && all(lessThanEqual(v_uv, vec2(1.0 + edge)))) {
            vec4 t = texture(tex, clamp(v_uv, 0.0, 1.0));  // premultiplied alpha
            c = t.rgb + grey * (1.0 - t.a);
        }
    } else if (lit == 1 && video == 1) {
        vec4 pc = pvp * vec4(v_world, 1.0);                // pc.w: distance along the aim
        float facing = dot(n, normalize(ppos - v_world));  // n is this front face's normal here
        if (pc.w > pnear && pc.w <= pfar && facing > 0.0) {
            vec2 ndc = pc.xy / pc.w;
            if (all(lessThanEqual(abs(ndc), vec2(1.0)))) {
                vec2 st = ndc * 0.5 + 0.5;                 // v = 1 is the top of the image, as in the texture
                float slope = sqrt(max(1.0 - facing * facing, 0.0)) / max(facing, 0.05);
                float bias = pc.w * ptexel * (1.5 + 2.0 * min(slope, 20.0)) + 1e-4 * pc.w;
                if (pc.w <= texture(pdepth, st).r * pfar + bias) {
                    vec4 t = texture(tex, st);
                    c = t.rgb + grey * (1.0 - t.a);
                }
            }
        }
    }
    c = mix(c, vec3(0.25, 0.55, 1.0), selected * 0.22);
    frag = vec4(c, 1.0);
}
"""


class _Mesh:
    """GPU buffers for one Geometry: positions, indices, one buffer per UV set."""

    def __init__(self, g, prog):
        self.count = len(g.indices)
        self.vao = QOpenGLVertexArrayObject()
        self.vao.create()
        self.vao.bind()
        self.vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.vbo.create()
        self.vbo.bind()
        self.vbo.allocate(g.positions.tobytes(), len(g.positions) * 4)
        prog.enableAttributeArray(0)
        prog.setAttributeBuffer(0, GL_FLOAT, 0, 3, 12)
        self.ibo = QOpenGLBuffer(QOpenGLBuffer.Type.IndexBuffer)
        self.ibo.create()
        self.ibo.bind()
        self.ibo.allocate(g.indices.tobytes(), len(g.indices) * 4)
        self.uvs = []
        for s in g.uv_sets:
            b = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
            b.create()
            b.bind()
            b.allocate(s.tobytes(), len(s) * 4)
            self.uvs.append(b)
        self.vao.release()

    def use_uv(self, prog, s):
        if s is not None and s < len(self.uvs):
            self.uvs[s].bind()
            prog.enableAttributeArray(1)
            prog.setAttributeBuffer(1, GL_FLOAT, 0, 2, 8)
        else:
            prog.disableAttributeArray(1)

    def destroy(self):
        for b in [self.vbo, self.ibo, *self.uvs]:
            b.destroy()
        self.vao.destroy()


class MappingView(MpvWidget):
    texture_changed = Signal(object)   # (texture w, h, source w, h), or None
    problem = Signal(str)              # rendering cannot work as intended (shown to the user)
    camera_input = Signal()            # the user dragged or scrolled in the view
    look_changed = Signal(bool)        # looking through the projector, or back to the orbit camera
    camera_moved = Signal()            # the orbit camera changed (by hand or by framing)

    def __init__(self, parent=None):
        super().__init__(parent)
        f = self.format()
        f.setDepthBufferSize(24)
        self.setFormat(f)
        self.setMinimumSize(320, 200)
        # A click in the view takes the keyboard (the QC player keeps NoFocus): a number being typed
        # in the inspector is committed as it loses focus, and Space, arrows and F reach the
        # workspace again without pressing Enter first.
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        # scene and assignments (from the Mapping screen)
        self.scene = None
        self.assigned = {}               # object key -> UV set
        self.selected = set()
        self._meshes = {}                # geometry id -> (Geometry, _Mesh)
        self._scene_dirty = False
        self._geo_version = 0            # bumped whenever the geometry may have changed
        # what is shown: "surfaces" (UV assignments) or "projectors" (the projector's picture)
        self.mode = "surfaces"
        self.projector = None            # nitpix.projector.Projector, or None
        self.clip_aspect = None          # display width / height of the clip, for the projector
        self.looking_through = False
        self.projection = None           # ProjectionPass, made with the context
        self._world = {}                 # object key -> (QMatrix4x4, mirrored)
        # camera
        self.yaw, self.pitch, self.dist = 0.0, 12.0, 5.0
        self.cam_target = QVector3D(0, 0, 0)
        self.radius = 1.0
        self.cam_version = 0
        self._drag = None
        # video texture
        self.fbo = None
        self.tex_size = None
        self.video_size = None
        self._fresh = False              # a new texture needs a frame even if libmpv has none queued
        self.has_frame = False
        self.prog = None
        self._blank = None               # bound to the sampler until there is video
        self._draw = None
        self.problem_text = ""
        self.stats = None                # a measurement harness may set a bounded deque here
        self._due = QTimer(self, singleShot=True, timerType=Qt.TimerType.PreciseTimer)
        self._due.timeout.connect(self.update)
        self.clock = FrameClock(self.m) if self.m else None
        if self.m:
            self.m["keepaspect"] = "no"
            self.m["background"] = "none"
            self.m.observe_property("video-params", lambda _n, v: self.relay.prop.emit("mapview-video", v))
        self.relay.prop.connect(self._on_video_params)

    # ---- scene -------------------------------------------------------------------------
    def set_scene(self, scene, frame=True):
        """A NormalizedScene or Stage, or None; also after the same Stage gained or lost a model file.
        Buffers for new geometry are built, and those for geometry that has gone released, at the next
        paint, with the context current. `frame` False keeps the camera (adding to a stage)."""
        self.scene = scene
        self.assigned, self.selected = {}, set()
        old, self._world = self._world, {}
        if scene is not None:
            for o in scene.objects:
                m = old.get(o.key)
                if m is None or m[2] is not o:
                    w = o.world
                    q = QMatrix4x4(*[w[c * 4 + r] for r in range(4) for c in range(4)])
                    m = (q, q.determinant() < 0, o)
                self._world[o.key] = m
        self._scene_dirty = True
        self._geo_version += 1
        if frame:
            self.frame_all(reset=True)
        else:
            self.update()

    def set_assignments(self, assigned: dict, selected=()):
        self.assigned = dict(assigned)
        self.selected = set(selected)
        self.update()

    # ---- projector -----------------------------------------------------------------------
    def set_mode(self, mode):
        """ "surfaces" or "projectors": which way the media is shown. Only repaints."""
        self.mode = mode
        if mode != "projectors":
            self.look_through(False)
        self.update()

    def set_projector(self, projector, clip_aspect=None):
        """The projector (or None) and the clip's aspect it follows. Only repaints; the visibility
        texture is rebuilt at the next paint if its pose, field of view or aspect changed."""
        self.projector = projector
        self.clip_aspect = clip_aspect
        if projector is None or projector.problem():
            self.look_through(False)
        self.update()

    def projecting(self) -> bool:
        p = self.projector
        return self.mode == "projectors" and p is not None and p.enabled and p.problem() is None and self.scene is not None

    def look_through(self, on: bool):
        """See the stage from the projector (its pose and image); any orbit, pan, zoom or framing
        returns to the orbit camera exactly as it was."""
        on = bool(on) and self.projector is not None and self.projector.problem() is None
        if on != self.looking_through:
            self.looking_through = on
            self.look_changed.emit(on)
            self.update()

    # ---- camera --------------------------------------------------------------------------
    def frame_all(self, reset=False):
        """Fit the whole stage in view; `reset` also returns to the front view."""
        self.look_through(False)
        if self.scene is not None:
            lo, hi = self.scene.bounds
            self.cam_target = QVector3D(*[(a + b) / 2 for a, b in zip(lo, hi)])
            self.radius = max(1e-3, 0.5 * math.dist(lo, hi))
        if reset:
            self.yaw, self.pitch = 0.0, 12.0
        self.dist = self.radius / math.sin(math.radians(20)) * 1.05
        self._camera_changed()

    def orbit(self, dx, dy):
        self.look_through(False)
        self.yaw = (self.yaw - dx * 0.3) % 360
        self.pitch = max(-89.0, min(89.0, self.pitch + dy * 0.3))
        self._camera_changed()

    def pan(self, dx, dy):
        self.look_through(False)
        s = self.dist * 0.0012
        y = math.radians(self.yaw)
        right = QVector3D(math.cos(y), 0, -math.sin(y))
        self.cam_target = self.cam_target - right * (dx * s) + QVector3D(0, dy * s, 0)
        self._camera_changed()

    def zoom(self, steps):
        self.look_through(False)
        self.dist = max(self.radius * 0.02, min(self.radius * 40, self.dist * (0.9 ** steps)))
        self._camera_changed()

    def _camera_changed(self):
        self.cam_version += 1
        self.camera_moved.emit()
        self.update()

    def _eye(self) -> QVector3D:
        if self.looking_through:
            return vec3(self.projector.position)
        p, y = math.radians(self.pitch), math.radians(self.yaw)
        return self.cam_target + QVector3D(math.cos(p) * math.sin(y), math.sin(p), math.cos(p) * math.cos(y)) * self.dist

    def view_projection(self, w, h) -> QMatrix4x4:
        if self.looking_through and self.scene is not None:
            return look_through(self.projector, self.projector.image_aspect(self.clip_aspect), w, h, self.scene.bounds)
        proj = QMatrix4x4()
        near = max(self.radius * 1e-3, self.dist - self.radius * 3, 1e-4)
        proj.perspective(40.0, w / max(1, h), near, self.dist + self.radius * 3)
        view = QMatrix4x4()
        view.lookAt(self._eye(), self.cam_target, QVector3D(0, 1, 0))
        return proj * view

    def mousePressEvent(self, e):
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        pan = e.button() in (Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton) or e.modifiers() & Qt.KeyboardModifier.ShiftModifier
        self._drag = ("pan" if pan else "orbit", e.position())
        self.camera_input.emit()

    def mouseMoveEvent(self, e):
        if not self._drag:
            return
        kind, last = self._drag
        d = e.position() - last
        self._drag = (kind, e.position())
        (self.pan if kind == "pan" else self.orbit)(d.x(), d.y())

    def mouseReleaseEvent(self, e):
        self._drag = None

    def wheelEvent(self, e):
        d = e.angleDelta().y() / 120 or e.pixelDelta().y() / 40
        if d:
            self.zoom(d)
            self.camera_input.emit()

    # ---- video texture ---------------------------------------------------------------------
    def _on_video_params(self, name, val):
        if name == "mapview-video" and isinstance(val, dict) and val.get("dw") and val.get("dh"):
            size = (int(val["dw"]), int(val["dh"]))
            if size != self.video_size:
                self.video_size = size
                self.update()

    def unload(self):
        super().unload()
        self.has_frame = False
        self.update()

    def _ensure_texture(self, f) -> bool:
        if not self.video_size:
            return False
        w, h = self.video_size
        s = min(1.0, MAX_TEXTURE / max(w, h))
        size = (max(1, round(w * s)), max(1, round(h * s)))
        if self.fbo is None or size != self.tex_size:
            fmt = QOpenGLFramebufferObjectFormat()
            fmt.setAttachment(QOpenGLFramebufferObject.Attachment.NoAttachment)
            fmt.setInternalTextureFormat(GL_RGBA8)
            self.fbo = QOpenGLFramebufferObject(size[0], size[1], fmt)
            self.tex_size = size
            f.glBindTexture(GL_TEXTURE_2D, self.fbo.texture())
            for k, v in ((GL_TEXTURE_MIN_FILTER, GL_LINEAR_MIPMAP_LINEAR), (GL_TEXTURE_MAG_FILTER, GL_LINEAR),
                         (GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE), (GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)):
                f.glTexParameteri(GL_TEXTURE_2D, k, v)
            if self.context().hasExtension(b"GL_EXT_texture_filter_anisotropic"):
                f.glTexParameterf(GL_TEXTURE_2D, GL_TEXTURE_MAX_ANISOTROPY, 8.0)
            f.glGenerateMipmap(GL_TEXTURE_2D)
            self._fresh, self.has_frame = True, False
            self.texture_changed.emit((size[0], size[1], w, h))
        return True

    def _update_video(self, f) -> bool:
        """Render libmpv's waiting frame into the texture when it is due. True if one was drawn."""
        if not self.ctx or not self.loaded or not self._ensure_texture(f):
            return False
        if not (self.ctx.update() or self._fresh):
            return False
        nf = self.clock.next_frame(self.ctx)
        wait = self.clock.ms_until(nf[1]) if nf else None
        if wait is not None and wait > 1.0 and not self._fresh:
            if not self._due.isActive():
                self._due.start(max(1, int(wait)))           # back at its display time
            return False
        self.ctx.render(opengl_fbo={"w": self.tex_size[0], "h": self.tex_size[1], "fbo": self.fbo.handle()},
                        flip_y=True, block_for_target_time=wait is None)   # v = 1 is the top of the image
        f.glBindTexture(GL_TEXTURE_2D, self.fbo.texture())
        f.glGenerateMipmap(GL_TEXTURE_2D)
        self._fresh, self.has_frame = False, True
        if self.clock.reason and self.clock.reason != self.problem_text:
            self._report(self.clock.reason)
        return True

    def _report(self, text):
        self.problem_text = text
        self.problem.emit(text)

    # ---- GL --------------------------------------------------------------------------------
    def initializeGL(self):
        super().initializeGL()
        try:
            self.prog = QOpenGLShaderProgram(self)
            ok = (self.prog.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, VERT)
                  and self.prog.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, FRAG) and self.prog.link())
            if not ok:
                raise RuntimeError(self.prog.log() or "shader did not compile")
            self._draw = IndexedDraw(self.context())
            self.projection = ProjectionPass(self)
            img = QImage(1, 1, QImage.Format.Format_RGBA8888)
            img.fill(QColor(0, 0, 0, 0))
            self._blank = QOpenGLTexture(img)
        except Exception as e:
            self.prog = None
            self._report(f"The Mapping view cannot draw on this system: {e}")
        if self.ctx is None and self.m is None:
            self._report("libmpv is not available, so Mapping cannot play media.")

    def _sync_scene(self):
        """Meshes to match the scene: geometry already uploaded (the same object under the same id)
        is kept; anything else is released or built."""
        if not self._scene_dirty or self.prog is None:
            return
        want = self.scene.geometries if self.scene is not None else {}
        for gid in [g for g, (geo, _m) in self._meshes.items() if want.get(g) is not geo]:
            self._meshes.pop(gid)[1].destroy()
        new = [(gid, g) for gid, g in want.items() if gid not in self._meshes]
        if new:
            self.prog.bind()
            for gid, g in new:
                self._meshes[gid] = (g, _Mesh(g, self.prog))
        self._scene_dirty = False

    def paintGL(self):
        f = self.context().extraFunctions()
        t0 = time.perf_counter() if self.stats is not None else None
        drew = self._update_video(f) if self.clock else False
        self._sync_scene()                     # also releases the last meshes when the stage is emptied
        projecting = self.projecting() and self.projection is not None
        if projecting:
            aspect = self.projector.image_aspect(self.clip_aspect)
            if self.projection.prepare(f, self.projector, aspect, self.scene, self._geo_version, self._meshes, self._world, self._draw):
                self.prog.bind()               # the visibility pass used its own program and target
        f.glBindFramebuffer(GL_FRAMEBUFFER, self.defaultFramebufferObject())
        dpr = self.devicePixelRatioF()
        W, H = int(self.width() * dpr), int(self.height() * dpr)
        f.glViewport(0, 0, W, H)
        for cap in (GL_BLEND, GL_SCISSOR_TEST, GL_CULL_FACE):
            f.glDisable(cap)
        f.glEnable(GL_DEPTH_TEST)
        f.glClearColor(0.07, 0.07, 0.075, 1.0)
        f.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        if self.prog is not None and self.scene is not None:
            vp = self.view_projection(W, H)
            eye = self._eye()
            self.prog.bind()
            self.prog.setUniformValue("tex", 0)
            self.prog.setUniformValue("eye", eye)
            self.prog.setUniformValue1f("edge", UV_EDGE)
            self.prog.setUniformValue("video", 1 if (self.fbo is not None and self.has_frame and self.loaded) else 0)
            self.prog.setUniformValue("mode", 1 if self.mode == "projectors" else 0)
            self.prog.setUniformValue("lit", 1 if projecting else 0)
            self.prog.setUniformValue("pdepth", 1)
            if projecting:
                pp = self.projection
                self.prog.setUniformValue("pvp", pp.pvp)
                self.prog.setUniformValue("ppos", vec3(self.projector.position))
                for name, v in (("pnear", pp.near), ("pfar", pp.far), ("ptexel", pp.texel)):
                    self.prog.setUniformValue1f(name, v)
            f.glActiveTexture(GL_TEXTURE1)            # always something valid on the visibility sampler
            f.glBindTexture(GL_TEXTURE_2D, self.projection.texture() if projecting else self._blank.textureId())
            f.glActiveTexture(GL_TEXTURE0)
            f.glBindTexture(GL_TEXTURE_2D, self.fbo.texture() if self.fbo is not None else self._blank.textureId())
            tint = self.mode != "projectors"     # selection is a Surfaces thing
            for o in self.scene.objects:
                model, mirrored, _o = self._world[o.key]
                uv = self.assigned.get(o.key)
                self.prog.setUniformValue("mvp", vp * model)
                self.prog.setUniformValue("model", model)
                self.prog.setUniformValue("media", 1 if uv is not None else 0)
                self.prog.setUniformValue1f("selected", 1.0 if tint and o.key in self.selected else 0.0)   # 1f: the Python-float overload sets it as an int (GL_INVALID_OPERATION)
                f.glFrontFace(GL_CW if mirrored else GL_CCW)      # negative scale turns winding round
                for gid in o.geometries:
                    mesh = self._meshes.get(gid)
                    if mesh is None:
                        continue
                    mesh = mesh[1]
                    mesh.vao.bind()
                    mesh.use_uv(self.prog, uv)
                    self._draw(GL_TRIANGLES, mesh.count)
                    mesh.vao.release()
            f.glFrontFace(GL_CCW)
            p = self.projector
            if (self.mode == "projectors" and p is not None and p.show_frustum and p.problem() is None
                    and not self.looking_through and self.projection is not None):
                self.projection.draw_frustum(f, vp, p, p.image_aspect(self.clip_aspect))
        if t0 is not None:
            self.stats.append((t0, time.perf_counter() - t0, drew, self.cam_version))

    def shutdown(self):
        """GL objects go with the context current, then the render context and the core."""
        self._due.stop()
        if self.context() is not None and self.isValid():
            self.makeCurrent()
            for _g, mesh in self._meshes.values():
                mesh.destroy()
            self._meshes = {}
            self.fbo = None
            if self.projection is not None:
                self.projection.destroy()
                self.projection = None
            if self._blank is not None:
                self._blank.destroy()
                self._blank = None
            if self.prog is not None:
                self.prog.removeAllShaders()
                self.prog = None
            self.doneCurrent()
        super().shutdown()
