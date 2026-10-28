"""Review screen: the player with transport and timeline on the left, checks / notes /
metadata on the right. Mirrors the web review view, with mpv playing the source."""
from __future__ import annotations

import math
import os
import platform
import re
import subprocess
import time
from fractions import Fraction

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QFontMetricsF, QPixmap
from shiboken6 import isValid
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox,
                               QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSplitter, QStackedLayout,
                               QTabWidget, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import frames, service
from ..timecode import frames_to_tc, is_drop_frame_rate, parse_rate, tc_to_frames
from . import theme
from .mpvwidget import MpvWidget
from .playhead import Playhead
from .timeline import Timeline
from .widgets import Cluster, ElideLabel, FlowLayout, repolish

RIGHT_PANE_MIN = 340  # checks / notes / metadata column


class PosCtx:
    """Frame <-> label maths for one asset (frame numbers for sequences, timecode otherwise)."""

    def __init__(self, a: dict):
        self.seq = a["kind"] == "sequence" and bool(a.get("seq"))
        if self.seq:
            self.first = a["seq"]["first"]
            self.count = a["seq"]["last"] - a["seq"]["first"] + 1
            self.rate = parse_rate(a.get("fps")) or Fraction(25)
            self.drop = False
            self.start = 0
        else:
            self.first = 0
            self.rate = parse_rate(a.get("fps")) or Fraction(25)
            tc = a.get("start_tc")
            self.drop = bool(tc and ";" in tc and is_drop_frame_rate(self.rate))
            try:
                self.start = tc_to_frames(tc, self.rate, self.drop) if tc else 0
            except ValueError:
                self.start = 0
            self.count = 1 if a["kind"] == "image" else max(1, a.get("frames") or round((a.get("duration") or 0) * float(self.rate)))

    def label(self, f) -> str:
        if f is None:
            return ""
        return str(self.first + f) if self.seq else frames_to_tc(self.start + int(f), self.rate, self.drop)

    def sub(self, f) -> str:
        return "" if f is None else (f"idx {f}" if self.seq else str(f))

    def counter(self, f) -> str:
        return f"{self.sub(f)} / {self.count} frames"

    def counter_shape(self) -> tuple[str, int, str]:
        """The counter line as (fixed prefix, digits that vary, fixed suffix), for sizing."""
        return ("idx " if self.seq else ""), len(str(self.count - 1)), f" / {self.count} frames"

    def parse(self, s: str):
        s = s.strip()
        if s.isdigit():
            return int(s) - self.first if self.seq else int(s)
        if self.seq:
            return None
        try:
            return tc_to_frames(s, self.rate, self.drop) - self.start
        except ValueError:
            return None



def checks_state_text(scan_state: str, any_findings: bool) -> str:
    """What the Checks tab says when nothing needs attention: only a finished scan counts as clean."""
    if scan_state == "pending":
        return "Not checked yet: this file is waiting for the automated checks.\nNo result until they have run."
    if scan_state in ("probing", "analysing"):
        return "Checking now…\nResults appear here when the automated checks finish."
    if scan_state == "error":
        return "The automated checks did not finish, so there is no automated result."
    if not any_findings:
        return "No automated checks apply to this file.\nReview visually, then set the review status above."
    return "No problems found by automated checks.\nReview visually, then set the review status above."

def chip(text: str, result: str) -> QLabel:
    lab = QLabel(text)
    lab.setStyleSheet(theme.chip_style(result))
    return lab


def btn(text, tip="", slot=None, **props) -> QPushButton:
    b = QPushButton(text)
    if tip:
        b.setToolTip(tip)
    for k, v in props.items():
        b.setProperty(k, v)
    if slot:
        b.clicked.connect(slot)
    return b


# Physical number-row keys, so ⇧2 means "2" whatever the layout types for it (" on UK, @ on US).
_MAC_DIGIT_KEYCODES = {29: 0, 18: 1, 19: 2, 20: 3, 21: 4, 23: 5, 22: 6, 26: 7, 28: 8, 25: 9}  # kVK_ANSI_0..9
_SHIFTED = {"!": 1, "@": 2, '"': 2, "#": 3, "£": 3, "$": 4, "§": 6, "^": 6, "&": 6, ")": 0, "=": 0}


def shifted_digit(ev) -> int | None:
    """The digit on the key pressed, for a Shift+number press, on any keyboard layout."""
    if platform.system() == "Darwin":
        d = _MAC_DIGIT_KEYCODES.get(ev.nativeVirtualKey())
        if d is not None:
            return d
    if Qt.Key.Key_0 <= ev.key() <= Qt.Key.Key_9:
        return ev.key() - Qt.Key.Key_0
    return _SHIFTED.get(ev.text())


def fmt_time(ts) -> str:
    return time.strftime("%d %b, %H:%M", time.localtime(ts)) if ts else ""


class GotoEdit(QLineEdit):
    """The go-to field. Escape, or a transport key while it is empty, hands focus (and the
    key) back to the player, so Space and the arrows never get stuck here."""
    TRANSPORT = (Qt.Key.Key_Space, Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Home, Qt.Key.Key_End,
                 Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_BracketLeft, Qt.Key.Key_BracketRight)

    def __init__(self, screen: "ReviewScreen"):
        super().__init__()
        self.screen = screen

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key.Key_Escape:
            self.clear()
            self.screen.setFocus()
            return
        if ev.key() in self.TRANSPORT and not self.text():
            self.screen.setFocus()
            self.screen.handle_key(ev)
            return
        super().keyPressEvent(ev)

    def focusOutEvent(self, ev):
        super().focusOutEvent(ev)
        if not self.text().strip():
            self.clear()


class FindingRow(QFrame):
    def __init__(self, f: dict, ctx: PosCtx, screen: "ReviewScreen"):
        super().__init__()
        self.f, self.screen = f, screen
        self.setObjectName("panel")
        eff = f.get("reviewer_status") or f["auto_result"]
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(6)
        c = chip(theme.RESULT_LABEL.get(eff, eff), eff)
        top.addWidget(c, 0, Qt.AlignmentFlag.AlignTop)
        title = QLabel(f["title"])
        title.setStyleSheet("font-weight: 500;")
        title.setWordWrap(True)
        title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        top.addWidget(title, 1)
        if f.get("frame_in") is not None and f["check_key"] != "seq.range":
            pos = btn(ctx.label(f["frame_in"]) + (f" – {ctx.label(f['frame_out'])}" if f.get("frame_out") not in (None, f["frame_in"]) else ""),
                      "Go to this position", lambda: screen.seek(f["frame_in"]), ghost=True)
            pos.setFont(theme.mono(11))
            pos.setStyleSheet(f"color: {theme.T['focus']}; padding: 1px 6px;")
            top.addWidget(pos, 0, Qt.AlignmentFlag.AlignTop)
        tag = QLabel(f["kind"])
        tag.setProperty("faint", True)
        top.addWidget(tag, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(top)
        meas = []
        if f.get("measured"):
            meas.append(f"measured <b>{f['measured']}</b>")
        if f.get("expected"):
            meas.append(f"expected {f['expected']}")
        if meas:
            m = QLabel(" · ".join(meas))
            m.setFont(theme.mono(11))
            m.setProperty("muted", True)
            m.setWordWrap(True)
            lay.addWidget(m)
        if f.get("detail"):
            d = QLabel(f["detail"])
            d.setWordWrap(True)
            lay.addWidget(d)
        if f.get("why"):
            w = QLabel(f["why"])
            w.setWordWrap(True)
            w.setProperty("faint", True)
            lay.addWidget(w)
        act = QHBoxLayout()
        self.group = QButtonGroup(self)
        self.group.setExclusive(False)
        for key, label, k in (("pass", "Pass", "1"), ("issue", "Issue", "2"), ("warning", "Warning", "3"), ("na", "N/A", "4")):
            b = btn(label, f"{label} (⇧{k})", decide=key, seg=True)
            b.setCheckable(True)
            b.setChecked(f.get("reviewer_status") == key)
            b.clicked.connect(lambda _, key=key: screen.decide(f["id"], None if f.get("reviewer_status") == key else key))
            act.addWidget(b)
        if f.get("reviewer_status"):
            act.addWidget(btn("Clear", "Clear decision (⇧0)", lambda: screen.decide(f["id"], None), ghost=True))
            by = ElideLabel(f"{f.get('reviewed_by') or ''} {fmt_time(f.get('reviewed_at'))}")
            by.setProperty("faint", True)
            act.addWidget(by, 1)
        elif f["auto_result"] not in ("pass", "info"):
            aw = ElideLabel("awaiting decision")
            aw.setProperty("faint", True)
            act.addWidget(aw, 1)
        else:
            act.addStretch(1)
        lay.addLayout(act)
        note = QLineEdit(f.get("reviewer_note") or "")
        note.setPlaceholderText("Reviewer note for this finding…")
        note.editingFinished.connect(lambda: screen.finding_note(f["id"], note.text()))
        lay.addWidget(note)


class ReviewScreen(QWidget):
    asset_changed = Signal(dict)       # list rows should refresh (result / review status)
    navigate = Signal(int)             # asset id to open (prev / next)
    status = Signal(str, bool)         # toast text, is_error

    def __init__(self, session: service.Session):
        super().__init__()
        self.session = session
        self.asset = None
        self.ctx = None
        self.focus_idx = -1
        self.order: list[int] = []   # asset ids, for prev/next
        self.note_draft = None
        self.released = False        # the playback session is with another workspace (Mapping)
        self._save_frame = QTimer(self, singleShot=True, interval=1500)
        self._save_frame.timeout.connect(self._persist_frame)
        self._build()

    # The position and the transport live in the Playhead (nitpix/ui/playhead.py), which owns
    # the requested frame while a scrub settles; these keep the screen's own names for them.
    @property
    def frame(self) -> int:
        return self.playhead.frame

    @frame.setter
    def frame(self, f: int):
        self.playhead.frame = f

    @property
    def _settle(self):
        return self.playhead.target

    def _settle_timeout(self):
        self.playhead._settle_timeout()

    # ---- layout ---------------------------------------------------------------
    def _build(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        split = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(split)
        left = QWidget()  # its minimum comes from the rows below; an explicit one would let them clip
        ll = QVBoxLayout(left)
        ll.setContentsMargins(12, 8, 12, 8)
        ll.setSpacing(8)
        # header, line 1: prev / next, the file name (the thing being reviewed), result, actions
        head = QHBoxLayout()
        head.setSpacing(6)
        self.prev_btn = btn("‹", "Previous asset (,)", lambda: self._nav(-1), ghost=True)
        self.next_btn = btn("›", "Next asset (.)", lambda: self._nav(1), ghost=True)
        head.addWidget(self.prev_btn)
        head.addWidget(self.next_btn)
        self.name_lab = ElideLabel("", Qt.TextElideMode.ElideMiddle, min_chars=8)
        self.name_lab.setStyleSheet("font-weight: 600; font-size: 16px;")
        self.chip_lab = QLabel()
        head.addWidget(self.name_lab, 1)
        head.addWidget(self.chip_lab)
        if platform.system() == "Darwin":
            head.addWidget(btn("Reveal", "Reveal in Finder", self._reveal, ghost=True))
        self.iina_btn = btn("Open in IINA", "Hand this asset to IINA at the current frame (e)", self.open_in_iina, ghost=True)
        head.addWidget(self.iina_btn)
        ll.addLayout(head)
        # header, line 2: the path (elided in the middle) and the review status
        sub = QHBoxLayout()
        sub.setSpacing(6)
        self.path_lab = ElideLabel("", Qt.TextElideMode.ElideMiddle, min_chars=4)
        self.path_lab.setFont(theme.mono(11))
        self.path_lab.setProperty("muted", True)
        sub.addWidget(self.path_lab, 1)
        self.review_group = QButtonGroup(self)
        seg = QHBoxLayout()
        seg.setSpacing(0)
        for k in theme.REVIEW_ORDER:
            b = btn(theme.REVIEW_LABEL[k], f"Review status: {theme.REVIEW_LABEL[k]}", lambda _, k=k: self.set_review(k), seg=True)
            b.setCheckable(True)
            self.review_group.addButton(b)
            b.setProperty("state", k)
            seg.addWidget(b)
        sub.addLayout(seg)
        ll.addLayout(sub)
        self.missing = QLabel("This file is no longer at its recorded path. Review data is kept; re-scan once the media is reconnected.")
        self.missing.setStyleSheet(f"background: {theme.T['fail_bg']}; color: {theme.T['fail']}; padding: 6px 10px; border-radius: 6px;")
        self.missing.setWordWrap(True)
        self.missing.hide()
        ll.addWidget(self.missing)
        # viewer
        self.viewer_stack = QStackedLayout()
        self.player = MpvWidget()
        self.player.pause_changed.connect(self._on_pause)
        self.player.file_loaded.connect(self._on_loaded)
        self.player.error.connect(lambda m: self.status.emit(m, True))
        self.playhead = Playhead(self.player, lambda: self.viewer_stack.currentWidget() is self.player, self)
        self.playhead.moved.connect(self._on_moved)
        self.playhead.pending_changed.connect(lambda _pending: self.ctx and self._update_tc())
        self.playhead.still_seek.connect(lambda _f: self._show_still())
        self.still = QLabel()
        self.still.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.still.setStyleSheet(f"background: {theme.T['viewer']}; color: {theme.T['muted']};")
        vwrap = QWidget()
        vwrap.setLayout(self.viewer_stack)
        self.viewer_stack.addWidget(self.player)
        self.viewer_stack.addWidget(self.still)
        vwrap.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        vwrap.setStyleSheet(f"background: {theme.T['viewer']};")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        for w in (self.player, self.still, vwrap):
            w.installEventFilter(self)  # a click on the picture takes the keyboard back from text fields
        ll.addWidget(vwrap, 1)
        self.player_lab = ElideLabel()
        self.player_lab.setFont(theme.mono(11))
        self.player_lab.setProperty("muted", True)
        ll.addWidget(self.player_lab)
        # transport: clusters in a flow layout, so a narrow window wraps them onto a second
        # row instead of growing the window
        tr = FlowLayout(hspacing=10, vspacing=6)
        self.tcbox = tcbox = QWidget()
        tcl = QVBoxLayout(tcbox)
        tcl.setContentsMargins(0, 0, 0, 0)
        tcl.setSpacing(0)
        self.tc_lab = QLabel("00:00:00:00")
        self.tc_lab.setProperty("tc", True)
        self.tc_lab.setFont(theme.tabular(self.tc_lab.font()))
        self.tc_sub = QLabel()
        self.tc_sub.setFont(theme.mono(11))
        self.tc_sub.setProperty("muted", True)
        tcl.addWidget(self.tc_lab)
        tcl.addWidget(self.tc_sub)
        for lab in (self.tc_lab, self.tc_sub):
            lab.installEventFilter(self)  # a font or style change re-sizes the block (_reserve_tc)
        tr.addWidget(tcbox)
        buttons = Cluster(spacing=2)
        for text, tip, slot in (("|◀", "Start (Home)", lambda: self.seek(0)), ("◀|", "Back one frame (←)", lambda: self.step(-1)),
                                ("▶", "Play / pause (Space)", self.toggle_play), ("|▶", "Forward one frame (→)", lambda: self.step(1)),
                                ("▶|", "End (End)", lambda: self.seek(self.ctx.count - 1 if self.ctx else 0))):
            b = buttons.add(btn(text, tip, slot))
            b.setFixedWidth(40)
            if text == "▶":
                self.play_btn = b
        tr.addWidget(buttons)
        self.tc_in = GotoEdit(self)
        self.tc_in.setFont(theme.mono(12))
        self.tc_in.setFixedWidth(120)
        self.tc_in.setPlaceholderText("go to…")
        self.tc_in.setToolTip("Go to timecode or frame (g)")
        self.tc_in.returnPressed.connect(self._goto)
        tr.addWidget(self.tc_in)
        self.in_btn = btn("In", "Set in point (i)", lambda: self.set_inout("in"))
        self.out_btn = btn("Out", "Set out point (o)", lambda: self.set_inout("out"))
        self.range_lab = QLabel()
        self.range_lab.setFont(theme.mono(11))
        self.range_lab.setProperty("muted", True)
        tr.addWidget(Cluster(self.in_btn, self.out_btn, self.range_lab, btn("×", "Clear in / out", lambda: self.set_inout(None), ghost=True)))
        tr.addWidget(Cluster(btn("⚑ Mark", "Bookmark this frame (b)", self.bookmark), btn("+ Note", "Note at this frame (n)", self.start_note)))
        jl = QLabel("Markers")
        jl.setProperty("muted", True)
        jl.setToolTip("Findings and notes with a position on the timeline")
        prev_b = btn("‹", "Previous finding or note ([)", lambda: self.jump_finding(-1))
        next_b = btn("›", "Next finding or note (])", lambda: self.jump_finding(1))
        for b in (prev_b, next_b):
            b.setFixedWidth(32)
        tr.addWidget(Cluster(prev_b, jl, next_b, spacing=6))
        ll.addLayout(tr)
        self.timeline = Timeline()
        self.timeline.seek_requested.connect(self.scrub)
        self.timeline.marker_clicked.connect(self._marker)
        self.timeline.installEventFilter(self)
        ll.addWidget(self.timeline)
        split.addWidget(left)
        # right: tabs
        self.tabs = QTabWidget()
        self.checks_scroll = QScrollArea()
        self.checks_scroll.setWidgetResizable(True)
        self.notes_scroll = QScrollArea()
        self.notes_scroll.setWidgetResizable(True)
        self.meta_tree = QTreeWidget()
        self.meta_tree.setHeaderLabels(["Field", "Value"])
        self.meta_tree.setRootIsDecorated(False)
        self.meta_tree.setAlternatingRowColors(True)
        self.tabs.addTab(self.checks_scroll, "Checks")
        self.tabs.addTab(self.notes_scroll, "Notes")
        self.tabs.addTab(self.meta_tree, "Metadata")
        self.tabs.setMinimumWidth(RIGHT_PANE_MIN)
        for sa in (self.checks_scroll, self.notes_scroll):
            sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        split.addWidget(self.tabs)
        split.setChildrenCollapsible(False)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([900, 460])

    # ---- loading ------------------------------------------------------------------
    def set_order(self, ids: list[int]):
        self.order = list(ids)
        self._nav_buttons()

    def load_asset(self, aid: int, keep_frame=False, frame: int | None = None):
        """Show an asset. `frame` opens it there instead of at its saved position. After the
        player was released to another workspace the asset is always loaded again, even if it is
        the same one."""
        p = self.session.project
        a = service.asset_detail(p, aid)
        if not a:
            self.status.emit("Asset not found", True)
            return
        prev = self.asset
        same = prev and prev["id"] == a["id"] and not self.released
        self.released = False
        self.asset = a
        self.ctx = PosCtx(a)
        keep = keep_frame and same
        start = min(a.get("last_frame") or 0, self.ctx.count - 1) if frame is None else max(0, min(self.ctx.count - 1, int(frame)))
        self.playhead.begin(self.ctx.count, None if keep else start, new=not same)
        if not keep:
            self.focus_idx = -1
            self.note_draft = None
        self._render_header()
        self.timeline.set_asset(a, self.ctx)
        self.timeline.set_frame(self.frame)
        self._render_tabs()
        self._reserve_tc()
        self._update_tc()
        self._nav_buttons()
        if not same:
            self._load_player()
        self.asset_changed.emit(a)

    def refresh(self):
        if self.asset and not self.released:
            self.load_asset(self.asset["id"], keep_frame=True)

    # ---- one playback session across workspaces ----------------------------------------------
    def release_playback(self):
        """Hand the playback session to another workspace: the player stops (no decoding, no
        sound, replies to earlier seeks ignored), a pending scrub is dropped at the frame asked
        for, and that frame is saved. Returns (asset id, frame) or None."""
        if self.released:
            return (self.asset["id"], self.frame) if self.asset else None
        self.released = True
        self.player.unload()
        if not self.asset:
            return None
        f = self.frame
        self.playhead.begin(self.ctx.count, f, new=True)
        self._save_frame.stop()
        self._persist_frame()
        return (self.asset["id"], f)

    def resume_playback(self, handoff=None):
        """Take the session back: load `handoff` (asset id, frame), or this screen's own asset."""
        if not self.released:
            return
        aid, f = handoff if handoff else ((self.asset["id"], self.frame) if self.asset else (None, None))
        if aid is None:
            self.released = False
            return
        self.load_asset(aid, frame=f)

    def _render_header(self):
        a = self.asset
        res = "fail" if a["scan_state"] == "error" else (a.get("qc_result") or "pending")
        self.name_lab.setText(a["name"])
        self.chip_lab.setText("Error" if a["scan_state"] == "error" else theme.RESULT_LABEL.get(res, "Pending"))
        self.chip_lab.setStyleSheet(theme.chip_style(res))
        self.path_lab.setText(a["path"])
        self.missing.setVisible(a.get("exists") is False)
        for b in self.review_group.buttons():
            b.setChecked(b.property("state") == a.get("review_status"))
        i, o = a.get("in_frame"), a.get("out_frame")
        for b, v in ((self.in_btn, i), (self.out_btn, o)):
            b.setProperty("set", v is not None)
            repolish(b)
        self.range_lab.setText(f"{self.ctx.label(i) if i is not None else '…'} – {self.ctx.label(o) if o is not None else '…'}" if i is not None or o is not None else "")
        self.range_lab.setVisible(i is not None or o is not None)
        env = self.session.iina.environment() if self.session.iina else {"available": False}
        self.iina_btn.setVisible(bool(env.get("available")) and a["kind"] != "other")

    def _nav_buttons(self):
        i = self.order.index(self.asset["id"]) if self.asset and self.asset["id"] in self.order else -1
        self.prev_btn.setEnabled(i > 0)
        self.next_btn.setEnabled(0 <= i < len(self.order) - 1)

    def _nav(self, d):
        i = self.order.index(self.asset["id"]) if self.asset and self.asset["id"] in self.order else -1
        if 0 <= i + d < len(self.order):
            self.navigate.emit(self.order[i + d])

    def _load_player(self):
        a = self.asset
        if a["kind"] in ("video", "sequence", "audio", "image") and a.get("exists", True) and self.player.m:
            self.viewer_stack.setCurrentWidget(self.player)
            self.player_lab.setText("Loading…")
            self.player.load(self.session.project, a, self.frame)
        else:
            self.player.unload()
            self.viewer_stack.setCurrentWidget(self.still)
            self.player_lab.setText("")
            self._show_still()

    def _show_still(self):
        a = self.asset
        if a["kind"] == "other" or a.get("exists") is False:
            self.still.setPixmap(QPixmap())
            self.still.setText(a.get("scan_error") or "No picture available.")
            return
        try:
            path = frames.grab(self.session.project, a, self.frame, 1280)
            self.still.setPixmap(QPixmap(path).scaled(self.still.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                                      Qt.TransformationMode.SmoothTransformation))
        except Exception as e:
            self.still.setPixmap(QPixmap())
            self.still.setText(str(e))

    def _on_loaded(self):
        vp = self.player.video_params()
        bits = [vp.get("codec") or "", vp.get("pixelformat") or "",
                f"{vp.get('colormatrix')} / {vp.get('primaries')} / {vp.get('gamma')}" if vp.get("colormatrix") else "",
                f"hwdec {vp.get('hwdec')}" if vp.get("hwdec") and vp.get("hwdec") != "no" else "software decode"]
        self.player_lab.setText("  ·  ".join(b for b in bits if b))
        a = self.asset
        if a.get("in_frame") is not None and a.get("out_frame") is not None:
            self.player.set_loop(a["in_frame"], a["out_frame"])
        self.player.seek_frame(self.frame)

    # ---- position --------------------------------------------------------------------
    def _on_moved(self, f: int):
        self._update_tc()
        self.timeline.set_frame(f)
        self._save_frame.start()

    def _on_pause(self, paused: bool):
        self.play_btn.setText("▶" if paused else "❚❚")

    def _update_tc(self):
        self.tc_lab.setText(self.ctx.label(self.frame))
        if bool(self.tc_lab.property("pending")) != self.playhead.pending:
            self.tc_lab.setProperty("pending", self.playhead.pending)  # dimmed until the picture is there
            repolish(self.tc_lab)
        self.tc_sub.setText(self.ctx.counter(self.frame))

    def _reserve_tc(self):
        """Give the timecode block one width that fits every position of this asset, so the
        transport beside it stays put while the counter runs. Labels use proportional digits
        and gain digits as frames go up, so each varying digit is counted as the font's widest
        and the longest digit count is used; the start timecode, separators and frame total are
        taken as they are. Sized per asset and on font or screen changes, never per frame."""
        if not self.ctx:
            return
        c = self.ctx
        last = c.count - 1
        tc = [str(c.first), str(c.first + last)] if c.seq else [c.label(0), c.label(last)]
        prefix, digits, suffix = c.counter_shape()
        need = max(self._text_room(self.tc_lab, tc, every_digit=True),
                   self._text_room(self.tc_sub, [prefix + "0" * digits + suffix], every_digit=False, span=(len(prefix), len(prefix) + digits)))
        m = self.tcbox.layout().contentsMargins()
        self.tcbox.setMinimumWidth(need + m.left() + m.right())

    @staticmethod
    def _text_room(lab: QLabel, samples, every_digit=True, span=None) -> int:
        lab.ensurePolished()  # the stylesheet's font, not the one set in code
        fm = QFontMetricsF(lab.font())
        wide = max("0123456789", key=fm.horizontalAdvance)
        w = 0.0
        for s in samples:
            if every_digit:
                s = re.sub(r"\d", wide, s)
            else:
                a, b = span
                s = s[:a] + wide * (b - a) + s[b:]
            w = max(w, fm.horizontalAdvance(s))
        m = lab.contentsMargins()
        return math.ceil(w) + m.left() + m.right() + 2 * lab.margin() + 1

    def showEvent(self, ev):
        super().showEvent(ev)
        h = self.window().windowHandle()
        if h is not None and not getattr(self, "_screen_hooked", False):
            h.screenChanged.connect(lambda _s: self._reserve_tc())  # another display's scaling
            self._screen_hooked = True

    def _persist_frame(self):
        if self.asset:
            try:
                service.update_asset(self.session.project, self.asset["id"], {"last_frame": self.frame})
            except Exception:
                pass

    def scrub(self, f: int):
        """Timeline press, drag and release: exact seeks that never block the GUI thread."""
        self.playhead.scrub(f)

    def seek(self, f: int):
        self.playhead.seek(f)

    def step(self, n: int):
        self.playhead.step(n)

    def toggle_play(self):
        a = self.asset or {}
        if not self.playhead.toggle_play(looping=a.get("in_frame") is not None and a.get("out_frame") is not None):
            self.status.emit("Nothing playable is loaded", True)

    def _goto(self):
        f = self.ctx.parse(self.tc_in.text()) if self.ctx else None
        if f is None or f < 0 or f >= self.ctx.count:
            self.status.emit("Not a valid position for this asset", True)
            return
        self.seek(f)
        self.tc_in.clear()
        self.setFocus()

    def set_inout(self, which):
        a = self.asset
        if which is None:
            a["in_frame"] = a["out_frame"] = None
        else:
            a["in_frame" if which == "in" else "out_frame"] = self.frame
            if a.get("in_frame") is not None and a.get("out_frame") is not None and a["out_frame"] < a["in_frame"]:
                a["in_frame"], a["out_frame"] = a["out_frame"], a["in_frame"]
        service.update_asset(self.session.project, a["id"], {"in_frame": a.get("in_frame"), "out_frame": a.get("out_frame")})
        self.player.set_loop(a.get("in_frame"), a.get("out_frame"))
        self.timeline.set_range(a.get("in_frame"), a.get("out_frame"))
        self._render_header()

    # ---- findings -------------------------------------------------------------------
    def visible_findings(self) -> list[dict]:
        return [f for f in self.asset["findings"] if f["check_key"] != "spec.preset"]

    def decide(self, fid: int, status):
        res = service.decide_finding(self.session.project, fid, status=status)
        self.asset["qc_result"] = res
        self.refresh()

    def finding_note(self, fid: int, text: str):
        f = next((x for x in self.asset["findings"] if x["id"] == fid), None)
        if f and (f.get("reviewer_note") or "") != text:
            service.decide_finding(self.session.project, fid, note=text)
            f["reviewer_note"] = text
            self.status.emit("Note saved", False)

    def markers(self) -> list[tuple[int, str, dict]]:
        """Positions worth jumping to: open or decided findings that are not a plain pass, and every note
        or bookmark. Sorted by frame; findings before notes on the same frame."""
        out = [(f["frame_in"], "finding", f) for f in self.asset["findings"]
               if f.get("frame_in") is not None and f["check_key"] != "seq.range"
               and (f.get("reviewer_status") or f["auto_result"]) not in ("pass", "info")]
        out += [(n["frame"], "note", n) for n in self.asset["notes"] if n.get("frame") is not None]
        return sorted(out, key=lambda m: (m[0], m[1] != "finding"))

    def jump_finding(self, d: int):
        ms = self.markers()
        if not ms:
            self.status.emit("No findings or notes with a position on this asset", True)
            return
        if d > 0:
            m = next((x for x in ms if x[0] > self.frame), ms[0])
        else:
            m = next((x for x in reversed(ms) if x[0] < self.frame), ms[-1])
        self.seek(m[0])
        self.tabs.setCurrentIndex(0 if m[1] == "finding" else 1)
        label = m[2]["title"] if m[1] == "finding" else (m[2].get("text") or "Bookmark")
        self.player.osd(f"{self.ctx.label(m[0])}  {label[:60]}")

    def _marker(self, kind, mid):
        self.tabs.setCurrentIndex(0 if kind == "finding" else 1)

    def _render_tabs(self):
        a = self.asset
        # checks
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)
        vis = self.visible_findings()
        det = [f for f in vis if f["kind"] == "detected" and f["auto_result"] in ("warning", "fail")]
        prob = [f for f in vis if f["kind"] == "measured" and (f.get("reviewer_status") or f["auto_result"]) in ("fail", "warning", "issue")]
        rest = [f for f in vis if f not in det and f not in prob]
        if a["scan_state"] == "error":
            e = QLabel(a.get("scan_error") or "Scan failed")
            e.setWordWrap(True)
            e.setStyleSheet(f"color: {theme.T['fail']};")
            lay.addWidget(e)
        if prob:
            lay.addWidget(self._group_label("Measured problems"))
            for f in prob:
                lay.addWidget(FindingRow(f, self.ctx, self))
        if det:
            hdr = QHBoxLayout()
            hdr.addWidget(self._group_label("Detections — confirm or dismiss"), 1)
            if any(not f.get("reviewer_status") for f in det):
                allb = QToolButton()
                allb.setText("Mark all open…")
                allb.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
                allb.setStyleSheet(f"QToolButton {{ border: 1px solid {theme.T['line2']}; border-radius: 6px; padding: 3px 8px; background: {theme.T['panel2']}; }}"
                                   "QToolButton::menu-indicator { image: none; }")
                menu = QMenu(allb)
                for key, label in (("pass", "Pass"), ("issue", "Issue"), ("na", "N/A")):
                    menu.addAction(label, lambda key=key: self._bulk(key))
                allb.setMenu(menu)
                hdr.addWidget(allb)
            lay.addLayout(hdr)
            for f in det:
                lay.addWidget(FindingRow(f, self.ctx, self))
        if not det and not prob:
            text = checks_state_text(a["scan_state"], bool(vis))
            if text:
                ok = QLabel(text)
                ok.setProperty("muted", True)
                ok.setWordWrap(True)
                ok.setAlignment(Qt.AlignmentFlag.AlignCenter)
                lay.addWidget(ok)
        tog = QToolButton()
        tog.setText(f"{len(rest)} passed / informational checks")
        tog.setCheckable(True)
        tog.setArrowType(Qt.ArrowType.RightArrow)
        tog.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        tog.setStyleSheet("QToolButton { border: 0; background: transparent; color: %s; }" % theme.T["muted"])
        restbox = QWidget()
        rl = QVBoxLayout(restbox)
        rl.setContentsMargins(0, 0, 0, 0)
        for f in rest:
            eff = f.get("reviewer_status") or f["auto_result"]
            row = QHBoxLayout()
            row.addWidget(chip(theme.RESULT_LABEL.get(eff, eff), eff))
            t = QLabel(f"<b style='font-weight:500'>{f['title']}</b> <span style='color:{theme.T['muted']}'>{f.get('measured') or ''}</span>"
                       + (f" <span style='color:{theme.T['faint']}'>(exp {f['expected']})</span>" if f.get("expected") else ""))
            t.setWordWrap(True)
            row.addWidget(t, 1)
            rl.addLayout(row)
        restbox.setVisible(False)
        tog.toggled.connect(lambda on: (restbox.setVisible(on), tog.setArrowType(Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow)))
        tog.setVisible(bool(rest))
        lay.addWidget(tog)
        lay.addWidget(restbox)
        lay.addStretch(1)
        self.checks_scroll.setWidget(box)
        self._render_notes()
        self._render_meta()
        n = len(a["notes"])
        self.tabs.setTabText(0, f"Checks ({len(det) + len(prob)})" if det or prob else "Checks")
        self.tabs.setTabText(1, f"Notes ({n})" if n else "Notes")

    def _group_label(self, text):
        lab = ElideLabel(text.upper())
        lab.setStyleSheet(f"color: {theme.T['muted']}; font-size: 11px; letter-spacing: .06em; font-weight: 600;")
        return lab

    def _bulk(self, key):
        p = self.session.project
        for f in self.asset["findings"]:
            if f["kind"] == "detected" and f["auto_result"] != "pass" and not f.get("reviewer_status"):
                service.decide_finding(p, f["id"], status=key)
        self.refresh()

    # ---- notes -----------------------------------------------------------------------
    def _render_notes(self):
        a = self.asset
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)
        d = self.note_draft
        form = QFrame()
        form.setObjectName("panel")
        fl = QVBoxLayout(form)
        row = QHBoxLayout()
        row.addWidget(QLabel("<b>Edit note</b>" if d and d.get("id") else "<b>New note</b>"))
        at = ElideLabel("at " + self.ctx.label(d["frame"] if d else self.frame))
        at.setFont(theme.mono(11))
        at.setProperty("muted", True)
        row.addWidget(at, 1)
        self.note_range = None
        if a.get("in_frame") is not None and a.get("out_frame") is not None:
            self.note_range = QCheckBox("in/out range")
            self.note_range.setToolTip("Attach the note to the whole in/out range instead of one frame")
            self.note_range.setChecked(bool(d and d.get("frame_out") is not None))
            row.addWidget(self.note_range)
        self.note_sev = QComboBox()
        for k, l in (("info", "Info"), ("warning", "Warning"), ("issue", "Issue")):
            self.note_sev.addItem(l, k)
        if d:
            self.note_sev.setCurrentIndex(max(0, self.note_sev.findData(d.get("severity") or "info")))
        row.addWidget(self.note_sev)
        fl.addLayout(row)
        old = getattr(self, "note_text", None)
        if old is not None:
            try:
                old.removeEventFilter(self)
            except RuntimeError:
                pass  # already deleted with the previous notes pane
        self.note_text = QPlainTextEdit(d.get("text", "") if d else "")
        self.note_text.setPlaceholderText("What did you see? (⌘↩ to save)" if platform.system() == "Darwin" else "What did you see? (Ctrl+Enter to save)")
        self.note_text.setFixedHeight(64)
        self.note_text.installEventFilter(self)
        fl.addWidget(self.note_text)
        brow = QHBoxLayout()
        hint = ElideLabel("Visual observations are reported separately from measured results.")
        hint.setProperty("faint", True)
        brow.addWidget(hint, 1)
        if d:
            brow.addWidget(btn("Cancel", "", self._cancel_note, ghost=True))
        brow.addWidget(btn("Save note", "⌘↩", self.save_note, primary=True))
        fl.addLayout(brow)
        lay.addWidget(form)
        if a["kind"] in ("video", "audio"):
            srow = QHBoxLayout()
            s = ElideLabel("Markers made in IINA with the IINfo plugin are added here on each scan.")
            s.setProperty("faint", True)
            srow.addWidget(s, 1)
            srow.addWidget(btn("Sync with IINfo", f"Write all markers and notes to {a['name']}.iinfo.json so IINfo shows them in IINA",
                               self.sync_iinfo))
            lay.addLayout(srow)
        if not a["notes"]:
            e = QLabel("No notes or bookmarks yet.\nPress n for a note, b for a bookmark.")
            e.setProperty("muted", True)
            e.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(e)
        for n in a["notes"]:
            lay.addWidget(self._note_row(n))
        lay.addStretch(1)
        self.notes_scroll.setWidget(box)
        if d:
            box_edit = self.note_text  # may be rebuilt before the timer fires
            QTimer.singleShot(0, lambda: isValid(box_edit) and box_edit.setFocus())

    def _note_row(self, n: dict) -> QWidget:
        f = QFrame()
        f.setObjectName("panel")
        lay = QHBoxLayout(f)
        lay.setContentsMargins(10, 6, 10, 6)
        kind = "Bookmark" if n["kind"] == "bookmark" else (n.get("severity") or "info")
        lay.addWidget(chip(kind, "info" if kind == "Bookmark" else kind))
        mid = QVBoxLayout()
        mid.setSpacing(0)
        pos = btn(self.ctx.label(n["frame"]) + (f" – {self.ctx.label(n['frame_out'])}" if n.get("frame_out") not in (None, n["frame"]) else ""),
                  "Go to this position", lambda: self.seek(n["frame"]), ghost=True)
        pos.setFont(theme.mono(11))
        pos.setStyleSheet(f"color: {theme.T['focus']}; padding: 0 4px; text-align: left;")
        mid.addWidget(pos, 0, Qt.AlignmentFlag.AlignLeft)
        t = QLabel(n.get("text") or "")
        t.setWordWrap(True)
        mid.addWidget(t)
        by = ElideLabel(f"{n.get('author') or ''} · {fmt_time(n.get('created'))}")
        by.setProperty("faint", True)
        mid.addWidget(by)
        lay.addLayout(mid, 1)
        lay.addWidget(btn("Edit", "", lambda: self._edit_note(n), ghost=True))
        lay.addWidget(btn("✕", "Delete", lambda: self._delete_note(n), ghost=True))
        return f

    def start_note(self):
        self.note_draft = {"frame": self.frame}
        self.tabs.setCurrentIndex(1)
        self._render_notes()

    def _edit_note(self, n):
        self.note_draft = dict(n)
        self._render_notes()

    def _cancel_note(self):
        self.note_draft = None
        self._render_notes()

    def save_note(self):
        text = self.note_text.toPlainText().strip()
        if not text:
            self.status.emit("Write something first", True)
            return
        a, d = self.asset, self.note_draft or {"frame": self.frame}
        rng = self.note_range is not None and self.note_range.isChecked()
        frame, frame_out = (a["in_frame"], a["out_frame"]) if rng else (d["frame"], None)
        p = self.session.project
        if d.get("id"):
            service.update_note(p, d["id"], text=text, severity=self.note_sev.currentData(), frame=frame, frame_out=frame_out, kind="note")
        else:
            service.add_note(p, a["id"], "note", frame, frame_out, self.note_sev.currentData(), text)
        self.note_draft = None
        self.player.osd("Note saved")
        self.refresh()

    def _delete_note(self, n):
        if QMessageBox.question(self, "Delete note", "Delete this note?") == QMessageBox.StandardButton.Yes:
            service.delete_note(self.session.project, n["id"])
            self.refresh()

    def bookmark(self):
        service.add_note(self.session.project, self.asset["id"], "bookmark", self.frame, None, "info", "Bookmark")
        self.player.osd(f"Bookmark {self.ctx.label(self.frame)}")
        self.status.emit(f"Bookmarked {self.ctx.label(self.frame)}", False)
        self.refresh()

    def sync_iinfo(self):
        a = self.asset
        if QMessageBox.question(self, "Sync with IINfo",
                                f'Write this asset\'s notes and IINfo markers to "{a["name"]}.iinfo.json" beside the media?\n\n'
                                "IINfo then shows them on its timeline in IINA. Markers already in IINfo are kept.") != QMessageBox.StandardButton.Yes:
            return
        try:
            r = service.iinfo_sync(self.session.project, a["id"])
            self.status.emit((f"Added {r['imported']} IINfo marker(s) · " if r["imported"] else "") + f"Wrote {r['events']} marker(s) for IINfo", False)
            self.refresh()
        except Exception as e:
            self.status.emit(str(e), True)

    def eventFilter(self, obj, ev):
        if ev.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange) and obj in (getattr(self, "tc_lab", None), getattr(self, "tc_sub", None)):
            QTimer.singleShot(0, self._reserve_tc)
            return False
        if ev.type() == ev.Type.MouseButtonPress and obj in (self.player, self.still, self.timeline, self.player.parentWidget()):
            self.setFocus()
            return False
        try:
            is_note = obj is self.note_text
        except (AttributeError, RuntimeError):
            is_note = False
        if is_note and ev.type() == ev.Type.KeyPress:
            if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and ev.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
                self.save_note()
                return True
            if ev.key() == Qt.Key.Key_Escape:
                self._cancel_note()
                self.setFocus()
                return True
        return super().eventFilter(obj, ev)

    # ---- metadata -----------------------------------------------------------------
    def _render_meta(self):
        a, m = self.asset, self.asset.get("metadata") or {}
        self.meta_tree.clear()

        def sec(title):
            it = QTreeWidgetItem([title.upper(), ""])
            it.setForeground(0, theme.colour("muted"))
            it.setFirstColumnSpanned(True)
            self.meta_tree.addTopLevelItem(it)

        def kv(k, v):
            if v not in (None, ""):
                self.meta_tree.addTopLevelItem(QTreeWidgetItem([k, str(v)]))

        sec("File")
        kv("Path", a["path"])
        kv("Size", f"{a['size']:,} bytes" if a.get("size") else None)
        kv("Modified", fmt_time(a.get("mtime")))
        kv("Container", m.get("container_long") or a.get("container"))
        kv("Duration", f"{a['duration']:.3f} s" if a.get("duration") is not None else None)
        kv("Bit rate", f"{m['bit_rate'] / 1e6:.1f} Mb/s" if m.get("bit_rate") else None)
        kv("Start timecode", a.get("start_tc"))
        for v in m.get("video_streams") or []:
            sec(f"Video stream #{v['index']}")
            kv("Codec", f"{v.get('codec_long') or v.get('codec')}{' · ' + v['profile'] if v.get('profile') else ''}{' (' + v['codec_tag'] + ')' if v.get('codec_tag') else ''}")
            kv("Resolution", f"{v.get('width')}×{v.get('height')}{' · rotated ' + str(v['rotation']) + '°' if v.get('rotation') else ''}")
            kv("Frame rate", f"{v.get('fps')} ({v.get('fps_float')}){' · VFR suspected' if v.get('vfr') else ''}")
            kv("Frames", v.get("frames"))
            kv("Scan", v.get("field_order"))
            kv("PAR / DAR", f"{v.get('sar')} / {v.get('dar')}")
            kv("Pixel format", v.get("pix_fmt"))
            kv("Bit depth", f"{v['bit_depth']}-bit{' float' if v.get('float') else ''}" if v.get("bit_depth") else None)
            kv("Chroma", v.get("chroma"))
            kv("Alpha", "yes" if v.get("alpha") else "no")
            kv("Primaries", v.get("color_primaries") or "unspecified")
            kv("Transfer", v.get("color_trc") or "unspecified")
            kv("Matrix", v.get("color_space") or "unspecified")
            kv("Range", v.get("color_range") or "unspecified")
        for s in m.get("audio_streams") or []:
            sec(f"Audio stream #{s['index']}")
            kv("Codec", s.get("codec_long") or s.get("codec"))
            kv("Layout", f"{s.get('layout')} · {s.get('channels')} ch")
            kv("Sample rate", f"{s['sample_rate']} Hz" if s.get("sample_rate") else None)
            kv("Sample format", f"{s.get('sample_fmt') or ''}{' · ' + str(s['bit_depth']) + '-bit' if s.get('bit_depth') else ''}")
            kv("Language", s.get("language"))
        an = m.get("analysis")
        if an:
            sec("Analysis")
            L = an.get("loudness") or {}
            kv("Integrated", f"{L['integrated']} LUFS" if L.get("integrated") is not None else None)
            kv("LRA", f"{L['lra']} LU" if L.get("lra") is not None else None)
            kv("True peak", f"{L['true_peak']} dBTP" if L.get("true_peak") is not None else None)
            kv("Decoded", f"{an['analysed_seconds']:.2f} s" if an.get("analysed_seconds") else None)
            kv("Decoder errors", an.get("decode_error_count"))
        if a["kind"] == "sequence" and a.get("seq"):
            s, h = a["seq"], m.get("header") or {}
            sec("Sequence")
            kv("Pattern", s.get("pattern"))
            kv("Folder", s.get("dir"))
            kv("Range", f"{s['first']}–{s['last']}")
            kv("Frames present", f"{s.get('count')} of {s.get('expected')}")
            kv("Header", f"{h['format']} · {h.get('width')}×{h.get('height')} · {h.get('bit_depth')}-bit · {h.get('channels')} ch" if h.get("format") else None)
        if m.get("format_tags"):
            sec("Container tags")
            for k, v in list(m["format_tags"].items())[:30]:
                kv(k, v)
        self.meta_tree.resizeColumnToContents(0)

    # ---- review status, IINA, Finder ----------------------------------------------
    def set_review(self, status: str):
        service.update_asset(self.session.project, self.asset["id"], {"review_status": status})
        self.refresh()
        unresolved = sum(1 for f in self.asset["findings"] if f["kind"] == "detected" and f["auto_result"] in ("warning", "fail") and not f.get("reviewer_status"))
        if status in ("approved", "approved_notes") and unresolved:
            self.status.emit(f"Signed off with {unresolved} detection(s) still unconfirmed", True)
        else:
            self.status.emit(f"{self.asset['name']}: {theme.REVIEW_LABEL[status]}", False)

    def open_in_iina(self):
        br = self.session.iina
        if not br or not self.asset:
            return
        try:
            self.player.set_pause(True)
            st = br.open(self.asset, self.frame)
            if st.get("error"):
                self.status.emit(st["error"], True)
            elif self.asset.get("in_frame") is not None and self.asset.get("out_frame") is not None:
                br.set_loop(self.asset["in_frame"], self.asset["out_frame"])
        except Exception as e:
            self.status.emit(str(e), True)

    def _reveal(self):
        path = self.asset["dir"] if self.asset["kind"] == "sequence" else self.asset["path"]
        if os.path.exists(path):
            subprocess.run(["open", "-R", path])

    # ---- keyboard ---------------------------------------------------------------------
    def handle_key(self, ev) -> bool:
        """Review shortcuts, when focus is not in a text field. Returns True if handled."""
        if not self.asset:
            return False
        k, mods, txt = ev.key(), ev.modifiers(), ev.text()
        fps = max(1, round(float(self.ctx.rate)))
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        if k == Qt.Key.Key_Left:
            self.step(-fps if shift else -1)
        elif k == Qt.Key.Key_Right:
            self.step(fps if shift else 1)
        elif k == Qt.Key.Key_Space:
            self.toggle_play()
        elif k == Qt.Key.Key_Home:
            self.seek(0)
        elif k == Qt.Key.Key_End:
            self.seek(self.ctx.count - 1)
        elif k == Qt.Key.Key_BracketLeft or txt == "[":
            self.jump_finding(-1)
        elif k == Qt.Key.Key_BracketRight or txt == "]":
            self.jump_finding(1)
        elif txt == "e":
            self.open_in_iina()
        elif txt == "g":
            self.tc_in.setFocus()
        elif txt == "i":
            self.set_inout("in")
        elif txt == "o":
            self.set_inout("out")
        elif txt == "b":
            self.bookmark()
        elif txt == "n":
            self.start_note()
        elif txt in ("j", "k"):
            vis = [f for f in self.visible_findings() if f["auto_result"] not in ("pass", "info") or f.get("reviewer_status")]
            if not vis:
                return True
            self.focus_idx = min(len(vis) - 1, self.focus_idx + 1) if txt == "j" else max(0, self.focus_idx - 1)
            f = vis[self.focus_idx]
            self.tabs.setCurrentIndex(0)
            if f.get("frame_in") is not None and f["check_key"] != "seq.range":
                self.seek(f["frame_in"])
        elif shift and shifted_digit(ev) in (1, 2, 3, 4, 0):
            # ⇧1–⇧4 decide the focused finding; plain 1–6 stay free for switching screens
            d = shifted_digit(ev)
            vis = [f for f in self.visible_findings() if f["auto_result"] not in ("pass", "info") or f.get("reviewer_status")]
            f = vis[self.focus_idx if self.focus_idx >= 0 else 0] if vis else None
            if f:
                self.decide(f["id"], {1: "pass", 2: "issue", 3: "warning", 4: "na", 0: None}[d])
        elif txt == ",":
            self._nav(-1)
        elif txt == ".":
            self._nav(1)
        elif txt == "a":
            self.set_review("approved")
        elif txt == "r":
            self.set_review("rejected")
        else:
            return False
        return True

    def shutdown(self):
        self.playhead.stop()
        self.player.shutdown()
