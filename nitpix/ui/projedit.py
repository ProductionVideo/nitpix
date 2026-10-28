"""Editing the Mapping projector: one command path with undo, and the viewport tool (pick a target,
drag the move arrows). The Mapping screen owns an editor and a tool; the panel and the view only
reflect them.

ProjectorEditor holds the committed projector (what is saved) and, during a drag, a preview (what
is shown). Every edit goes through commit(): it is checked, pushed onto a QUndoStack as a small
before/after pair of Projector values (never the stage), applied, and reported once through
`committed`, which the screen saves. Undo and redo use the same path. A preview is shown only;
cancelling it restores the committed projector, and nothing transient is ever saved. reset()
starts a new history (another project, a restored setup); nothing from the old one can be replayed.

ViewportTool is the view's tool (nitpix/ui/mapview.py): it sees presses, moves and releases first.

- Move arrows (shown on the projector in the Projectors tab, not while looking through): hovering
  one highlights it; pressing on it starts a drag along that world axis, shown as a preview with
  its position; releasing commits one edit; Escape or another button cancels to where it was. The
  body keeps its orientation (the target moves with it) unless "keep aim" is on, when the target
  stays and the projector turns to it. A press anywhere else is navigation as usual.
- Pick target: hovering asks the GPU for the nearest surface under the cursor (at most about 30
  times a second), shows a marker there and says what it is, how far from the projector, and
  whether something nearer blocks the projector's line to it. A click (a press that does not move
  more than CLICK_PX) on a surface aims there: position and roll stay; one edit; pick mode ends. A
  click on empty space changes nothing and says so. A drag is navigation. Escape cancels.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QObject, QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QUndoCommand, QUndoStack

CLICK_PX = 4.0
GRAB_PX = 9.0                 # how near an arrow a press must be to take it
HOVER_MS = 33                 # pick queries at most this often


class _Edit(QUndoCommand):
    def __init__(self, editor, before, after, text):
        super().__init__(text)
        self.editor, self.before, self.after = editor, before, after

    def redo(self):
        self.editor._apply(self.after)

    def undo(self):
        self.editor._apply(self.before)


class ProjectorEditor(QObject):
    changed = Signal()                 # what is shown changed (committed, undone, previewed, cancelled)
    committed = Signal()               # the committed projector changed: save it

    def __init__(self, parent=None):
        super().__init__(parent)
        self.stack = QUndoStack(self)
        self.state = None              # the committed Projector (or None)
        self.preview = None            # shown instead while a drag is under way

    @property
    def shown(self):
        return self.preview if self.preview is not None else self.state

    def commit(self, new, text) -> str | None:
        """Make `new` the projector as one undoable edit. Returns why it was refused (and nothing
        changes), or None. Committing the same value does nothing."""
        self.preview = None
        if new is not None and new.problem():
            self.changed.emit()        # the fields go back to the committed values
            return new.problem()
        if new == self.state:
            self.changed.emit()
            return None
        self.stack.push(_Edit(self, self.state, new, text))
        self.changed.emit()            # again: the stack's index (undo text) moves after the command runs
        return None

    def _apply(self, p):
        self.state, self.preview = p, None
        self.changed.emit()
        self.committed.emit()

    def show_preview(self, p):
        self.preview = p
        self.changed.emit()

    def cancel_preview(self):
        if self.preview is not None:
            self.preview = None
            self.changed.emit()

    def reset(self, p):
        """A new history from `p` (not an edit: nothing is saved)."""
        self.stack.clear()
        self.state, self.preview = p, None
        self.changed.emit()

    def undo(self):
        self.preview = None
        self.stack.undo()
        self.changed.emit()

    def redo(self):
        self.preview = None
        self.stack.redo()
        self.changed.emit()


def _dist_to_segment(p: QPointF, a: QPointF, b: QPointF) -> float:
    ab, ap = b - a, p - a
    L = ab.x() ** 2 + ab.y() ** 2
    t = 0.0 if L == 0 else max(0.0, min(1.0, (ap.x() * ab.x() + ap.y() * ab.y()) / L))
    q = a + ab * t
    return math.hypot(p.x() - q.x(), p.y() - q.y())


class ViewportTool(QObject):
    mode_changed = Signal(str)         # "idle", "pick", "drag"
    feedback = Signal(str, str)        # text for the view's banner, kind: "info" / "hit" / "miss" / "warn" / ""

    def __init__(self, view, editor: ProjectorEditor, name_of, parent=None):
        super().__init__(parent)
        self.view, self.editor, self.name_of = view, editor, name_of
        self.mode = "idle"
        self.keep_aim = False
        self.enabled = False           # the Projectors tab, with a projector, not looking through
        self._press = None             # (position, button) of a press in pick mode
        self._drag = None              # (axis, start point, start projector, axis on screen, metres per unit)
        self._hover_pos = None
        self.hover = None              # the last pick query: dict
        self._timer = QTimer(self, singleShot=True)
        self._timer.timeout.connect(self._query)

    # ---- modes -------------------------------------------------------------------------
    def set_enabled(self, on):
        on = bool(on)
        if on != self.enabled:
            self.enabled = on
            if not on:
                self.cancel()
            self._gizmo()

    def start_pick(self):
        if self.editor.state is None:
            return
        self.cancel()
        self._set_mode("pick")
        self.feedback.emit("Pick target: click a surface to aim the projector there · Esc cancels", "info")
        self.view.setCursor(Qt.CursorShape.CrossCursor)

    def cancel(self) -> bool:
        """Escape: leave pick mode or drop a drag (back to the committed projector). True if anything stopped."""
        was = self.mode
        if self._drag is not None:
            self._drag = None
            self.editor.cancel_preview()
        self._press = None
        self._timer.stop()
        self.hover = None
        if self.view.hit_marker is not None:
            self.view.hit_marker = None
            self.view.update()
        self.view.unsetCursor()
        self._set_mode("idle")
        if was != "idle":
            self.feedback.emit("Cancelled: nothing changed" if was == "drag" else "", "")
        self._gizmo()
        return was != "idle"

    def _set_mode(self, m):
        if m != self.mode:
            self.mode = m
            self.mode_changed.emit(m)

    def _gizmo(self):
        v = self.view
        show = self.enabled and self.mode != "pick" and self.editor.state is not None and not v.looking_through
        if show != v.gizmo:
            v.gizmo = show
            if not show:
                v.gizmo_active = None
            v.update()

    # ---- events from the view --------------------------------------------------------------
    def press(self, e) -> bool:
        if not self.enabled or self.view.looking_through:
            return False
        if self._drag is not None:                         # another button during a drag: cancel it
            self.cancel()
            return True
        if self.mode == "pick":
            if e.button() == Qt.MouseButton.LeftButton:
                self._press = e.position()
            return False                                   # a drag from here is still navigation
        if e.button() == Qt.MouseButton.LeftButton and self.view.gizmo:
            axis = self._arrow_at(e.position())
            if axis:
                self._begin_drag(axis, e.position())
                return True
        return False

    def move(self, e, navigating=False) -> bool:
        if self._drag is not None:
            self._drag_to(e.position())
            return True
        if not self.enabled:
            return False
        if self.mode == "pick" and not navigating:
            self._hover_pos = e.position()
            if not self._timer.isActive():
                self._timer.start(HOVER_MS)
        elif self.mode == "idle" and not navigating and self.view.gizmo:
            axis = self._arrow_at(e.position())
            if axis != self.view.gizmo_active:
                self.view.gizmo_active = axis
                self.view.setCursor(Qt.CursorShape.SizeAllCursor if axis else Qt.CursorShape.ArrowCursor)
                self.view.update()
        return False

    def release(self, e):
        if self._drag is not None and e.button() == Qt.MouseButton.LeftButton:
            self._end_drag()
            return
        if self.mode == "pick" and self._press is not None and e.button() == Qt.MouseButton.LeftButton:
            moved = math.hypot(e.position().x() - self._press.x(), e.position().y() - self._press.y())
            self._press = None
            if moved <= CLICK_PX:
                self._click(e.position())

    def leave(self):
        if self.mode == "pick" and self.view.hit_marker is not None:
            self._timer.stop()
            self.view.hit_marker = None
            self.view.update()
            self.feedback.emit("Pick target: click a surface to aim the projector there · Esc cancels", "info")
        if self.mode == "idle" and self.view.gizmo_active:
            self.view.gizmo_active = None
            self.view.update()

    # ---- picking ------------------------------------------------------------------------------
    def probe(self, pos):
        """What is under this point of the view: {"point", "key", "name", "distance", "blocked_by"}, or
        {"miss": True}. Two GPU ray queries: from the camera, then from the projector to that point."""
        o, d = self.view.ray_at(pos)
        hit = self.view.query_ray(o, d)
        if hit is None:
            return {"miss": True}
        point, key = hit
        p = self.editor.state
        out = {"point": point, "key": key, "name": self.name_of(key), "distance": None, "blocked_by": None}
        if p is not None:
            dist = math.dist(p.position, point)
            out["distance"] = dist
            if dist > 1e-6:
                ray = tuple((point[i] - p.position[i]) / dist for i in range(3))
                first = self.view.query_ray(p.position, ray, far=dist * 1.01 + 0.01)
                if first is not None:
                    gap = math.dist(p.position, first[0])
                    if gap < dist - max(0.01, dist * 0.002):
                        out["blocked_by"] = self.name_of(first[1])
        return out

    def _query(self):
        if self.mode != "pick" or self._hover_pos is None:
            return
        h = self.hover = self.probe(self._hover_pos)
        if h.get("miss"):
            self.view.hit_marker = None
            self.feedback.emit("No surface here: a click would change nothing", "miss")
        else:
            self.view.hit_marker = h["point"]
            where = f"{h['name']} · {h['distance']:.2f} m from the projector" if h["distance"] is not None else h["name"]
            if h["blocked_by"]:
                self.feedback.emit(f"{where} · blocked from the projector by {h['blocked_by']}", "warn")
            else:
                self.feedback.emit(f"{where} · clear line from the projector · click to aim", "hit")
        self.view.update()

    def _click(self, pos):
        h = self.probe(pos)
        if h.get("miss"):
            self.view.hit_marker = None
            self.view.update()
            self.feedback.emit("No surface there: nothing changed. Click a surface, or Esc to cancel", "miss")
            return
        p = self.editor.state
        why = self.editor.commit(p.aimed_at(h["point"]), f"Aim at {h['name']}")
        self.cancel()
        if why:
            self.feedback.emit(f"Not aimed: {why}", "warn")
        else:
            note = f" (blocked from the projector by {h['blocked_by']})" if h["blocked_by"] else ""
            self.feedback.emit(f"Aimed at {h['name']}, {h['distance']:.2f} m away{note}", "hit")

    # ---- the move arrows ----------------------------------------------------------------------
    def _arrow_at(self, pos):
        best, axis = GRAB_PX, None
        for a in ("x", "y", "z"):
            seg = self.view.arrow_on_screen(a)
            if seg is None:
                continue
            if math.hypot(seg[1].x() - seg[0].x(), seg[1].y() - seg[0].y()) < 12:   # pointing at the camera: not usable
                continue
            d = _dist_to_segment(pos, *seg)
            if d < best:
                best, axis = d, a
        return axis

    def _begin_drag(self, axis, pos):
        from .helpers import AXES
        p = self.editor.state
        a = AXES[axis]
        unit = max(1e-6, self.view.world_per_px(p.position) * 100)          # 1 unit: 100 px worth of metres there
        s0 = self.view.to_screen(p.position)
        s1 = self.view.to_screen(tuple(p.position[i] + a[i] * unit for i in range(3)))
        if s0 is None or s1 is None:
            return
        self._drag = (axis, pos, p, s1 - s0, unit)
        self.view.gizmo_active = axis
        self._set_mode("drag")
        self._drag_to(pos)

    def _drag_to(self, pos):
        from .helpers import AXES
        axis, start, p, screen_axis, unit = self._drag
        d = pos - start
        L2 = screen_axis.x() ** 2 + screen_axis.y() ** 2
        t = (d.x() * screen_axis.x() + d.y() * screen_axis.y()) / L2 * unit if L2 > 0 else 0.0
        a = AXES[axis]
        new = p.moved(tuple(a[i] * t for i in range(3)), keep_aim=self.keep_aim)
        self.editor.show_preview(new)
        i = "xyz".index(axis)
        self.feedback.emit(f"Moving {axis.upper()}: {t:+.2f} m → {axis.upper()} {new.position[i]:.2f} m"
                           + (" · keeping aim on the target" if self.keep_aim else "")
                           + " · release to place, Esc cancels", "info")

    def _end_drag(self):
        axis, _start, p, _sa, _u = self._drag
        new = self.editor.preview
        self._drag = None
        self._set_mode("idle")
        if new is None or new == p:
            self.editor.cancel_preview()
            self.feedback.emit("", "")
            return
        why = self.editor.commit(new, f"Move {axis.upper()}")
        i = "xyz".index(axis)
        self.feedback.emit(f"Not moved: {why}" if why else f"Moved {axis.upper()} to {new.position[i]:.2f} m", "warn" if why else "")
