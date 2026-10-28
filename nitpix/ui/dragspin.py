"""Opt-in horizontal number dragging. A click still edits text; a drag previews until release.
Signals describe a gesture, not a stream of committed edits. No wheel changes or cursor warping.
"""
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QApplication, QDoubleSpinBox


class DragDoubleSpinBox(QDoubleSpinBox):
    drag_started = Signal()
    dragged = Signal(float)
    drag_finished = Signal(bool)

    def enable_dragging(self):
        self._press_x = None
        self._dragging = False
        self.lineEdit().installEventFilter(self)
        self.installEventFilter(self)                    # keys arrive at the spin box (the text field's focus proxy)
        self.lineEdit().setCursor(Qt.CursorShape.SizeHorCursor)

    def eventFilter(self, obj, ev):
        if obj is self and hasattr(self, "_dragging") and self._dragging and ev.type() == QEvent.Type.KeyPress \
                and ev.key() == Qt.Key.Key_Escape:
            self._finish_drag(False)
            return True
        if obj is self.lineEdit() and hasattr(self, "_dragging"):
            kind = ev.type()
            if kind == QEvent.Type.MouseButtonPress and ev.button() == Qt.MouseButton.LeftButton:
                self._press_x = self._last_x = ev.globalPosition().x()
                self._drag_value = self.value()
            elif kind == QEvent.Type.MouseMove and self._press_x is not None and ev.buttons() & Qt.MouseButton.LeftButton:
                x = ev.globalPosition().x()
                if not self._dragging and abs(x - self._press_x) >= QApplication.startDragDistance():
                    self._dragging = True
                    self.drag_started.emit()
                if self._dragging:
                    mods = ev.modifiers()
                    factor = 0.1 if mods & Qt.KeyboardModifier.ShiftModifier else (10 if mods & Qt.KeyboardModifier.ControlModifier else 1)
                    self._drag_value = max(self.minimum(), min(self.maximum(), self._drag_value +
                                           (x - self._last_x) * self.singleStep() / 10 * factor))
                    self._last_x = x
                    value = round(self._drag_value, self.decimals())
                    old = self.blockSignals(True)
                    self.setValue(value)
                    self.blockSignals(old)
                    self.dragged.emit(value)
                    return True
            elif kind == QEvent.Type.MouseButtonRelease and ev.button() == Qt.MouseButton.LeftButton:
                if self._dragging:
                    self._finish_drag(True)
                    return True
                self._press_x = None
            elif kind == QEvent.Type.KeyPress and ev.key() == Qt.Key.Key_Escape and self._dragging:
                self._finish_drag(False)
                return True
            elif kind in (QEvent.Type.FocusOut, QEvent.Type.UngrabMouse):
                self._press_x = None
                if self._dragging:
                    self._finish_drag(False)
        return super().eventFilter(obj, ev)

    def _finish_drag(self, accept):
        self._dragging = False
        self._press_x = None
        self.drag_finished.emit(accept)
