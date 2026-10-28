"""Saving the Mapping setup into its project (nitpix/mapsave.py describes the document).

The Mapping screen calls changed() after every edit worth keeping: stage files, assignments, UV
sets, the clip, the tab, the projector, the camera. A save follows after a short pause (longer for
camera moves), so a drag or a run of edits is one write, and never one per frame. flush() writes at
once: on closing or switching the project and on quitting.

A saver belongs to one Project object: bind() at restore, unbind() when the project goes, so it
can never write one project's setup into another. Nothing is written while a restore is under way
(suspended), so an incomplete stage never replaces the saved one. A saved document that could not
be read is set aside under its own key before the first new save, never overwritten. A failed write
is reported (state "error", with the reason) and tried again with the next change or retry().

held: the user chose to close without the changes that could not be saved. Nothing is written
while held; if the close does not happen after all (refused, failed), release() makes the saver
exactly as it was (still bound, the same pending or error state), so later edits are saved and a
failing save is still caught.
"""
from __future__ import annotations

import json
import time

from PySide6.QtCore import QObject, QTimer, Signal

from ..mapsave import KEY

QUICK_MS, CAMERA_MS = 400, 1500


class SetupSaver(QObject):
    state_changed = Signal(str, str)           # "saved" / "pending" / "error" / "off", detail

    def __init__(self, build, parent=None):
        super().__init__(parent)
        self._build = build                    # () -> the document, from the screen's state
        self.project = None
        self.suspended = False
        self.held = False
        self.unreadable = None                 # the stored text that could not be read, to set aside
        self.state, self.detail = "off", ""
        self.writes = 0
        self._timer = QTimer(self, singleShot=True)
        self._timer.timeout.connect(self.flush)

    def bind(self, project, unreadable=None):
        self.project, self.unreadable = project, unreadable
        self._set("saved" if project else "off", "")

    def unbind(self):
        self._timer.stop()
        self.project, self.unreadable, self.held = None, None, False
        self._set("off", "")

    def changed(self, camera=False):
        if self.project is None or self.suspended:
            return
        if not (camera and self._timer.isActive() and self._timer.remainingTime() < CAMERA_MS):
            self._timer.start(CAMERA_MS if camera else QUICK_MS)
        if self.state != "error":
            self._set("pending", "")

    def flush(self) -> bool:
        """Write now if there is anything to write. True when the setup is saved."""
        self._timer.stop()
        if self.held:
            return True                        # going without these changes: nothing written
        if self.project is None or self.suspended or self.state not in ("pending", "error"):
            return self.state == "saved"
        try:
            doc = self._build()
            doc["saved"] = time.time()
            if self.unreadable is not None:
                self.project.set_meta(f"{KEY}.unreadable-{int(time.time())}", self.unreadable)
                self.unreadable = None
            self.project.set_meta(KEY, json.loads(json.dumps(doc)))
        except Exception as e:                 # a locked, read-only or closed database, a full disk...
            self._set("error", str(e) or type(e).__name__)
            return False
        self.writes += 1
        self._set("saved", "")
        return True

    def hold(self):
        self._timer.stop()
        self.held = True

    def release(self):
        """The close was abandoned: saving resumes where it was."""
        if self.held:
            self.held = False
            if self.state in ("pending", "error") and self.project is not None:
                self._timer.start(QUICK_MS)

    def retry(self):
        if self.state == "error":
            self.flush()

    def _set(self, state, detail):
        if (state, detail) != (self.state, self.detail):
            self.state, self.detail = state, detail
            self.state_changed.emit(state, detail)
