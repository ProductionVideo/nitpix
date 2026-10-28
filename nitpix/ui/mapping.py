"""The Mapping workspace: a project's media on an imported OBJ / FBX stage.

Separate from the QC review screen, with its own player (nitpix/ui/mapview.py), scene and
assignments, sharing the transport behaviour through the Playhead. One playback session at a
time: MainWindow calls activate() / deactivate() as the user moves between the workspaces, and a
deactivated workspace has no file loaded, so it cannot play, decode, make sound or seek.

The stage is composed from one or more model files (nitpix/stage.py). Add models… reads files
one at a time through the import helper and puts each on the stage as it arrives, keeping what is
there (objects, assignments, clip, playback and camera). Replace stage… keeps the current stage
until the new files are read, then swaps once to whichever of them could be used. Cancelling
stops the file being read and those still queued: an Add keeps the files already added, a Replace
leaves the stage as it was. Remove takes one file's objects off the stage; files on disk are only
ever read.

Two tabs beside the shared view and transport: Surfaces (the stage's files, objects and UV
assignments) and Projectors (one virtual projector, nitpix/projector.py). The tab decides what the
view shows: UV assignments, or the projector's picture on every surface (never both). Switching
tabs only repaints; the clip, frame, camera, stage and assignments all stay.

The setup is kept with the project (nitpix/ui/mapsetup.py, nitpix/mapsave.py): every model file
is copied into the project as it is added (nitpix/models.py), and the stage, assignments, clip,
tab, projector and camera are saved shortly after each change. Opening Mapping in a project
restores them, reading the kept copies through the same import queue. A kept copy that is missing
or has changed is listed as missing, with its saved settings held back, until it is relinked to a
file (its assignments return only if it is the same model) or taken off the stage.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from PySide6.QtCore import QItemSelection, QItemSelectionModel, QModelIndex, Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QMenu, QSplitter, QStackedWidget, QTabWidget, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from .. import mapsave, models, service
from ..mapping import MIXED, Assignments, share
from ..projector import from_view
from ..sceneimport import Importer
from ..stage import Stage, split_key
from . import theme
from .mapview import MAX_TEXTURE, MappingView
from .playhead import Playhead
from .projection import VISIBILITY_SIZE
from .mapsetup import SetupSaver
from .projectors import ProjectorPanel
from .review import PosCtx, btn
from .timeline import Timeline
from .widgets import ElideLabel, repolish

KEY = Qt.ItemDataRole.UserRole
PANE_MARGINS = (14, 12, 4, 4)                  # inside each tab: clear of the splitter, the tab bar and the window edge
SOURCE = Qt.ItemDataRole.UserRole + 1         # a model file's row: its source id
MISSING = Qt.ItemDataRole.UserRole + 2        # ...of a saved model that is not on the stage
MEDIA_KINDS = ("video", "sequence", "image")
SAVE_NOTE = ("Kept with the project: the model files (copied into it), the stage, assignments, clip, projector and "
             "camera. Clips themselves stay where they are.")
CAMERA_HINT = "Drag to orbit · ⇧-drag or right-drag to pan · scroll to zoom · F frames the stage"


class _Filter(QLineEdit):
    """The object filter: Escape clears it and hands the keys back to the workspace."""
    escaped = Signal()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key.Key_Escape:
            self.clear()
            self.escaped.emit()
            return
        super().keyPressEvent(ev)


@dataclass
class _Batch:
    """One Add or Replace: files read one after another (one helper process at a time)."""
    mode: str                                 # "add" or "replace"
    queue: list                               # paths still to read
    total: int
    current: str = ""
    added: list = field(default_factory=list)      # add: Sources put on the stage
    read: list = field(default_factory=list)       # replace: scenes read, swapped in at the end
    failed: list = field(default_factory=list)     # (name, why)
    skipped: list = field(default_factory=list)    # (name, why): not read at all
    notes: list = field(default_factory=list)      # more to say (a relinked file that differs)
    unmatched: int = 0                             # saved assignments whose objects no longer exist
    cancelled: bool = False


class MappingScreen(QWidget):
    status = Signal(str, bool)                 # toast text, is_error
    _imported = Signal(int, object, object, object)   # from the importer's thread: generation, scene, error, resource

    def __init__(self, session: service.Session):
        super().__init__()
        self.session = session
        self.active = False
        self.asset = None                      # the mapped clip (asset detail)
        self.ctx = None
        self.stage = Stage()
        self.assign = None
        self._batch = None                     # the Add / Replace in progress
        self.projector = None                  # nitpix.projector.Projector, or None
        self.clip_aspect = None                # the mapped clip's display width / height
        self.resources = {}                    # resource id -> its record (nitpix/models.py)
        self.src_res = {}                      # source id -> resource id
        self.missing = {}                      # source id -> {resource, why, assign}: saved, not on the stage
        self._restored_for = None              # the Project whose saved setup was read
        self._saved_clip = None                # the saved clip, for the first activation after a restore
        self._saved_camera = None
        self._pending_assign = {}              # saved assignments for sources still being restored
        self.saver = SetupSaver(self._document, self)
        self._pending_frame = 0                # where a newly loaded clip is to be shown
        self.importer = Importer(self._imported.emit)
        self._imported.connect(self._on_imported, Qt.ConnectionType.QueuedConnection)
        self._build()

    @property
    def scene(self):
        """The stage on show, or None while it has no model files."""
        return self.stage if self.stage else None

    # ---- layout ------------------------------------------------------------------------
    def _build(self):
        """Stage view on the left with its transport; objects and the selection inspector on the
        right. The top bar is one row that elides rather than wraps, so nothing moves during playback."""
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(QLabel("Clip"))
        self.media_box = QComboBox()
        self.media_box.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.media_box.setMinimumContentsLength(14)
        self.media_box.setToolTip("The clip every assigned object shows. Changing it changes the shared source for all of them.")
        self.media_box.activated.connect(lambda _i: self._choose_media(self.media_box.currentData()))
        head.addWidget(self.media_box, 2)
        self.scene_lab = ElideLabel("No stage imported", Qt.TextElideMode.ElideMiddle, min_chars=8)
        self.scene_lab.setProperty("muted", True)
        head.addWidget(self.scene_lab, 3)
        self.add_btn = btn("Add models…", "Add OBJ or FBX model files to the stage, where they were exported. "
                           "What is on the stage stays, with its assignments.", self.add_dialog)
        head.addWidget(self.add_btn)
        self.replace_btn = btn("Replace stage…", "Swap the whole stage for other model files. The current stage stays "
                               "until they have been read.", self.replace_dialog)
        head.addWidget(self.replace_btn)
        self.cancel_btn = btn("Cancel import", "", self.cancel_import)
        self.cancel_btn.hide()
        head.addWidget(self.cancel_btn)
        self.save_lab = QLabel()
        self.save_lab.setProperty("faint", True)
        self.save_lab.setToolTip(SAVE_NOTE)
        head.addWidget(self.save_lab)
        self.retry_btn = btn("Retry save", "Try saving the Mapping setup into the project again", lambda: self.saver.retry())
        self.retry_btn.hide()
        head.addWidget(self.retry_btn)
        self.saver.state_changed.connect(self._on_save_state)
        root.addLayout(head)
        self.problem_lab = QLabel()
        self.problem_lab.setWordWrap(True)
        self.problem_lab.setStyleSheet(f"background: {theme.T['fail_bg']}; color: {theme.T['fail']}; padding: 6px 10px; border-radius: 6px;")
        self.problem_lab.hide()
        root.addWidget(self.problem_lab)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setHandleWidth(8)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(6)
        self.view = MappingView()
        self.view.file_loaded.connect(self._on_loaded)
        self.view.error.connect(lambda m: self.status.emit(m, True))
        self.view.pause_changed.connect(lambda p: self.play_btn.setText("▶" if p else "❚❚"))
        self.view.texture_changed.connect(self._on_texture)
        self.view.problem.connect(self._on_problem)
        self.view.setToolTip(CAMERA_HINT)
        self.view.camera_input.connect(lambda: self.hint_lab.hide())
        self.view.camera_moved.connect(lambda: self.saver.changed(camera=True))   # learned; the view's tooltip still has it
        ll.addWidget(self.view, 1)
        self.playhead = Playhead(self.view, lambda: True, self)
        self.playhead.moved.connect(self._on_moved)
        self.playhead.pending_changed.connect(lambda _p: self.ctx and self._update_tc())
        info = QHBoxLayout()
        self.tex_lab = ElideLabel("")
        self.tex_lab.setFont(theme.mono(11))
        self.tex_lab.setProperty("muted", True)
        self.mode_lab = QLabel()                              # what the view is showing: says the tab
        self.mode_lab.setProperty("muted", True)
        info.addWidget(self.mode_lab)
        info.addWidget(self.tex_lab, 1)
        self.hint_lab = ElideLabel(CAMERA_HINT)               # until the camera is first moved this session
        self.hint_lab.setProperty("faint", True)
        info.addWidget(self.hint_lab, 2)
        info.addWidget(btn("Frame all", "Fit the whole stage in view (F)", lambda: self.view.frame_all()))
        info.addWidget(btn("Reset view", "Front view, whole stage", lambda: self.view.frame_all(reset=True)))
        ll.addLayout(info)
        tr = QHBoxLayout()
        tr.setSpacing(10)
        tcbox = QWidget()
        tcl = QVBoxLayout(tcbox)
        tcl.setContentsMargins(0, 0, 0, 0)
        tcl.setSpacing(0)
        self.tc_lab = QLabel("--:--:--:--")
        self.tc_lab.setProperty("tc", True)
        self.tc_lab.setFont(theme.tabular(self.tc_lab.font()))
        self.tc_sub = QLabel()
        self.tc_sub.setFont(theme.mono(11))
        self.tc_sub.setProperty("muted", True)
        tcl.addWidget(self.tc_lab)
        tcl.addWidget(self.tc_sub)
        tr.addWidget(tcbox)
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(2)
        for text, tip, slot in (("|◀", "Start (Home)", lambda: self.playhead.seek(0)), ("◀|", "Back one frame (←)", lambda: self.playhead.step(-1)),
                                ("▶", "Play / pause (Space)", self.toggle_play), ("|▶", "Forward one frame (→)", lambda: self.playhead.step(1)),
                                ("▶|", "End (End)", lambda: self.playhead.seek(self.ctx.count - 1 if self.ctx else 0))):
            b = btn(text, tip, slot)
            b.setFixedWidth(40)
            rl.addWidget(b)
            if text == "▶":
                self.play_btn = b
        tr.addWidget(row)
        tr.addStretch(1)
        ll.addLayout(tr)
        self.timeline = Timeline()
        self.timeline.seek_requested.connect(self.playhead.scrub)
        ll.addWidget(self.timeline)
        split.addWidget(left)

        right = QWidget()
        rlay = QVBoxLayout(right)
        rlay.setContentsMargins(*PANE_MARGINS)
        rlay.setSpacing(6)
        frow = QHBoxLayout()
        self.filter = _Filter()
        self.filter.setPlaceholderText("Filter objects")
        self.filter.setClearButtonEnabled(True)
        self.filter.setToolTip("Show objects whose name or path contains this. Selection and assignments are kept.")
        self.filter.textChanged.connect(self._apply_filter)
        self.filter.escaped.connect(lambda: self.tree.setFocus())
        frow.addWidget(self.filter, 1)
        self.shown_btn = btn("Select shown", "Select every object the filter shows", self.select_shown)
        frow.addWidget(self.shown_btn)
        rlay.addLayout(frow)
        self.result = QFrame()                         # what the last Add / Replace did, until dismissed
        res = QHBoxLayout(self.result)
        res.setContentsMargins(0, 0, 0, 0)
        self.result_lab = QLabel()
        self.result_lab.setWordWrap(True)
        self.result_lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        res.addWidget(self.result_lab, 1)
        res.addWidget(btn("×", "Dismiss", lambda: self.result.hide(), ghost=True), 0, Qt.AlignmentFlag.AlignTop)
        self.result.hide()
        rlay.addWidget(self.result)
        self.objects = QStackedWidget()
        self.empty_lab = QLabel()
        self.empty_lab.setWordWrap(True)
        self.empty_lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_lab.setProperty("muted", True)
        empty = QWidget()
        el = QVBoxLayout(empty)
        el.addStretch(1)
        el.addWidget(self.empty_lab)
        self.empty_import = btn("Add models…", "Read one or more OBJ or FBX model files as one stage", self.add_dialog, primary=True)
        el.addWidget(self.empty_import, 0, Qt.AlignmentFlag.AlignHCenter)
        el.addStretch(2)
        self.objects.addWidget(empty)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Object", "Media"])
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setIndentation(14)
        self.tree.setUniformRowHeights(True)          # one height for every row: no per-row measuring
        self.tree.setTextElideMode(Qt.TextElideMode.ElideMiddle)      # "Floor_…_07_Rev3", not "Floor_Inlay_LED_Pan…"
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)   # sized once: to-contents re-measured
        widest = max(self.tree.fontMetrics().horizontalAdvance(t) for t in ("● UV 8 ⚠", "no UVs", "Media"))   # every row on each change
        self.tree.header().resizeSection(1, widest + 28)
        self.tree.itemSelectionChanged.connect(self._on_selection)
        self.tree.itemClicked.connect(self._on_row_clicked)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._row_menu)
        self.objects.addWidget(self.tree)
        rlay.addWidget(self.objects, 1)

        insp = QFrame()
        insp.setObjectName("inspector")
        insp.setStyleSheet(f"QFrame#inspector {{ border-top: 1px solid {theme.T['line2']}; }}")
        il = QVBoxLayout(insp)
        il.setContentsMargins(0, 10, 0, 0)
        il.setSpacing(6)
        self.sel_lab = ElideLabel("")
        self.sel_lab.setStyleSheet("font-weight: 600;")
        il.addWidget(self.sel_lab)
        self.clip_lab = ElideLabel("", Qt.TextElideMode.ElideMiddle)
        self.clip_lab.setProperty("muted", True)
        il.addWidget(self.clip_lab)
        uvrow = QHBoxLayout()
        uvrow.addWidget(QLabel("UV set"))
        self.uv_box = QComboBox()
        self.uv_box.setToolTip("Which of the objects' own UV sets the media uses. Coordinates are used as authored. "
                               "For objects already showing media the change applies at once.")
        self.uv_box.activated.connect(self._on_uv_chosen)
        uvrow.addWidget(self.uv_box, 1)
        il.addLayout(uvrow)
        self.warn_lab = QLabel()
        self.warn_lab.setWordWrap(True)
        self.warn_lab.setStyleSheet(f"color: {theme.T['warn']};")
        self.warn_lab.hide()
        il.addWidget(self.warn_lab)
        acts = QHBoxLayout()
        self.apply_btn = btn("Apply clip", "Show the clip on the selected objects", self.apply_selected)
        self.clear_btn = btn("Clear", "Return the selected objects to neutral grey", self.clear_selected)
        acts.addWidget(self.apply_btn, 1)
        acts.addWidget(self.clear_btn)
        il.addLayout(acts)
        self.all_btn = btn("Apply to all with UVs", "Every object with UVs, in every model file, shows the clip", self.apply_all, ghost=True)
        il.addWidget(self.all_btn)
        self.remove_btn = btn("Remove from stage", "", self.remove_selected_source, ghost=True)
        self.remove_btn.hide()
        il.addWidget(self.remove_btn)
        rlay.addWidget(insp)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(right, "Surfaces")
        self.tabs.setTabToolTip(0, "The stage's files and objects, and which show the clip through their own UVs.")
        self.proj_panel = ProjectorPanel()
        self.tabs.addTab(self.proj_panel, "Projectors")
        self.tabs.setTabToolTip(1, "One virtual projector casting the clip onto every surface (UVs not needed).")
        self.tabs.currentChanged.connect(self._on_tab)
        p = self.proj_panel
        p.add_models_requested.connect(self.add_dialog)
        p.add_requested.connect(self.add_projector)
        p.from_view_requested.connect(self.projector_from_view)
        p.remove_requested.connect(self.remove_projector)
        p.changed.connect(self.edit_projector)
        p.look_requested.connect(lambda on: self.view.look_through(on))
        p.done_editing.connect(lambda: self.view.setFocus())
        self.view.look_changed.connect(p.set_looking)
        split.addWidget(self.tabs)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        split.setSizes([900, 340])
        root.addWidget(split, 1)
        self._show_objects()
        self._import_ui(False)
        self._refresh_controls()
        self._on_tab(0)

    # ---- workspace lifecycle -----------------------------------------------------------------
    def activate(self, handoff=None):
        """Become the one playback session. `handoff` (asset id, frame) is what QC was showing. The
        first time in a project, the saved setup is restored and its clip wins over the handoff."""
        self.active = True
        self.refresh_media()
        aid, frame = handoff if handoff else (self.asset["id"] if self.asset else None, self.playhead.frame)
        if self.session.project is not None and self._restored_for is not self.session.project:
            saved = self.begin_restore()
            if saved is not None and self.media_box.findData(saved) >= 0:
                aid, frame = saved, (frame if handoff and handoff[0] == saved else None)
        if aid is None or self.media_box.findData(aid) < 0:
            aid = self.media_box.itemData(0) if self.media_box.count() else None
            frame = None
        if aid is not None:
            self._load_media(aid, frame)

    def deactivate(self):
        """Give up the playback session: nothing loaded, nothing pending. Returns (asset id, frame) or None."""
        if not self.active:
            return None
        self.active = False
        h = (self.asset["id"], self.playhead.frame) if self.asset else None
        if self.ctx:
            self.playhead.begin(self.ctx.count, self.playhead.frame, new=True)   # drop a pending scrub
        self.view.unload()                    # stops decoding and audio; replies to old seeks are ignored
        return h

    def flush(self) -> bool:
        """Save now if there are unsaved changes (before the project closes or the app quits)."""
        return self.saver.flush()

    def reset_project(self):
        """Another project (or none): drop the stage, assignments, clip and any import in progress.
        The caller flushes first, while the old project is still open; nothing is written here."""
        self.saver.unbind()
        self._restored_for = None
        self._saved_clip = self._saved_camera = None
        self._pending_assign = {}
        self.resources, self.src_res, self.missing = {}, {}, {}
        self._batch = None
        self.importer.cancel()
        self._import_ui(False)
        self.deactivate()
        self.asset = self.ctx = None
        self._replace_with([])
        self.result.hide()
        self.remove_projector()
        self.media_box.clear()
        self.tc_lab.setText("--:--:--:--")
        self.tc_sub.setText("")

    def shutdown(self):
        self.flush()
        self.saver.unbind()
        self.importer.cancel()
        self.playhead.stop()
        self.view.shutdown()

    # ---- the saved setup --------------------------------------------------------------------
    def begin_restore(self):
        """Read this project's saved setup and start restoring it. Returns the saved clip's id (or None).
        A project without one starts empty; one that cannot be read starts empty too, and its stored
        text is set aside (never overwritten) before anything new is saved."""
        p = self.session.project
        self._restored_for = p
        models.clean_incoming(str(p.folder))
        raw = p.get_meta(mapsave.KEY)
        if raw is None:
            self.saver.bind(p)
            return None
        try:
            doc = mapsave.validate(raw)
        except mapsave.SetupError as e:
            self.saver.bind(p, unreadable=raw)
            self._on_problem(f"the saved Mapping setup could not be read ({e}). It is kept unchanged in the project; "
                             "Mapping starts empty, and anything you set up now is saved beside it.")
            return None
        self.saver.bind(p)
        self.saver.suspended = True
        self._replace_with([])
        self.stage.next_id = doc["next_id"]
        self.resources = dict(doc["resources"])
        self._pending_assign = dict(doc["assignments"])
        self._saved_clip = doc["clip"]
        cam = doc["camera"]
        if cam:
            self._saved_camera = cam
            self._apply_camera(cam)
        self.projector = mapsave.projector_from(doc["projectors"][0]) if doc["projectors"] else None
        self.tabs.setCurrentIndex(mapsave.TABS.index(doc["tab"]))
        items = []
        for s in doc["sources"]:
            res = self.resources[s["resource"]]
            origin = s.get("origin") or res["origin"]        # this placement's own file (several can share one copy)
            path = models.resolve(str(p.folder), res)
            if path is None or not os.path.isfile(path):
                self._mark_missing(s["id"], s["resource"], "the copy kept in this project is missing" if path
                                   else "its saved location is not inside this project", origin)
            else:
                items.append({"path": path, "sid": s["id"], "rid": s["resource"], "origin": origin})
        self._projector_changed()
        self._start("restore", items)
        return doc["clip"]

    def _end_restore(self, b):
        """After the last saved model was read (or the restore was cancelled): report, then save again normally."""
        self.saver.suspended = False
        self._pending_assign = {}
        if self._saved_camera:
            self._apply_camera(self._saved_camera)
        self._changed()
        self._show_objects()
        self.saver.state = "saved"                    # restoring changed nothing that needs writing
        self.saver.state_changed.emit("saved", "")
        n_ok = len(b.added)
        lines = []
        if self.missing:
            lines.append(f"{len(self.missing)} saved model file{'s' if len(self.missing) != 1 else ''} could not be restored:")
            for sid, m in self.missing.items():
                lines.append(f"✕ {self.resources.get(m['resource'], {}).get('name', sid)}: {m['why']}")
            lines.append("Right-click a file marked missing to relink it or take it off the stage. Its saved settings are kept until then.")
        if b.unmatched:
            lines.append(f"{b.unmatched} saved assignment{'s' if b.unmatched != 1 else ''} no longer match an object and were not applied.")
        if lines:
            self.result_lab.setText("\n".join(lines))
            self.result_lab.setToolTip("")
            self.result_lab.setStyleSheet(f"color: {theme.T['warn']};")
            self.result.show()
            self.status.emit(f"Mapping restored {n_ok} of {n_ok + len(self.missing)} model files.", True)
        elif n_ok:
            self.status.emit(f"Mapping setup restored: {n_ok} model file{'s' if n_ok != 1 else ''}.", False)

    def _apply_camera(self, cam):
        from PySide6.QtGui import QVector3D
        v = self.view
        v.cam_target = QVector3D(*cam["target"])
        v.yaw, v.pitch, v.dist = cam["yaw"], cam["pitch"], cam["dist"]
        lo, hi = self.stage.bounds
        v.radius = max(1e-3, 0.5 * ((hi[0] - lo[0]) ** 2 + (hi[1] - lo[1]) ** 2 + (hi[2] - lo[2]) ** 2) ** 0.5) if self.stage else v.radius
        v._camera_changed()

    def _document(self) -> dict:
        """The setup as saved (nitpix/mapsave.py): only sources with a copy in the project, missing ones included."""
        srcs = [{"id": s.id, "resource": self.src_res[s.id], "origin": s.origin} for s in self.stage.sources if s.id in self.src_res]
        srcs += [{"id": sid, "resource": m["resource"], "origin": m["origin"]} for sid, m in self.missing.items()]
        kept = {s["id"] for s in srcs}
        assign = {k: v for k, v in (self.assign.uv if self.assign else {}).items() if k.partition(":")[0] in kept}
        for m in self.missing.values():
            assign.update(m["assign"])
        v = self.view
        return {"version": mapsave.VERSION,
                "resources": {rid: r for rid, r in self.resources.items() if rid in {s["resource"] for s in srcs}},
                "sources": srcs, "next_id": max([self.stage.next_id] + [int(k[1:]) + 1 for k in kept]),
                "assignments": assign, "clip": self.asset["id"] if self.asset else self._saved_clip,
                "tab": mapsave.TABS[self.tabs.currentIndex()],
                "camera": {"yaw": v.yaw, "pitch": v.pitch, "dist": v.dist,
                           "target": [v.cam_target.x(), v.cam_target.y(), v.cam_target.z()]},
                "projectors": [mapsave.projector_to(self.projector)] if self.projector else []}

    def _on_save_state(self, state, detail):
        text = {"saved": "Saved in project", "pending": "Saving…", "error": "Not saved", "off": ""}[state]
        self.save_lab.setText(text)
        self.save_lab.setProperty("faint", state != "error")
        self.save_lab.setStyleSheet(f"color: {theme.T['fail']};" if state == "error" else "")
        self.save_lab.setToolTip(f"The Mapping setup could not be saved: {detail}" if state == "error" else SAVE_NOTE)
        self.retry_btn.setVisible(state == "error")
        if state == "error":
            self._on_problem(f"the setup could not be saved into the project ({detail}). Your changes are still here; "
                             "nitpix tries again with your next change, or use Retry save.")
            self.status.emit("Mapping setup not saved: " + detail, True)
        elif self.problem_lab.text().startswith("Mapping: the setup could not be saved"):
            self._on_problem("")

    # ---- media ---------------------------------------------------------------------------
    def refresh_media(self):
        p = self.session.project
        keep = self.media_box.currentData()
        self.media_box.clear()
        if not p:
            return
        for r in service.list_assets(p):
            if r["kind"] in MEDIA_KINDS and r.get("scan_state") != "error":
                self.media_box.addItem(r["name"], r["id"])
        i = self.media_box.findData(self.asset["id"] if self.asset else keep)
        if i >= 0:
            self.media_box.setCurrentIndex(i)

    def _choose_media(self, aid):
        if aid is None or (self.asset and aid == self.asset["id"]):
            return
        self._load_media(aid, None)
        self.saver.changed()
        self.status.emit(f"Mapping now shows {self.asset['name'] if self.asset else 'nothing'} on every assigned object", False)

    def _load_media(self, aid, frame):
        a = service.asset_detail(self.session.project, aid)
        if not a:
            return
        if a.get("exists") is False:
            self.status.emit(f"{a['name']} is not at its recorded path", True)
            return
        self.asset, self.ctx = a, PosCtx(a)
        f = min(a.get("last_frame") or 0, self.ctx.count - 1) if frame is None else max(0, min(self.ctx.count - 1, int(frame)))
        self.playhead.begin(self.ctx.count, f, new=True)
        self._pending_frame = f
        self.timeline.set_asset(a, self.ctx)
        w, h = a.get("width"), a.get("height")
        self.clip_aspect = w / h if w and h else None          # the projector follows it unless set
        self._projector_changed()
        self.timeline.set_frame(f)
        self._update_tc()
        i = self.media_box.findData(aid)
        if i >= 0:
            self.media_box.setCurrentIndex(i)
        self._refresh_controls()
        if self.active:
            self.view.load(self.session.project, a, f)

    def _on_loaded(self):
        if self.asset:
            self.view.seek_frame(self._pending_frame)

    def _on_moved(self, f):
        self._update_tc()
        self.timeline.set_frame(f)

    def _update_tc(self):
        if not self.ctx:
            return
        self.tc_lab.setText(self.ctx.label(self.playhead.frame))
        if bool(self.tc_lab.property("pending")) != self.playhead.pending:
            self.tc_lab.setProperty("pending", self.playhead.pending)   # dimmed until the picture is there
            repolish(self.tc_lab)
        self.tc_sub.setText(self.ctx.counter(self.playhead.frame))

    def _on_texture(self, t):
        if not t:
            self.tex_lab.setText("")
            return
        tw, th, sw, sh = t
        self.tex_lab.setText(f"Texture {tw}×{th}" if (tw, th) == (sw, sh) else f"Preview {tw}×{th} of {sw}×{sh}")
        self.tex_lab.setToolTip("" if (tw, th) == (sw, sh) else
                                f"Media over {MAX_TEXTURE} px is downscaled here; QC shows it full size.")

    def _on_problem(self, text):
        self.problem_lab.setText(f"Mapping: {text}")
        self.problem_lab.setVisible(bool(text))

    def toggle_play(self):
        if not self.playhead.toggle_play(looping=False):
            self.status.emit("Choose a clip to map first" if not self.asset else "The clip is still loading", True)

    # ---- import ------------------------------------------------------------------------
    def add_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Add model files to the stage", "", "Stage models (*.obj *.fbx *.OBJ *.FBX)")
        if paths:
            self.add_models(paths)

    def replace_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Replace the stage with these model files", "", "Stage models (*.obj *.fbx *.OBJ *.FBX)")
        if paths:
            self.replace_stage(paths)

    def add_models(self, paths):
        """Put these files on the stage as well, each as soon as it is read (and kept in the project)."""
        self._start("add", [{"path": p} for p in paths])

    def replace_stage(self, paths):
        """A new stage from these files; the current one stays until they are read."""
        self._start("replace", [{"path": p} for p in paths])

    def relink(self, sid, path):
        """A file for a saved model that could not be restored. Its saved assignments come back only
        if it is the same model (same bytes); otherwise it is added without them, and that is said."""
        if sid in self.missing:
            self._start("relink", [{"path": path, "sid": sid}])

    def relink_dialog(self, sid):
        m = self.missing.get(sid)
        if m is None:
            return
        res = self.resources.get(m["resource"], {})
        path, _ = QFileDialog.getOpenFileName(self, f"Find {res.get('name', 'the model')}", os.path.dirname(res.get("origin", "")),
                                              "Stage models (*.obj *.fbx *.OBJ *.FBX)")
        if path:
            self.relink(sid, path)

    def retry_restore(self, sids):
        """Read saved models again from the copies kept in the project (after a cancelled restore)."""
        p = self.session.project
        items = []
        for sid in sids:
            m = self.missing.get(sid)
            path = models.resolve(str(p.folder), self.resources.get(m["resource"], {})) if (m and p) else None
            if path and os.path.isfile(path):
                items.append({"path": path, "sid": sid, "rid": m["resource"], "origin": m["origin"]})
        if items:
            self._start("restore", items)

    def _start(self, mode, items):
        if self._batch is not None:
            return
        queue, skipped, seen = [], [], set()
        for it in items:
            p = it["path"]
            real, name = os.path.realpath(p), os.path.basename(p)
            there = self._already(p) if mode == "add" else None
            if there is not None:
                skipped.append((name, f"already on the stage as {self.stage.label(there)}"))
            elif real in seen and mode in ("add", "replace"):     # a restore may place one kept copy twice
                skipped.append((name, "chosen twice"))
            else:
                seen.add(real)
                queue.append(it)
        self._batch = _Batch(mode, queue, len(queue), skipped=skipped)
        self.result.hide()
        if mode == "restore":
            self.saver.suspended = True               # an incomplete stage must not replace the saved one
        if not queue:
            return self._finish()
        self._import_ui(True)
        self._next()

    def _next(self):
        b = self._batch
        b.current = b.queue.pop(0)
        n = b.total - len(b.queue)
        name = self._item_name(b.current)
        verb = {"add": "Adding", "replace": "Reading", "restore": "Restoring", "relink": "Relinking"}[b.mode]
        what = verb + (f" {n} of {b.total}: " if b.total > 1 else " ") + name + "…"
        self.scene_lab.setText(what)
        if not self.stage and not self.missing:       # a stage on show keeps its list while files are read
            self._show_objects(f"{what}\nPlayback carries on meanwhile.")
        keep = str(self.session.project.folder) if (b.mode != "restore" and self.session.project) else None
        self.importer.start(b.current["path"], keep)

    def _item_name(self, item):
        if item.get("rid") and item["rid"] in self.resources:
            return self.resources[item["rid"]]["name"]
        return os.path.basename(item["path"])

    def cancel_import(self):
        """Stop the file being read and any still queued. An Add keeps what it already added; a
        restore leaves the rest listed as not restored, with their saved settings kept."""
        if self._batch is None:
            return
        self.importer.cancel()
        b = self._batch
        b.cancelled = True
        for it in [b.current, *b.queue]:
            b.skipped.append((self._item_name(it), "not read"))
            if b.mode == "restore":
                self._mark_missing(it["sid"], it["rid"], "not restored: the restore was cancelled", it["origin"])
        b.queue = []
        self._finish()

    def _import_ui(self, busy):
        for w in (self.add_btn, self.empty_import):
            w.setEnabled(not busy)
        self.add_btn.setVisible(not busy)
        self.replace_btn.setVisible(not busy)
        self.replace_btn.setEnabled(not busy and bool(self.stage))
        self.cancel_btn.setVisible(busy)
        if not busy:
            self.scene_lab.setText(self._scene_text())

    def _on_imported(self, gen, scene, error, resource):
        b = self._batch
        if b is None or gen != self.importer.generation:
            return                                    # cancelled, superseded, or another project
        it = b.current
        name = self._item_name(it)
        if error:
            b.failed.append((name, error))
            if b.mode == "restore":
                self._mark_missing(it["sid"], it["rid"], f"could not be read: {error}", it["origin"])
        elif b.mode == "add":
            b.added.append(self._add_scene(scene, self._register(resource, scene)))
        elif b.mode == "replace":
            b.read.append((scene, self._register(resource, scene)))
        elif b.mode == "restore":
            if scene.source.get("sha256") != self.resources[it["rid"]]["sha256"]:
                b.failed.append((name, "the copy kept in this project has changed since it was saved"))
                self._mark_missing(it["sid"], it["rid"], "the copy kept in this project has changed since it was saved", it["origin"])
            else:
                scene.source["origin"] = it["origin"]
                self._forget_missing_row(it["sid"])
                held = self.missing.pop(it["sid"], None)          # tried again after a cancelled restore
                saved = held["assign"] if held else {k: v for k, v in self._pending_assign.items() if k.startswith(it["sid"] + ":")}
                src = self._add_scene(scene, it["rid"], sid=it["sid"])
                b.added.append(src)
                b.unmatched += self._reapply(src, saved)
        else:                                         # relink
            sid = it["sid"]
            m = self.missing[sid]
            rid = self._register(resource, scene)
            same = resource["sha256"] == self.resources[m["resource"]]["sha256"]
            self._forget_missing_row(sid)
            self.missing.pop(sid)
            src = self._add_scene(scene, rid, sid=sid)
            b.added.append(src)
            if same:
                b.unmatched += self._reapply(src, m["assign"])
            elif m["assign"]:
                b.notes.append(f"{name} is not the model that was saved (different contents), so its "
                               f"{len(m['assign'])} saved assignment{'s' if len(m['assign']) != 1 else ''} were not applied.")
        if b.queue:
            self._next()
        else:
            self._finish()

    def _finish(self):
        b, self._batch = self._batch, None
        if b.mode == "replace" and b.read and not b.cancelled:
            self._replace_with([s for s, _r in b.read], [r for _s, r in b.read])   # once, with every file that could be used
        self._import_ui(False)
        if b.mode == "restore":
            self._end_restore(b)
            return
        if b.mode == "relink":
            self._changed()
        text, bad = self._summary(b)
        new = self.stage.sources if b.mode == "replace" and b.read and not b.cancelled else b.added
        notes = [f"{self.stage.label(s)}: {w}" for s in new for w in s.scene.warnings]
        notes += [f"{self.stage.label(s)}: material library {m}" for s in new for m in self.resources.get(self.src_res.get(s.id), {}).get("missing", [])]
        notes += b.notes
        if text:
            self.status.emit(text if not notes else f"{text} {' '.join(notes)}", bad)
        if not self.stage and not self.missing:
            self._show_objects(text or None)
        else:
            self._show_objects()
            if text and (bad or notes or b.skipped):
                self._show_result(b, notes, bad, text)

    def _show_result(self, b, notes, bad, text):
        """Above the list, until dismissed: a count, then one short line per file that needs a look
        (the list already names the files added). The full wording is in the tooltip and the toast."""
        cut = lambda t: t if len(t) <= 90 else t[:89].rstrip() + "…"
        n = len(b.added) if b.mode in ("add", "restore", "relink") else len(b.read)
        verb = {"add": "added.", "restore": "restored.", "relink": "relinked."}.get(b.mode)
        head = (("Cancelled: " if b.cancelled else "") + f"{n} of {b.total} model file{'s' if b.total != 1 else ''} "
                + (verb or ("read; the stage was replaced." if n and not b.cancelled else "read; the stage is unchanged.")))
        lines = [head] if b.total else []
        lines += [cut(f"✕ {name}: {why}") for name, why in b.failed]
        lines += [cut(f"– {name}: {why}") for name, why in b.skipped]
        lines += [cut(f"· {note}") for note in notes]
        if self.missing:
            lines.append("Right-click a file marked missing to relink it or take it off the stage.")
        self.result_lab.setText("\n".join(lines))
        self.result_lab.setToolTip(text + "".join(f"\n{note}" for note in notes))
        self.result_lab.setStyleSheet(f"color: {theme.T['warn' if bad else 'muted']};")
        self.result.show()

    def _summary(self, b) -> tuple[str, bool]:
        """What an Add, Replace or Relink did, for the person who started it, and whether anything went wrong."""
        def names(xs):
            xs = list(xs)
            return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]
        failed = " ".join(f"{n} could not be used: {why}" + ("" if why.endswith(".") else ".") for n, why in b.failed)
        skipped = [(n, why) for n, why in b.skipped if why != "not read"]
        unread = [n for n, why in b.skipped if why == "not read"]
        added = [self.stage.label(s) for s in b.added if self.stage.source(s.id) is s]
        parts = []
        if b.mode == "add":
            if b.cancelled:
                parts.append(f"Cancelled: {names(added) if added else 'nothing'} added"
                             + (" (kept on the stage)" if b.added else "") + (f"; {names(unread)} not read." if unread else "."))
            elif b.added:
                parts.append(f"Added {names(added)}." if not b.failed else f"Added {len(added)} of {b.total} model files: {names(added)}.")
        elif b.mode == "relink":
            if b.added:
                parts.append(f"Relinked {names(added)}" + (", with its saved assignments." if not b.notes else "."))
            elif b.cancelled:
                parts.append("Relink cancelled: still missing.")
        else:
            if b.cancelled:
                parts.append("Replace cancelled: the stage is unchanged.")
            elif b.read:
                parts.append("Stage replaced" + (f" with {len(b.read)} of {b.total} model files." if b.failed else "."))
            elif b.total:
                parts.append("The stage is unchanged: none of the files could be used.")
        if failed:
            parts.append(failed)
        parts += [f"{n}: {why}, not added again." if why.startswith("already") else f"{n}: {why}." for n, why in skipped]
        bad = bool(b.failed) or (not b.added and not b.read and not b.cancelled)
        return " ".join(parts), bad

    # ---- the stage -------------------------------------------------------------------------
    def _add_scene(self, scene, rid=None, sid=None):
        """One more model file on the stage (`rid`: its copy in the project; `sid`: a saved source
        id being restored). Everything already there stays as it is."""
        first = not self.stage and self._saved_camera is None
        if self.assign is None:
            self.assign = Assignments(self.stage)
        if rid:
            scene.source.setdefault("origin", self.resources[rid]["origin"])   # shown and compared; the copy is what was read
        src = self.stage.add(scene, sid)
        if rid:
            self.src_res[src.id] = rid
        self.view.set_scene(self.stage, frame=first)  # only the new meshes are uploaded; the camera stays
        self.saver.changed()
        self._add_rows(src)
        self._relabel()
        self._apply_filter(self.filter.text())
        self._push()
        self._show_objects()
        return src

    def _replace_with(self, scenes, rids=None):
        """A new stage of these scenes (none: empty). Assignments, missing files and the filter start
        again; source ids carry on, so no id means two different files in one session."""
        self.tree.clear()                             # rows (and the selection) go while their objects still exist
        self.stage = Stage(next_id=max([self.stage.next_id] + [int(k[1:]) + 1 for k in self.missing]))
        self.src_res, self.missing = {}, {}
        for sc, rid in zip(scenes, rids or [None] * len(scenes)):
            if rid:
                sc.source.setdefault("origin", self.resources[rid]["origin"])
            src = self.stage.add(sc)
            if rid:
                self.src_res[src.id] = rid
        self.assign = Assignments(self.stage) if self.stage else None
        self.saver.changed()
        self.filter.clear()
        self.view.set_scene(self.stage if self.stage else None)
        self._fill_tree()
        self._show_objects()
        self.scene_lab.setText(self._scene_text())

    def remove_source(self, sid):
        """Take one model file, its objects and their assignments off the stage. The file is not touched."""
        if self.stage.source(sid) is None:
            return
        for i in range(self.tree.topLevelItemCount()):    # rows first: the selection changes while the objects still exist
            if self.tree.topLevelItem(i).data(0, SOURCE) == sid:
                self.tree.takeTopLevelItem(i)
                break
        src = self.stage.remove(sid)
        self.src_res.pop(sid, None)                   # the copy in the project stays (removal deletes nothing)
        self.assign.prune()
        self.view.set_scene(self.stage if self.stage else None, frame=False)
        self.saver.changed()
        if not self.stage:
            self.assign = None
        self._relabel()
        self._on_selection()
        self._show_objects()
        self.scene_lab.setText(self._scene_text())
        self.replace_btn.setEnabled(bool(self.stage) and self._batch is None)
        self.status.emit(f"Removed {src.name} from the stage (the file is unchanged).", False)

    def forget_missing(self, sid):
        """Take a saved model that could not be restored off the stage, with its saved settings."""
        m = self.missing.pop(sid, None)
        if m is not None:
            self._forget_missing_row(sid)
            self.saver.changed()
            self._show_objects()
            self.status.emit(f"Took {self.resources.get(m['resource'], {}).get('name', sid)} off the stage.", False)

    def _already(self, path):
        """The source on the stage that was read from this file, if any."""
        real = os.path.realpath(path)
        for src in self.stage.sources:
            if os.path.realpath(src.origin) == real:
                return src
        return None

    def _register(self, resource, scene=None):
        """The kept copy's record, by its id. A copy already listed keeps its record (several files on
        the stage can share one copy); where this file came from goes with its source instead."""
        if not resource:
            return None
        rec = {k: resource[k] for k in ("path", "name", "sha256", "size", "origin", "sidecars", "missing")}
        self.resources.setdefault(resource["id"], rec)
        if scene is not None:
            scene.source["origin"] = resource["origin"]
        return resource["id"]

    def _reapply(self, src, saved) -> int:
        """Saved assignments onto a restored source's objects; how many no longer match one."""
        lost = 0
        if self.assign is None:
            self.assign = Assignments(self.stage)
        for k, uv in saved.items():
            o = self.stage.object(k)
            if o is not None and src.objects and k.startswith(src.id + ":") and uv < o.uv_sets:
                self.assign.uv[k] = uv
            else:
                lost += 1
        return lost

    def remove_selected_source(self):
        sid = self._selected_source()
        if sid:
            self.remove_source(sid)

    def _selected_source(self):
        """The one model file every selected object comes from, else None."""
        sids = {split_key(k)[0] for k in self.selected_keys()}
        return sids.pop() if len(sids) == 1 else None

    def _show_objects(self, message=None):
        """The object list once there is a stage; before that, what to do next (or what happened).
        Called after every change to the stage, so the Projectors tab follows it too."""
        self._projector_changed()
        has = (bool(self.stage) or bool(self.missing)) and message is None
        self.objects.setCurrentIndex(1 if has else 0)
        self.filter.setEnabled(has)
        self.shown_btn.setEnabled(has)
        if not has:
            self.empty_lab.setText(message or "No stage yet.\nAdd one or more OBJ or FBX model files: together they "
                                   "form one stage, each where it was exported. Then choose the objects that show the clip.")
            self.empty_import.setVisible(self.add_btn.isEnabled())

    def _scene_text(self):
        s = self.stage
        if not s:
            self.scene_lab.setToolTip("")
            return "No stage imported"
        tris = sum(o.triangles for o in s.objects)
        self.scene_lab.setToolTip("\n".join(src.origin for src in s.sources))
        what = s.label(s.sources[0]) if len(s.sources) == 1 else f"{len(s.sources)} model files"
        return f"{what}: {len(s.objects)} objects, {tris:,} triangles"

    # ---- objects and assignments ------------------------------------------------------------
    def _fill_tree(self):
        self.tree.clear()
        for src in self.stage.sources:
            self._add_rows(src)
        for sid in self.missing:
            self._add_missing_row(sid)
        self._relabel()
        self._apply_filter(self.filter.text())

    def _add_rows(self, src):
        """One model file's row, with its objects below it in the file's own hierarchy."""
        top = QTreeWidgetItem(["", ""])
        top.setData(0, SOURCE, src.id)
        top.setFlags(top.flags() & ~Qt.ItemFlag.ItemIsSelectable)     # clicking it selects its objects
        f = top.font(0)
        f.setBold(True)
        top.setFont(0, f)
        self.tree.addTopLevelItem(top)
        items = {}
        muted = theme.colour("muted")
        for o in src.objects:
            parts = split_key(o.key)[1].split("/")
            parent = top
            for d in range(1, len(parts)):
                k = "/".join(parts[:d])
                if k not in items:
                    it = QTreeWidgetItem([o.path[d - 1] + ("" if "#" not in parts[d - 1] else f" ({parts[d - 1].rsplit('#', 1)[1]})"), ""])
                    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                    it.setForeground(0, muted)
                    parent.addChild(it)
                    items[k] = it
                parent = items[k]
            it = QTreeWidgetItem([o.display, ""])
            it.setData(0, KEY, o.key)
            parent.addChild(it)
        top.setExpanded(True)
        for it in items.values():
            it.setExpanded(True)
        self._refresh_rows()
        self._refresh_controls()

    def _add_missing_row(self, sid):
        """A saved model that is not on the stage: its name, why, and (right-click) what to do."""
        m = self.missing[sid]
        res = self.resources.get(m["resource"], {})
        top = QTreeWidgetItem([f"{res.get('name', sid)} — missing", f"{len(m['assign'])} saved" if m["assign"] else ""])
        top.setData(0, SOURCE, sid)
        top.setData(0, MISSING, True)
        top.setFlags(top.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        for c in range(2):
            top.setForeground(c, theme.colour("warn"))
        top.setToolTip(0, f"Not on the stage: {m['why']}.\nKept in the project as {res.get('path', '?')}"
                          f"\nOriginally {m['origin'] or '?'}\n{len(m['assign'])} saved assignment(s) held back."
                          "\nRight-click to relink it to a file or take it off the stage.")
        self.tree.addTopLevelItem(top)

    def _mark_missing(self, sid, rid, why, origin):
        """A saved model that did not come back; its saved assignments are held, not dropped."""
        prior = self.missing.get(sid, {}).get("assign")
        assign = prior if prior is not None else {k: v for k, v in self._pending_assign.items() if k.startswith(sid + ":")}
        self._forget_missing_row(sid)
        self.missing[sid] = {"resource": rid, "why": why, "assign": assign, "origin": origin or self.resources.get(rid, {}).get("origin", "")}
        self._add_missing_row(sid)

    def _forget_missing_row(self, sid):
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            if it.data(0, SOURCE) == sid and it.data(0, MISSING):
                self.tree.takeTopLevelItem(i)
                return

    def _relabel(self):
        """File rows named so that two files with one name can be told apart."""
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            src = self.stage.source(top.data(0, SOURCE))
            if src is None:
                continue
            top.setText(0, self.stage.label(src))
            tris = sum(o.triangles for o in src.objects)
            kept = f"\nKept in the project as {self.resources[self.src_res[src.id]]['path']}" if src.id in self.src_res else ""
            tip = (f"{src.origin}{kept}\n{len(src.objects)} objects, {tris:,} triangles · {src.scene.importer}"
                   + "".join(f"\n{w}" for w in src.scene.warnings) + "\nClick to select its objects; right-click to remove it.")
            top.setToolTip(0, tip)

    def _on_row_clicked(self, it, _col):
        sid = it.data(0, SOURCE)
        if sid:
            self.select([i.data(0, KEY) for i in self._rows(it) if not self._hidden(i)])

    def _row_menu(self, pos):
        it = self.tree.itemAt(pos)
        while it is not None and it.parent() is not None:
            it = it.parent()
        if it is not None and it.data(0, MISSING):
            sid = it.data(0, SOURCE)
            name = self.resources.get(self.missing[sid]["resource"], {}).get("name", sid)
            m = QMenu(self)
            m.addAction(f"Relink {name}…", lambda: self.relink_dialog(sid))
            kept = models.resolve(str(self.session.project.folder), self.resources.get(self.missing[sid]["resource"], {})) if self.session.project else None
            if kept and os.path.isfile(kept):
                m.addAction("Try restoring it again", lambda: self.retry_restore([sid]))
            m.addAction(f"Take {name} off the stage", lambda: self.forget_missing(sid))
            m.exec(self.tree.viewport().mapToGlobal(pos))
            return
        src = self.stage.source(it.data(0, SOURCE)) if it is not None else None
        if src is None:
            return
        m = QMenu(self)
        m.addAction(f"Select the objects of {self.stage.label(src)}", lambda: self._on_row_clicked(it, 0))
        m.addAction(f"Remove {self.stage.label(src)} from stage", lambda: self.remove_source(src.id))
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def _rows(self, under=None):
        """Object rows, everywhere or below one row."""
        out = []
        stack = [under] if under is not None else [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            it = stack.pop()
            if it.data(0, KEY):
                out.append(it)
            stack.extend(it.child(i) for i in range(it.childCount()))
        return out

    def _refresh_rows(self):
        """Media column, colour and tooltip per object row; only rows that change are touched
        (each change makes the view re-measure, which added up to minutes on big stages)."""
        if not self.assign:
            return
        colours = {k: theme.colour(k) for k in ("warn", "muted", "ink")}
        for it in self._rows():
            k = it.data(0, KEY)
            uv = self.assign.uv.get(k)
            w = self.assign.warning(k)
            text = "no UVs" if not self.assign.eligible(k) else f"● UV {uv + 1}" + (" ⚠" if w else "") if uv is not None else ""
            if it.text(1) == text and it.data(1, KEY) is not None:
                continue
            it.setData(1, KEY, True)                  # filled once; afterwards only changes
            it.setText(1, text)
            it.setForeground(1, colours["warn" if w else "muted" if uv is None else "ink"])
            o = self.assign.object(k)
            src = self.stage.source_of(k)
            tip = (f"{split_key(k)[1]}\nin {self.stage.label(src)}\n{o.triangles:,} triangles · {len(o.slots)} material slot(s): {', '.join(o.slots)}\n"
                   + (f"Shows the clip: {w}.\n" if w else "") + self.assign.details(k))
            for c in range(2):
                it.setToolTip(c, tip)

    def _apply_filter(self, text):
        """Hide objects that do not match (and groups with nothing shown). Hidden objects keep
        their selection and assignments; the inspector says when some of the selection is hidden."""
        t = text.strip().lower()

        def walk(it) -> bool:
            k = it.data(0, KEY)
            shown = any([walk(it.child(i)) for i in range(it.childCount())])
            if it.data(0, MISSING):                   # a missing file has no objects to match: shown unless filtered out by name
                shown = not t or t in it.text(0).lower()
            if k:
                shown = shown or not t or t in k.lower() or t in it.text(0).lower()
            it.setHidden(not shown)
            return shown

        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))
        self._refresh_controls()

    def select_shown(self):
        want = [it.data(0, KEY) for it in self._rows() if not self._hidden(it)]
        self.select(want)

    @staticmethod
    def _hidden(it) -> bool:
        while it is not None:
            if it.isHidden():
                return True
            it = it.parent()
        return False

    def _selected_items(self):
        """Selected object rows, including any the filter hides (QTreeWidget.selectedItems() leaves those out)."""
        items = (self.tree.itemFromIndex(i) for i in self.tree.selectionModel().selectedIndexes() if i.column() == 0)   # selectedRows() is quadratic
        return [it for it in items if it is not None and it.data(0, KEY)]

    def selected_keys(self) -> list[str]:
        return [it.data(0, KEY) for it in self._selected_items()]

    def select(self, keys):
        """Select objects by key (also used by tests and automation). One selection change, with
        neighbouring rows as one range: row by row, a stage of 100k objects took minutes."""
        want = set(keys)
        sel = QItemSelection()
        model = self.tree.model()
        last = self.tree.columnCount() - 1

        def walk(parent):
            run = None                                # (first, last) row of a run of wanted children
            n = model.rowCount(parent)
            for r in range(n + 1):
                idx = model.index(r, 0, parent) if r < n else None
                wanted = idx is not None and idx.data(KEY) in want
                if wanted:
                    run = (run[0], r) if run else (r, r)
                elif run:
                    sel.select(model.index(run[0], 0, parent), model.index(run[1], last, parent))
                    run = None
                if idx is not None and model.rowCount(idx):
                    walk(idx)
        walk(QModelIndex())
        self.tree.selectionModel().select(sel, QItemSelectionModel.SelectionFlag.ClearAndSelect)

    def _on_selection(self):
        self._refresh_controls()
        self._push()

    def _push(self):
        self.view.set_assignments(self.assign.uv if self.assign else {}, self.selected_keys())

    def _refresh_controls(self):
        keys = self.selected_keys()
        a = self.assign
        clip = self.asset["name"] if self.asset else None
        self.uv_box.clear()
        self.warn_lab.hide()
        apply_n = clear_n = 0
        if a and keys:
            choices = a.uv_choices(keys)
            state = a.uv_state(keys)
            if state is MIXED:
                self.uv_box.addItem("Mixed: choose to set all", None)
            for s in choices:
                self.uv_box.addItem(f"UV {s + 1}", s)
            if isinstance(state, int) and state in choices:
                self.uv_box.setCurrentIndex(self.uv_box.findData(state))
            eligible = [k for k in keys if a.eligible(k)]
            apply_n = len(eligible) if choices else 0
            clear_n = sum(1 for k in keys if k in a.uv)
            shown = sum(1 for k in keys if k in a.uv)
            hidden = sum(1 for it in self._selected_items() if self._hidden(it))
            first = a.object(keys[0])
            bits = [first.display if len(keys) == 1 else f"{len(keys)} selected"]
            if len(self.stage.sources) > 1 and self._selected_source():
                bits.append(f"in {self.stage.label(self.stage.source_of(keys[0]))}")
            if len(keys) > 1 or shown:
                bits.append("shows the clip" if len(keys) == 1 else f"{shown} show the clip" if shown else "none show the clip")
            if hidden:
                bits.append(f"{hidden} hidden by the filter")
            n_no = len(keys) - len(eligible)
            if n_no:
                bits.append("no UVs: stays neutral" if len(keys) == 1 else f"{n_no} without UVs")
            if eligible and not choices:
                bits.append("no UV set they all share")
            self.sel_lab.setText(" · ".join(bits))
            self._selection_warning(eligible)
        else:
            self.sel_lab.setText("Nothing selected" if a else "")
        self.clip_lab.setText(f"Clip: {clip}" if clip else "Choose a clip above")
        self.clip_lab.setToolTip(clip or "")
        self.apply_btn.setText(f"Apply clip to {apply_n} selected" if apply_n else "Apply clip")
        self.apply_btn.setEnabled(bool(apply_n))
        if bool(self.apply_btn.property("primary")) != bool(apply_n):
            self.apply_btn.setProperty("primary", bool(apply_n))     # the strong style only when it can act
            repolish(self.apply_btn)
        self.clear_btn.setText(f"Clear {clear_n}" if clear_n else "Clear")
        self.clear_btn.setEnabled(bool(clear_n))
        self.uv_box.setEnabled(self.uv_box.count() > 0)
        n_all = sum(1 for o in a.scene.objects if o.uv_sets) if a else 0
        self.all_btn.setText(f"Apply to all {n_all} with UVs" if n_all else "Apply to all with UVs")
        self.all_btn.setEnabled(bool(n_all))
        sid = self._selected_source() if keys else None
        src = self.stage.source(sid) if sid else None
        self.remove_btn.setVisible(src is not None)
        if src is not None:
            name = self.stage.label(src)
            self.remove_btn.setText(f"Remove {name} from stage")
            self.remove_btn.setToolTip(f"Take {name} and its {len(src.objects)} objects (and their assignments) off the stage. "
                                       f"The file itself is not changed.\n{src.origin}")

    def _selection_warning(self, eligible):
        """For the selection, in the UV set chosen (or each object's own when mixed): which parts
        are, or would be once applied, neutral because their UVs leave the image."""
        a, s = self.assign, self._chosen_uv()
        hits = []
        for k in eligible:
            use = s if s is not None and s < a.object(k).uv_sets else None
            r = a.coverage(k, use)
            if r and r.outside:
                hits.append((k, use if use is not None else a.uv.get(k, 0), r))
        if not hits:
            return
        verb = "shows" if all(k in a.uv for k, _s, _r in hits) else "would show"
        if len(hits) == 1:
            k, used, r = hits[0]
            text = (f"UV {used + 1} is partly outside the image: about {share(r.neutral_share)} of "
                    f"{a.object(k).display if len(eligible) > 1 else 'this surface'} {verb} neutral there.")
        else:
            worst = max(r.neutral_share for _k, _s, r in hits)
            text = f"{len(hits)} of {len(eligible)} have UVs partly outside the image (up to {share(worst)} neutral)."
        self.warn_lab.setText(text)
        self.warn_lab.setToolTip("\n\n".join(f"{a.object(k).display}: {a.details(k, used)}" for k, used, _r in hits[:8])
                                 + ("\n\n…" if len(hits) > 8 else ""))
        self.warn_lab.show()

    def _chosen_uv(self):
        return self.uv_box.currentData() if self.uv_box.count() else None

    def _on_uv_chosen(self, _i):
        s = self._chosen_uv()
        keys = [k for k in self.selected_keys() if k in self.assign.uv]
        if s is not None and keys:
            self._report(self.assign.apply(keys, s))
            self._changed()

    def apply_selected(self):
        keys = self.selected_keys()
        if self.assign and keys:
            self._report(self.assign.apply(keys, self._chosen_uv()))
            self._changed()

    def clear_selected(self):
        if self.assign:
            self.assign.clear(self.selected_keys())
            self._changed()

    def apply_all(self):
        if self.assign:
            self._report(self.assign.apply_all(None))
            self._changed()

    def _report(self, skipped):
        if skipped:
            names = [self.assign.object(k).display if self.assign.object(k) else k for k in skipped]
            self.status.emit(f"Left neutral: {', '.join(names[:4])}{'…' if len(names) > 4 else ''} ({next(iter(skipped.values()))})", False)

    def _changed(self):
        self._refresh_rows()
        self._refresh_controls()
        self._push()
        self.saver.changed()

    # ---- projector ---------------------------------------------------------------------------
    def _on_tab(self, i):
        mode = "projectors" if i == 1 else "surfaces"
        self.view.set_mode(mode)
        if hasattr(self, "saver"):
            self.saver.changed()
        self.mode_lab.setText("Projector preview" if mode == "projectors" else "UV assignments")
        self.mode_lab.setToolTip("The view shows the projector's picture on every surface (UVs not used)." if mode == "projectors"
                                 else "The view shows the clip on assigned objects through their own UVs.")

    def add_projector(self):
        """A projector where the view's camera is, aimed where it looks."""
        self.projector = None
        self.projector_from_view()

    def projector_from_view(self):
        v = self.view
        eye, tgt = v._eye(), v.cam_target
        if v.looking_through and self.projector is not None:       # already looking through: nothing to take
            return
        self.projector = from_view((eye.x(), eye.y(), eye.z()), (tgt.x(), tgt.y(), tgt.z()), 40.0,
                                   max(1, v.width()) / max(1, v.height()),
                                   (self.projector.image_aspect(self.clip_aspect) if self.projector else self.clip_aspect or 16 / 9),
                                   base=self.projector)
        self._projector_changed()
        self.saver.changed()

    def edit_projector(self, fields):
        if self.projector is not None:
            self.projector = self.projector.with_(**fields)
            self._projector_changed()
            self.saver.changed()

    def remove_projector(self):
        """The projector goes; the stage, its files and the UV assignments are untouched."""
        self.view.look_through(False)
        had, self.projector = self.projector, None
        self._projector_changed()
        if had is not None:
            self.saver.changed()

    def _projector_changed(self):
        self.view.set_projector(self.projector, self.clip_aspect)
        vis = None
        if self.projector is not None:
            a = self.projector.image_aspect(self.clip_aspect)
            vis = (VISIBILITY_SIZE, max(1, round(VISIBILITY_SIZE / a)))
        self.proj_panel.show_state(bool(self.stage), self.projector, self.clip_aspect, vis)

    # ---- keyboard ------------------------------------------------------------------------------
    def closed_selector(self, w) -> bool:
        """The clip or UV selector, closed and not editable: the workspace's keys still apply there."""
        return w in (self.media_box, self.uv_box) and not w.isEditable() and not w.view().isVisible()

    def handle_key(self, ev) -> bool:
        k, txt = ev.key(), ev.text()
        shift = bool(ev.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        fps = max(1, round(float(self.ctx.rate))) if self.ctx else 25
        if k == Qt.Key.Key_Space:
            if not ev.isAutoRepeat():         # held down: one toggle, not play/pause flicker
                self.toggle_play()
        elif k == Qt.Key.Key_Left:
            self.playhead.step(-fps if shift else -1)
        elif k == Qt.Key.Key_Right:
            self.playhead.step(fps if shift else 1)
        elif k == Qt.Key.Key_Home:
            self.playhead.seek(0)
        elif k == Qt.Key.Key_End and self.ctx:
            self.playhead.seek(self.ctx.count - 1)
        elif txt in ("f", "F"):
            self.view.frame_all()
        else:
            return False
        return True
