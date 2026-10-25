"""Asset timeline: finding and note markers, luma and audio curves, in/out range, playhead."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from . import theme

LANE_H = 16
CURVE_H = 36
LABEL_W = 48
PAD = 10


class Timeline(QWidget):
    seek_requested = Signal(int)
    marker_clicked = Signal(str, int)   # ("finding" | "note", id)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.asset = None
        self.ctx = None
        self.frame = 0
        self.count = 1
        self.findings = []
        self.notes = []
        self.luma = []
        self.audio = {}
        self.in_frame = self.out_frame = None
        self._hits = []  # (rect, kind, id, frame)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_asset(self, asset: dict, ctx):
        self.asset, self.ctx = asset, ctx
        self.count = max(1, ctx.count)
        self.findings = [f for f in asset.get("findings", []) if f.get("frame_in") is not None and f["check_key"] != "seq.range"]
        self.notes = [n for n in asset.get("notes", []) if n.get("frame") is not None]
        an = (asset.get("metadata") or {}).get("analysis") or {}
        self.luma = [v for v in (an.get("yavg") or []) if v is not None] if an.get("yavg") else []
        self.audio = (an.get("audio_rms") or {}).get("channels") or {}
        self.in_frame, self.out_frame = asset.get("in_frame"), asset.get("out_frame")
        lanes = 2 + (1 if self.luma else 0)
        self.setFixedHeight(PAD * 2 + lanes * LANE_H + (CURVE_H if self.luma else 0) + len(self.audio) * LANE_H + 18)
        self.update()

    def set_frame(self, f: int):
        if f != self.frame:
            self.frame = f
            self.update()

    def set_range(self, a, b):
        self.in_frame, self.out_frame = a, b
        self.update()

    # ---- geometry ------------------------------------------------------------
    def _track(self) -> QRectF:
        return QRectF(LABEL_W, PAD, max(1, self.width() - LABEL_W - PAD), self.height() - PAD * 2 - 18)

    def _x(self, frame) -> float:
        t = self._track()
        return t.left() + (min(max(frame, 0), self.count - 1) / max(1, self.count - 1)) * t.width()

    def _frame_at(self, x) -> int:
        t = self._track()
        return int(round((x - t.left()) / max(1, t.width()) * (self.count - 1)))

    # ---- painting ------------------------------------------------------------
    def paintEvent(self, _):
        if not self.asset:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = self._track()
        p.fillRect(self.rect(), theme.colour("panel"))
        self._hits = []
        y = t.top()
        if self.in_frame is not None and self.out_frame is not None:
            x0, x1 = self._x(self.in_frame), self._x(self.out_frame + 1)
            p.fillRect(QRectF(x0, t.top(), max(2, x1 - x0), t.height()), QColor(47, 107, 255, 40))
        # lanes
        y = self._lane(p, y, "Checks", self.findings, "finding")
        y = self._lane(p, y, "Notes", self.notes, "note")
        if self.luma:
            self._curve(p, QRectF(t.left(), y, t.width(), CURVE_H), "Luma", self.luma, 255.0, theme.colour("ink2"))
            y += CURVE_H
        for i, (ch, vals) in enumerate(sorted(self.audio.items(), key=lambda kv: int(kv[0]))):
            self._audio(p, QRectF(t.left(), y, t.width(), LANE_H), f"A{ch}", vals)
            y += LANE_H
        # ticks
        p.setPen(QPen(theme.colour("faint")))
        p.setFont(theme.mono(10))
        lab_w = p.fontMetrics().horizontalAdvance(self.ctx.label(self.count - 1)) + 28
        n = max(2, min(5, int(t.width() // lab_w)))  # ticks that fit without labels touching
        for k in range(n):
            f = round(k / (n - 1) * (self.count - 1))
            x = self._x(f)
            p.drawLine(int(x), int(t.bottom()), int(x), int(t.bottom() + 4))
            lab = self.ctx.label(f)
            w = p.fontMetrics().horizontalAdvance(lab)
            p.drawText(int(min(max(x - w / 2, t.left()), t.right() - w)), int(self.height() - 4), lab)
        # playhead
        x = self._x(self.frame)
        p.setPen(QPen(theme.colour("fail"), 2))
        p.drawLine(int(x), int(t.top() - 4), int(x), int(t.bottom()))
        p.setBrush(theme.colour("fail"))
        p.setPen(Qt.PenStyle.NoPen)
        path = QPainterPath()
        path.moveTo(x - 5, t.top() - 8)
        path.lineTo(x + 5, t.top() - 8)
        path.lineTo(x, t.top() - 2)
        path.closeSubpath()
        p.drawPath(path)

    def _label(self, p, y, h, text):
        p.setPen(QPen(theme.colour("muted")))
        p.setFont(theme.mono(10))
        p.drawText(QRectF(0, y, LABEL_W - 6, h), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, text)

    def _lane(self, p, y, label, items, kind):
        t = self._track()
        p.setPen(QPen(theme.colour("line")))
        p.drawLine(int(t.left()), int(y + LANE_H / 2), int(t.right()), int(y + LANE_H / 2))
        self._label(p, y, LANE_H, label)
        for it in items:
            if kind == "finding":
                f0, f1 = it["frame_in"], it.get("frame_out")
                eff = it.get("reviewer_status") or it.get("auto_result")
                key = theme.RESULT_COLOURS.get(eff, ("muted", "panel2"))[0]
                colour = theme.colour(key)
            else:
                f0, f1 = it["frame"], it.get("frame_out")
                colour = theme.colour("focus") if it.get("kind") != "bookmark" else theme.colour("warn")
            x0 = self._x(f0)
            x1 = self._x(f1 + 1) if f1 is not None and f1 > f0 else x0 + 3
            r = QRectF(x0 - 1, y + 2, max(3, x1 - x0), LANE_H - 4)
            p.fillRect(r, colour)
            self._hits.append((r.adjusted(-3, -2, 3, 2), kind, it["id"], f0))
        return y + LANE_H

    def _curve(self, p, rect, label, vals, vmax, colour):
        self._label(p, rect.top(), rect.height(), label)
        p.setPen(QPen(theme.colour("line")))
        p.drawRect(rect)
        if len(vals) < 2:
            return
        path = QPainterPath()
        n = len(vals)
        for i, v in enumerate(vals):
            x = rect.left() + i / (n - 1) * rect.width()
            yy = rect.bottom() - (min(max(v, 0), vmax) / vmax) * (rect.height() - 2) - 1
            path.moveTo(x, yy) if i == 0 else path.lineTo(x, yy)
        p.setPen(QPen(colour, 1))
        p.drawPath(path)

    def _audio(self, p, rect, label, vals):
        """RMS in dB per window, drawn as a filled band: -60 dB at the bottom, 0 at the top."""
        self._label(p, rect.top(), rect.height(), label)
        if not vals:
            return
        n = len(vals)
        path = QPainterPath()
        path.moveTo(rect.left(), rect.bottom())
        for i, v in enumerate(vals):
            x = rect.left() + (i / max(1, n - 1)) * rect.width()
            db = -60 if v is None else max(-60, min(0, v))
            path.lineTo(x, rect.bottom() - ((db + 60) / 60) * (rect.height() - 2))
        path.lineTo(rect.right(), rect.bottom())
        path.closeSubpath()
        p.fillPath(path, QColor(47, 107, 255, 150))

    # ---- mouse -----------------------------------------------------------------
    def mousePressEvent(self, e):
        pos = e.position()
        for r, kind, mid, f in self._hits:
            if r.contains(pos):
                self.seek_requested.emit(f)
                self.marker_clicked.emit(kind, mid)
                return
        if self._track().adjusted(0, -10, 0, 20).contains(pos):
            self.seek_requested.emit(self._frame_at(pos.x()))

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton:
            self.seek_requested.emit(self._frame_at(e.position().x()))
        else:
            for r, kind, mid, f in self._hits:
                if r.contains(e.position()):
                    self.setToolTip(self.ctx.label(f))
                    return
            self.setToolTip("")
