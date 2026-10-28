"""The Projectors inspector of the Mapping workspace: one virtual projector's controls, in two
groups (place and aim; image), with the view controls below. It only reflects the projector it is
given and says what the user asked for through signals; the Mapping screen sends every edit
through its editor (nitpix/ui/projedit.py), so each committed change is one undoable step.

Numbers are committed on Enter, on leaving the field (also by clicking the 3D view), or on an
arrow step, never per keystroke; Enter and Escape hand the keys back to the workspace, so Space
plays again. The scroll wheel never changes a number: it scrolls the panel.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QGridLayout, QHBoxLayout, QLabel, QSpinBox,
                               QStackedWidget, QVBoxLayout, QWidget)

from ..projector import FITS, MAX_RASTER, MAX_THROW, MIN_RASTER, MIN_THROW, RASTERS
from . import theme
from .review import btn
from .widgets import ElideLabel

HELP = ("A geometric preview: the clip cast from one pinhole projector onto every surface of the stage, UVs or not; "
        "nearer surfaces block it. No brightness, colour, lens shift, focus or calibration. Image sizes are for a "
        "flat surface square to the beam at that distance, not the size on a tilted or curved surface.")
FIT_TEXT = {"fit": "Fit (keep the clip's shape)", "stretch": "Stretch to fill"}


def _commit_keys(box, ev) -> bool:
    """Enter commits and Escape drops the typed text; both give the keys back (box.done)."""
    if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Escape):
        if ev.key() == Qt.Key.Key_Escape:
            box.setValue(box.value())
        else:
            box.interpretText()
        box.done.emit()
        return True
    return False


class _Spin(QDoubleSpinBox):
    """A number committed on Enter / leaving / stepping. Enter and Escape give the keys back."""
    done = Signal()

    def __init__(self, lo, hi, decimals, step, suffix=""):
        super().__init__()
        self.setRange(lo, hi)
        self.setDecimals(decimals)
        self.setSingleStep(step)
        self.setSuffix(suffix)
        self.setKeyboardTracking(False)
        self.setAccelerated(True)
        self.setMinimumWidth(52)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)     # not by the wheel: scrolling the panel never edits a number

    def wheelEvent(self, ev):
        ev.ignore()                                          # the panel scrolls; numbers change by typing or the arrows

    def keyPressEvent(self, ev):
        if not _commit_keys(self, ev):
            super().keyPressEvent(ev)


class _Int(QSpinBox):
    done = Signal()

    def __init__(self, lo, hi):
        super().__init__()
        self.setRange(lo, hi)
        self.setKeyboardTracking(False)
        self.setMinimumWidth(60)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, ev):
        ev.ignore()

    def keyPressEvent(self, ev):
        if not _commit_keys(self, ev):
            super().keyPressEvent(ev)


def _head(text):
    lab = QLabel(text.upper())
    lab.setProperty("faint", True)
    lab.setStyleSheet("font-weight: 600; letter-spacing: 1px; padding-top: 6px;")
    return lab


class ProjectorPanel(QWidget):
    add_requested = Signal()                         # Add projector (where the view's camera is)
    pick_requested = Signal()                        # Pick target in the view
    place_requested = Signal()                       # Place at view: the pose only
    match_requested = Signal()                       # Match view: pose and throw
    remove_requested = Signal()
    look_requested = Signal(bool)
    undo_requested = Signal()
    redo_requested = Signal()
    changed = Signal(dict, str)                      # fields of the Projector to replace, what the edit is called
    keep_aim_changed = Signal(bool)
    grid_changed = Signal(bool)
    frame_requested = Signal()                       # frame the projector and its target in the view
    done_editing = Signal()                          # Enter / Escape in a number: back to the workspace
    add_models_requested = Signal()

    def __init__(self):
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        self.pages = QStackedWidget()
        root.addWidget(self.pages, 1)

        # 0: no stage / 1: no projector
        for text, label, sig in (("No stage yet. Add the stage's model files; the projector lights all of their surfaces.",
                                  "Add models…", self.add_models_requested),
                                 ("No projector yet. Add one where the view's camera is now, aimed where it looks, "
                                  "then pick the surface to aim at and set its throw ratio.", "Add projector", self.add_requested)):
            page = QWidget()
            pl = QVBoxLayout(page)
            pl.setContentsMargins(0, 8, 0, 8)
            lab = QLabel(text)
            lab.setWordWrap(True)
            lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lab.setProperty("muted", True)
            pl.addWidget(lab)
            b = btn(label, "", sig.emit, primary=True)
            pl.addWidget(b, 0, Qt.AlignmentFlag.AlignHCenter)
            if sig is self.add_requested:
                self.add_btn = b
                self.undo_empty = btn("Undo", "", self.undo_requested.emit, ghost=True)
                pl.addWidget(self.undo_empty, 0, Qt.AlignmentFlag.AlignHCenter)
            pl.addStretch(1)
            self.pages.addWidget(page)

        # 2: the projector
        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(0, 2, 0, 8)
        pl.setSpacing(8)
        top = QHBoxLayout()
        title = QLabel("Projector 1")
        title.setStyleSheet("font-weight: 600;")
        title.setToolTip(HELP)
        top.addWidget(title)
        top.addStretch(1)
        self.undo_btn = btn("Undo", "Undo the last projector edit (⌘Z)", self.undo_requested.emit, ghost=True)
        self.redo_btn = btn("Redo", "Redo (⇧⌘Z)", self.redo_requested.emit, ghost=True)
        top.addWidget(self.undo_btn)
        top.addWidget(self.redo_btn)
        pl.addLayout(top)

        pl.addWidget(_head("Place and aim"))
        self.pick_btn = btn("Pick target", "Then click a surface in the view: the projector turns to aim there "
                            "(its position and roll stay). Esc cancels.", self.pick_requested.emit, primary=True)
        pl.addWidget(self.pick_btn)
        acts = QHBoxLayout()
        acts.setSpacing(6)
        self.place_btn = btn("Place at view", "Move the projector to the view's camera, aimed where the view looks. "
                             "The pose only: throw ratio, raster and roll stay.", self.place_requested.emit)
        acts.addWidget(self.place_btn, 1)
        self.match_btn = btn("Match view", "Place at the view's camera and set the throw ratio so the image fills "
                             "the view (changes the lens setting too).", self.match_requested.emit)
        acts.addWidget(self.match_btn, 1)
        pl.addLayout(acts)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        for c, axis in enumerate("XYZ"):
            h = QLabel(axis)
            h.setProperty("faint", True)
            h.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(h, 0, c + 1)
        self.pos = [_Spin(-10000, 10000, 2, 0.1, " m") for _ in range(3)]
        self.tgt = [_Spin(-10000, 10000, 2, 0.1, " m") for _ in range(3)]
        for row, (name, spins, field, tip) in enumerate((
                ("Body", self.pos, "position", "Where the projector's lens is, in the stage's metres (Y up). "
                 "Drag the coloured arrows in the view to move it along one axis."),
                ("Target", self.tgt, "target", "The point it aims at: the middle of the image lands there. "
                 "Pick target sets it from a surface."))):
            lab = QLabel(name)
            lab.setToolTip(tip)
            grid.addWidget(lab, row + 1, 0)
            for c, s in enumerate(spins):
                s.setToolTip(tip)
                s.valueChanged.connect(lambda _v, f=field, ss=spins, a="XYZ"[c]: self.changed.emit(
                    {f: tuple(x.value() for x in ss)}, f"Move {a}" if f == "position" else f"Target {a}"))
                s.done.connect(self.done_editing.emit)
                grid.addWidget(s, row + 1, c + 1)
        grid.addWidget(QLabel("Roll"), 3, 0)
        self.roll = _Spin(-180, 180, 1, 1.0, "°")
        self.roll.setToolTip("Turns the image about the aim; positive turns its top to the right, seen from behind "
                             "the projector. 0 keeps the image's top edge level (towards +Y).")
        self.roll.valueChanged.connect(lambda v: self.changed.emit({"roll": v}, "Roll"))
        self.roll.done.connect(self.done_editing.emit)
        grid.addWidget(self.roll, 3, 1)
        self.keep_aim = QCheckBox("Keep aim when moving")
        self.keep_aim.setToolTip("Off: moving the body moves the target with it (the projector keeps pointing the same "
                                 "way). On: the target stays put and the projector turns to keep aiming at it.")
        self.keep_aim.toggled.connect(self.keep_aim_changed.emit)
        grid.addWidget(self.keep_aim, 4, 1, 1, 3)
        for c in range(1, 4):
            grid.setColumnStretch(c, 1)
        pl.addLayout(grid)

        pl.addWidget(_head("Image"))
        img = QGridLayout()
        img.setHorizontalSpacing(8)
        img.setVerticalSpacing(8)
        img.addWidget(QLabel("Throw ratio"), 0, 0)
        self.throw = _Spin(MIN_THROW, MAX_THROW, 3, 0.05, " : 1")
        self.throw.setToolTip("Distance ÷ image width. At 6 m, 1.5 : 1 gives a 4 m wide image.")
        self.throw.valueChanged.connect(lambda v: self.changed.emit({"throw": v}, "Throw ratio"))
        self.throw.done.connect(self.done_editing.emit)
        img.addWidget(self.throw, 0, 1)
        self.fov_lab = QLabel()
        self.fov_lab.setProperty("muted", True)
        self.fov_lab.setToolTip("The horizontal angle follows from the throw ratio.")
        img.addWidget(self.fov_lab, 0, 2, 1, 2)
        img.addWidget(QLabel("Native raster"), 1, 0)
        self.raster_box = QComboBox()
        for w, h in RASTERS:
            self.raster_box.addItem(f"{w} × {h}", f"{w}x{h}")          # (a tuple would not survive as item data)
        self.raster_box.addItem("Custom", "")
        self.raster_box.setToolTip("The projector's own pixels: the beam's shape. Changing the clip never changes it.")
        self.raster_box.activated.connect(self._raster_chosen)
        img.addWidget(self.raster_box, 1, 1, 1, 3)
        self.rw, self.rh = _Int(MIN_RASTER, MAX_RASTER), _Int(MIN_RASTER, MAX_RASTER)
        for sp in (self.rw, self.rh):
            sp.setToolTip("Native raster width and height in pixels")
            sp.valueChanged.connect(lambda _v: self.changed.emit({"raster": (self.rw.value(), self.rh.value()), "raster_note": ""},
                                                                 "Native raster"))
            sp.done.connect(self.done_editing.emit)
        px = QLabel("W × H px")
        px.setProperty("faint", True)
        img.addWidget(px, 2, 0)
        img.addWidget(self.rw, 2, 1)
        img.addWidget(self.rh, 2, 2)
        img.addWidget(QLabel("Content"), 3, 0)
        self.fit_box = QComboBox()
        for k in FITS:
            self.fit_box.addItem(FIT_TEXT[k], k)
        self.fit_box.setToolTip("How the clip sits in the raster. Fit keeps its shape; the bars beside or above it "
                                "project nothing. Stretch fills the raster.")
        self.fit_box.activated.connect(lambda _i: self.changed.emit({"fit": self.fit_box.currentData()}, "Content fit"))
        img.addWidget(self.fit_box, 3, 1, 1, 3)
        for c in range(1, 4):
            img.setColumnStretch(c, 1)
        pl.addLayout(img)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setFont(theme.mono(11))
        self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        pl.addWidget(self.info)
        self.hit = QLabel()
        self.hit.setWordWrap(True)
        self.hit.setProperty("muted", True)
        pl.addWidget(self.hit)
        self.problem = QLabel()
        self.problem.setWordWrap(True)
        self.problem.setStyleSheet(f"color: {theme.T['warn']};")
        self.problem.hide()
        pl.addWidget(self.problem)

        pl.addWidget(_head("View"))
        vr = QHBoxLayout()
        vr.setSpacing(6)
        self.look_btn = btn("Look through", "See the stage from the projector. Navigation is paused until you exit "
                            "(Esc); your view comes back exactly as it was.",
                            lambda: self.look_requested.emit(self.look_btn.property("looking") is not True))
        vr.addWidget(self.look_btn, 1)
        self.frame_btn = btn("Frame it", "Fit the projector and its target in the view", self.frame_requested.emit)
        vr.addWidget(self.frame_btn, 1)
        pl.addLayout(vr)
        checks = QHBoxLayout()
        checks.setSpacing(10)
        self.enabled = QCheckBox("Projection")
        self.enabled.setToolTip("Off: the stage shows neutral grey, and the projector's settings are kept.")
        self.enabled.toggled.connect(lambda on: self.changed.emit({"enabled": on}, "Show projection" if on else "Hide projection"))
        self.frustum = QCheckBox("Frustum")
        self.frustum.setToolTip("The beam's edges and the image outline at the target's distance (they light nothing).")
        self.frustum.toggled.connect(lambda on: self.changed.emit({"show_frustum": on}, "Show frustum" if on else "Hide frustum"))
        self.grid_chk = QCheckBox("Grid")
        self.grid_chk.setToolTip("A grid in eighths, the centre cross and the image edge, drawn over the projected "
                                 "clip to judge framing. Not saved; the clip is unchanged.")
        self.grid_chk.toggled.connect(self.grid_changed.emit)
        for w in (self.enabled, self.frustum, self.grid_chk):
            checks.addWidget(w)
        checks.addStretch(1)
        pl.addLayout(checks)
        self.remove_btn = btn("Remove projector", "The stage, its files and the UV assignments are not affected. "
                              "Undo brings it back.", self.remove_requested.emit, ghost=True)
        pl.addWidget(self.remove_btn, 0, Qt.AlignmentFlag.AlignLeft)
        note = ElideLabel("Saved with the project's Mapping setup.")
        note.setProperty("faint", True)
        note.setToolTip(HELP)
        pl.addWidget(note)
        pl.addStretch(1)
        self.pages.addWidget(page)
        self.show_state(False, None, {})

    def _raster_chosen(self, _i):
        r = self.raster_box.currentData()
        if not r:                                      # Custom: type the numbers
            self.rw.setFocus()
            self.rw.selectAll()
            return
        self.changed.emit({"raster": tuple(int(n) for n in r.split("x")), "raster_note": ""}, "Native raster")

    def show_state(self, has_stage, projector, info):
        """Fill the panel from the state (signals held back: this is not an edit). `info`: centre-ray
        facts, undo state and visibility size from the screen; `refused` says why an edit was not made."""
        self.undo_empty.setVisible(bool(info.get("can_undo")))
        self.undo_empty.setText(f"Undo {info.get('undo_text', '')}".strip())
        if not has_stage:
            self.pages.setCurrentIndex(0)
            return
        if projector is None:
            self.pages.setCurrentIndex(1)
            return
        self.pages.setCurrentIndex(2)
        p = projector
        widgets = [self.enabled, self.frustum, self.throw, self.roll, self.rw, self.rh, self.raster_box, self.fit_box,
                   *self.pos, *self.tgt]
        for w in widgets:
            w.blockSignals(True)
        self.enabled.setChecked(p.enabled)
        self.frustum.setChecked(p.show_frustum)
        for s, v in zip(self.pos, p.position):
            s.setValue(v)
        for s, v in zip(self.tgt, p.target):
            s.setValue(v)
        self.roll.setValue(p.roll)
        self.throw.setValue(p.throw)
        self.rw.setValue(p.raster[0])
        self.rh.setValue(p.raster[1])
        i = self.raster_box.findData(f"{p.raster[0]}x{p.raster[1]}")
        self.raster_box.setCurrentIndex(i if i >= 0 and not p.raster_note else self.raster_box.count() - 1)
        self.fit_box.setCurrentIndex(max(0, self.fit_box.findData(p.fit)))
        for w in widgets:
            w.blockSignals(False)
        self.fov_lab.setText(f"{p.h_fov:.1f}° wide")
        problem = info.get("refused") or p.problem()
        self.problem.setText(problem or "")
        self.problem.setVisible(bool(problem))
        if p.problem():
            self.info.setText("")
            self.hit.setText("")
        else:
            d = p.distance()
            w, h = p.image_size()
            lines = [f"{d:.2f} m to the target", f"Image there {w:.2f} × {h:.2f} m · {w / p.raster[0] * 1000:.1f} mm per pixel"]
            if p.raster_note == "inferred":
                lines.append(f"Raster {p.raster[0]} × {p.raster[1]}: custom, inferred from an earlier preview")
            self.info.setText("\n".join(lines))
            self.info.setToolTip("Ideal size on a flat surface square to the beam at the target's distance; a tilted or "
                                 "curved surface shows a different size." + (f"\nVisibility texture {info['vis'][0]}×{info['vis'][1]} "
                                 "(shadow detail)." if info.get("vis") else ""))
            c = info.get("centre")
            if c is None:
                self.hit.setText("")
            elif c.get("miss"):
                self.hit.setText("The middle of the beam meets no surface: it is not aimed at the stage. Pick target to aim it.")
            else:
                gap = abs(c["distance"] - d)
                self.hit.setText(f"The middle of the beam meets {c['name']} at {c['distance']:.2f} m"
                                 + ("." if gap < 0.01 else f" ({'nearer' if c['distance'] < d else 'farther'} than the target)."))
        self.undo_btn.setEnabled(bool(info.get("can_undo")))
        self.redo_btn.setEnabled(bool(info.get("can_redo")))
        self.undo_btn.setToolTip(f"Undo {info['undo_text']} (⌘Z)" if info.get("undo_text") else "Undo the last projector edit (⌘Z)")
        self.redo_btn.setToolTip(f"Redo {info['redo_text']} (⇧⌘Z)" if info.get("redo_text") else "Redo (⇧⌘Z)")
        for b in (self.look_btn, self.pick_btn, self.frame_btn):
            b.setEnabled(not p.problem())

    def set_looking(self, on):
        self.look_btn.setProperty("looking", bool(on))
        self.look_btn.setText("Exit look-through" if on else "Look through")

    def set_picking(self, on):
        self.pick_btn.setText("Picking… (Esc)" if on else "Pick target")
