"""Media list: every asset with its result, filters, and the actions on a selection."""
from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QMenu, QPushButton, QTableView, QVBoxLayout, QWidget)

from . import theme

# The file name leads: it is how a reviewer knows what they are looking at.
COLS = [("name", "File"), ("qc_result", "Result"), ("review_status", "Review"), ("kind", "Type"), ("vcodec", "Codec"),
        ("res", "Resolution"), ("fps_float", "fps"), ("duration", "Duration"), ("open_detections", "Unconfirmed"),
        ("fails", "Fails"), ("warnings", "Warnings"), ("notes", "Notes"), ("dir", "Folder")]
NAME_COL = 0
COL_WIDTH = {"name": 320, "qc_result": 84, "review_status": 118, "kind": 80, "vcodec": 84, "res": 92, "fps_float": 56,
             "duration": 68, "open_detections": 90, "fails": 50, "warnings": 70, "notes": 50}


def setup_columns(table):
    table.horizontalHeader().setMinimumSectionSize(44)
    for i, (key, _) in enumerate(COLS):
        table.setColumnWidth(i, COL_WIDTH.get(key, 80))


def fmt_duration(s):
    if s is None:
        return ""
    s = int(round(s))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


class AssetModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self.rows: list[dict] = []

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = list(rows)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(COLS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return COLS[section][1]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        a = self.rows[index.row()]
        key = COLS[index.column()][0]
        if role == Qt.ItemDataRole.UserRole:
            return a
        if role == Qt.ItemDataRole.DisplayRole:
            if key == "qc_result":
                return "Error" if a["scan_state"] == "error" else ("Scanning…" if a["scan_state"] in ("probing", "analysing") else theme.RESULT_LABEL.get(a.get("qc_result") or "pending", "Pending"))
            if key == "res":
                return f"{a['width']}×{a['height']}" if a.get("width") else ""
            if key == "fps_float":
                return f"{a['fps_float']:g}" if a.get("fps_float") else ""
            if key == "duration":
                return fmt_duration(a.get("duration"))
            if key == "review_status":
                return theme.REVIEW_LABEL.get(a.get("review_status"), "")
            if key in ("open_detections", "fails", "warnings", "notes"):
                return str(a.get(key) or "") if a.get(key) else ""
            if key == "kind":
                return {"video": "Video", "audio": "Audio", "image": "Still", "sequence": "Sequence"}.get(a.get("kind"), a.get("kind"))
            return a.get(key)
        if role == Qt.ItemDataRole.ForegroundRole:
            if key == "qc_result":
                res = "fail" if a["scan_state"] == "error" else (a.get("qc_result") or "pending")
                return theme.colour(theme.RESULT_COLOURS.get(res, ("muted", "panel2"))[0])
            if key == "open_detections" and a.get("open_detections"):
                return theme.colour("warn")
            if key in ("dir", "fps_float", "duration", "res", "vcodec"):
                return theme.colour("muted")
        if role == Qt.ItemDataRole.FontRole:
            if key in ("res", "fps_float", "duration", "dir"):
                return theme.mono(11)
            if key == "name":
                return theme.sans(13, QFont.Weight.Medium)
        if role == Qt.ItemDataRole.ToolTipRole and key == "name":
            return a.get("path")
        if role == Qt.ItemDataRole.TextAlignmentRole and key in ("open_detections", "fails", "warnings", "notes", "fps_float", "duration"):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None


class AssetFilter(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.text = ""
        self.result = "all"
        self.kind = ""
        self.review = ""
        self.detections = False

    def filterAcceptsRow(self, row, parent):
        a = self.sourceModel().rows[row]
        if self.text:
            t = self.text.lower()
            if t.startswith("check:"):
                return a["id"] in getattr(self, "check_hits", set())
            if t not in (a.get("name") or "").lower() and t not in (a.get("dir") or "").lower():
                return False
        if self.result != "all":
            res = "fail" if a["scan_state"] == "error" else (a.get("qc_result") or "pending")
            if res != self.result:
                return False
        if self.kind and a.get("kind") != self.kind:
            return False
        if self.review and a.get("review_status") != self.review:
            return False
        if self.detections and not a.get("open_detections"):
            return False
        return True

    def lessThan(self, left, right):
        a, b = self.sourceModel().rows[left.row()], self.sourceModel().rows[right.row()]
        key = COLS[left.column()][0]
        if key == "res":
            key = "width"
        va, vb = a.get(key), b.get(key)
        if isinstance(va, str) or isinstance(vb, str):
            return str(va or "").lower() < str(vb or "").lower()
        return (va or 0) < (vb or 0)


class MediaScreen(QWidget):
    open_asset = Signal(int)
    add_media = Signal()
    recheck = Signal(list)
    remove = Signal(list)
    bulk_review = Signal(list, str)

    def __init__(self):
        super().__init__()
        self.model = AssetModel()
        self.proxy = AssetFilter()
        self.proxy.setSourceModel(self.model)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search media…  (check:content.black lists assets with that problem)")
        self.search.setToolTip("Search by file name or folder. check:<key> lists assets with that problem, e.g. check:content.black")
        self.search.setMinimumWidth(120)
        self.search.textChanged.connect(self._filters)
        bar.addWidget(self.search, 1)
        self.f_result = QComboBox()
        for k, l in (("all", "All results"), ("fail", "Fail"), ("warning", "Warning"), ("pass", "Pass"), ("pending", "Pending")):
            self.f_result.addItem(l, k)
        self.f_kind = QComboBox()
        for k, l in (("", "All types"), ("video", "Video"), ("audio", "Audio"), ("image", "Stills"), ("sequence", "Sequences")):
            self.f_kind.addItem(l, k)
        self.f_review = QComboBox()
        self.f_review.addItem("Any review status", "")
        for k in theme.REVIEW_ORDER:
            self.f_review.addItem(theme.REVIEW_LABEL[k], k)
        self.f_det = QCheckBox("Unconfirmed detections")
        for w in (self.f_result, self.f_kind, self.f_review):
            w.currentIndexChanged.connect(self._filters)
            bar.addWidget(w)
        self.f_det.toggled.connect(self._filters)
        bar.addWidget(self.f_det)
        add = QPushButton("+ Add media")
        add.setProperty("primary", True)
        add.clicked.connect(self.add_media)
        bar.addWidget(add)
        lay.addLayout(bar)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(NAME_COL, Qt.SortOrder.AscendingOrder)  # Qt's default is column 0 descending
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.horizontalHeader().setStretchLastSection(True)
        setup_columns(self.table)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        self.table.doubleClicked.connect(lambda ix: self.open_asset.emit(self.proxy.data(ix, Qt.ItemDataRole.UserRole)["id"]))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        lay.addWidget(self.table, 1)
        self.summary = QLabel()
        self.summary.setProperty("muted", True)
        lay.addWidget(self.summary)

    def set_rows(self, rows, check_hits=None):
        sel = self.selected_ids()
        self.model.set_rows(rows)
        if check_hits is not None:
            self.proxy.check_hits = set(check_hits)
        self.proxy.invalidateFilter()
        self._summary()
        for r in range(self.proxy.rowCount()):
            if self.proxy.data(self.proxy.index(r, 0), Qt.ItemDataRole.UserRole)["id"] in sel:
                self.table.selectRow(r)

    def visible_ids(self) -> list[int]:
        return [self.proxy.data(self.proxy.index(r, 0), Qt.ItemDataRole.UserRole)["id"] for r in range(self.proxy.rowCount())]

    def selected_ids(self) -> list[int]:
        return [self.proxy.data(ix, Qt.ItemDataRole.UserRole)["id"] for ix in self.table.selectionModel().selectedRows()]

    def current_id(self):
        ix = self.table.currentIndex()
        return self.proxy.data(ix, Qt.ItemDataRole.UserRole)["id"] if ix.isValid() else None

    def _filters(self, *_):
        self.proxy.text = self.search.text().strip()
        self.proxy.result = self.f_result.currentData()
        self.proxy.kind = self.f_kind.currentData()
        self.proxy.review = self.f_review.currentData()
        self.proxy.detections = self.f_det.isChecked()
        self.proxy.invalidateFilter()
        self._summary()

    def _summary(self):
        n, total = self.proxy.rowCount(), self.model.rowCount()
        self.summary.setText(f"{n} of {total} assets" if n != total else f"{total} assets")

    def _menu(self, pos):
        ids = self.selected_ids()
        if not ids:
            return
        m = QMenu(self)
        m.addAction("Open in review", lambda: self.open_asset.emit(ids[0]))
        m.addAction(f"Re-check {len(ids)} selected", lambda: self.recheck.emit(ids))
        sub = m.addMenu("Set review status")
        for k in theme.REVIEW_ORDER:
            sub.addAction(theme.REVIEW_LABEL[k], lambda k=k: self.bulk_review.emit(ids, k))
        m.addSeparator()
        m.addAction(f"Remove {len(ids)} from project", lambda: self.remove.emit(ids))
        m.exec(self.table.viewport().mapToGlobal(pos))

    def keyPressEvent(self, ev):
        if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.current_id() is not None:
            self.open_asset.emit(self.current_id())
        elif ev.text() == "/":
            self.search.setFocus()
        else:
            super().keyPressEvent(ev)
