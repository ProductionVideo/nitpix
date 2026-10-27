"""The Mapping workspace: a project's media on an imported OBJ / FBX stage.

Separate from the QC review screen, with its own player (nitpix/ui/mapview.py), scene and
assignments, sharing the transport behaviour through the Playhead. One playback session at a
time: MainWindow calls activate() / deactivate() as the user moves between the workspaces, and a
deactivated workspace has no file loaded, so it cannot play, decode, make sound or seek.

Session only for now: the imported stage, the assignments and the camera last while the
app runs and are not saved with the project.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QSplitter, QStackedWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import service
from ..mapping import MIXED, Assignments, share
from ..sceneimport import Importer
from . import theme
from .mapview import MAX_TEXTURE, MappingView
from .playhead import Playhead
from .review import PosCtx, btn
from .timeline import Timeline
from .widgets import ElideLabel, repolish

KEY = Qt.ItemDataRole.UserRole
MEDIA_KINDS = ("video", "sequence", "image")
SESSION_NOTE = "Session only: the imported stage, its assignments and the camera are not saved with the project."
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


class MappingScreen(QWidget):
    status = Signal(str, bool)                 # toast text, is_error
    _imported = Signal(int, object, object)    # from the importer's thread: generation, scene, error

    def __init__(self, session: service.Session):
        super().__init__()
        self.session = session
        self.active = False
        self.asset = None                      # the mapped clip (asset detail)
        self.ctx = None
        self.scene = None
        self.assign = None
        self.scene_name = ""
        self._kept_name = ""
        self._pending_frame = 0                # where a newly loaded clip is to be shown
        self.importer = Importer(self._imported.emit)
        self._imported.connect(self._on_imported, Qt.ConnectionType.QueuedConnection)
        self._build()

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
        self.import_btn = btn("Import stage…", "Read an OBJ or FBX stage model", self.import_dialog)
        head.addWidget(self.import_btn)
        self.cancel_btn = btn("Cancel import", "", self.cancel_import)
        self.cancel_btn.hide()
        head.addWidget(self.cancel_btn)
        note = QLabel("Session only: not saved")
        note.setProperty("faint", True)
        note.setToolTip(SESSION_NOTE)
        head.addWidget(note)
        root.addLayout(head)
        self.problem_lab = QLabel()
        self.problem_lab.setWordWrap(True)
        self.problem_lab.setStyleSheet(f"background: {theme.T['fail_bg']}; color: {theme.T['fail']}; padding: 6px 10px; border-radius: 6px;")
        self.problem_lab.hide()
        root.addWidget(self.problem_lab)

        split = QSplitter(Qt.Orientation.Horizontal)
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
        self.view.camera_input.connect(lambda: self.hint_lab.hide())   # learned; the view's tooltip still has it
        ll.addWidget(self.view, 1)
        self.playhead = Playhead(self.view, lambda: True, self)
        self.playhead.moved.connect(self._on_moved)
        self.playhead.pending_changed.connect(lambda _p: self.ctx and self._update_tc())
        info = QHBoxLayout()
        self.tex_lab = ElideLabel("")
        self.tex_lab.setFont(theme.mono(11))
        self.tex_lab.setProperty("muted", True)
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
        rlay.setContentsMargins(0, 0, 0, 0)
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
        self.objects = QStackedWidget()
        self.empty_lab = QLabel()
        self.empty_lab.setWordWrap(True)
        self.empty_lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_lab.setProperty("muted", True)
        empty = QWidget()
        el = QVBoxLayout(empty)
        el.addStretch(1)
        el.addWidget(self.empty_lab)
        self.empty_import = btn("Import stage…", "Read an OBJ or FBX stage model", self.import_dialog, primary=True)
        el.addWidget(self.empty_import, 0, Qt.AlignmentFlag.AlignHCenter)
        el.addStretch(2)
        self.objects.addWidget(empty)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Object", "Media"])
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setIndentation(14)
        self.tree.setTextElideMode(Qt.TextElideMode.ElideMiddle)      # "Floor_…_07_Rev3", not "Floor_Inlay_LED_Pan…"
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.itemSelectionChanged.connect(self._on_selection)
        self.objects.addWidget(self.tree)
        rlay.addWidget(self.objects, 1)

        insp = QFrame()
        insp.setObjectName("inspector")
        insp.setStyleSheet(f"QFrame#inspector {{ border-top: 1px solid {theme.T['line2']}; }}")
        il = QVBoxLayout(insp)
        il.setContentsMargins(0, 8, 0, 0)
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
        self.all_btn = btn("Apply to all with UVs", "Every object with UVs shows the clip", self.apply_all, ghost=True)
        il.addWidget(self.all_btn)
        rlay.addWidget(insp)
        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        split.setSizes([900, 340])
        root.addWidget(split, 1)
        self._show_objects()
        self._refresh_controls()

    # ---- workspace lifecycle -----------------------------------------------------------------
    def activate(self, handoff=None):
        """Become the one playback session. `handoff` (asset id, frame) is what QC was showing."""
        self.active = True
        self.refresh_media()
        aid, frame = handoff if handoff else (self.asset["id"] if self.asset else None, self.playhead.frame)
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

    def reset_project(self):
        """Another project (or none): drop the stage, assignments, clip and any import in progress."""
        self.importer.cancel()
        self._import_ui(False)
        self.deactivate()
        self.asset = self.ctx = None
        self._set_scene(None, "")
        self.media_box.clear()
        self.tc_lab.setText("--:--:--:--")
        self.tc_sub.setText("")

    def shutdown(self):
        self.importer.cancel()
        self.playhead.stop()
        self.view.shutdown()

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
    def import_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import a stage model", "", "Stage models (*.obj *.fbx *.OBJ *.FBX)")
        if path:
            self.import_scene(path)

    def import_scene(self, path):
        import os
        self._kept_name = self.scene_name             # the stage on show, if this import fails or is cancelled
        self.scene_name = os.path.basename(path)
        self._import_ui(True)
        self.importer.start(path)

    def cancel_import(self):
        self.importer.cancel()
        self._import_ui(False)
        if self.scene:
            self.scene_name = self._kept_name
        self.scene_lab.setText(f"{self.scene_name}: import cancelled" if not self.scene else self._scene_text())
        self._show_objects()

    def _import_ui(self, busy):
        self.import_btn.setEnabled(not busy)
        self.empty_import.setEnabled(not busy)
        self.import_btn.setVisible(not busy)
        self.cancel_btn.setVisible(busy)
        if busy:
            self.scene_lab.setText(f"Importing {self.scene_name}…")
            if self.scene is None:                    # a stage on show keeps its list while the next one loads
                self._show_objects(f"Importing {self.scene_name}…\nPlayback carries on meanwhile.")

    def _on_imported(self, gen, scene, error):
        if gen != self.importer.generation:
            return                                    # cancelled, superseded, or another project
        self._import_ui(False)
        if error:
            failed, self.scene_name = self.scene_name, self._kept_name if self.scene else self.scene_name
            self.scene_lab.setText(self._scene_text() if self.scene else "No stage imported")
            self.status.emit(f"{failed}: {error}", True)
            self._show_objects(None if self.scene else f"{failed} could not be used: {error}")   # a stage keeps its list
            return
        self._set_scene(scene, self.scene_name)
        if scene.warnings:
            self.status.emit(" ".join(scene.warnings), False)

    def _set_scene(self, scene, name):
        self.scene = scene
        self.assign = Assignments(scene) if scene else None
        self.scene_name = name
        self.filter.clear()
        self.view.set_scene(scene)
        self.scene_lab.setText(self._scene_text() if scene else "No stage imported")
        self.scene_lab.setToolTip(scene.source["path"] if scene else "")
        self._fill_tree()
        self._show_objects()

    def _show_objects(self, message=None):
        """The object list once there is a stage; before that, what to do next (or what happened)."""
        has = self.scene is not None and message is None
        self.objects.setCurrentIndex(1 if has else 0)
        self.filter.setEnabled(has)
        self.shown_btn.setEnabled(has)
        if not has:
            self.empty_lab.setText(message or "No stage yet.\nImport an OBJ or FBX stage, then choose the objects that show the clip.")
            self.empty_import.setVisible(self.import_btn.isEnabled())

    def _scene_text(self):
        s = self.scene
        tris = sum(o.triangles for o in s.objects)
        return f"{self.scene_name}: {len(s.objects)} objects, {tris:,} triangles"

    # ---- objects and assignments ------------------------------------------------------------
    def _fill_tree(self):
        self.tree.clear()
        if not self.scene:
            self._refresh_controls()
            return
        items = {}
        for o in self.scene.objects:
            parts = o.key.split("/")
            parent = None
            for d in range(1, len(parts)):
                k = "/".join(parts[:d])
                if k not in items:
                    it = QTreeWidgetItem([o.path[d - 1] + ("" if "#" not in parts[d - 1] else f" ({parts[d - 1].rsplit('#', 1)[1]})"), ""])
                    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                    it.setForeground(0, theme.colour("muted"))
                    (parent.addChild(it) if parent else self.tree.addTopLevelItem(it))
                    items[k] = it
                parent = items[k]
            it = QTreeWidgetItem([o.display, ""])
            it.setData(0, KEY, o.key)
            (parent.addChild(it) if parent else self.tree.addTopLevelItem(it))
            items[o.key] = it
        self.tree.expandAll()
        self._refresh_rows()
        self._apply_filter(self.filter.text())
        self._refresh_controls()

    def _rows(self):
        out = []
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            it = stack.pop()
            if it.data(0, KEY):
                out.append(it)
            stack.extend(it.child(i) for i in range(it.childCount()))
        return out

    def _refresh_rows(self):
        if not self.assign:
            return
        for it in self._rows():
            k = it.data(0, KEY)
            uv = self.assign.uv.get(k)
            w = self.assign.warning(k)
            it.setText(1, "no UVs" if not self.assign.eligible(k) else f"● UV {uv + 1}" + (" ⚠" if w else "") if uv is not None else "")
            it.setForeground(1, theme.colour("warn" if w else "muted" if uv is None else "ink"))
            o = self.assign.object(k)
            tip = (f"{o.key}\n{o.triangles:,} triangles · {len(o.slots)} material slot(s): {', '.join(o.slots)}\n"
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
        items = (self.tree.itemFromIndex(i) for i in self.tree.selectionModel().selectedRows(0))
        return [it for it in items if it is not None and it.data(0, KEY)]

    def selected_keys(self) -> list[str]:
        return [it.data(0, KEY) for it in self._selected_items()]

    def select(self, keys):
        """Select objects by key (also used by tests and automation)."""
        want = set(keys)
        self.tree.clearSelection()
        for it in self._rows():
            if it.data(0, KEY) in want:
                it.setSelected(True)

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
