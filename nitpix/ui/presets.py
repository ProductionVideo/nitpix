"""Presets screen: choose the delivery preset for the project and edit custom ones with
pickers, the same vocabulary and validation as the web editor (vocab.py does the real work)."""
from __future__ import annotations

import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton, QScrollArea, QSizePolicy, QSplitter, QVBoxLayout,
                               QWidget)

from .. import service, vocab
from . import theme
from .review import btn, chip
from .widgets import Cluster, ElideLabel, FlowLayout, repolish

# (key, label, type, vocab field / example). Types: pick = any-of chips from the vocabulary plus typed
# "other" values; choice = one value with suggestions; int / num / tc / regex = validated text;
# bool / alpha / req = fixed choices. A tuple of fields is laid out on one row.
FIELDS = {
    "video": [("containers", "Containers", "pick", "containers"), ("codecs", "Codecs", "pick", "codecs"),
              ("profiles", "Profiles", "pick", "profiles"),
              (("width", "Frame size", "int", "1920"), ("height", "×", "int", "1080")),
              ("fps", "Frame rates", "pick", "fps"), ("progressive", "Progressive only", "bool", ""),
              ("bit_depth_min", "Min bit depth", "int", "10"), ("chroma", "Chroma", "pick", "chroma"), ("alpha", "Alpha", "alpha", ""),
              ("color_primaries", "Primaries", "pick", "color_primaries"), ("color_trc", "Transfer", "pick", "color_trc"),
              ("color_space", "Matrix", "pick", "color_space"), ("par", "Pixel aspect", "choice", "par"),
              (("min_duration", "Duration (s)", "num", "min"), ("max_duration", "to", "num", "max")),
              ("start_tc", "Start timecode", "tc", "10:00:00:00"), ("timecode", "Timecode track", "req", "")],
    "audio": [("required", "Audio", "req", ""), ("codecs", "Codecs", "pick", "audio_codecs"),
              ("sample_rate", "Sample rate", "choice", "sample_rate"), ("channels", "Total channels", "int", "2"),
              ("loudness_target", "Loudness target (LUFS)", "num", "-23"), ("loudness_tolerance", "Tolerance (LU)", "num", "1"),
              ("true_peak_max", "Max true peak (dBTP)", "num", "-1")],
    "sequence": [("formats", "Formats", "pick", "formats"),
                 (("width", "Frame size", "int", ""), ("height", "×", "int", "")),
                 ("bit_depth", "Bit depths", "pick", "bit_depth"), ("start_frame", "Start frame", "int", "1001"),
                 ("padding", "Padding", "int", "4"), ("naming_regex", "Name prefix regex", "regex", r"^[A-Za-z0-9_]+\.$"),
                 ("alpha", "Alpha", "alpha", "")],
    "still": [("formats", "Formats", "pick", "formats"),
              (("width", "Frame size", "int", "1920"), ("height", "×", "int", "1080")),
              ("alpha", "Alpha", "alpha", "")],
}
SECTION_TITLE = {"video": "Video", "audio": "Audio", "sequence": "Image sequences", "still": "Still images"}
TC_RE = re.compile(r"^\d{1,2}[:;.]\d{2}[:;.]\d{2}[:;.]\d{2,3}$")


def num_of(s: str, integer: bool):
    """"−23" (typographic minus) and "1,920" are how people type; float() would reject them."""
    t = str(s).strip().replace("−", "-").replace("–", "-")
    t = re.sub(r"[,\s]", "", t) if integer else t.replace(",", ".")
    m = re.match(r"^[+-]?\d+(\.\d+)?", t)
    if not m:
        return None
    return int(float(m.group(0))) if integer else float(m.group(0))


def field_error(t: str, v: str) -> str:
    if v == "":
        return ""
    if t == "int":
        n = num_of(v, True)
        return "Whole number" if n is None or n < 0 else ""
    if t == "num":
        return "" if num_of(v, False) is not None else "Number, e.g. -23"
    if t == "tc":
        return "" if TC_RE.match(v) else "HH:MM:SS:FF (; for drop-frame)"
    if t == "regex":
        try:
            re.compile(v)
            return ""
        except re.error:
            return "Not a valid regex"
    return ""


# ---- field widgets --------------------------------------------------------------------
class PickField(QWidget):
    """Any-of chips. Values not in the vocabulary (typed in "+ other") become amber chips."""
    changed = Signal()

    def __init__(self, options: list[dict], field: str = "", editable=True):
        super().__init__()
        self.options = options
        self.field = field
        self.flow = FlowLayout(self, hspacing=4, vspacing=4)
        self.chips: dict[str, QPushButton] = {}
        self.add = None
        for o in options:
            self._chip(o["value"], o["label"], False)
        if editable:
            self.add = QLineEdit()
            self.add.setProperty("pickadd", True)
            self.add.setPlaceholderText("+ other")
            self.add.setToolTip("Add a value that isn't listed (ffprobe's name or a common one), then press Enter")
            self.add.setFixedWidth(84)
            self.add.returnPressed.connect(self._commit)
            self.add.editingFinished.connect(self._commit)
            self.flow.addWidget(self.add)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def _chip(self, value, label, custom) -> QPushButton:
        b = QPushButton(label + ("  ×" if custom else ""))
        b.setCheckable(True)
        b.setProperty("pick", True)
        b.setProperty("custom", custom)
        b.setProperty("value", value)
        b.setToolTip(str(value))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        if custom:
            b.setChecked(True)
            b.clicked.connect(lambda: self._remove(b))
        else:
            b.toggled.connect(lambda _: self.changed.emit())
        self.chips[str(value)] = b
        if self.add is not None and custom:
            self.flow.removeWidget(self.add)
            self.flow.addWidget(b)
            self.flow.addWidget(self.add)
        else:
            self.flow.addWidget(b)
        return b

    def _remove(self, b):
        self.chips.pop(str(b.property("value")), None)
        self.flow.removeWidget(b)
        b.deleteLater()
        self.changed.emit()

    def _commit(self):
        if self.add is None or not self.add.text().strip():
            return
        for v in [s.strip() for s in self.add.text().split(",") if s.strip()]:
            canon = vocab.normalise(self.field, v) if self.field else None  # "Avid DNxHR" -> dnxhd, "48k" -> 48000
            hit = next((b for b in self.chips.values()
                        if v.lower() in (str(b.property("value")).lower(), b.text().replace("  ×", "").lower())
                        or (canon is not None and str(b.property("value")) == str(canon))), None)
            if hit:
                hit.setChecked(True)  # typing a listed option selects it instead of duplicating it
            else:
                self._chip(v, v, True)
        self.add.clear()
        self.changed.emit()

    def values(self) -> list:
        return [b.property("value") for b in self.chips.values() if b.isChecked()]

    def set_values(self, vals):
        vals = vals if isinstance(vals, list) else ([] if vals in (None, "") else [vals])
        known = {str(o["value"]) for o in self.options}
        for b in list(self.chips.values()):
            if b.property("custom"):
                self._remove(b)
        for s, b in self.chips.items():
            b.blockSignals(True)
            b.setChecked(any(str(v) == s for v in vals))
            b.blockSignals(False)
        for v in vals:
            if str(v) not in known:
                self._chip(v, str(v), True)

    def restrict(self, allowed: set | None):
        """Show only `allowed` vocabulary chips (plus whatever is selected); None shows all."""
        for b in self.chips.values():
            if not b.property("custom"):
                b.setVisible(allowed is None or str(b.property("value")) in allowed or b.isChecked())
        self.flow.invalidate()
        self.updateGeometry()


class TextField(QLineEdit):
    """Validated text with the error shown underneath by the form."""

    def __init__(self, t: str, example: str = ""):
        super().__init__()
        self.t = t
        self.err = QLabel()
        self.err.setProperty("err", True)
        self.err.hide()
        self.setPlaceholderText(f"not checked · e.g. {example}" if example else "not checked")
        self.textChanged.connect(self.validate)
        self.setMinimumWidth(90)

    def validate(self) -> bool:
        msg = field_error(self.t, self.text().strip())
        self.err.setText(msg)
        self.err.setVisible(bool(msg))
        if bool(self.property("bad")) != bool(msg):
            self.setProperty("bad", bool(msg))
            repolish(self)
        return not msg

    def value(self):
        v = self.text().strip()
        if v == "":
            return None
        if self.t == "int":
            return num_of(v, True)
        if self.t == "num":
            return num_of(v, False)
        return v

    def set_value(self, v):
        self.setText("" if v is None else (f"{v:g}" if isinstance(v, float) else str(v)))


class ChoiceField(QComboBox):
    """One value, typed or chosen from suggestions; the vocabulary value is stored when a
    suggestion is chosen, the typed text otherwise (vocab normalises it on save)."""

    def __init__(self, options: list[dict]):
        super().__init__()
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.addItem("", None)
        for o in options:
            self.addItem(o["label"], o["value"])
        self.lineEdit().setPlaceholderText("not checked")
        self.setMinimumWidth(140)

    def value(self):
        t = self.currentText().strip()
        if not t:
            return None
        i = self.findText(t)
        return self.itemData(i) if i > 0 else t

    def set_value(self, v):
        if v in (None, ""):
            return self.setCurrentIndex(0)
        i = self.findData(v)
        if i < 0:
            i = self.findText(str(v))
        if i >= 0:
            self.setCurrentIndex(i)
        else:
            self.setEditText(str(v))


# ---- the editor ------------------------------------------------------------------------
class PresetEditor(QWidget):
    changed = Signal()

    def __init__(self, vocab: dict):
        super().__init__()
        self.vocab = vocab
        self.widgets: dict[tuple[str, str], QWidget] = {}
        self.types: dict[tuple[str, str], str] = {}
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)
        grid.setColumnMinimumWidth(0, 120)
        row = 0
        self.name = QLineEdit()
        self.name.setPlaceholderText("Spec name")
        self.desc = QLineEdit()
        self.desc.setPlaceholderText("What this delivery is for")
        for lab, w in (("Name", self.name), ("Description", self.desc)):
            grid.addWidget(self._label(lab), row, 0)
            grid.addWidget(w, row, 1)
            w.textChanged.connect(lambda _: self.changed.emit())
            row += 1
        for sec, fields in FIELDS.items():
            head = QLabel(SECTION_TITLE[sec].upper())
            head.setStyleSheet(f"color: {theme.T['muted']}; font-size: 11px; letter-spacing: .06em; font-weight: 600; padding-top: 10px;")
            grid.addWidget(head, row, 0, 1, 2)
            row += 1
            for f in fields:
                group = f if isinstance(f[0], tuple) else (f,)
                grid.addWidget(self._label(group[0][1]), row, 0)
                if len(group) == 1:
                    w = self._make(sec, *group[0])
                    grid.addWidget(w, row, 1)
                    if isinstance(w, TextField):
                        row += 1
                        grid.addWidget(w.err, row, 1)
                else:
                    cl = Cluster(spacing=6)
                    for i, (k, lab, t, ex) in enumerate(group):
                        if i:
                            cl.add(self._label(lab))
                        w = cl.add(self._make(sec, k, lab, t, ex))
                        w.setPlaceholderText(f"e.g. {ex}" if ex else "any")  # short: these share a row
                        w.setMaximumWidth(140)
                    cl.lay.addStretch(1)
                    grid.addWidget(cl, row, 1)
                    row += 1
                    errs = Cluster(*[self.widgets[(sec, k)].err for k, *_ in group], spacing=6)
                    grid.addWidget(errs, row, 1)
                row += 1
        tip = QLabel("Leave a field empty, or select nothing, to skip that check. Several selections mean “any of”. Values are "
                     "matched loosely, so “ProRes 422 HQ”, “Rec.709”, “23.98” or “48k” all work in “+ other” and suggestion boxes. "
                     "Hover an option to see the name ffprobe reports.")
        tip.setWordWrap(True)
        tip.setProperty("faint", True)
        grid.addWidget(tip, row, 0, 1, 2)
        self.widgets[("video", "codecs")].changed.connect(self._filter_profiles)

    def _label(self, text) -> QLabel:
        lab = QLabel(text)
        lab.setProperty("muted", True)
        lab.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        lab.setContentsMargins(0, 5, 0, 0)
        return lab

    def _make(self, sec, k, lab, t, ex) -> QWidget:
        if t == "pick":
            w = PickField(self.vocab.get(ex) or [], ex)
            w.changed.connect(self.changed.emit)
        elif t == "choice":
            w = ChoiceField(self.vocab.get(ex) or [])
            w.currentTextChanged.connect(lambda _: self.changed.emit())
        elif t == "bool":
            w = QCheckBox("")
            w.toggled.connect(lambda _: self.changed.emit())
        elif t == "alpha":
            w = QComboBox()
            for v, l in (("any", "Any"), ("required", "Required"), ("forbidden", "Not allowed")):
                w.addItem(l, v)
            w.currentIndexChanged.connect(lambda _: self.changed.emit())
        elif t == "req":
            w = QComboBox()
            for v, l in ((None, "Not checked"), (True, "Required"), (False, "Optional")):
                w.addItem(l, v)
            if k == "timecode":
                w.setToolTip("Most LED / web / playback-server deliveries carry no timecode, so it is only reported unless required here")
            w.currentIndexChanged.connect(lambda _: self.changed.emit())
        else:
            w = TextField(t, ex)
            w.textChanged.connect(lambda _: self.changed.emit())
        if isinstance(w, (QComboBox,)) and not isinstance(w, ChoiceField):
            w.setMinimumWidth(140)
            w.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.widgets[(sec, k)] = w
        self.types[(sec, k)] = t
        return w

    def _filter_profiles(self):
        codecs = [str(c) for c in self.widgets[("video", "codecs")].values()]
        cp = self.vocab.get("codec_profiles") or {}
        if not codecs or any(c not in cp for c in codecs):
            return self.widgets[("video", "profiles")].restrict(None)
        self.widgets[("video", "profiles")].restrict({p for c in codecs for p in cp.get(c, [])})

    def load(self, p: dict):
        self.blockSignals(True)
        self.name.setText(p.get("name") or "")
        self.desc.setText(p.get("description") or "")
        for (sec, k), w in self.widgets.items():
            cur = (p.get(sec) or {}).get(k)
            t = self.types[(sec, k)]
            w.blockSignals(True)
            if t == "pick":
                w.set_values(cur)
            elif t == "choice":
                w.set_value(cur)
            elif t == "bool":
                w.setChecked(bool(cur))
            elif t == "alpha":
                w.setCurrentIndex(max(0, w.findData(cur or "any")))
            elif t == "req":
                w.setCurrentIndex({None: 0, True: 1, False: 2}.get(cur, 0))
            else:
                w.set_value(cur)
            w.blockSignals(False)
        self._filter_profiles()
        self.blockSignals(False)

    def collect(self, pid: str | None) -> dict:
        """The preset body for service.save_preset. Raises ValueError on invalid fields."""
        bad = [w for (sec, k), w in self.widgets.items() if isinstance(w, TextField) and not w.validate()]
        if bad:
            bad[0].setFocus()
            raise ValueError("Fix the highlighted fields before saving.")
        out = {"id": pid, "name": self.name.text().strip() or "Custom spec", "description": self.desc.text().strip()}
        for sec in FIELDS:
            o = {}
            for (s, k), w in self.widgets.items():
                if s != sec:
                    continue
                t = self.types[(s, k)]
                if t == "pick":
                    v = w.values()
                    if v:
                        o[k] = v
                elif t == "bool":
                    if w.isChecked():
                        o[k] = True
                elif t == "alpha":
                    if w.currentData() != "any":
                        o[k] = w.currentData()
                elif t == "req":
                    if w.currentData() is not None:
                        o[k] = w.currentData()
                else:
                    v = w.value()
                    if v is not None:
                        o[k] = v
            if o:
                out[sec] = o
        return out


# ---- the screen -------------------------------------------------------------------------
class PresetsScreen(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.data = {"presets": [], "active": None, "vocab": {}}
        self.current: dict | None = None
        self.dirty = False
        self.editor: PresetEditor | None = None
        root = QHBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        root.addWidget(split)
        # left: the list
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        h = QLabel("Delivery specs")
        h.setProperty("h1", True)
        ll.addWidget(h)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.currentItemChanged.connect(self._pick)
        ll.addWidget(self.list, 1)
        lrow = QHBoxLayout()
        lrow.addWidget(btn("+ New", "Start a blank custom spec", self._new))
        self.dup_btn = btn("Duplicate", "Copy this spec so it can be edited", self._dup)
        lrow.addWidget(self.dup_btn)
        self.del_btn = btn("Delete", "", self._delete, ghost=True)
        lrow.addWidget(self.del_btn)
        lrow.addStretch(1)
        ll.addLayout(lrow)
        left.setMinimumWidth(240)
        split.addWidget(left)
        # right: header + editor
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(8)
        hrow = QHBoxLayout()
        self.title = ElideLabel("")
        self.title.setStyleSheet("font-size: 15px; font-weight: 600;")
        hrow.addWidget(self.title, 1)
        self.active_chip = chip("Active for this project", "pass")
        hrow.addWidget(self.active_chip)
        self.use_btn = btn("Use for this project", "Re-evaluates stored measurements instantly, no re-decode", self._use, primary=True)
        hrow.addWidget(self.use_btn)
        self.save_btn = btn("Save spec", "", self._save, primary=True)
        hrow.addWidget(self.save_btn)
        rl.addLayout(hrow)
        self.banner = QLabel("Built-in specs are read-only. Duplicate this spec to customise it.")
        self.banner.setProperty("banner", True)
        self.banner.setWordWrap(True)
        rl.addWidget(self.banner)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        rl.addWidget(self.scroll, 1)
        right.setMinimumWidth(420)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        split.setSizes([300, 900])

    # ---- data -----------------------------------------------------------------------
    def refresh(self, select: str | None = None):
        p = self.win.session.project
        self.data = service.presets(p)
        if self.editor is None:
            self.editor = PresetEditor(self.data["vocab"])
            self.editor.changed.connect(self._dirty)
            box = QFrame()
            box.setObjectName("panel")
            bl = QVBoxLayout(box)
            bl.setContentsMargins(14, 12, 14, 12)
            bl.addWidget(self.editor)
            self.scroll.setWidget(box)
        want = select or (self.current or {}).get("id") or self.data["active"]
        self.list.blockSignals(True)
        self.list.clear()
        hit = None
        for pr in self.data["presets"]:
            it = QListWidgetItem(pr["name"])
            it.setData(Qt.ItemDataRole.UserRole, pr["id"])
            it.setToolTip(pr.get("description") or "")
            sub = ("Active · " if pr["id"] == self.data["active"] else "") + ("Built-in" if pr.get("builtin") else "Custom")
            it.setText(f"{pr['name']}\n{sub}")
            if pr["id"] == self.data["active"]:
                it.setForeground(theme.colour("pass"))
            self.list.addItem(it)
            if pr["id"] == want:
                hit = it
        self.dirty = False  # a refresh is an explicit reload; nothing to ask about
        self.list.setCurrentItem(hit or self.list.item(0))
        self.list.blockSignals(False)
        self._show(self._by_id(want) or (self.data["presets"][0] if self.data["presets"] else None))

    def _by_id(self, pid):
        return next((x for x in self.data["presets"] if x["id"] == pid), None)

    def _pick(self, it, prev=None):
        if not it:
            return
        pid = it.data(Qt.ItemDataRole.UserRole)
        if self.current and pid == self.current["id"]:
            return
        if self.dirty and QMessageBox.question(self, "Unsaved changes", "Discard the changes to this spec?") != QMessageBox.StandardButton.Yes:
            self.list.blockSignals(True)
            self.list.setCurrentItem(prev)
            self.list.blockSignals(False)
            return
        self._show(self._by_id(pid))

    def _editable_default(self, pr: dict | None) -> bool:
        """The factory default spec is edited in place (saved as this project's own version) until
        that version exists; after that, edits go to the project's version."""
        src, own = service.default_spec_ids()
        return bool(pr) and pr["id"] == src and self._by_id(own) is None

    def _show(self, pr: dict | None):
        self.current = pr
        self.dirty = False
        if not pr:
            return
        locked = bool(pr.get("builtin")) and not self._editable_default(pr)
        active = pr["id"] == self.data["active"]
        self.title.setText(pr["name"])
        self.active_chip.setVisible(active)
        self.use_btn.setVisible(not active)
        self.save_btn.setVisible(not locked)
        self.save_btn.setEnabled(False)
        src, own = service.default_spec_ids()
        mine = self._by_id(own) if pr["id"] == src else None
        self.banner.setText(f"This project has its own version of this spec, “{mine['name']}”. Edit that one, or Duplicate this one to "
                            "start again from the factory settings." if mine else
                            "Built-in specs are read-only. Duplicate this spec to customise it.")
        self.banner.setVisible(locked)
        self.del_btn.setEnabled(not pr.get("builtin"))
        self.editor.load(pr)
        self.editor.setEnabled(not locked)

    def _dirty(self):
        self.dirty = True
        self.save_btn.setEnabled(True)

    # ---- actions ---------------------------------------------------------------------
    def _recheck_all(self, why: str):
        p = self.win.session.project
        ids = [r["id"] for r in p.q("SELECT id FROM assets")]
        if ids and self.win.session.jobs:
            self.win.session.jobs.recheck(ids, reanalyse=False)
            self.win.toast(f"{why} · re-evaluating {len(ids)} asset(s)")
        else:
            self.win.toast(why)

    def _use(self):
        if not self.current:
            return
        if self.dirty and (not self.current.get("builtin") or self._editable_default(self.current)):
            if not self._save(quiet=True):  # using a spec must not throw away its edits
                return
        service.update_settings(self.win.session.project, {"preset_id": self.current["id"]})
        self.win.refresh_rail()
        self.refresh(self.current["id"])
        self._recheck_all(f"Spec: {self.current['name']}")

    def _save(self, quiet=False) -> bool:
        default = self._editable_default(self.current)
        if not self.current or (self.current.get("builtin") and not default):
            return False
        p = self.win.session.project
        try:
            body = self.editor.collect(self.current["id"])
            if default:  # first save of the factory default: this project's own version, active if the default was
                pid, warnings, _ = service.save_default_spec(p, body)
            else:
                pid, warnings = service.save_preset(p, body)
        except ValueError as e:
            self.win.toast(str(e), True)
            return False
        for w in warnings:
            self.win.toast(w, True)
        self.dirty = False
        self.win.refresh_rail()
        self.refresh(pid)
        if quiet:
            return True
        if pid == self.data["active"]:
            self._recheck_all("Spec saved")
        elif not warnings:
            self.win.toast("Spec saved")
        return True

    def _new(self):
        pid, _ = service.save_preset(self.win.session.project, {"name": "New delivery spec", "description": ""})
        self.refresh(pid)
        self.editor.name.setFocus()
        self.editor.name.selectAll()

    def _dup(self):
        if not self.current:
            return
        src = self.current
        try:
            locked = src.get("builtin") and not self._editable_default(src)
            body = {k: v for k, v in src.items() if k in ("name", "description", "video", "audio", "sequence", "still")} if locked else self.editor.collect(None)
        except ValueError as e:
            return self.win.toast(str(e), True)
        body["id"] = None
        body["name"] = (body.get("name") or src["name"]) + " (copy)"
        pid, warnings = service.save_preset(self.win.session.project, body)
        for w in warnings:
            self.win.toast(w, True)
        self.dirty = False
        self.refresh(pid)

    def _delete(self):
        if not self.current or self.current.get("builtin"):
            return
        if QMessageBox.question(self, "Delete spec", f"Delete “{self.current['name']}”?") != QMessageBox.StandardButton.Yes:
            return
        try:
            service.delete_preset(self.win.session.project, self.current["id"])
        except ValueError as e:
            return self.win.toast(str(e), True)
        self.current = None
        self.dirty = False
        self.win.refresh_rail()
        self.refresh()
