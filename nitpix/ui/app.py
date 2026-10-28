"""Main window: rail navigation, the screens, scan jobs and the project lifecycle."""
from __future__ import annotations

import os
import platform
import subprocess
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QSurfaceFormat
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
                               QPushButton, QScrollArea, QSpinBox, QStackedWidget, QTableView, QTextEdit, QVBoxLayout,
                               QWidget, QAbstractItemView)

from .. import __version__, demo, libmpv, reports, service, updates, variant
from ..probe import tool_versions
from ..store import recent_projects
from . import deps, display, theme
from .media import AssetModel, MediaScreen, setup_columns
from .mpvwidget import gl_format
from .presets import PresetsScreen
from .prefs import EXPERIMENTAL_UPDATES, prefs
from .shortcuts import ShortcutsDialog
from .review import ReviewScreen, btn, chip
from .widgets import ElideLabel, nav_icon, tidy_combo

VIEWS = ["dashboard", "media", "review", "presets", "reports", "settings"]   # number keys 1-6, as before Mapping
NAV = ["dashboard", "media", "review", "mapping", "presets", "reports", "settings"]   # the sidebar, top to bottom
VIEW_LABEL = {"dashboard": "Dashboard", "media": "Media", "review": "Review", "presets": "Specs", "reports": "Reports",
              "settings": "Settings", "mapping": "Mapping"}
ROOT = Path(__file__).resolve().parent.parent.parent


def brand_lockup(size=24):
    """Shared outlined artwork in the start screen and project sidebar."""
    brand_dir = Path(__file__).resolve().parent.parent / "web" / "branding"
    widget = QWidget()
    widget.setObjectName("brand")  # transparent in the stylesheet: it sits on the rail's panel colour
    widget.setAccessibleName("nitpix")
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(round(size / 4))
    for asset, width in (("mark.svg", size), ("wordmark.svg", round(size * 2.6))):
        logo = QSvgWidget(str(brand_dir / asset))
        logo.renderer().setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)
        logo.setFixedSize(width, size)
        logo.setObjectName("brand")
        layout.addWidget(logo)
    layout.addStretch()
    widget.setFixedHeight(size)
    return widget


def fmt_bytes(n):
    if not n:
        return "0 B"
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:.1f} {u}" if u != "B" else f"{n} B"
        n /= 1024


TEXT_WIDGETS = (QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox)


def text_focused(w=None) -> bool:
    w = w if w is not None else QApplication.focusWidget()
    while w is not None:  # a line edit inside a combo box, a viewport inside a text edit
        if isinstance(w, TEXT_WIDGETS):
            return True
        w = w.parentWidget()
    return False


# ---- small screens ---------------------------------------------------------------------
class StartScreen(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        lay = QVBoxLayout(self)
        lay.setContentsMargins(60, 60, 60, 60)
        lay.addWidget(brand_lockup(48))
        lay.addSpacing(20)
        h = QLabel("Delivery QC, frame by frame.")
        h.setProperty("h1", True)
        lay.addWidget(h)
        p = QLabel("Scan video, audio, still and image-sequence deliveries, check them against a delivery spec, then "
                   "confirm every automated finding on the source media.")
        p.setProperty("muted", True)
        p.setWordWrap(True)
        lay.addWidget(p)
        row = QHBoxLayout()
        row.addWidget(btn("Open project…", "", self.win.open_dialog, primary=True))
        row.addWidget(btn("New project…", "", self.win.new_dialog))
        row.addWidget(btn("Load demo delivery", "Generates a small demo delivery and scans it", self.win.load_demo))
        row.addStretch(1)
        lay.addLayout(row)
        lay.addSpacing(20)
        lay.addWidget(QLabel("<b>Recent projects</b>"))
        self.recent = QListWidget()
        self.recent.itemActivated.connect(lambda it: self.win.open_project(it.data(Qt.ItemDataRole.UserRole)))
        lay.addWidget(self.recent, 1)
        self.refresh()

    def refresh(self):
        self.recent.clear()
        for r in recent_projects():
            it = QListWidgetItem(f"{r.get('name') or Path(r['path']).stem}    {r['path']}")
            it.setData(Qt.ItemDataRole.UserRole, r["path"])
            it.setFont(theme.mono(12))
            self.recent.addItem(it)


class AddMediaDialog(QDialog):
    def __init__(self, win: "MainWindow", roots: list[str]):
        super().__init__(win)
        self.setWindowTitle("Add media")
        self.resize(640, 360)
        lay = QVBoxLayout(self)
        hint = QLabel("Files or folders to scan, one per line. Folders are scanned recursively. Image sequences: add the folder "
                      "that holds the frames, or any one frame of it; frames named like name.####.ext become one asset.")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.paths = QPlainTextEdit("\n".join(roots))
        self.paths.setFont(theme.mono(12))
        lay.addWidget(self.paths, 1)
        row = QHBoxLayout()
        row.addWidget(btn("Choose folder…", "Scan a delivery folder: videos, audio, stills and image sequences", self._folder))
        row.addWidget(btn("Choose files…", "", self._files))
        row.addWidget(btn("Choose image sequence…", "Pick any one frame; the whole sequence in that folder is added", self._sequence))
        row.addStretch(1)
        self.force = QCheckBox("Re-check files that haven't changed")
        row.addWidget(self.force)
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        scan = bb.addButton("Scan", QDialogButtonBox.ButtonRole.AcceptRole)
        scan.setProperty("primary", True)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _folder(self):
        d = QFileDialog.getExistingDirectory(self, "Choose a folder to scan")
        if d:
            self.paths.appendPlainText(d)

    def _files(self):
        fs, _ = QFileDialog.getOpenFileNames(self, "Choose files to scan")
        for f in fs:
            self.paths.appendPlainText(f)

    def _sequence(self):
        f, _ = QFileDialog.getOpenFileName(self, "Choose any one frame of the image sequence", "",
                                           "Image frames (*.exr *.dpx *.tif *.tiff *.png *.jpg *.jpeg *.tga *.cin);;All files (*)")
        if f:
            self.paths.appendPlainText(f)

    def roots(self) -> list[str]:
        return [l.strip() for l in self.paths.toPlainText().splitlines() if l.strip()]


class JobBar(QFrame):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.setObjectName("panel")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 6, 12, 6)
        self.label = ElideLabel()
        self.label.setStyleSheet("font-weight: 600;")
        self.phase = ElideLabel()
        self.phase.setProperty("muted", True)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setFixedWidth(160)
        self.current = ElideLabel()
        self.current.setFont(theme.mono(11))
        self.current.setProperty("muted", True)
        self.pause = btn("Pause", "", self._pause)
        self.cancel = btn("Cancel", "", self._cancel, ghost=True)
        lay.addWidget(self.label, 1)
        lay.addWidget(self.phase, 1)
        lay.addWidget(self.bar)
        lay.addWidget(self.current, 2)
        lay.addWidget(self.pause)
        lay.addWidget(self.cancel)
        self.hide()

    def show_job(self, j: dict | None):
        if not j or j["state"] in ("done", "error", "cancelled"):
            self.hide()
            return
        self.show()
        eta = f" · ETA {int(j['eta'] // 60)}:{int(j['eta'] % 60):02d}" if j.get("eta") else ""
        self.label.setText(j["label"])
        self.phase.setText(f"{j['phase']} {j['done']}/{j['total']}{eta}")
        self.bar.setValue(int(j["progress"] * 1000))
        self.current.setText("  ".join(f"{c['name']} {int(c.get('progress', 0) * 100)}%" for c in j["current"][:3]))
        self.pause.setText("Resume" if j["state"] == "paused" else "Pause")

    def _pause(self):
        jobs = self.win.session.jobs
        j = jobs.active if jobs else None
        if j:
            jobs.resume() if j.state == "paused" else jobs.pause()

    def _cancel(self):
        if self.win.session.jobs:
            self.win.session.jobs.cancel()


class DashboardScreen(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(12)
        self.title = QLabel()
        self.title.setProperty("h1", True)
        self.title.setWordWrap(True)
        self.sub = ElideLabel()
        self.sub.setProperty("muted", True)
        lay.addWidget(self.title)
        lay.addWidget(self.sub)
        self.kpis = QGridLayout()
        lay.addLayout(self.kpis)
        lay.addWidget(QLabel("<b>Needs attention</b>"))
        self.model = AssetModel()
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        setup_columns(self.table)
        self.table.doubleClicked.connect(lambda ix: self.win.show_asset(self.model.rows[ix.row()]["id"]))
        lay.addWidget(self.table, 1)
        self.issues = QLabel()
        self.issues.setWordWrap(True)
        self.issues.setProperty("muted", True)
        lay.addWidget(self.issues)

    def refresh(self):
        p = self.win.session.project
        d = service.dashboard(p)
        s = p.settings
        self.title.setText(s.get("project_name") or "Untitled delivery")
        pr = self.win.session.preset()
        self.sub.setText(f"{s.get('client') or 'No client'} · Spec: {pr.get('name') or 'none'}")
        while self.kpis.count():
            w = self.kpis.takeAt(0).widget()
            if w:
                w.hide()
                w.setParent(None)  # gone now, not on the next event-loop pass
                w.deleteLater()
        rv = d["by_review"]
        signed = sum(rv.get(k, 0) for k in ("approved", "approved_notes", "rejected"))
        cards = [("Assets", f"{d['assets']['n'] or 0}", fmt_bytes(d["assets"]["bytes"] or 0)),
                 ("Fail", str(d["by_result"].get("fail", 0)), "assets with failed checks"),
                 ("Warning", str(d["by_result"].get("warning", 0)), "assets to look at"),
                 ("Pass", str(d["by_result"].get("pass", 0)), "assets clean"),
                 ("Unconfirmed detections", str(d["open_detections"]), "need a reviewer decision"),
                 ("Signed off", f"{signed} / {d['assets']['n'] or 0}", f"{rv.get('approved', 0)} approved · {rv.get('approved_notes', 0)} w/ notes · {rv.get('rejected', 0)} rejected")]
        for i, (lab, val, sub) in enumerate(cards):
            card = QFrame()
            card.setObjectName("panel")
            cl = QVBoxLayout(card)
            l1 = QLabel(lab.upper())
            l1.setStyleSheet(f"color: {theme.T['muted']}; font-size: 11px; letter-spacing: .06em;")
            l2 = QLabel(val)
            l2.setStyleSheet("font-size: 24px; font-weight: 600;")
            l3 = ElideLabel(sub)
            l3.setProperty("faint", True)
            cl.addWidget(l1)
            cl.addWidget(l2)
            cl.addWidget(l3)
            self.kpis.addWidget(card, i // 3, i % 3)
        self.model.set_rows(d["needs_attention"])
        top = ", ".join(f"{t['check_key']} ({t['n']} on {t['assets']} assets)" for t in d["top_issues"][:6])
        self.issues.setText(("Most common problems: " + top) if top else "No problems recorded yet.")


class ReportsScreen(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(12)
        h = QLabel("Reports")
        h.setProperty("h1", True)
        lay.addWidget(h)
        p = QLabel("Reports include file paths, metadata, every check with its reason, automated detections with reviewer "
                   "decisions, reviewer notes and sign-off status. Files are written to the project's exports folder.")
        p.setWordWrap(True)
        p.setProperty("muted", True)
        lay.addWidget(p)
        row = QHBoxLayout()
        self.scope = QComboBox()
        self.scope.addItem("All assets", "all")
        self.scope.addItem("Assets matching the Media filter", "filter")
        self.scope.addItem("Selected assets", "selection")
        row.addWidget(self.scope)
        for fmt in ("csv", "json", "pdf"):
            row.addWidget(btn(f"Export {fmt.upper()}", "", lambda _, fmt=fmt: self._export(fmt)))
        row.addWidget(btn("Open exports folder", "", self._open_folder, ghost=True))
        row.addStretch(1)
        lay.addLayout(row)
        self.files = QListWidget()
        self.files.setFont(theme.mono(12))
        self.files.itemActivated.connect(lambda it: self._reveal(it.text()))
        lay.addWidget(self.files, 1)
        so = QFrame()
        so.setObjectName("panel")
        form = QFormLayout(so)
        self.so_status = QComboBox()
        for k, l in (("approved", "Approved for delivery"), ("approved_notes", "Approved with notes"), ("rejected", "Rejected — redeliver")):
            self.so_status.addItem(l, k)
        self.so_by = QLineEdit()
        self.so_note = QLineEdit()
        form.addRow(QLabel("<b>Batch sign-off</b>"))
        form.addRow("Decision", self.so_status)
        form.addRow("Signed by", self.so_by)
        form.addRow("Note", self.so_note)
        brow = QHBoxLayout()
        brow.addWidget(btn("Record sign-off", "", self._signoff, primary=True))
        brow.addWidget(btn("Clear", "", lambda: self._signoff(clear=True), ghost=True))
        self.so_state = QLabel()
        self.so_state.setProperty("muted", True)
        brow.addWidget(self.so_state, 1)
        form.addRow(brow)
        lay.addWidget(so)

    def refresh(self):
        p = self.win.session.project
        self.files.clear()
        exp = p.safe_path("exports")
        for f in sorted(exp.glob("*"), key=lambda f: f.stat().st_mtime, reverse=True)[:40]:
            self.files.addItem(f.name)
        so = p.get_meta("signoff")
        self.so_by.setText((so or {}).get("by") or p.settings.get("reviewer") or "")
        self.so_state.setText(f"Signed off: {so['status']} by {so['by']} · {time.strftime('%d %b %Y %H:%M', time.localtime(so['at']))}" if so else "Not signed off yet.")

    def _ids(self):
        s = self.scope.currentData()
        if s == "filter":
            return self.win.media.visible_ids()
        if s == "selection":
            return self.win.media.selected_ids()
        return None

    def _export(self, fmt):
        p = self.win.session.project
        try:
            fn = {"csv": reports.export_csv, "json": reports.export_json, "pdf": reports.export_pdf}[fmt]
            path = fn(p, self._ids())
            self.win.toast(f"Wrote {Path(path).name}")
            self.refresh()
        except Exception as e:
            self.win.toast(str(e), True)

    def _open_folder(self):
        self._reveal(None)

    def _reveal(self, name):
        p = self.win.session.project.safe_path("exports", name) if name else self.win.session.project.safe_path("exports")
        if platform.system() == "Darwin":
            subprocess.run(["open", "-R" if name else "", str(p)] if name else ["open", str(p)])
        elif platform.system() == "Windows":
            os.startfile(str(p))  # noqa: S606
        else:
            subprocess.run(["xdg-open", str(p.parent if name else p)])

    def _signoff(self, clear=False):
        p = self.win.session.project
        service.signoff(p, None if clear else self.so_status.currentData(), self.so_by.text().strip(), self.so_note.text().strip())
        self.refresh()
        self.win.toast("Sign-off cleared" if clear else "Sign-off recorded")


class SettingsScreen(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        h = QLabel("Settings")
        h.setProperty("h1", True)
        outer.addWidget(h)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        box = QWidget()
        form = QFormLayout(box)
        self.name = QLineEdit()
        self.client = QLineEdit()
        self.reviewer = QLineEdit()
        self.reviewer.setToolTip("Stamped on decisions, notes and sign-offs")
        self.preset = QComboBox()
        self.workers = QSpinBox()
        self.workers.setRange(1, 8)
        self.content = QCheckBox("Black / freeze / flash / silence / clipping detection")
        self.loudness = QCheckBox("Loudness (EBU R128)")
        self.max_secs = QSpinBox()
        self.max_secs.setRange(0, 36000)
        self.max_secs.setSpecialValueText("whole file")
        self.max_secs.setSuffix(" s")
        self.iina_cli = QLineEdit()
        self.iina_mode = QComboBox()
        self.iina_mode.addItem("Per session (default)", "per_session")
        self.iina_mode.addItem("Fixed socket", "fixed")
        self.iina_sock = QLineEdit()
        form.addRow(QLabel("<b>Project</b>"))
        form.addRow("Project name", self.name)
        form.addRow("Client", self.client)
        form.addRow("Reviewer (your name)", self.reviewer)
        form.addRow("Delivery spec", self.preset)
        form.addRow(QLabel("<b>Analysis</b>"))
        form.addRow("Parallel workers", self.workers)
        form.addRow("", self.content)
        form.addRow("", self.loudness)
        form.addRow("Decode at most", self.max_secs)
        form.addRow(QLabel("<b>IINA (optional, macOS)</b>"))
        form.addRow("iina-cli path", self.iina_cli)
        form.addRow("Socket mode", self.iina_mode)
        form.addRow("Fixed socket path", self.iina_sock)
        self.tools = QLabel()
        self.tools.setProperty("muted", True)
        self.tools.setWordWrap(True)
        form.addRow(QLabel("<b>Tools</b>"))
        form.addRow(self.tools)
        self.fix_btn = btn("Install missing components…", "", lambda: (deps.check(self), self.refresh(), self.win.refresh_rail()))
        form.addRow("", self.fix_btn)
        form.addRow(QLabel("<b>Updates</b>"))
        urow = QHBoxLayout()
        self.version_lab = QLabel(f"nitpix {__version__}")
        urow.addWidget(self.version_lab)
        self.update_btn = btn("Check for updates", "Asks GitHub for the latest release", lambda: self.win.check_updates(manual=True))
        urow.addWidget(self.update_btn)
        urow.addStretch(1)
        form.addRow(urow)
        self.auto_updates = QCheckBox("Check for updates when nitpix starts (at most once a day)")
        self.auto_updates.toggled.connect(lambda on: prefs().setValue("updates/auto", on))
        form.addRow("", self.auto_updates)
        if variant.IS_EXPERIMENTAL:  # updates are the release's business
            self.version_lab.setText(f"nitpix {__version__} (experimental build)")
            self.update_btn.setEnabled(False)
            self.auto_updates.setEnabled(False)
            note = QLabel(EXPERIMENTAL_UPDATES)
            note.setWordWrap(True)
            note.setProperty("muted", True)
            form.addRow("", note)
        row = QHBoxLayout()
        row.addWidget(btn("Save settings", "", self.save, primary=True))
        row.addStretch(1)
        form.addRow(row)
        scroll.setWidget(box)
        outer.addWidget(scroll, 1)

    def refresh(self):
        p = self.win.session.project
        s = p.settings
        self.name.setText(s.get("project_name") or "")
        self.client.setText(s.get("client") or "")
        self.reviewer.setText(s.get("reviewer") or "")
        self.preset.clear()
        for pr in service.presets(p)["presets"]:
            self.preset.addItem(pr["name"], pr["id"])
        self.preset.setCurrentIndex(max(0, self.preset.findData(s.get("preset_id"))))
        an = s["analysis"]
        self.workers.setValue(int(an.get("workers", 2)))
        self.content.setChecked(bool(an.get("content_checks", True)))
        self.loudness.setChecked(bool(an.get("loudness", True)))
        self.max_secs.setValue(int(an.get("max_analysis_seconds") or 0))
        ii = s["iina"]
        self.iina_cli.setText(ii.get("cli_path") or "")
        self.iina_mode.setCurrentIndex(max(0, self.iina_mode.findData(ii.get("socket_mode"))))
        self.iina_sock.setText(ii.get("fixed_socket") or "")
        tv = tool_versions()
        mpv = f"libmpv {libmpv.version()} at {libmpv.LIBMPV}" if libmpv.available() else f"libmpv not found ({libmpv.MPV_ERROR}); the frame viewer is used instead"
        scr = self.window().screen() if self.window() else None
        disp = [f"Display {display.describe(scr)}" + ("  (Low Resolution mode: see Get Info)" if display.low_resolution(
            (scr.geometry().width(), scr.geometry().height()), self.window().devicePixelRatioF()) else "")] if scr else []
        self.tools.setText("\n".join([f"{k}: {v['version'] or 'not found'}" for k, v in tv.items()] + [mpv] + disp))
        self.fix_btn.setVisible(bool(deps.missing()))
        self.auto_updates.blockSignals(True)
        self.auto_updates.setChecked(not variant.IS_EXPERIMENTAL and prefs().value("updates/auto", True, type=bool))
        self.auto_updates.blockSignals(False)

    def save(self):
        p = self.win.session.project
        patch = {"project_name": self.name.text().strip() or "Untitled delivery", "client": self.client.text().strip(),
                 "reviewer": self.reviewer.text().strip(), "preset_id": self.preset.currentData(),
                 "analysis": {"workers": self.workers.value(), "content_checks": self.content.isChecked(),
                              "loudness": self.loudness.isChecked(), "max_analysis_seconds": self.max_secs.value()},
                 "iina": {"cli_path": self.iina_cli.text().strip(), "socket_mode": self.iina_mode.currentData(),
                          "fixed_socket": self.iina_sock.text().strip()}}
        before = p.settings.get("preset_id")
        service.update_settings(p, patch)
        if patch["preset_id"] != before:
            ids = [r["id"] for r in p.q("SELECT id FROM assets")]
            if ids:
                self.win.session.jobs.recheck(ids, reanalyse=False)
        self.win.toast("Settings saved")
        self.win.refresh_rail()


# ---- main window ---------------------------------------------------------------------------
class MainWindow(QMainWindow):
    demo_done = Signal(str)  # from the generator thread; "" on success
    update_result = Signal(dict, bool)  # from the update-check thread: result, manual

    def __init__(self, session: service.Session):
        super().__init__()
        self.demo_done.connect(self._demo_ready)
        self.update_result.connect(self._update_ready)
        self._updating = False
        helpm = self.menuBar().addMenu("Help")
        act = helpm.addAction("Check for Updates…", lambda: self.check_updates(manual=True))
        act.setMenuRole(act.MenuRole.ApplicationSpecificRole)  # macOS shows it in the nitpix menu
        helpm.addAction("Keyboard Shortcuts", self.show_help)
        helpm.addAction("nitpix on GitHub", lambda: QDesktopServices.openUrl(QUrl(updates.RELEASES.rsplit("/releases", 1)[0])))
        self.session = session
        self.setWindowTitle("nitpix")
        self._place_window()  # the minimum size comes from the layouts (about 1040 × 640)
        self.last_job_state = None
        self._list_at = 0.0
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        # rail: icons + labels, collapsible to icons only (remembered)
        rail = QFrame()
        rail.setObjectName("rail")
        rl = QVBoxLayout(rail)
        rl.setContentsMargins(8, 10, 8, 10)
        rl.setSpacing(2)
        top = QHBoxLayout()
        top.setSpacing(0)
        self.brand = brand_lockup()
        top.addWidget(self.brand, 1)
        self.rail_toggle = QPushButton()
        self.rail_toggle.setProperty("nav", True)
        self.rail_toggle.setFixedSize(30, 30)
        self.rail_toggle.setToolTip("Collapse / expand the sidebar")
        self.rail_toggle.clicked.connect(lambda: self.set_rail_collapsed(not self.rail_collapsed))
        top.addWidget(self.rail_toggle)
        rl.addLayout(top)
        self.proj_lab = QLabel()
        self.proj_lab.setWordWrap(True)
        self.proj_lab.setProperty("muted", True)
        self.proj_lab.setContentsMargins(2, 0, 0, 8)
        rl.addWidget(self.proj_lab)
        self.nav = {}
        ink, ink_on = theme.T["ink2"], theme.T["accent_ink"]
        for v in NAV:
            b = QPushButton(VIEW_LABEL[v])
            b.setIcon(nav_icon(v, ink, ink_on))
            b.setIconSize(QSize(18, 18))
            b.setProperty("nav", True)
            b.setCheckable(True)
            b.setToolTip(f"{VIEW_LABEL[v]} ({VIEWS.index(v) + 1})" if v in VIEWS else    # Mapping has no number key
                         "Mapping: the project's media on an imported stage")
            b.clicked.connect(lambda _, v=v: self.show_view(v))
            rl.addWidget(b)
            self.nav[v] = b
        rl.addStretch(1)
        self.tools_lab = QLabel()
        self.tools_lab.setProperty("faint", True)
        self.tools_lab.setContentsMargins(2, 0, 0, 4)
        rl.addWidget(self.tools_lab)
        self.close_btn = QPushButton("Close project")
        self.close_btn.setIcon(nav_icon("close", theme.T["muted"], ink_on))
        self.close_btn.setIconSize(QSize(18, 18))
        self.close_btn.setProperty("nav", True)
        self.close_btn.setToolTip("Close project")
        self.close_btn.clicked.connect(self.close_project)
        rl.addWidget(self.close_btn)
        self.help_lab = QPushButton("?  Shortcuts")
        self.help_lab.setProperty("nav", True)
        self.help_lab.setToolTip("Shortcuts and power moves for this screen (?)")
        self.help_lab.setStyleSheet(f"color: {theme.T['muted']}; font-size: 12px;")
        self.help_lab.clicked.connect(self.show_help)
        rl.addWidget(self.help_lab)
        root.addWidget(rail)
        self.rail = rail
        self.rail_collapsed = False
        self.set_rail_collapsed(prefs().value("rail/collapsed", False, type=bool))
        # centre
        centre = QVBoxLayout()
        centre.setContentsMargins(0, 0, 0, 0)
        centre.setSpacing(0)
        self.stack = QStackedWidget()
        self.mapping = None          # the Mapping workspace, made when first opened
        self.start = StartScreen(self)
        self.dashboard = DashboardScreen(self)
        self.media = MediaScreen()
        self.review = ReviewScreen(session)
        self.presets = PresetsScreen(self)
        self.reports_screen = ReportsScreen(self)
        self.settings_screen = SettingsScreen(self)
        self.screens = {"start": self.start, "dashboard": self.dashboard, "media": self.media, "review": self.review,
                        "presets": self.presets, "reports": self.reports_screen, "settings": self.settings_screen}
        for s in self.screens.values():
            self.stack.addWidget(s)
        centre.addWidget(self.stack, 1)
        self.jobbar = JobBar(self)
        centre.addWidget(self.jobbar)
        root.addLayout(centre, 1)
        # wiring
        self.media.open_asset.connect(self.show_asset)
        self.media.add_media.connect(self.add_media)
        self.media.recheck.connect(self._recheck)
        self.media.remove.connect(self._remove)
        self.media.bulk_review.connect(self._bulk_review)
        self.review.navigate.connect(self.show_asset)
        self.review.asset_changed.connect(lambda a: self._refresh_list(force=True))
        self.review.status.connect(self.toast)
        # toast
        self.toast_lab = QLabel(self)
        self.toast_lab.hide()
        self.toast_timer = QTimer(self, singleShot=True)
        self.toast_timer.timeout.connect(self.toast_lab.hide)
        # job polling
        self.poll = QTimer(self, interval=500)
        self.poll.timeout.connect(self._poll)
        self.poll.start()
        self.show_view("start")
        self.refresh_rail()
        QApplication.instance().installEventFilter(self)
        self.setAcceptDrops(True)
        self._lowres_warned = False
        self.pending_roots: list[str] = []   # dropped while a scan was running; scanned next
        self.drop_hint = QLabel("Drop to scan into this project", self)
        self.drop_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop_hint.setStyleSheet(f"background: rgba(12,12,12,200); color: {theme.T['ink']}; font-size: 20px; font-weight: 600;"
                                     f"border: 2px dashed {theme.T['focus']}; border-radius: 12px;")
        self.drop_hint.hide()

    def eventFilter(self, obj, ev):
        """Transport keys reach the player from anywhere in the review screen: a button that was
        just clicked, the tabs, the metadata tree. Text fields and dialogs keep their keys."""
        if ev.type() == QEvent.Type.Polish and isinstance(obj, QComboBox):  # every dropdown, wherever it is built
            tidy_combo(obj)
        if ev.type() == QEvent.Type.FileOpen:  # a file or folder dropped on the Dock icon / opened with nitpix
            self.ingest([ev.file()])
            return True
        if ev.type() == QEvent.Type.KeyPress and QApplication.activeModalWidget() is None:
            fw = QApplication.focusWidget() or (obj if isinstance(obj, QWidget) else None)
            view = getattr(self, "current_view", None)
            if view == "mapping" and self.mapping is not None and ev.key() == Qt.Key.Key_Space \
                    and not ev.modifiers() and self.mapping.closed_selector(fw):
                return self.mapping.handle_key(ev)          # Space plays/pauses rather than opening the clip / UV list
            if not text_focused(fw) and (fw is None or fw.window() is self):
                if view == "review" and self.review.handle_key(ev):
                    return True
                if view == "mapping" and self.mapping is not None and self.mapping.handle_key(ev):
                    return True
                t = ev.text()
                if t == "?":
                    self.show_help()
                    return True
                if t.isdigit() and 1 <= int(t) <= len(VIEWS) and self.session.project \
                        and not ev.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
                    self.show_view(VIEWS[int(t) - 1])
                    return True
        return super().eventFilter(obj, ev)

    # ---- project lifecycle -------------------------------------------------------
    def open_project(self, path: str, name: str | None = None) -> bool:
        """False when it did not open: an error, or the user kept the current project open."""
        if self.session.project is not None and not self.mapping_released("Opening another project"):
            return False
        try:
            self.session.open(path, name)
        except Exception as e:
            self.toast(str(e), True)
            return False
        self._reset_mapping()
        self.review.asset = None
        self.review.released = False
        self.refresh_rail()
        self.show_view("dashboard")
        return True

    def open_dialog(self):
        d = QFileDialog.getExistingDirectory(self, "Open a project folder (.nitpix)")
        if d:
            self.open_project(d)

    def new_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("New project")
        form = QFormLayout(dlg)
        name = QLineEdit("Untitled delivery")
        folder = QLineEdit(os.path.expanduser("~/nitpix Projects"))
        pick = btn("Choose…", "", lambda: folder.setText(QFileDialog.getExistingDirectory(dlg, "Where to keep the project") or folder.text()))
        row = QHBoxLayout()
        row.addWidget(folder, 1)
        row.addWidget(pick)
        form.addRow("Project name", name)
        form.addRow("Save in", row)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if dlg.exec() == QDialog.DialogCode.Accepted and name.text().strip():
            slug = "".join(c if c.isalnum() or c in "-_ " else "_" for c in name.text().strip()).strip().replace(" ", "_")
            if self.open_project(os.path.join(folder.text().strip(), slug + ".nitpix"), name.text().strip()):
                self.add_media()

    def load_demo(self):
        loc = demo.locations()
        if loc["delivery"].exists():
            return self._demo_ready(None)
        self.toast("Generating the demo delivery with ffmpeg (30–60 s)…")
        self.start.setEnabled(False)

        def work():
            try:
                demo.generate(loc["delivery"], log=lambda *_: None)
                self.demo_done.emit("")
            except Exception as e:
                self.demo_done.emit(str(e))
        threading.Thread(target=work, daemon=True).start()

    def _demo_ready(self, err):
        self.start.setEnabled(True)
        if err:
            return self.toast("Demo generation failed: " + err[-300:], True)
        loc = demo.locations()
        if self.open_project(str(loc["project"]), demo.PROJECT_NAME) and self.session.project:
            self.session.project.update_settings({"client": "Demo client", "preset_id": "lhs_hd_prores_25"})
            self.session.jobs.scan_roots([str(loc["delivery"])])

    def close_project(self) -> bool:
        if not self.mapping_released("Closing the project"):
            return False
        try:
            self._reset_mapping()
            self.review.player.unload()
            self.review.asset = None
            self.review.released = False
            self.session.close()
        except Exception as e:
            self.toast(str(e), True)
            return False
        self.start.refresh()
        self.show_view("start")
        self.refresh_rail()
        return True

    def mapping_released(self, action: str) -> bool:
        """Mapping's unsaved setup written into the project still open, before it closes. If that save
        fails, the user chooses: try again, go on without those changes, or keep the project open
        (False: nothing has been closed, stopped or torn down)."""
        if self.mapping is None:
            return True
        while (why := self.mapping.settle()) is not None:
            choice = self.ask_unsaved_mapping(action, why)
            if choice == "discard":
                self.mapping.discard_unsaved()
                return True
            if choice != "retry":
                return False
        return True

    def ask_unsaved_mapping(self, action: str, why: str) -> str:
        """"retry", "discard" or "cancel"."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Mapping setup not saved")
        box.setText("The Mapping setup could not be saved into the project.")
        box.setInformativeText(f"{why}\n\n{action} now would lose the Mapping changes made since the last save. "
                               "The rest of the project is not affected.")
        retry = box.addButton("Retry save", QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton("Don't save Mapping changes", QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(retry)
        box.setEscapeButton(cancel)
        box.exec()
        return {retry: "retry", discard: "discard"}.get(box.clickedButton(), "cancel")

    # ---- Mapping ------------------------------------------------------------------------------
    def ensure_mapping(self):
        """The Mapping workspace, made on first use: no player or GL resources until then."""
        if self.mapping is None:
            from .mapping import MappingScreen
            self.mapping = MappingScreen(self.session)
            self.mapping.status.connect(self.toast)
            self.screens["mapping"] = self.mapping
            self.stack.addWidget(self.mapping)
        return self.mapping

    def _reset_mapping(self):
        if self.mapping is not None:
            if getattr(self, "current_view", None) == "mapping":
                self.current_view = "dashboard"
            self.mapping.reset_project()

    # ---- views ---------------------------------------------------------------------
    def show_view(self, v: str):
        if v != "start" and not self.session.project:
            v = "start"
        if v == "mapping":
            self.ensure_mapping()
        if getattr(self, "current_view", None) == "mapping" and v != "mapping":
            self._handoff = self.mapping.deactivate()      # one playback session: Mapping lets go first
        if v == "review" and self.review.released:
            self.stack.setCurrentWidget(self.review)       # shown first, so the player has its GL context
            handoff, self._handoff = getattr(self, "_handoff", None), None
            self.review.resume_playback(handoff)
            for k, b in self.nav.items():
                b.setChecked(k == "review")
            self.current_view = "review"
            self._title(self.review.asset["name"] if self.review.asset else None)
            self.review.setFocus()
            return
        if v == "mapping" and getattr(self, "current_view", None) != "mapping":
            self.stack.setCurrentWidget(self.mapping)      # shown first, so its view has a GL context
            self.mapping.activate(self.review.release_playback())
        if v == "review" and not self.review.asset:
            ids = self.media.visible_ids() or [r["id"] for r in service.list_assets(self.session.project)]
            if not ids:
                self.toast("No assets yet. Add media first.", True)
                v = "media"
            else:
                return self.show_asset(ids[0])
        self.stack.setCurrentWidget(self.screens[v])
        for k, b in self.nav.items():
            b.setChecked(k == v)
        self.rail.setVisible(v != "start")
        self.refresh_rail()
        if v == "dashboard":
            self.dashboard.refresh()
        elif v == "media":
            self._refresh_list(force=True)
            self.media.table.setFocus()
        elif v == "presets":
            self.presets.refresh()
        elif v == "reports":
            self.reports_screen.refresh()
        elif v == "settings":
            self.settings_screen.refresh()
        self.current_view = v
        self._title()

    def _title(self, asset_name: str | None = None):
        p = self.session.project
        bits = [b for b in (asset_name, p.settings.get("project_name") if p else None,
                            "nitpix (experimental)" if variant.IS_EXPERIMENTAL else "nitpix") if b]
        self.setWindowTitle(" — ".join(bits))

    def _place_window(self):
        """Fit the window to this screen; restore the last size and position when it still fits."""
        screen = self.screen() or QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else None
        st = prefs()
        geo = st.value("window/geometry")
        if geo is not None and self.restoreGeometry(geo) and (avail is None or avail.contains(self.frameGeometry().center())):
            if avail is None or (self.width() <= avail.width() and self.height() <= avail.height()):
                return
        if avail is None:
            return self.resize(1280, 800)
        w, h = min(1500, int(avail.width() * 0.92)), min(920, int(avail.height() * 0.9))
        self.resize(w, h)
        self.move(avail.x() + (avail.width() - w) // 2, avail.y() + (avail.height() - h) // 2)

    def show_asset(self, aid: int):
        if getattr(self, "current_view", None) == "mapping":
            self.mapping.deactivate()
        self._handoff = None                               # a specific asset was asked for
        self._refresh_list(force=True)
        self.review.set_order(self.media.visible_ids() or [r["id"] for r in self.media.model.rows])
        self.stack.setCurrentWidget(self.review)  # shown first, so the player has its GL context when it loads
        self.review.load_asset(aid)
        for k, b in self.nav.items():
            b.setChecked(k == "review")
        self.current_view = "review"
        self._title(self.review.asset["name"] if self.review.asset else None)
        self.review.setFocus()

    def set_rail_collapsed(self, collapsed: bool):
        """Icons only (52 px) or icons with labels (168 px)."""
        self.rail_collapsed = bool(collapsed)
        c = self.rail_collapsed
        self.rail.setFixedWidth(52 if c else 168)
        self.brand.setVisible(not c)
        self.rail_toggle.setIcon(nav_icon("expand" if c else "collapse", theme.T["muted"], theme.T["accent_ink"]))
        self.rail_toggle.setIconSize(QSize(18, 18))
        for v, b in self.nav.items():
            b.setText("" if c else VIEW_LABEL[v])
            b.setProperty("iconOnly", c)
            b.style().unpolish(b)
            b.style().polish(b)
        self.close_btn.setText("" if c else "Close project")
        self.help_lab.setText("?" if c else "?  Shortcuts")
        self.close_btn.setProperty("iconOnly", c)
        self.close_btn.style().unpolish(self.close_btn)
        self.close_btn.style().polish(self.close_btn)
        prefs().setValue("rail/collapsed", c)
        self.refresh_rail()

    def refresh_rail(self):
        p = self.session.project
        c = self.rail_collapsed
        if p and not c:
            pr = self.session.preset()
            self.proj_lab.setText(f"<b>{p.settings.get('project_name')}</b><br><span style='font-size:11px'>Spec: {pr.get('name') or 'none'}</span>")
        else:
            self.proj_lab.setText("")
        self.proj_lab.setVisible(bool(p) and not c)
        tv = tool_versions()
        state = [("FFmpeg", tv["ffmpeg"]["available"]), ("libmpv", libmpv.available())]
        if self.session.iina:
            state.append(("IINA", self.session.iina.environment()["available"]))
        self.tools_lab.setText("\n".join(f"{'●' if ok else '○'} {n} {'ready' if ok else 'missing'}" for n, ok in state) if not c
                               else "".join("●" if ok else "○" for _, ok in state))
        self.tools_lab.setToolTip("\n".join(f"{n}: {'ready' if ok else 'missing'}" for n, ok in state))
        self.tools_lab.setWordWrap(not c)
        self.tools_lab.setAlignment(Qt.AlignmentFlag.AlignCenter if c else Qt.AlignmentFlag.AlignLeft)

    # ---- media actions ------------------------------------------------------------
    def _refresh_list(self, force=False):
        if not self.session.project:
            return
        if not force and time.time() - self._list_at < 2.0:
            return
        self._list_at = time.time()
        rows = service.list_assets(self.session.project)
        q = self.media.search.text().strip()
        hits = service.list_assets(self.session.project, check=q[6:]) if q.startswith("check:") else None
        self.media.set_rows(rows, hits)

    def add_media(self):
        p = self.session.project
        roots = [r["path"] for r in p.q("SELECT path FROM roots")]
        dlg = AddMediaDialog(self, roots)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            if not dlg.roots():
                return self.toast("Add at least one file or folder path", True)
            try:
                self.session.jobs.scan_roots(dlg.roots(), force=dlg.force.isChecked())
                self.show_view("media")
            except Exception as e:
                self.toast(str(e), True)

    def _recheck(self, ids):
        self.session.jobs.recheck(ids, reanalyse=True)
        self.toast(f"Re-checking {len(ids)} asset(s)")

    def _remove(self, ids):
        if QMessageBox.question(self, "Remove", f"Remove {len(ids)} asset(s) from the project? Files on disk are untouched.") == QMessageBox.StandardButton.Yes:
            service.remove_assets(self.session.project, ids)
            self._refresh_list(force=True)

    def _bulk_review(self, ids, status):
        service.bulk_review(self.session.project, ids, status)
        self._refresh_list(force=True)
        self.toast(f"{len(ids)} asset(s): {theme.REVIEW_LABEL[status]}")

    # ---- job polling --------------------------------------------------------------
    def _poll(self):
        jobs = self.session.jobs
        if not jobs:
            self.jobbar.hide()
            return
        j = jobs.latest()
        d = j.as_dict() if j else None
        self.jobbar.show_job(d)
        state = d["state"] if d else None
        if state in ("running", "paused"):
            if self.current_view == "media":
                self._refresh_list()
        if self.pending_roots and not jobs.active:
            roots, self.pending_roots = self.pending_roots, []
            try:
                jobs.scan_roots(roots)
                self.toast(f"Scanning {len(roots)} queued item(s)")
            except Exception as e:
                self.toast(str(e), True)
        if state != self.last_job_state and self.last_job_state in ("running", "paused", "cancelling"):
            self._refresh_list(force=True)
            if self.current_view == "dashboard":
                self.dashboard.refresh()
            elif self.current_view == "review" and self.review.asset:
                self.review.refresh()
            if d and d.get("summary"):
                s = d["summary"]
                self.toast(f"Scan {state}: {s.get('media_files', 0)} files, {s.get('sequences', 0)} sequences, {d.get('failed', 0)} errors")
        self.last_job_state = state

    # ---- toast + keys -------------------------------------------------------------------
    def toast(self, text: str, error: bool = False):
        self.toast_lab.setText(text)
        self.toast_lab.setStyleSheet(f"background: {theme.T['fail_bg' if error else 'panel2']}; color: {theme.T['fail' if error else 'ink']}; "
                                     f"border: 1px solid {theme.T['fail' if error else 'line2']}; border-radius: 6px; padding: 8px 14px;")
        self.toast_lab.adjustSize()
        self.toast_lab.move((self.width() - self.toast_lab.width()) // 2, self.height() - self.toast_lab.height() - 24)
        self.toast_lab.show()
        self.toast_lab.raise_()
        self.toast_timer.start(5000 if error else 2500)

    def keyPressEvent(self, ev):
        if text_focused():
            return super().keyPressEvent(ev)
        t = ev.text()
        if self.current_view == "review" and self.review.handle_key(ev):
            return
        if self.current_view == "mapping" and self.mapping is not None and self.mapping.handle_key(ev):
            return
        if t == "?":
            return self.show_help()
        if self.current_view == "review" and ev.key() == Qt.Key.Key_Escape:
            return self.show_view("media")
        if t.isdigit() and 1 <= int(t) <= len(VIEWS) and self.session.project:
            return self.show_view(VIEWS[int(t) - 1])
        super().keyPressEvent(ev)

    # ---- display -------------------------------------------------------------------------
    def check_display(self):
        scr = self.screen()
        if not scr or self._lowres_warned:
            return
        g = scr.geometry()
        if display.low_resolution((g.width(), g.height()), self.devicePixelRatioF()):
            self._lowres_warned = True
            QMessageBox.information(self, "nitpix is in Low Resolution mode",
                                    "This display is Retina, but macOS is running nitpix at standard resolution, "
                                    "so text and pictures look soft.<br><br>To fix it: quit nitpix, select it in "
                                    "Applications, choose <b>File ▸ Get Info</b> and untick <b>Open in Low Resolution</b>, "
                                    "then open nitpix again.")

    def showEvent(self, ev):
        super().showEvent(ev)
        if not getattr(self, "_screen_hooked", False) and self.windowHandle():
            self._screen_hooked = True
            self.windowHandle().screenChanged.connect(lambda _: QTimer.singleShot(300, self.check_display))
        QTimer.singleShot(800, self.check_display)

    # ---- drag and drop ---------------------------------------------------------------
    @staticmethod
    def _dropped_paths(ev) -> list[str]:
        md = ev.mimeData()
        return [u.toLocalFile() for u in md.urls() if u.isLocalFile() and u.toLocalFile()] if md.hasUrls() else []

    def dragEnterEvent(self, ev):
        paths = self._dropped_paths(ev)
        if not paths:
            return ev.ignore()
        ev.acceptProposedAction()
        proj = any(p.rstrip("/").endswith((".nitpix", ".lhsqc")) for p in paths)
        self.drop_hint.setText("Drop to open this project" if proj else
                               ("Drop to scan into this project" if self.session.project else "Drop to start a project with this media"))
        self.drop_hint.setGeometry(self.rect().adjusted(24, 24, -24, -24))
        self.drop_hint.show()
        self.drop_hint.raise_()

    def dragMoveEvent(self, ev):
        ev.acceptProposedAction() if self._dropped_paths(ev) else ev.ignore()

    def dragLeaveEvent(self, ev):
        self.drop_hint.hide()

    def dropEvent(self, ev):
        self.drop_hint.hide()
        paths = self._dropped_paths(ev)
        if paths:
            ev.acceptProposedAction()
            QTimer.singleShot(0, lambda: self.ingest(paths))  # let the drag finish before any dialog

    def ingest(self, paths: list[str]):
        """Take media from anywhere (window drop, Dock icon, Finder "Open With") and scan it now.
        A dropped project folder opens; with no project open, one is made next to the others."""
        paths = [os.path.abspath(p) for p in paths if p and os.path.exists(p)]
        if not paths:
            return
        projects = [p for p in paths if p.rstrip("/").endswith((".nitpix", ".lhsqc"))]
        if projects:
            return self.open_project(projects[0])
        if not self.session.project:
            base = Path(paths[0])
            name = (base.name if base.is_dir() else base.stem) or "Quick review"
            folder = Path(os.path.expanduser("~/nitpix Projects"))
            slug = "".join(c if c.isalnum() or c in "-_ " else "_" for c in name).strip().replace(" ", "_") or "Quick_review"
            target, i = folder / f"{slug}.nitpix", 2
            while target.exists():
                target, i = folder / f"{slug}_{i}.nitpix", i + 1
            self.open_project(str(target), name)
            if not self.session.project:
                return
        jobs = self.session.jobs
        if jobs.active:
            self.pending_roots += [p for p in paths if p not in self.pending_roots]
            self.toast(f"Queued {len(paths)} item(s); they scan when the current batch finishes")
            return
        try:
            jobs.scan_roots(paths)
        except Exception as e:
            return self.toast(str(e), True)
        self.toast(f"Scanning {Path(paths[0]).name}" + (f" and {len(paths) - 1} more" if len(paths) > 1 else ""))
        self.show_view("media")

    # ---- updates ---------------------------------------------------------------------
    def check_updates(self, manual: bool = False):
        """Ask GitHub for the latest release in a thread; the answer comes back as update_result.
        Experimental builds never check: the release is the one that updates."""
        if variant.IS_EXPERIMENTAL:
            if manual:
                self.toast(EXPERIMENTAL_UPDATES)
            return
        st = prefs()
        if not manual:
            if not st.value("updates/auto", True, type=bool):
                return
            if time.time() - float(st.value("updates/last", 0) or 0) < 24 * 3600:
                return
        if self._updating:
            return
        self._updating = True
        if manual:
            self.toast("Checking for updates…")
        threading.Thread(target=lambda: self.update_result.emit(updates.check(), manual), daemon=True).start()

    def _update_ready(self, r: dict, manual: bool):
        self._updating = False
        if variant.IS_EXPERIMENTAL:
            return
        st = prefs()
        if r.get("error"):
            if manual:
                self.toast(r["error"], True)
            return  # a quiet start-up check stays quiet when offline
        st.setValue("updates/last", time.time())
        if not r.get("newer"):
            if manual:
                self.toast(f"nitpix {r['current']} is up to date")
            return
        if not manual and st.value("updates/skip") == r["latest"]:
            return
        box = QMessageBox(self)
        box.setWindowTitle("Update available")
        box.setText(f"<b>nitpix {r['latest']}</b> is available. You have {r['current']}.")
        notes = r.get("notes") or ""
        box.setInformativeText((notes[:600] + ("…" if len(notes) > 600 else "")) if notes else "See the release page for what changed.")
        inst = None
        if r.get("download") and updates.can_install():
            inst = box.addButton("Install and relaunch", QMessageBox.ButtonRole.AcceptRole)
            inst.setProperty("primary", True)
        get = box.addButton("Download" if inst is None else "Download only", QMessageBox.ButtonRole.ActionRole)
        get.setProperty("primary", inst is None)
        box.addButton("Later", QMessageBox.ButtonRole.RejectRole)
        skip = box.addButton("Skip this version", QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(inst or get)
        box.exec()
        if inst is not None and box.clickedButton() is inst:
            self._install_update(r)
        elif box.clickedButton() is get:
            QDesktopServices.openUrl(QUrl(r.get("download") or r["url"]))
            if r.get("download"):
                self.toast("Downloading. Open the DMG and drag nitpix to Applications, replacing this copy.")
        elif box.clickedButton() is skip:
            st.setValue("updates/skip", r["latest"])

    def _install_update(self, r: dict):
        from PySide6.QtWidgets import QProgressDialog
        if variant.IS_EXPERIMENTAL:
            return self.toast(EXPERIMENTAL_UPDATES, True)
        jobs = self.session.jobs
        if jobs and jobs.active:
            return self.toast("Finish or cancel the running scan before updating.", True)
        dlg = QProgressDialog(f"Downloading nitpix {r['latest']}…", "", 0, 100, self)
        dlg.setWindowTitle("Updating nitpix")
        dlg.setCancelButton(None)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        state = {"pct": 0, "done": None, "error": None}

        def work():
            try:
                state["done"] = updates.install(r["download"], progress=lambda d, t: state.__setitem__("pct", int(100 * d / t) if t else 0))
            except Exception as e:
                state["error"] = str(e)

        threading.Thread(target=work, daemon=True).start()
        timer = QTimer(self, interval=150)

        def poll():
            dlg.setValue(min(99, state["pct"]))
            if state["pct"] >= 100:
                dlg.setLabelText("Installing…")
            if state["error"] or state["done"]:
                timer.stop()
                dlg.close()
                if state["error"]:
                    self.toast("Update failed: " + state["error"][-200:], True)
                else:
                    updates.relaunch(state["done"])
                    self.close()
        timer.timeout.connect(poll)
        timer.start()
        dlg.exec()

    def show_help(self):
        ShortcutsDialog(self, getattr(self, "current_view", None)).exec()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self.drop_hint.isVisible():
            self.drop_hint.setGeometry(self.rect().adjusted(24, 24, -24, -24))
        if self.toast_lab.isVisible():
            self.toast_lab.move((self.width() - self.toast_lab.width()) // 2, self.height() - self.toast_lab.height() - 24)

    def closeEvent(self, ev):
        if self.session.project is not None and not self.mapping_released("Quitting"):
            return ev.ignore()                 # before anything is stopped or released
        jobs = self.session.jobs
        if jobs and jobs.active:
            if QMessageBox.question(self, "Quit", "A scan is running. Cancel it and quit?") != QMessageBox.StandardButton.Yes:
                return ev.ignore()
            jobs.cancel()
            time.sleep(0.3)
        self.poll.stop()
        prefs().setValue("window/geometry", self.saveGeometry())
        if self.mapping is not None:   # its player and GL resources first, while the window still exists
            self.mapping.shutdown()
        self.review.shutdown()
        try:
            self.session._close_locked()
        except Exception:
            pass
        super().closeEvent(ev)


def _restore_check(app, win, shot: str):
    """Verification of packaged builds: open Mapping on a project that has a saved setup (for example a
    copy whose original model files are gone) and check what comes back, then that it plays, scrubs
    and hands over to QC at the exact frame and back. PASS/FAIL per step, like _mapping_check."""
    import time as _time
    base = os.path.splitext(shot)[0]
    results, errors = [], []
    win.review.player.error.connect(errors.append)
    saved = win.session.project.get_meta("mapping.setup") or {}

    def ms():
        return win.mapping

    def loaded_players():
        return [n for n, p in (("QC", win.review.player), ("Mapping", ms().view if ms() else None)) if p is not None and p.m is not None and p.m.path]

    def check(name, ok, detail=""):
        one = len(loaded_players()) <= 1
        results.append(("PASS" if ok and one else "FAIL", name, f"{detail}{'' if one else ' TWO PLAYERS LOADED'}"))

    steps = []

    def add(fn, wait=800):
        steps.append((fn, wait))

    def s_open():
        win.nav["mapping"].click()
        ms().view.error.connect(errors.append)
    add(s_open, 300)

    def s_wait(limit=[400]):
        if (ms()._batch is not None or not ms().view.loaded) and limit[0] > 0:
            limit[0] -= 1
            return QTimer.singleShot(100, s_wait)
        m = ms()
        want_ids = [x["id"] for x in saved.get("sources", [])]
        check("Saved setup restored from the project's own model copies",
              [x.id for x in m.stage.sources] == want_ids and not m.missing and m.assign is not None
              and dict(m.assign.uv) == saved.get("assignments") and all(os.path.realpath(x.path).startswith(os.path.realpath(str(win.session.project.folder)))
                                                                      for x in m.stage.sources),
              f"{m.scene_lab.text()}; {len(saved.get('assignments', {}))} assignments; missing {sorted(m.missing)}")
        check("Projector, tab and clip restored", (m.projector is not None) == bool(saved.get("projectors"))
              and m.tabs.currentIndex() == ("surfaces", "projectors").index(saved.get("tab", "surfaces"))
              and (m.asset["id"] if m.asset else None) == saved.get("clip"), m.asset["name"] if m.asset else "no clip")
        win.grab().save(f"{base}-restored.png")
        m.playhead.seek(0)
    add(s_wait, 1500)
    add(lambda: ms().toggle_play(), 1200)
    add(lambda: check("Plays after restore", not ms().view.paused and ms().view.frame > 5, f"frame {ms().view.frame}"), 10)
    add(lambda: (ms().toggle_play(), ms().playhead.scrub(57)), 800)
    add(lambda: ms().playhead.step(1), 600)
    add(lambda: check("Scrub to 57, step: frame 58", ms().playhead.frame == ms().view.frame == 58, f"frame {ms().view.frame}"), 10)
    add(lambda: win.show_view("review"), 1500)
    add(lambda: check("To QC at the same clip and frame", win.review.asset["id"] == ms().asset["id"] and win.review.frame == 58,
                      f"QC {win.review.frame}"), 10)
    add(lambda: win.show_view("mapping"), 1500)
    add(lambda: check("Back to Mapping, setup kept", ms().playhead.frame == 58 and len(ms().stage.sources) == len(saved.get("sources", []))), 10)

    def s_quit():
        t = _time.monotonic()
        win.close()
        results.append(("PASS" if _time.monotonic() - t < 3 else "FAIL", "Window closed", f"{_time.monotonic() - t:.2f} s"))
        results.append(("PASS" if not errors else "FAIL", "No player errors", repr(errors[:3])))
        print("\n".join(f"restore check: {r} | {n} | {d}" for r, n, d in results), flush=True)
        app.quit()
    add(s_quit, 0)

    def run(i=0):
        fn, wait = steps[i]
        try:
            fn()
        except Exception as e:
            results.append(("FAIL", f"step {i}", repr(e)))
        if i + 1 < len(steps):
            QTimer.singleShot(wait, lambda: run(i + 1))
    QTimer.singleShot(1500, run)


def _mapping_check(app, win, stage: str, shot: str):
    """Verification of packaged builds: the Mapping journey, step by step, each logged PASS/FAIL.
    Needs a project with two visual clips. Keys are real key events through the window's routing
    (not physical keys). At every step: only one player holds a file (one source of sound), and
    no player error; the longest event-loop stall is reported. Grabs the window at a few points
    (shot-1-mapping.png ... next to `shot`). NITPIX_SCREENSHOT_SLOW names a model slow enough to
    cancel mid-import, NITPIX_SCREENSHOT_BAD a damaged one, NITPIX_SCREENSHOT_PARTS a folder of
    separately exported screen models to add to the stage while it plays (and then remove one).
    Ends by closing the window while Mapping plays."""
    import time as _time
    from PySide6.QtGui import QKeyEvent
    base = os.path.splitext(shot)[0]
    results, errors, gaps = [], [], [0.0]
    tick = {"t": _time.monotonic()}

    def beat():                                   # event-loop responsiveness while the journey runs
        now = _time.monotonic()
        gaps[0] = max(gaps[0], now - tick["t"])
        tick["t"] = now
    hb = QTimer(win)
    hb.timeout.connect(beat)
    hb.start(10)
    win.review.player.error.connect(errors.append)

    def ms():
        return win.mapping

    def loaded_players():
        return [n for n, p in (("QC", win.review.player), ("Mapping", ms().view if ms() else None)) if p is not None and p.m is not None and p.m.path]

    def check(name, ok, detail=""):
        one = len(loaded_players()) <= 1
        stall = f" (longest stall since the last check {gaps[0] * 1000:.0f} ms)" if gaps[0] > 0.1 else ""
        gaps[0] = 0.0
        results.append(("PASS" if ok and one else "FAIL", name, f"{detail}{'' if one else ' TWO PLAYERS LOADED'}{stall}"))

    def space(widget):
        widget.setFocus()
        for t in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            QApplication.sendEvent(widget, QKeyEvent(t, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier, " "))

    def wait_import(then, limit=600):
        if ms()._batch is not None and limit > 0:
            return QTimer.singleShot(100, lambda: wait_import(then, limit - 1))
        then()

    clips = [r["id"] for r in service.list_assets(win.session.project) if r["kind"] in ("video", "sequence", "image")]
    steps = []

    def add(fn, wait=800):
        steps.append((fn, wait))

    def s_open():
        win.nav["mapping"].click()
        ms().view.error.connect(errors.append)
    add(s_open, 1500)
    add(lambda: (win.grab().save(f"{base}-1-mapping.png"), check("Mapping from the sidebar, clip handed over",
                 win.current_view == "mapping" and ms().view.loaded, ms().asset["name"] if ms().asset else "no clip")), 10)
    add(lambda: space(ms().media_box), 800)
    add(lambda: check("Space on the closed clip selector plays", not ms().view.paused and not ms().media_box.view().isVisible()), 10)
    add(lambda: space(ms().media_box), 500)
    add(lambda: check("Space again pauses", ms().view.paused), 10)
    slow = os.environ.get("NITPIX_SCREENSHOT_SLOW") or stage      # a model big enough to cancel mid-import
    add(lambda: (ms().add_models([slow]), QTimer.singleShot(300, ms().cancel_import)), 1000)
    add(lambda: check("Import cancelled: no stage, Add offered again", ms().scene is None and ms().add_btn.isEnabled()
                      and not ms().importer.busy, ms().empty_lab.text().splitlines()[0]), 10)
    add(lambda: ms().add_models([stage]), 300)
    add(lambda: wait_import(lambda: check("Import restarted", ms().scene is not None,
                                          f"{len(ms().scene.objects) if ms().scene else 0} objects")), 1500)

    def K(key):                                   # a key of the stage model, the first file on the stage
        return f"{ms().stage.sources[0].id}:{key}"
    bad = os.environ.get("NITPIX_SCREENSHOT_BAD")
    if bad:
        add(lambda: (ms().add_models([bad])), 300)
        add(lambda: wait_import(lambda: check("Damaged model refused, stage kept", ms().scene is not None and ms().objects.currentIndex() == 1
                                              and len(ms().stage.sources) == 1, win.toast_lab.text())), 1000)
        add(lambda: (ms().replace_stage([bad])), 300)
        add(lambda: wait_import(lambda: check("Replace with a damaged model: stage unchanged", len(ms().stage.sources) == 1
                                              and ms().stage.sources[0].name == os.path.basename(stage), win.toast_lab.text())), 1000)
    add(lambda: (ms().filter.setText("scre"), ms().select_shown()), 300)
    add(lambda: (ms().apply_selected(), check("Filter, Select shown, Apply", len(ms().assign.uv) == 3, ms().sel_lab.text())), 300)
    add(lambda: (ms().filter.clear(), ms().select([K("Stage/Screens/Left")]), ms().clear_selected(),
                 check("Clear one", sorted(ms().assign.uv) == [K("Stage/Screens/Centre"), K("Stage/Screens/Right")])), 300)

    def s_uv():
        ms().select([K("Stage/Screens/Centre")])
        i = ms().uv_box.findData(1)
        ms().uv_box.setCurrentIndex(i)
        ms().uv_box.activated.emit(i)
        check("UV set 2 on Centre", ms().assign.uv.get(K("Stage/Screens/Centre")) == 1)
    add(s_uv, 300)
    add(lambda: space(ms().uv_box), 300)

    def s_move():
        for n in range(60):
            QTimer.singleShot(25 * n, lambda n=n: (ms().view.orbit(4, 1) if n % 3 == 0 else ms().view.pan(3, 1) if n % 3 == 1
                                                   else ms().view.zoom(0.2 if n < 30 else -0.2),
                                                   ms().select([K("Stage/Floor")] if n % 2 else [])))
    add(s_move, 2000)
    add(lambda: check("Plays through orbit/pan/zoom and selection (Space on UV selector)", not ms().view.paused, f"frame {ms().view.frame}"), 10)
    parts = os.environ.get("NITPIX_SCREENSHOT_PARTS")
    if parts:
        files = sorted(os.path.join(parts, f) for f in os.listdir(parts) if f.lower().endswith((".obj", ".fbx")))
        before = {}

        def s_add():
            v = ms().view
            before.update(path=v.m.path, frame=v.frame, cam=(v.yaw, v.dist), uv=dict(ms().assign.uv), t=_time.monotonic())
            ms().add_models(files)
        add(s_add, 300)

        def s_added():
            v = ms().view
            check(f"Added {len(files)} model files while playing: stage, assignments, camera, clip kept",
                  len(ms().stage.sources) == 1 + len(files) and not v.paused and v.m.path == before["path"]
                  and v.frame > before["frame"] and (v.yaw, v.dist) == before["cam"] and ms().assign.uv == before["uv"],
                  f"{ms().scene_lab.text()}; frame {before['frame']} -> {v.frame} in {_time.monotonic() - before['t']:.2f} s")
            ms().select([o.key for s in ms().stage.sources[1:] for o in s.objects])
            ms().apply_selected()
            ms().view.frame_all()
        add(lambda: wait_import(s_added), 1200)

        def s_remove():
            win.grab().save(f"{base}-2-composed.png")
            gone = ms().stage.sources[1]
            n = len(ms().assign.uv)
            ms().remove_source(gone.id)
            check(f"Removed {gone.name} while playing: only its objects went", ms().stage.source(gone.id) is None
                  and len(ms().assign.uv) == n - len(gone.objects) and not ms().view.paused, ms().scene_lab.text())
        add(s_remove, 800)

    # Projectors: the other tab, while it plays; the stage, clip and UV assignments stay.
    pj = {}
    def s_projectors():
        v = ms().view
        pj.update(path=v.m.path, uv=dict(ms().assign.uv), frame=v.frame)
        ms().tabs.setCurrentIndex(1)
        ms().add_projector()
        p = ms().proj_panel
        for spin, val in zip(p.pos + p.tgt, (0.0, 2.5, 9.0, 0.0, 2.0, 0.0)):
            spin.setValue(val)
        p.fov.setValue(35.0)
    add(s_projectors, 1200)

    def s_projected():
        v = ms().view
        pr = ms().projector
        check("Projectors tab: projector added and set while playing; same clip, still playing",
              v.mode == "projectors" and v.projecting() and pr.position == (0.0, 2.5, 9.0) and pr.h_fov == 35.0
              and v.m.path == pj["path"] and not v.paused and v.frame > pj["frame"] and v.projection.rebuilds >= 1,
              f"{ms().proj_panel.info.text().splitlines()[0]}; visibility built {v.projection.rebuilds}x")
        win.grab().save(f"{base}-4-projector.png")
        v.look_through(True)
    add(s_projected, 800)

    def s_through():
        win.grab().save(f"{base}-5-through.png")
        n = ms().view.projection.rebuilds
        for i in range(30):
            QTimer.singleShot(20 * i, lambda: ms().view.orbit(3, 0.5))
        pj.update(rebuilds=n)
    add(s_through, 1000)
    add(lambda: check("Look through, then orbit: back to the orbit view, visibility reused",
                      not ms().view.looking_through and ms().view.projection.rebuilds == pj["rebuilds"],
                      f"built {ms().view.projection.rebuilds}x"), 10)

    def s_spin():
        spin = ms().proj_panel.pos[0]
        played = ms().view.paused
        spin.setFocus()
        spin.selectAll()
        for key, text in ((Qt.Key.Key_1, "1"), (Qt.Key.Key_Space, " ")):
            for t in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
                QApplication.sendEvent(spin, QKeyEvent(t, key, Qt.KeyboardModifier.NoModifier, text))
        pj.update(paused=played, view=win.current_view)
        QApplication.sendEvent(spin, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier))
    add(s_spin, 400)

    def s_after_spin():
        check("Typing 1 and Space in a number: no screen change, no play/pause; Enter commits it",
              win.current_view == pj["view"] == "mapping" and ms().view.paused == pj["paused"]
              and ms().projector.position[0] == 1.0, f"x = {ms().projector.position[0]}")
        space(ms().view)
    add(s_after_spin, 600)
    add(lambda: (check("Space after leaving the number toggles playback", ms().view.paused != pj["paused"]),
                 space(ms().view)), 600)

    def s_back():
        ms().tabs.setCurrentIndex(0)
        check("Back to Surfaces: UV assignments shown again, clip unchanged",
              ms().view.mode == "surfaces" and ms().assign.uv == pj["uv"] and ms().view.m.path == pj["path"])
    add(s_back, 500)
    add(lambda: (ms().toggle_play() if not ms().view.paused else None, ms().playhead.scrub(100)), 800)
    add(lambda: ms().playhead.step(1), 600)
    add(lambda: (win.grab().save(f"{base}-2-mapped.png"), check("Paused, scrub to 100, step", ms().playhead.frame == ms().view.frame == 101,
                 f"frame {ms().playhead.frame}")), 10)

    def s_clip():
        win._uv = dict(ms().assign.uv)
        other = next((c for c in clips if c != ms().asset["id"]), None)
        i = ms().media_box.findData(other)
        ms().media_box.setCurrentIndex(i)
        ms().media_box.activated.emit(i)
    add(lambda: ms().toggle_play(), 600)
    add(s_clip, 2500)
    add(lambda: check("Clip changed while playing", ms().view.loaded and ms().asset["id"] != clips[0] and ms().assign.uv == win._uv,
                      f"{ms().asset['name']}, paused {ms().view.paused}, frame {ms().view.frame}"), 10)
    add(lambda: (ms().toggle_play() if not ms().view.paused else None), 500)
    add(lambda: (setattr(win, "_f", ms().playhead.frame), win.show_view("review")), 1500)
    add(lambda: check("To QC at the same clip and frame", win.review.asset["id"] == ms().asset["id"] and win.review.frame == win._f,
                      f"QC {win.review.frame}, Mapping {win._f}"), 10)
    add(lambda: (win.review.playhead.seek(42) if hasattr(win.review, "playhead") else None), 800)
    add(lambda: win.show_view("mapping"), 1500)
    add(lambda: check("Back to Mapping at QC's frame, stage kept", ms().playhead.frame == 42 and ms().scene is not None,
                      f"frame {ms().playhead.frame}"), 10)

    def s_reopen():
        win._proj = win.session.project.folder
        m = ms()
        win._kept = ([(x.id, m.stage.label(x)) for x in m.stage.sources], dict(m.assign.uv) if m.assign else {},
                     m.projector, m.asset["id"] if m.asset else None, m.tabs.currentIndex())
        win.close_project()
    add(s_reopen, 800)
    add(lambda: check("Close project: Mapping reset, nothing loaded", ms().scene is None and not loaded_players()
                      and ms().saver.project is None), 10)
    add(lambda: (win.open_project(str(win._proj)), win.show_view("mapping")) if win._proj else None, 800)

    def s_restored():
        m = ms()
        now = ([(x.id, m.stage.label(x)) for x in m.stage.sources], dict(m.assign.uv) if m.assign else {},
               m.projector, m.asset["id"] if m.asset else None, m.tabs.currentIndex())
        check("Reopen project: the setup is restored (files, assignments, projector, clip, tab)",
              now == win._kept and not m.missing and m.view.loaded and m.save_lab.text() == "Saved in project",
              f"{m.scene_lab.text()}; {len(now[1])} assignments")
        win.grab().save(f"{base}-6-restored.png")
    add(lambda: wait_import(s_restored), 1500)
    add(lambda: (ms().tabs.setCurrentIndex(0), ms().apply_all(), ms().toggle_play()), 1500)

    def s_quit():
        check("Playing before quit", not ms().view.paused)
        t = _time.monotonic()
        win.grab().save(f"{base}-3-end.png")
        hb.stop()
        win.close()
        results.append(("PASS" if _time.monotonic() - t < 3 else "FAIL", "Window closed while playing", f"{_time.monotonic() - t:.2f} s"))
        results.append(("PASS" if not errors else "FAIL", "No player errors", repr(errors[:3])))
        print("\n".join(f"mapping journey: {r} | {n} | {d}" for r, n, d in results), flush=True)
        app.quit()
    add(s_quit, 0)

    def run(i=0):
        fn, wait = steps[i]
        try:
            fn()
        except Exception as e:  # a broken step is a failed step, not a hung check
            results.append(("FAIL", f"step {i}", f"{type(e).__name__}: {e}"))
        if i + 1 < len(steps):
            QTimer.singleShot(wait, lambda: run(i + 1))
    QTimer.singleShot(1500, run)


def _stress(app, win, count: int):
    """Jump between assets like a reviewer holding '.', sometimes playing; print what was used."""
    import random
    ids = [r["id"] for r in service.list_assets(win.session.project)]
    seen, n = set(), [0]

    def tick():
        rv = win.review
        if rv.player.loaded:
            seen.add(str(rv.player.video_params().get("hwdec")))
        win.show_asset(random.choice(ids))
        if rv.player.loaded and random.random() < 0.3:
            rv.toggle_play()
        n[0] += 1
        if n[0] < count:
            QTimer.singleShot(random.choice((40, 80, 150, 300, 600)), tick)
        else:
            libs = subprocess.run(["lsof", "-p", str(os.getpid())], capture_output=True, text=True).stdout
            print(f"stress ok: {n[0]} switches, hwdec {sorted(seen)}, MoltenVK loaded: {'MoltenVK' in libs}", flush=True)
            win.close()
            app.quit()
    QTimer.singleShot(1500, tick)


def run(project: str | None = None) -> int:
    QSurfaceFormat.setDefaultFormat(gl_format())
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("nitpix-exp" if variant.IS_EXPERIMENTAL else "nitpix")
    app.setWindowIcon(QIcon(str(Path(__file__).resolve().parent.parent / "web" / "branding" / "app-icon.png")))
    theme.load_fonts()
    app.setStyleSheet(theme.STYLESHEET)
    win = MainWindow(service.Session())
    win.show()
    if not os.environ.get("NITPIX_SCREENSHOT") and not os.environ.get("NITPIX_NO_DEPS_CHECK"):
        QTimer.singleShot(0, lambda: (deps.check(win), win.refresh_rail()))
    if not os.environ.get("NITPIX_SCREENSHOT") and not os.environ.get("NITPIX_STRESS") and not os.environ.get("NITPIX_NO_UPDATE_CHECK"):
        QTimer.singleShot(3000, win.check_updates)  # after the window is up; quiet unless there is news
    if project:
        win.open_project(project)
    if os.environ.get("NITPIX_STRESS") and win.session.project:  # verification: rapid asset switching
        _stress(app, win, int(os.environ["NITPIX_STRESS"]))
    shot = os.environ.get("NITPIX_SCREENSHOT")
    if shot:  # verification of packaged builds: grab the window after a few seconds and quit
        aid = os.environ.get("NITPIX_SCREENSHOT_ASSET")
        if aid and win.session.project:
            QTimer.singleShot(1500, lambda: win.show_asset(int(aid)))
        if os.environ.get("NITPIX_SCREENSHOT_RESTORE") and win.session.project:
            _restore_check(app, win, shot)
        elif os.environ.get("NITPIX_SCREENSHOT_STAGE") and win.session.project:
            _mapping_check(app, win, os.environ["NITPIX_SCREENSHOT_STAGE"], shot)
        else:
            QTimer.singleShot(int(os.environ.get("NITPIX_SCREENSHOT_DELAY", "7000")), lambda: (win.grab().save(shot), app.quit()))
    return app.exec()
