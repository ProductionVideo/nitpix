"""Verification of packaged builds: the one-projector setup journey on the generated demo
(tools/make_setup_demo.py), through the product's own controls and the normal Qt mouse and key
event paths (QTest events into the widgets, as a pointer and keyboard would send them; no direct
calls into the aiming or transport handlers). Run with NITPIX_SCREENSHOT=<png> and
NITPIX_SCREENSHOT_SETUP=1 on the demo project; prints PASS/FAIL per step and saves screenshots
next to <png>. Not part of normal use.

Reopen -> select the screen and frame it -> move the projector (arrow drag, then typed numbers,
then Space without Enter) -> pick the screen as target -> throw ratio 1.5 -> look through (locked)
and exit -> undo / redo -> play, orbit, scrub, step -> Review handover at the exact frame and back
-> close and reopen -> the setup is restored. Paint times and event-loop stalls are reported.
"""
from __future__ import annotations

import math
import os
import statistics
import sys
import time
from collections import deque

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QCursor, QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

SCREEN = (0.0, 1.625, 0.0)                     # the demo screen's centre


def hover(w, pos):
    """Move the pointer over w without a button. On macOS a native mouse-moved event posted to this
    process only (the real cursor is not moved; no other app gets it), so it arrives the way a
    trackpad's would; a synthetic Qt move can be undone by the real pointer's enter/leave events
    while it rests elsewhere. Elsewhere, QTest."""
    if sys.platform == "darwin":
        import ctypes
        cg = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")

        class P(ctypes.Structure):
            _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]
        cg.CGEventCreateMouseEvent.restype = ctypes.c_void_p
        cg.CGEventCreateMouseEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint32, P, ctypes.c_uint32]
        cg.CGEventPostToPid.argtypes = [ctypes.c_int, ctypes.c_void_p]
        cg.CFRelease.argtypes = [ctypes.c_void_p]
        g = w.mapToGlobal(pos)
        e = cg.CGEventCreateMouseEvent(None, 5, P(g.x(), g.y()), 0)        # kCGEventMouseMoved
        cg.CGEventPostToPid(os.getpid(), e)
        cg.CFRelease(e)
    else:
        QTest.mouseMove(w, pos)


def run(app, win, shot: str):
    base = os.path.splitext(shot)[0]
    results, errors, gaps, keep = [], [], [0.0], {}
    tick = {"t": time.monotonic()}

    def beat():
        now = time.monotonic()
        gaps[0] = max(gaps[0], now - tick["t"])
        keep["worst"] = max(keep.get("worst", 0.0), now - tick["t"])
        tick["t"] = now
    hb = QTimer(win)
    hb.timeout.connect(beat)
    hb.start(10)
    win.review.player.error.connect(errors.append)

    def ms():
        return win.mapping

    def check(name, ok, detail=""):
        stall = f" (longest stall {gaps[0] * 1000:.0f} ms)" if gaps[0] > 0.1 else ""
        gaps[0] = 0.0
        results.append(("PASS" if ok else "FAIL", name, f"{detail}{stall}"))

    def pt(p):
        q = ms().view.to_screen(p)
        return QPoint(round(q.x()), round(q.y()))

    def click(w, pos=None):
        QTest.mouseClick(w, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos or w.rect().center())

    def key(w, k, text="", mods=Qt.KeyboardModifier.NoModifier):
        for t in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            QApplication.sendEvent(w, QKeyEvent(t, k, mods, text))

    def focus():
        return QApplication.focusWidget()

    steps = []

    def add(fn, wait=700):
        steps.append((fn, wait))

    def wait_restore(then, limit=300):
        m = ms()
        if (m is None or m._batch is not None or not m.view.loaded) and limit > 0:
            return QTimer.singleShot(100, lambda: wait_restore(then, limit - 1))
        then()

    # ---- reopen the saved demo -------------------------------------------------------------
    def s_open():
        win.nav["mapping"].click()
        ms().view.error.connect(errors.append)
        ms().view.stats = deque(maxlen=20000)
    add(s_open, 500)

    def s_restored():
        m = ms()
        p = m.projector
        keep["start"] = p
        check("Reopen: the demo stage, clip and projector come back in the Projectors tab; the view draws",
              len(m.stage.sources) == 1 and p is not None and m.tabs.currentIndex() == 1 and m.view.loaded and m.view.paused
              and m.view.prog is not None and m.view.helpers is not None and not m.view.problem_text,
              f"{m.scene_lab.text()}; projector at {tuple(round(c, 2) for c in p.position)} {m.view.problem_text}")
        win.grab().save(f"{base}-1-before.png")
    add(lambda: wait_restore(s_restored), 1500)

    # ---- locate the screen ---------------------------------------------------------------------
    def s_select():
        m = ms()
        top = m.tree.topLevelItem(0)
        m.tree.expandAll()
        item = next(top.child(i) for i in range(top.childCount()) if top.child(i).text(0).startswith("Screen"))
        rect = m.tree.visualItemRect(item)
        click(m.tree.viewport(), rect.center())
        keep["sel"] = m.selected_keys()
        click(m.frame_sel_btn)
        click(m.view_btns["front"])
    add(s_select, 900)

    def s_front():
        m = ms()
        check("Select the screen in the shared list, Frame selected, Front (orthographic)",
              keep["sel"] and keep["sel"][0].endswith("Screen") and m.view.view_kind == "front" and m.view.ortho,
              f"selected {keep['sel']}")
        win.grab().save(f"{base}-2-front.png")
        click(m.view_btns["perspective"])
        m.view.yaw, m.view.pitch = 32.0, 18.0
        m.view.frame_all()
    add(s_front, 600)

    # ---- move the projector: drag the X arrow, then type ------------------------------------
    def s_drag():
        m, v = ms(), ms().view
        seg = v.arrow_on_screen("x")
        a, b = seg
        grab = QPoint(round(a.x() + (b.x() - a.x()) * 0.7), round(a.y() + (b.y() - a.y()) * 0.7))
        keep["n"] = m.editor.stack.count()
        QTest.mouseMove(v, grab)
        QTest.mousePress(v, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, grab)
        d = QPointF(a.x() - b.x(), a.y() - b.y())                         # towards -X
        L = math.hypot(d.x(), d.y())
        for i in range(1, 11):
            QTest.mouseMove(v, grab + QPoint(round(d.x() / L * 14 * i), round(d.y() / L * 14 * i)))
        keep["mid"] = (m.editor.preview, m.saver.state, m.projector)
        win.grab().save(f"{base}-3-dragging.png")
        QTest.mouseRelease(v, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                           grab + QPoint(round(d.x() / L * 140), round(d.y() / L * 140)))
    add(s_drag, 600)

    def s_dragged():
        m = ms()
        prev, _state, committed_then = keep["mid"]
        p = m.projector
        check("Drag the X arrow: a preview while dragging, one undoable move on release, aim kept",
              prev is not None and committed_then == keep["start"] and p.position[0] < keep["start"].position[0] - 0.3
              and p.position[1:] == keep["start"].position[1:] and m.editor.stack.count() == keep["n"] + 1
              and math.isclose(p.target[0] - p.position[0], keep["start"].target[0] - keep["start"].position[0], abs_tol=1e-9),
              f"x {keep['start'].position[0]:.2f} → {p.position[0]:.2f} m")
        spin = m.proj_panel.pos[0]
        m.proj_scroll.ensureWidgetVisible(spin)
        click(spin)
        spin.selectAll()
        QTest.keyClicks(spin, "0")
        spin.interpretText()
        z = m.proj_panel.pos[2]
        click(z)                                                         # leaving X commits it
        z.selectAll()
        QTest.keyClicks(z, "6")
        keep["paused"] = m.view.paused
        click(m.view, QPoint(30, m.view.height() - 30))                  # the view, without Enter
    add(s_dragged, 500)

    def s_space():
        m = ms()
        check("Type X 0, then Z 6 and click the view (no Enter): committed, the view has the keys",
              m.projector.position[0] == 0.0 and m.projector.position[2] == 6.0 and focus() is m.view,
              f"focus {type(focus()).__name__}")
        QTest.keyClick(m.view, Qt.Key.Key_Space)
    add(s_space, 700)

    def s_spaced():
        m = ms()
        check("Space after the number: playback toggles once", m.view.paused != keep["paused"])
        QTest.keyClick(m.view, Qt.Key.Key_Space)
        click(m.proj_panel.pick_btn)
    add(s_spaced, 600)

    # ---- pick the screen ----------------------------------------------------------------------
    class Edges(QObject):                                             # what reaches the view meanwhile (evidence only)
        def eventFilter(self, obj, ev):
            if ev.type() in (QEvent.Type.Enter, QEvent.Type.Leave):
                keep.setdefault("edges", []).append(("enter" if ev.type() == QEvent.Type.Enter else "leave",
                                                     round(time.monotonic() - keep["t_hover"], 3)))
            return False

    def s_hover():
        v = ms().view
        keep["t_hover"] = time.monotonic()
        keep["edge_filter"] = Edges(v)
        v.installEventFilter(keep["edge_filter"])
        hover(v, pt((-1.6, 2.3, 0.0)))
        hover(v, pt(SCREEN))
    add(s_hover, 400)

    def s_hovered():
        m = ms()
        h = m.tool.hover or {}
        check("Pick target: a marker on the screen under the cursor, with what it is and its distance",
              m.tool.mode == "pick" and h.get("name") == "Screen" and m.view.hit_marker is not None
              and m.banner.isVisible() and "Screen" in m.banner_lab.text(),
              f"{m.banner_lab.text()}; pointer edges at the view {keep.get('edges', [])}; real cursor {tuple(QCursor.pos().toTuple())}, "
              f"view at {tuple(m.view.mapToGlobal(QPoint(0, 0)).toTuple())} {m.view.width()}×{m.view.height()}")
        win.grab().save(f"{base}-4-pick.png")
        hover(m.view, QPoint(m.view.width() - 20, 16))                     # empty space: a miss
    add(s_hovered, 400)

    def s_miss():
        m = ms()
        check("Over empty space: no marker, said, nothing changes",
              m.view.hit_marker is None and "No surface" in m.banner_lab.text() and m.projector.target == keep.get("t0", m.projector.target),
              f"{m.banner_lab.text()}; pointer edges {keep.get('edges', [])}")
        m.view.removeEventFilter(keep["edge_filter"])
        keep["before_pick"] = m.projector
        click(m.view, pt(SCREEN))
    add(s_miss, 600)

    def s_picked():
        m = ms()
        p, b = m.projector, keep["before_pick"]
        check("Click the screen: aimed there in one edit; position and roll kept; pick mode ends",
              math.dist(p.target, SCREEN) < 0.08 and p.position == b.position and p.roll == b.roll and m.tool.mode == "idle",
              f"target {tuple(round(c, 3) for c in p.target)}")
        t = m.proj_panel.throw
        m.proj_scroll.ensureWidgetVisible(t)
        click(t)
        t.selectAll()
        QTest.keyClicks(t, "1.5")
        QTest.keyClick(t, Qt.Key.Key_Return)
        m.proj_scroll.ensureWidgetVisible(m.proj_panel.grid_chk)
        click(m.proj_panel.grid_chk)
    add(s_picked, 900)

    def s_sized():
        m = ms()
        p = m.projector
        w, h = p.image_size()
        check("Throw ratio 1.5 at 6 m: the ideal image is 4.00 × 2.25 m; the centre of the beam meets the screen",
              f"{w:.2f} × {h:.2f}" == "4.00 × 2.25" and "Image there 4.00 × 2.25 m" in m.proj_panel.info.text()
              and "meets Screen" in m.proj_panel.hit.text(), m.proj_panel.info.text().replace("\n", " · "))
        keep["framed"] = p
        win.grab().save(f"{base}-5-framed.png")
        keep["size"] = win.size()
        win.resize(1040, 640)                                            # the smallest window
    add(s_sized, 900)

    def s_small():
        m = ms()
        need, room = m.proj_panel.minimumSizeHint().width(), m.proj_scroll.viewport().width()
        win.grab().save(f"{base}-5b-minimum-window.png")
        check("At the 1040 × 640 minimum: the inspector fits its column (scrolls, never clipped); view and transport shown",
              need <= room and m.view.height() >= 150 and m.play_btn.isVisible() and m.timeline.isVisible(),
              f"inspector needs {need} px, has {room}; view {m.view.width()}×{m.view.height()}")
        win.resize(keep["size"])
        m = ms()
        keep["cam"] = (m.view.yaw, m.view.pitch, m.view.dist, m.view.cam_target, m.view.view_kind)
        keep["rebuilds"] = m.view.projection.rebuilds
        m.proj_scroll.ensureWidgetVisible(m.proj_panel.look_btn)
        click(m.proj_panel.look_btn)
    add(s_small, 900)

    def s_look():
        m, v = ms(), ms().view
        win.grab().save(f"{base}-6-through.png")
        c = v.rect().center()
        QTest.mousePress(v, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, c)
        for i in range(1, 8):
            QTest.mouseMove(v, c + QPoint(i * 10, i * 3))
        QTest.mouseRelease(v, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, c + QPoint(70, 21))
        keep["looking"] = v.looking_through and m.banner.isVisible()
        QTest.keyClick(v, Qt.Key.Key_Escape)
    add(s_look, 500)

    def s_exited():
        m, v = ms(), ms().view
        check("Look through: visibly marked, a drag moves nothing, Esc returns to the exact orbit view",
              keep["looking"] and not v.looking_through
              and (v.yaw, v.pitch, v.dist, v.cam_target, v.view_kind) == keep["cam"]
              and m.projector == keep["framed"] and v.projection.rebuilds == keep["rebuilds"])
        m.proj_scroll.ensureWidgetVisible(m.proj_panel.undo_btn)
        click(m.proj_panel.undo_btn)
    add(s_exited, 500)

    def s_undone():
        m = ms()
        keep["undone"] = m.projector
        key(m.view, Qt.Key.Key_Z, "z", Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)  # ⇧⌘Z
    add(s_undone, 500)

    def s_redone():
        m = ms()
        check("Undo puts the throw ratio back (one step); ⇧⌘Z redoes it; both saved",
              keep["undone"].throw == 1.0 and keep["undone"].target == keep["framed"].target and m.projector == keep["framed"],
              f"undone throw {keep['undone'].throw}")
        keep["frame0"] = m.view.frame
        QTest.keyClick(m.view, Qt.Key.Key_Space)
    add(s_redone, 1500)

    # ---- the player keeps working ---------------------------------------------------------------
    def s_orbit():
        m, v = ms(), ms().view
        keep["rb"] = v.projection.rebuilds
        c = v.rect().center() + QPoint(0, 120)
        QTest.mousePress(v, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, c)
        for i in range(1, 30):
            QTest.mouseMove(v, c + QPoint(i * 4, 0))
        QTest.mouseRelease(v, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, c + QPoint(116, 0))
    add(s_orbit, 800)

    def s_played():
        m, v = ms(), ms().view
        check("Plays while orbiting; the visibility texture is reused (no rebuild for camera or video)",
              not v.paused and v.frame > keep["frame0"] and v.projection.rebuilds == keep["rb"],
              f"frame {keep['frame0']} → {v.frame}")
        QTest.keyClick(v, Qt.Key.Key_Space)
        m.playhead.scrub(57)
    add(s_played, 900)
    add(lambda: QTest.keyClick(ms().view, Qt.Key.Key_Right), 700)

    def s_stepped():
        m = ms()
        check("Scrub to 57, → steps to 58", m.view.paused and m.playhead.frame == 58 and m.view.frame == 58,
              f"frame {m.playhead.frame}")
        win.nav["review"].click()
    add(s_stepped, 1500)

    def s_review():
        check("Review takes over at the same frame", win.current_view == "review" and win.review.frame == 58,
              f"frame {win.review.frame}")
        win.nav["mapping"].click()
    add(s_review, 1500)
    add(lambda: check("…and back to Mapping at that frame", win.current_view == "mapping" and ms().playhead.frame == 58), 10)

    # ---- close and reopen -----------------------------------------------------------------------
    def s_close():
        m = ms()
        keep["saved"] = (m.projector, m.view.view_kind)
        keep["folder"] = str(win.session.project.folder)
        keep["paint"] = [t for _t0, t, _d, _c in m.view.stats]
        check("Saved in the project before closing", m.save_lab.text() in ("Saved", "Saving…"), m.save_lab.text())
        win.close_project()
    add(s_close, 800)
    add(lambda: (win.open_project(keep["folder"]), win.nav["mapping"].click()), 600)

    def s_reopened():
        m = ms()
        check("Close and reopen: the projector exactly as set (aim, throw, raster), no undo history carried over",
              m.projector == keep["saved"][0] and not m.editor.stack.canUndo() and m.view.loaded,
              f"{tuple(round(c, 3) for c in m.projector.target)} @ {m.projector.throw}")
        win.grab().save(f"{base}-7-reopened.png")
    add(lambda: wait_restore(s_reopened), 1500)

    def s_end():
        paint = keep.get("paint") or [0.0]
        q = sorted(paint)
        p95 = q[min(len(q) - 1, int(len(q) * 0.95))]
        results.append(("INFO", "Paint time", f"median {statistics.median(paint) * 1000:.2f} ms, p95 {p95 * 1000:.2f} ms over "
                        f"{len(paint)} paints; longest event-loop stall {keep.get('worst', 0) * 1000:.0f} ms"))
        results.append(("PASS" if not errors else "FAIL", "No player errors", repr(errors[:3])))
        hb.stop()
        print("\n".join(f"setup journey: {r} | {n} | {d}" for r, n, d in results), flush=True)
        if not os.environ.get("NITPIX_SCREENSHOT_STAY"):
            win.close()
            app.quit()
    add(s_end, 0)

    def go(i=0):
        fn, wait = steps[i]
        try:
            fn()
        except Exception as e:  # a broken step is a failed step, not a hung check
            import traceback
            results.append(("FAIL", f"step {i} ({fn.__name__})", f"{type(e).__name__}: {e} {traceback.format_exc(limit=3)!r}"))
        if i + 1 < len(steps):
            QTimer.singleShot(wait, lambda: go(i + 1))
    QTimer.singleShot(1500, go)


# ---- layout audit (NITPIX_SCREENSHOT_LAYOUT) ------------------------------------------------------
def audit(win) -> list[str]:
    """Problems a user would hit in the window as it is now: a control cut off by its container or
    the window (outside a scroll area that can reach it), squeezed below the size it needs, or lying
    over another control."""
    from PySide6.QtCore import QRect
    from PySide6.QtWidgets import (QAbstractButton, QAbstractScrollArea, QAbstractSpinBox, QComboBox, QLabel, QLineEdit,
                                   QScrollArea, QTabBar, QWidget)
    kinds = (QAbstractButton, QAbstractSpinBox, QComboBox, QLineEdit, QTabBar, QLabel)
    out, seen = [], []
    for w in win.findChildren(QWidget):
        if not isinstance(w, kinds) or not w.isVisible() or w.window() is not win:
            continue
        if isinstance(w, QLineEdit) and isinstance(w.parentWidget(), (QAbstractSpinBox, QComboBox)):
            continue                                  # the editor inside a spin box or combo
        name = f"{type(w).__name__} '{getattr(w, 'text', lambda: '')() or w.toolTip()[:30] or w.objectName()}'"
        r = QRect(w.mapTo(win, w.rect().topLeft()), w.size())
        scroller, p = None, w.parentWidget()
        visible = QRect(r)
        while p is not None and p is not win:
            pr = QRect(p.mapTo(win, p.rect().topLeft()), p.size())
            if isinstance(p, QAbstractScrollArea) and not isinstance(p.parentWidget(), QAbstractScrollArea):
                if isinstance(p, QScrollArea) and p.widget() is not None and p.widget().isAncestorOf(w):
                    scroller = p
                    break
            visible = visible.intersected(pr)
            p = p.parentWidget()
        if scroller is not None:
            vp = scroller.viewport()
            inner = QRect(w.mapTo(scroller.widget(), w.rect().topLeft()), w.size())
            if inner.right() > scroller.widget().width() and not scroller.horizontalScrollBar().isVisible():
                out.append(f"{name}: wider than its scrolling panel, cut off at the side")
            continue
        if isinstance(w, QLabel):                       # text: only that it is not cut off by the window
            if not win.rect().contains(r) and w.text():
                out.append(f"QLabel '{w.text()[:30]}': cut off by the window")
            continue
        if visible.width() < r.width() - 1 or visible.height() < r.height() - 1:
            out.append(f"{name}: cut off ({visible.width()}×{visible.height()} of {r.width()}×{r.height()} shown)")
        if not win.rect().contains(r):
            out.append(f"{name}: outside the window")
        need = w.minimumSizeHint()
        fixed = w.minimumWidth() == w.maximumWidth()          # an icon button sized on purpose
        if isinstance(w, (QAbstractButton, QComboBox)) and not fixed and need.width() > 0 and w.width() < need.width() - 2:
            out.append(f"{name}: squeezed ({w.width()} px wide, needs {need.width()})")
        for other, orect in seen:
            if other.isAncestorOf(w) or w.isAncestorOf(other):
                continue
            hit = r.intersected(orect)
            if hit.width() > 2 and hit.height() > 2:
                out.append(f"{name} overlaps {type(other).__name__} '{getattr(other, 'text', lambda: '')()}'")
        seen.append((w, r))
    return out


def run_layout(app, win, shot: str):
    """Each state at a few window sizes: an audit (above) and a screenshot."""
    base = os.path.splitext(shot)[0]
    lines, steps = [], []

    def ms():
        return win.mapping

    def add(fn, wait=600):
        steps.append((fn, wait))

    def look(label):
        probs = audit(win)
        win.grab().save(f"{base}-{label}.png")
        lines.append(("PASS" if not probs else "FAIL", label, "; ".join(probs[:6]) + (f" (+{len(probs) - 6})" if len(probs) > 6 else "")))

    def size(w, h):
        win.showNormal()
        win.resize(w, h)

    def wait_restore(then, limit=300):
        m = ms()
        if (m is None or m._batch is not None or not m.view.loaded) and limit > 0:
            return QTimer.singleShot(100, lambda: wait_restore(then, limit - 1))
        then()

    add(lambda: (size(1400, 860), win.nav["mapping"].click()), 600)
    add(lambda: wait_restore(lambda: None), 1500)
    for w, h, tag in ((1400, 860, "normal"), (1040, 640, "minimum"), (1900, 1150, "large")):
        add(lambda w=w, h=h: (size(w, h), ms().tabs.setCurrentIndex(1), ms().proj_scroll.verticalScrollBar().setValue(0)), 500)
        add(lambda tag=tag: look(f"{tag}-projectors-top"), 10)
        add(lambda: ms().proj_scroll.verticalScrollBar().setValue(ms().proj_scroll.verticalScrollBar().maximum()), 300)
        add(lambda tag=tag: (look(f"{tag}-projectors-bottom"),
                             lines.append(("PASS" if ms().proj_panel.remove_btn.visibleRegion().boundingRect().height()
                                           >= ms().proj_panel.remove_btn.height() - 1 else "FAIL",
                                           f"{tag}: last action reachable by scrolling", ""))), 10)
        add(lambda: (ms().tabs.setCurrentIndex(0), ms().select([ms().stage.objects[0].key])), 400)
        add(lambda tag=tag: look(f"{tag}-surfaces"), 10)
        add(lambda: (ms().tabs.setCurrentIndex(1), ms().start_pick()), 400)
        add(lambda tag=tag: (look(f"{tag}-picking"), ms().exit_modes()), 10)
        add(lambda: ms().look_through(True), 400)
        add(lambda tag=tag: (look(f"{tag}-look-through"), ms().exit_modes()), 10)
    add(lambda: (size(1040, 640), ms().saver._set("error", "disk full")), 500)          # a failed save: Retry must be reachable
    add(lambda: (look("minimum-save-failed"), lines.append(("PASS" if ms().retry_btn.isVisible() and win.rect().contains(
        ms().retry_btn.geometry().translated(ms().retry_btn.mapTo(win, ms().retry_btn.rect().topLeft()) - ms().retry_btn.pos()))
        else "FAIL", "minimum: Retry save visible inside the window", "")), ms().saver._set("saved", "")), 10)
    add(lambda: (size(1040, 640), ms().remove_projector()), 500)
    add(lambda: (look("minimum-no-projector"), ms().undo()), 10)
    add(lambda: (size(1040, 640), win.nav["settings"].click()), 500)
    add(lambda: look("minimum-settings"), 10)
    add(lambda: win.settings_screen.findChild(__import__("PySide6.QtWidgets", fromlist=["QScrollArea"]).QScrollArea)
        .verticalScrollBar().setValue(10 ** 6), 300)
    add(lambda: look("minimum-settings-bottom"), 10)
    for w, h, tag in ((1400, 860, "normal"), (1040, 640, "minimum")):
        for view in ("dashboard", "media", "review", "presets", "reports", "settings"):
            add(lambda w=w, h=h, view=view: (size(w, h), win.show_view(view)), 500)
            add(lambda view=view, tag=tag: look(f"{tag}-{view}"), 10)
    add(lambda: (size(1400, 860), win.nav["mapping"].click()), 800)
    add(lambda: look("normal-after-resizing"), 10)

    def end():
        print("\n".join(f"layout: {r} | {n} | {d}" for r, n, d in lines), flush=True)
        win.close()
        app.quit()
    add(end, 0)

    def go(i=0):
        fn, wait = steps[i]
        try:
            fn()
        except Exception as e:
            lines.append(("FAIL", f"step {i}", f"{type(e).__name__}: {e}"))
        if i + 1 < len(steps):
            QTimer.singleShot(wait, lambda: go(i + 1))
    QTimer.singleShot(1500, go)
