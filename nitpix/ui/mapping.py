"""The Mapping workspace (experimental): a project's media on an imported OBJ / FBX stage.

Separate from the QC review screen, with its own player (nitpix/ui/mapview.py), scene and
assignments, sharing the transport behaviour through the Playhead. One playback session at a
time: MainWindow calls activate() / deactivate() as the user moves between the workspaces, and a
deactivated workspace has no file loaded, so it cannot play, decode, make sound or seek.

Session only in this alpha: the imported stage, the assignments and the camera last while the
app runs and are not saved with the project.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QSplitter,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import service
from ..mapping import MIXED, Assignments
from ..sceneimport import Importer
from . import theme
from .mapview import MAX_TEXTURE, MappingView
from .playhead import Playhead
from .review import PosCtx, btn
from .timeline import Timeline
from .widgets import ElideLabel, FlowLayout, repolish

KEY = Qt.ItemDataRole.UserRole
MEDIA_KINDS = ("video", "sequence", "image")


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
        self._pending_frame = 0                # where a newly loaded clip is to be shown
        self.importer = Importer(self._imported.emit)
        self._imported.connect(self._on_imported, Qt.ConnectionType.QueuedConnection)
        self._build()

    # ---- layout ------------------------------------------------------------------------
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(8)
        head = FlowLayout(hspacing=10, vspacing=6)
        title = QLabel("Mapping")
        title.setStyleSheet("font-weight: 600; font-size: 16px;")
        head.addWidget(title)
        head.addWidget(QLabel("Mapped clip"))
        self.media_box = QComboBox()
        self.media_box.setMinimumWidth(220)
        self.media_box.setToolTip("The clip every assigned object shows. Changing it changes the shared source for all of them.")
        self.media_box.activated.connect(lambda _i: self._choose_media(self.media_box.currentData()))
        head.addWidget(self.media_box)
        self.import_btn = btn("Import stage…", "Read an OBJ or FBX stage model", self.import_dialog)
        head.addWidget(self.import_btn)
        self.cancel_btn = btn("Cancel import", "", self.cancel_import, ghost=True)
        self.cancel_btn.hide()
        head.addWidget(self.cancel_btn)
        self.scene_lab = ElideLabel("No stage imported", Qt.TextElideMode.ElideMiddle, min_chars=8)
        self.scene_lab.setProperty("muted", True)
        head.addWidget(self.scene_lab)
        root.addLayout(head)
        note = ElideLabel("Experimental · session only: the imported stage and its assignments are not saved with the project.")
        note.setProperty("faint", True)
        root.addWidget(note)
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
        self.view.setToolTip("Drag to orbit · right-drag or ⇧-drag to pan · scroll to zoom · F frames the stage")
        ll.addWidget(self.view, 1)
        self.playhead = Playhead(self.view, lambda: True, self)
        self.playhead.moved.connect(self._on_moved)
        self.playhead.pending_changed.connect(lambda _p: self.ctx and self._update_tc())
        info = QHBoxLayout()
        self.tex_lab = ElideLabel("")
        self.tex_lab.setFont(theme.mono(11))
        self.tex_lab.setProperty("muted", True)
        info.addWidget(self.tex_lab, 1)
        info.addWidget(btn("Frame all", "Fit the whole stage in view (F)", lambda: self.view.frame_all(), ghost=True))
        info.addWidget(btn("Reset view", "Front view, whole stage", lambda: self.view.frame_all(reset=True), ghost=True))
        ll.addLayout(info)
        tr = FlowLayout(hspacing=10, vspacing=6)
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
        ll.addLayout(tr)
        self.timeline = Timeline()
        self.timeline.seek_requested.connect(self.playhead.scrub)
        ll.addWidget(self.timeline)
        split.addWidget(left)

        right = QWidget()
        rlay = QVBoxLayout(right)
        rlay.setContentsMargins(0, 0, 0, 0)
        rlay.setSpacing(6)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Object", "Media", "Note"])
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.itemSelectionChanged.connect(self._on_selection)
        rlay.addWidget(self.tree, 1)
        self.sel_lab = ElideLabel("Select objects in the list (⌘/⇧-click for several).")
        self.sel_lab.setProperty("muted", True)
        rlay.addWidget(self.sel_lab)
        uvrow = QHBoxLayout()
        uvrow.addWidget(QLabel("UV set"))
        self.uv_box = QComboBox()
        self.uv_box.setToolTip("Which of the objects' own UV sets the media uses. Coordinates are used as authored.")
        self.uv_box.activated.connect(self._on_uv_chosen)
        uvrow.addWidget(self.uv_box, 1)
        rlay.addLayout(uvrow)
        acts = QHBoxLayout()
        self.apply_btn = btn("Apply current media", "Show the mapped clip on the selected objects", self.apply_selected, primary=True)
        self.clear_btn = btn("Clear assignment", "Return the selected objects to neutral grey", self.clear_selected)
        acts.addWidget(self.apply_btn)
        acts.addWidget(self.clear_btn)
        rlay.addLayout(acts)
        self.all_btn = btn("Apply to all eligible objects", "Every object with UVs shows the mapped clip", self.apply_all, ghost=True)
        rlay.addWidget(self.all_btn)
        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        split.setSizes([900, 340])
        root.addWidget(split, 1)
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
        self.tex_lab.setText(f"Texture {tw}×{th}" if (tw, th) == (sw, sh) else
                             f"Preview {tw}×{th} of {sw}×{sh}: media over {MAX_TEXTURE} px is downscaled here; QC shows it full size")

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
        self.scene_name = os.path.basename(path)
        self._import_ui(True)
        self.importer.start(path)

    def cancel_import(self):
        self.importer.cancel()
        self._import_ui(False)
        self.scene_lab.setText(f"{self.scene_name}: import cancelled" if not self.scene else self._scene_text())

    def _import_ui(self, busy):
        self.import_btn.setEnabled(not busy)
        self.cancel_btn.setVisible(busy)
        if busy:
            self.scene_lab.setText(f"Importing {self.scene_name}…")

    def _on_imported(self, gen, scene, error):
        if gen != self.importer.generation:
            return                                    # cancelled, superseded, or another project
        self._import_ui(False)
        if error:
            self.scene_lab.setText(self._scene_text() if self.scene else "No stage imported")
            self.status.emit(f"{self.scene_name}: {error}", True)
            return
        self._set_scene(scene, self.scene_name)
        if scene.warnings:
            self.status.emit(" ".join(scene.warnings), False)

    def _set_scene(self, scene, name):
        self.scene = scene
        self.assign = Assignments(scene) if scene else None
        self.scene_name = name
        self.view.set_scene(scene)
        self.scene_lab.setText(self._scene_text() if scene else "No stage imported")
        self._fill_tree()

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
                    it = QTreeWidgetItem([o.path[d - 1] + ("" if "#" not in parts[d - 1] else f" ({parts[d - 1].rsplit('#', 1)[1]})"), "", ""])
                    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                    it.setForeground(0, theme.colour("muted"))
                    (parent.addChild(it) if parent else self.tree.addTopLevelItem(it))
                    items[k] = it
                parent = items[k]
            it = QTreeWidgetItem([o.display, "", ""])
            it.setData(0, KEY, o.key)
            it.setToolTip(0, f"{o.key}\n{o.triangles:,} triangles · {len(o.slots)} material slot(s): {', '.join(o.slots)}\n"
                             f"{o.uv_sets} UV set(s)")
            (parent.addChild(it) if parent else self.tree.addTopLevelItem(it))
            items[o.key] = it
        self.tree.expandAll()
        self._refresh_rows()
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
            it.setText(1, f"● UV {uv + 1}" if uv is not None else "")
            w = self.assign.warning(k)
            it.setText(2, w or "")
            it.setForeground(2, theme.colour("warn" if w else "muted"))

    def selected_keys(self) -> list[str]:
        return [it.data(0, KEY) for it in self.tree.selectedItems() if it.data(0, KEY)]

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
        self.uv_box.clear()
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
            assigned = a.assigned_state(keys)
            self.apply_btn.setEnabled(bool(eligible) and bool(choices))
            self.clear_btn.setEnabled(assigned is True or assigned is MIXED)
            n_no = len(keys) - len(eligible)
            bits = [f"{len(keys)} selected"]
            if assigned is MIXED:
                bits.append("some show media")
            if n_no:
                bits.append(f"{n_no} without UVs (stay neutral)")
            if eligible and not choices:
                bits.append("no UV set they all share")
            self.sel_lab.setText(" · ".join(bits))
        else:
            self.apply_btn.setEnabled(False)
            self.clear_btn.setEnabled(False)
            self.sel_lab.setText("Select objects in the list (⌘/⇧-click for several)." if a else "Import a stage to map media onto it.")
        self.uv_box.setEnabled(self.uv_box.count() > 0)
        self.all_btn.setEnabled(bool(a))

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
    def handle_key(self, ev) -> bool:
        k, txt = ev.key(), ev.text()
        shift = bool(ev.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        fps = max(1, round(float(self.ctx.rate))) if self.ctx else 25
        if k == Qt.Key.Key_Space:
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
