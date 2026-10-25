#!/usr/bin/env python3
"""Draw the nitpix app icon and write it as .icns (macOS) and .png.

The mark: one tiny coral pixel, given space by four quiet inspection corners.
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

INK, CORAL, BG, EDGE = "#777771", "#F2645A", "#F1F1EE", "#D8D8D2"


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
    # Quiet corners leave the saturated pixel as the only focal point.
    # Snap to device pixels and preserve a 2 px centre at favicon sizes.
    lo, hi = round(s * 0.28), round(s * 0.72)
    arm, stroke = max(2, round(s * 0.125)), max(1, round(s * 0.033))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(INK))
    for x in (lo, hi - arm):
        for y in (lo, hi - arm):
            vx = x if x == lo else hi - stroke
            hy = y if y == lo else hi - stroke
            p.drawRect(QRectF(x, hy, arm, stroke))
            p.drawRect(QRectF(vx, y, stroke, arm))
    pixel = max(2, round(s * 0.0625))
    p.setBrush(QColor(CORAL))
    p.drawRect(QRectF((size - pixel) // 2, (size - pixel) // 2, pixel, pixel))
    p.end()
    return img


def main(out: str):
    app = QApplication.instance() or QApplication([])  # noqa: F841
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    draw(1024).save(os.path.splitext(out)[0] + ".png")
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
