#!/usr/bin/env python3
"""Draw the nitpix app icon and write it as .icns (macOS) and .png.

The mark: four soft corner marks settling on one coral pixel, on a dark squircle that matches the app.
Geometry only, no text, so it stays crisp from 16 px to 1024 px.

Usage: .venv/bin/python tools/make_icon.py build/nitpix.icns"""
import math
import os
import subprocess
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

INK, CORAL, BG, EDGE = "#B9B9B3", "#F2645A", "#161616", "#2E2E2B"


def squircle(rect: QRectF, n: float = 4.6) -> QPainterPath:
    """Apple-style superellipse |x|^n + |y|^n = 1."""
    cx, cy, rx, ry = rect.center().x(), rect.center().y(), rect.width() / 2, rect.height() / 2
    path = QPainterPath()
    steps = 360
    for i in range(steps + 1):
        t = 2 * math.pi * i / steps
        c, s = math.cos(t), math.sin(t)
        x = cx + rx * math.copysign(abs(c) ** (2 / n), c)
        y = cy + ry * math.copysign(abs(s) ** (2 / n), s)
        if i == 0:
            path.moveTo(QPointF(x, y))
        else:
            path.lineTo(QPointF(x, y))
    path.closeSubpath()
    return path


def draw(size: int) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = float(size)
    inset = s * 0.08  # macOS icons leave a margin inside the canvas
    body = QRectF(inset, inset, s - 2 * inset, s - 2 * inset)
    shape = squircle(body)
    p.fillPath(shape, QColor(BG))
    if size >= 64:
        p.setPen(QPen(QColor(EDGE), max(1.0, s * 0.003)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(shape)
    # Four open corner marks with round ends and a round coral dot: a viewfinder that has
    # settled on one pixel. Soft shapes, nothing sharp or badge-like.
    lo, hi = s * 0.30, s * 0.70
    arm, stroke = s * 0.11, max(1.5, s * 0.042)
    pen = QPen(QColor(INK), stroke)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    for cx, dx in ((lo, 1), (hi, -1)):
        for cy, dy in ((lo, 1), (hi, -1)):
            corner = QPainterPath(QPointF(cx + dx * arm, cy))
            corner.lineTo(QPointF(cx, cy))
            corner.lineTo(QPointF(cx, cy + dy * arm))
            p.drawPath(corner)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(CORAL))
    r = max(1.5, s * 0.052)
    p.drawEllipse(QPointF(s / 2, s / 2), r, r)
    p.end()
    return img


def main(out: str):
    app = QApplication.instance() or QApplication([])  # noqa: F841
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    big = draw(1024)
    big.save(os.path.splitext(out)[0] + ".png")
    big.save(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "nitpix", "web", "branding", "app-icon.png"))
    with tempfile.TemporaryDirectory() as tmp:
        iconset = os.path.join(tmp, "nitpix.iconset")
        os.mkdir(iconset)
        for base in (16, 32, 128, 256, 512):
            draw(base).save(os.path.join(iconset, f"icon_{base}x{base}.png"))
            draw(base * 2).save(os.path.join(iconset, f"icon_{base}x{base}@2x.png"))
        if sys.platform == "darwin":
            subprocess.run(["iconutil", "-c", "icns", iconset, "-o", out], check=True)
            print("wrote", out)
        else:
            print("iconutil is macOS-only; PNGs written to", os.path.dirname(out))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "build/nitpix.icns")
