"""Dependency check: what nitpix needs (ffmpeg, ffprobe, libmpv), whether it is there, and a
one-click Homebrew install for the development setup. The packaged .app carries all three,
so this only shows up when running from source or when a bundle is damaged."""
from __future__ import annotations

import os
import platform
import shutil
import sys

from PySide6.QtCore import QProcess, Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout

from .. import libmpv
from ..probe import find_tool
from . import theme

NEEDS = [("ffprobe", "reads the metadata of every file", "ffmpeg"),
         ("ffmpeg", "decodes media for the content checks and frame grabs", "ffmpeg"),
         ("libmpv", "plays the source media in the review screen (without it you get frame grabs)", "mpv")]


def missing() -> list[tuple[str, str, str]]:
    out = []
    for name, why, pkg in NEEDS:
        ok = libmpv.available() if name == "libmpv" else bool(find_tool(name))
        if not ok:
            out.append((name, why, pkg))
    return out


def brew() -> str | None:
    return shutil.which("brew") or next((p for p in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew") if os.path.exists(p)), None)


class DepsDialog(QDialog):
    def __init__(self, parent, items: list[tuple[str, str, str]]):
        super().__init__(parent)
        self.setWindowTitle("Missing components")
        self.resize(620, 420)
        self.items = items
        lay = QVBoxLayout(self)
        frozen = bool(getattr(sys, "frozen", False))
        head = QLabel("This nitpix.app is missing components it was built with; please re-download it." if frozen else
                      "nitpix needs these to scan and play media:")
        head.setWordWrap(True)
        lay.addWidget(head)
        for name, why, _ in items:
            l = QLabel(f"<b>{name}</b> — {why}")
            l.setWordWrap(True)
            lay.addWidget(l)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(theme.mono(11))
        self.log.hide()
        lay.addWidget(self.log, 1)
        row = QHBoxLayout()
        self.install = None
        pkgs = sorted({pkg for _, _, pkg in items})
        if not frozen and platform.system() == "Darwin" and brew():
            self.install = QPushButton(f"Install with Homebrew ({' '.join(pkgs)})")
            self.install.setProperty("primary", True)
            self.install.clicked.connect(self._install)
            row.addWidget(self.install)
        elif not frozen:
            hint = QLabel({"Darwin": "Install Homebrew from brew.sh, then: brew install " + " ".join(pkgs),
                           "Windows": "Install ffmpeg (gyan.dev build) and libmpv-2.dll (mpv.io), on PATH or next to nitpix.exe",
                           "Linux": "sudo apt install ffmpeg libmpv2   (or your distribution's equivalent)"}.get(platform.system(), ""))
            hint.setWordWrap(True)
            hint.setProperty("muted", True)
            row.addWidget(hint, 1)
        row.addStretch(1)
        bb = QDialogButtonBox()
        self.cont = bb.addButton("Continue anyway", QDialogButtonBox.ButtonRole.RejectRole)
        bb.rejected.connect(self.reject)
        bb.accepted.connect(self.accept)
        row.addWidget(bb)
        lay.addLayout(row)
        self.proc = None

    def _install(self):
        pkgs = sorted({pkg for _, _, pkg in self.items})
        self.install.setEnabled(False)
        self.log.show()
        self.log.appendPlainText(f"$ brew install {' '.join(pkgs)}\n")
        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.proc.readyReadStandardOutput.connect(lambda: self.log.appendPlainText(bytes(self.proc.readAllStandardOutput()).decode(errors="replace").rstrip()))
        self.proc.finished.connect(self._done)
        env = self.proc.processEnvironment()
        env.insert("HOMEBREW_NO_AUTO_UPDATE", "1")
        self.proc.setProcessEnvironment(env)
        self.proc.start(brew(), ["install", *pkgs])

    def _done(self, code, _status):
        libmpv.reset()
        left = missing()
        if code == 0 and not left:
            self.log.appendPlainText("\nDone. Everything nitpix needs is installed.")
            self.cont.setText("Continue")
            self.accept()
        else:
            self.log.appendPlainText(f"\nbrew exited with {code}; still missing: {', '.join(n for n, _, _ in left) or 'nothing'}")
            self.install.setEnabled(True)


def check(parent) -> bool:
    """Show the dialog when something is missing. Returns True when all is present afterwards."""
    items = missing()
    if not items:
        return True
    dlg = DepsDialog(parent, items)
    dlg.setWindowModality(Qt.WindowModality.WindowModal)
    dlg.exec()
    return not missing()
