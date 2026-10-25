"""Small layout helpers that keep the window from growing past the screen.

A plain QLabel's minimum width is the full text, so a long file path in a header forces
the whole window wider. `ElideLabel` shrinks and elides instead. `FlowLayout` wraps its
items onto more rows when the row is too narrow, so toolbars never push the window out."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QFontMetrics, QPainter
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLayout, QSizePolicy, QWidget


class ElideLabel(QLabel):
    """Single-line label that elides instead of demanding its full width. `elide` is
    `Qt.TextElideMode.ElideMiddle` for paths (keeps the extension), ElideRight otherwise."""

    def __init__(self, text: str = "", elide=Qt.TextElideMode.ElideRight, min_chars: int = 6, parent=None):
        super().__init__(text, parent)
        self.elide = elide
        self.min_chars = min_chars
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setTextFormat(Qt.TextFormat.PlainText)

    def minimumSizeHint(self) -> QSize:
        fm = QFontMetrics(self.font())
        m = self.contentsMargins()
        return QSize(fm.horizontalAdvance("M") * self.min_chars + m.left() + m.right(), fm.height() + m.top() + m.bottom())

    def sizeHint(self) -> QSize:
        fm = QFontMetrics(self.font())
        m = self.contentsMargins()
        return QSize(fm.horizontalAdvance(self.text()) + m.left() + m.right() + 2, fm.height() + m.top() + m.bottom())

    def paintEvent(self, ev):
        p = QPainter(self)
        r = self.contentsRect()
        fm = QFontMetrics(self.font())
        p.setPen(self.palette().color(self.foregroundRole()))
        p.drawText(r, int(self.alignment() | Qt.AlignmentFlag.AlignVCenter), fm.elidedText(self.text(), self.elide, r.width()))

    def setText(self, text):
        super().setText(text)
        self.setToolTip(text if text and QFontMetrics(self.font()).horizontalAdvance(text) > self.width() else "")
        self.updateGeometry()
        self.update()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        t = self.text()
        self.setToolTip(t if t and QFontMetrics(self.font()).horizontalAdvance(t) > self.width() else "")


class FlowLayout(QLayout):
    """Qt's flow-layout example: items keep their size hints and wrap to new rows."""

    def __init__(self, parent=None, margin=0, hspacing=6, vspacing=6):
        super().__init__(parent)
        self._items = []
        self._h, self._v = hspacing, vspacing
        self.setContentsMargins(margin, margin, margin, margin)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._layout(QRect(0, 0, w, 0), True)

    def setGeometry(self, r):
        super().setGeometry(r)
        self._layout(r, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        s = QSize()
        for it in self._items:
            w = it.widget()
            if w is None or not w.isHidden():
                s = s.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return s + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _layout(self, r: QRect, test: bool) -> int:
        m = self.contentsMargins()
        eff = r.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, row_h = eff.x(), eff.y(), 0
        for it in self._items:
            w = it.widget()
            if w is not None and w.isHidden():
                continue
            sz = it.sizeHint()
            nx = x + sz.width() + self._h
            if nx - self._h > eff.right() + 1 and row_h > 0:
                x = eff.x()
                y += row_h + self._v
                nx = x + sz.width() + self._h
                row_h = 0
            if not test:
                it.setGeometry(QRect(QPoint(x, y), sz))
            x = nx
            row_h = max(row_h, sz.height())
        return y + row_h - r.y() + m.bottom()


class Cluster(QWidget):
    """A few widgets kept together on one line; the unit a FlowLayout wraps."""

    def __init__(self, *widgets, spacing=4):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(spacing)
        for w in widgets:
            lay.addWidget(w)
        self.lay = lay

    def add(self, w):
        self.lay.addWidget(w)
        return w


def repolish(w: QWidget):
    """Apply a dynamic-property change (e.g. setProperty("bad", True)) to the stylesheet."""
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()


def nav_icon(name: str, light: str, dark: str, size: int = 18):
    """Simple line icons for the rail, drawn in code so they match the theme at any DPI.
    `light` is used normally, `dark` when the button is checked (accent background)."""
    from PySide6.QtGui import QIcon, QPixmap, QPen, QColor, QPainterPath
    icon = QIcon()
    for colour, state in ((light, QIcon.State.Off), (dark, QIcon.State.On)):
        for scale in (1, 2):
            s = size * scale
            pm = QPixmap(s, s)
            pm.setDevicePixelRatio(scale)
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)  # paints in logical pixels; the pixmap's ratio does the scaling
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            pen = QPen(QColor(colour), 1.6)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            u = size / 18.0  # design on an 18-unit grid
            if name == "dashboard":
                for x, y in ((2, 2), (10, 2), (2, 10), (10, 10)):
                    p.drawRoundedRect(QRectF(x * u, y * u, 6 * u, 6 * u), 1.5 * u, 1.5 * u)
            elif name == "media":
                p.drawRoundedRect(QRectF(2 * u, 3 * u, 14 * u, 12 * u), 2 * u, 2 * u)
                for x in (5.5, 9, 12.5):
                    p.drawLine(QPointF(x * u, 3 * u), QPointF(x * u, 6 * u))
                    p.drawLine(QPointF(x * u, 12 * u), QPointF(x * u, 15 * u))
                p.drawLine(QPointF(2 * u, 6 * u), QPointF(16 * u, 6 * u))
                p.drawLine(QPointF(2 * u, 12 * u), QPointF(16 * u, 12 * u))
            elif name == "review":
                p.drawRoundedRect(QRectF(2 * u, 3 * u, 14 * u, 12 * u), 2 * u, 2 * u)
                tri = QPainterPath()
                tri.moveTo(7.5 * u, 6 * u)
                tri.lineTo(12 * u, 9 * u)
                tri.lineTo(7.5 * u, 12 * u)
                tri.closeSubpath()
                p.setBrush(QColor(colour))
                p.drawPath(tri)
                p.setBrush(Qt.BrushStyle.NoBrush)
            elif name == "presets":
                for i, y in enumerate((4, 9, 14)):
                    p.drawLine(QPointF(2 * u, y * u), QPointF(4.5 * u, (y + 1.5) * u))
                    p.drawLine(QPointF(4.5 * u, (y + 1.5) * u), QPointF(7 * u, (y - 2) * u)) if i != 1 else p.drawLine(QPointF(2 * u, y * u), QPointF(6 * u, y * u))
                    p.drawLine(QPointF(9 * u, y * u), QPointF(16 * u, y * u))
            elif name == "reports":
                p.drawRoundedRect(QRectF(3.5 * u, 2 * u, 11 * u, 14 * u), 1.5 * u, 1.5 * u)
                for y in (6, 9, 12):
                    p.drawLine(QPointF(6.5 * u, y * u), QPointF(11.5 * u, y * u))
            elif name == "settings":
                import math
                p.drawEllipse(QPointF(9 * u, 9 * u), 3 * u, 3 * u)
                for k in range(8):
                    a = math.pi / 4 * k
                    p.drawLine(QPointF(9 * u + 5.2 * u * math.cos(a), 9 * u + 5.2 * u * math.sin(a)),
                               QPointF(9 * u + 7 * u * math.cos(a), 9 * u + 7 * u * math.sin(a)))
            elif name == "close":
                p.drawEllipse(QPointF(9 * u, 9 * u), 6.5 * u, 6.5 * u)
                p.drawLine(QPointF(6.5 * u, 6.5 * u), QPointF(11.5 * u, 11.5 * u))
                p.drawLine(QPointF(11.5 * u, 6.5 * u), QPointF(6.5 * u, 11.5 * u))
            elif name in ("collapse", "expand"):
                d = 1 if name == "collapse" else -1  # collapse points into the rail, expand out of it
                p.drawLine(QPointF((9 - 2 * d) * u, 9 * u), QPointF((9 + 2 * d) * u, 5 * u))
                p.drawLine(QPointF((9 - 2 * d) * u, 9 * u), QPointF((9 + 2 * d) * u, 13 * u))
                p.drawLine(QPointF((9 + 5 * d) * u, 4 * u), QPointF((9 + 5 * d) * u, 14 * u))
            p.end()
            icon.addPixmap(pm, QIcon.Mode.Normal, state)
            icon.addPixmap(pm, QIcon.Mode.Active, state)
    return icon
