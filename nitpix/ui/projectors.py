"""The Projectors tab of the Mapping workspace: one virtual projector's controls. It only edits a
nitpix.projector.Projector and says so through signals; the Mapping screen owns the state, hands
it to the view and decides what happens to it on project changes. Numbers are committed on Enter,
on leaving the field or on an arrow step, never per keystroke; Enter and Escape hand the keys
back to the workspace (so Space plays again).
"""
from __future__ import annotations

import math

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QDoubleSpinBox, QGridLayout, QHBoxLayout, QLabel, QStackedWidget, QVBoxLayout,
                               QWidget)

from ..projector import MAX_FOV, MIN_FOV
from . import theme
from .review import btn
from .widgets import ElideLabel

INTRO = ("Casts the clip from one virtual projector onto every surface of the stage, with or without UVs; "
         "nearer surfaces block it. A geometric preview: no brightness, colour, lens or calibration data.")


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
        self.setMinimumWidth(60)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)     # not by the wheel: scrolling the panel never edits a number

    def wheelEvent(self, ev):
        ev.ignore()                                          # the panel scrolls; numbers change by typing or the arrows

    def keyPressEvent(self, ev):
        if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Escape):
            if ev.key() == Qt.Key.Key_Escape:
                self.setValue(self.value())          # drop the uncommitted text
            else:
                self.interpretText()
            self.done.emit()
            return
        super().keyPressEvent(ev)


class ProjectorPanel(QWidget):
    add_requested = Signal()                         # Add projector (from the current view)
    from_view_requested = Signal()
    remove_requested = Signal()
    look_requested = Signal(bool)
    changed = Signal(dict)                           # fields of the Projector to replace
    done_editing = Signal()                          # Enter / Escape in a number: back to the workspace
    add_models_requested = Signal()

    def __init__(self):
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 4, 4)       # as the Surfaces tab (nitpix/ui/mapping.py PANE_MARGINS)
        root.setSpacing(10)
        intro = QLabel(INTRO)
        intro.setWordWrap(True)
        intro.setProperty("muted", True)
        root.addWidget(intro)
        self.pages = QStackedWidget()
        root.addWidget(self.pages, 1)

        # 0: no stage / 1: no projector
        for text, label, sig in (("No stage yet. Add the stage's model files; the projector lights all of their surfaces.",
                                  "Add models…", self.add_models_requested),
                                 ("No projector. Add one where the view's camera is now, aimed where it looks; "
                                  "then set its position, target and field of view.", "Add projector", self.add_requested)):
            page = QWidget()
            pl = QVBoxLayout(page)
            pl.addStretch(1)
            lab = QLabel(text)
            lab.setWordWrap(True)
            lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lab.setProperty("muted", True)
            pl.addWidget(lab)
            b = btn(label, "", sig.emit, primary=True)
            pl.addWidget(b, 0, Qt.AlignmentFlag.AlignHCenter)
            pl.addStretch(2)
            self.pages.addWidget(page)
            if sig is self.add_requested:
                self.add_btn = b

        # 2: the projector
        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(0, 4, 0, 0)
        pl.setSpacing(10)
        checks = QHBoxLayout()
        checks.setSpacing(18)
        self.enabled = QCheckBox("Show projection")
        self.enabled.setToolTip("Off: the stage shows neutral grey here, and the projector's settings are kept.")
        self.enabled.toggled.connect(lambda on: self.changed.emit({"enabled": on}))
        self.frustum = QCheckBox("Show frustum")
        self.frustum.setToolTip("The projector's lines and image outline at the target's distance (they light nothing).")
        self.frustum.toggled.connect(lambda on: self.changed.emit({"show_frustum": on}))
        checks.addWidget(self.enabled)
        checks.addWidget(self.frustum)
        checks.addStretch(1)
        pl.addLayout(checks)

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
                ("Position", self.pos, "position", "Where the projector's lens is, in the stage's metres (Y up)."),
                ("Target", self.tgt, "target", "The point it aims at: the middle of the image lands here."))):
            lab = QLabel(name)
            lab.setToolTip(tip)
            grid.addWidget(lab, row + 1, 0)
            for c, s in enumerate(spins):
                s.setToolTip(tip)
                s.valueChanged.connect(lambda _v, f=field, ss=spins: self.changed.emit({f: tuple(x.value() for x in ss)}))
                s.done.connect(self.done_editing.emit)
                grid.addWidget(s, row + 1, c + 1)
        grid.addWidget(QLabel("Field of view"), 3, 0)
        self.fov = _Spin(MIN_FOV, MAX_FOV, 1, 1.0, "°")
        self.fov.setToolTip("The image's full horizontal angle. The throw ratio below follows from it.")
        self.fov.valueChanged.connect(lambda v: self.changed.emit({"h_fov": v}))
        self.fov.done.connect(self.done_editing.emit)
        grid.addWidget(self.fov, 3, 1)
        grid.addWidget(QLabel("Aspect"), 4, 0)
        self.aspect = _Spin(0.1, 10.0, 3, 0.01)
        self.aspect.setToolTip("Image width / height. From the clip, it follows whichever clip is chosen.")
        self.aspect.valueChanged.connect(lambda v: self.changed.emit({"aspect": v}) if not self.from_clip.isChecked() else None)
        self.aspect.done.connect(self.done_editing.emit)
        grid.addWidget(self.aspect, 4, 1)
        self.from_clip = QCheckBox("From clip")
        self.from_clip.toggled.connect(lambda on: self.changed.emit({"aspect": None if on else self.aspect.value()}))
        grid.addWidget(self.from_clip, 4, 2, 1, 2)
        for c in range(1, 4):
            grid.setColumnStretch(c, 1)
        pl.addLayout(grid)

        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setProperty("muted", True)
        self.info.setFont(theme.mono(11))
        pl.addWidget(self.info)
        self.problem = QLabel()
        self.problem.setWordWrap(True)
        self.problem.setStyleSheet(f"color: {theme.T['warn']};")
        self.problem.hide()
        pl.addWidget(self.problem)
        acts = QHBoxLayout()
        self.view_btn = btn("Create from current view", "Move the projector to the view's camera, aimed where it looks, "
                            "with the largest image of this aspect that fits the view.", self.from_view_requested.emit)
        acts.addWidget(self.view_btn, 1)
        self.look_btn = btn("Look through projector", "See the stage from the projector. Drag, scroll or F return to the orbit view.",
                            lambda: self.look_requested.emit(self.look_btn.property("looking") is not True))
        acts.addWidget(self.look_btn, 1)
        pl.addLayout(acts)
        self.remove_btn = btn("Remove projector", "The stage, its files and the UV assignments are not affected.",
                              self.remove_requested.emit, ghost=True)
        pl.addWidget(self.remove_btn)
        pl.addStretch(1)
        self.pages.addWidget(page)
        note = ElideLabel("Saved with the project's Mapping setup.")
        note.setProperty("faint", True)
        root.addWidget(note)
        self.show_state(False, None, None, None)

    def show_state(self, has_stage, projector, clip_aspect, visibility):
        """Fill the panel from the state (signals held back: this is not an edit)."""
        if not has_stage:
            self.pages.setCurrentIndex(0)
            return
        if projector is None:
            self.pages.setCurrentIndex(1)
            return
        self.pages.setCurrentIndex(2)
        widgets = [self.enabled, self.frustum, self.fov, self.aspect, self.from_clip, *self.pos, *self.tgt]
        for w in widgets:
            w.blockSignals(True)
        self.enabled.setChecked(projector.enabled)
        self.frustum.setChecked(projector.show_frustum)
        for s, v in zip(self.pos, projector.position):
            s.setValue(v)
        for s, v in zip(self.tgt, projector.target):
            s.setValue(v)
        self.fov.setValue(projector.h_fov)
        aspect = projector.image_aspect(clip_aspect)
        self.aspect.setValue(aspect)
        self.from_clip.setChecked(projector.aspect is None)
        self.aspect.setEnabled(projector.aspect is not None)
        for w in widgets:
            w.blockSignals(False)
        problem = projector.problem()
        self.problem.setText(problem or "")
        self.problem.setVisible(bool(problem))
        if problem:
            self.info.setText("")
        else:
            d = projector.distance()
            w = 2 * d * math.tan(math.radians(projector.h_fov) / 2)
            self.info.setText(f"Throw ratio {projector.throw_ratio():.2f}:1 · {d:.2f} m to the target\n"
                              f"Image there {w:.2f} × {w / aspect:.2f} m · aspect {aspect:.3f}"
                              + (" (clip)" if projector.aspect is None else "")
                              + (f"\nVisibility {visibility[0]}×{visibility[1]}" if visibility else ""))
        self.look_btn.setEnabled(not problem)

    def set_looking(self, on):
        self.look_btn.setProperty("looking", bool(on))
        self.look_btn.setText("Back to orbit view" if on else "Look through projector")
