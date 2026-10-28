"""Shortcuts and power moves, grouped by screen. The panel opens on the screen you are on, so a
reviewer sees review keys first and someone triaging a delivery sees the media-list moves."""
from __future__ import annotations

import platform

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QScrollArea, QVBoxLayout, QWidget

from . import theme

MOD = "⌘" if platform.system() == "Darwin" else "Ctrl"

# view -> (title, one-line purpose, [(keys, what it does)])
SECTIONS = {
    "review": ("Review", "Judge the source media frame by frame.", [
        ("Space", "Play / pause. At the end of a clip, plays again from the start"),
        ("← →", "Step one frame"),
        ("⇧← ⇧→", "Step one second"),
        ("Home  End", "First / last frame"),
        ("g", "Go to a timecode or frame number (Esc or an arrow key returns to the player)"),
        ("[  ]", "Previous / next marker: findings and notes in timeline order"),
        ("j  k", "Focus next / previous finding, and jump to it"),
        ("⇧1 ⇧2 ⇧3 ⇧4", "Decide the focused finding: Pass / Issue / Warning / N/A"),
        ("⇧0", "Clear the decision"),
        ("i  o", "Set in / out. With both set, playback loops the range"),
        ("b", "Bookmark this frame"),
        ("n", "Note at this frame (tick “in/out range” to cover the loop)"),
        (f"{MOD}↩", "Save the note you are writing"),
        (",  .", "Previous / next asset in the media list's current order and filter"),
        ("a  r", "Approve / reject the asset"),
        ("e", "Open in IINA at this frame (when IINA is installed)"),
        ("Esc", "Back to the media list"),
        ("Click", "Click the timeline to seek; click the picture to take the keys back from a text field"),
    ]),
    "media": ("Media", "Triage a delivery: filter, sort, open, bulk-decide.", [
        ("Drop", "Drag files or folders onto the window to scan them straight away"),
        ("/", "Search by file or folder name"),
        ("check:key", "In search: assets with that problem, e.g. check:content.black or check:colour.alpha"),
        ("↑ ↓  Enter", "Move through the list, open the asset in Review"),
        ("Double-click", "Open in Review"),
        (f"⇧-click  {MOD}-click", "Select a range / several assets"),
        ("Right-click", "Re-check, set review status, or remove the selection in one go"),
        ("Header", "Click a column header to sort; the Review screen's , and . follow that order"),
    ]),
    "presets": ("Specs", "Describe what a delivery must be.", [
        ("Chips", "Several selections mean “any of”; select nothing to skip the check"),
        ("+ other", "Type a value that isn't listed (“ProRes 422 HQ”, “Rec.709”, “48k”); it maps onto the vocabulary"),
        ("Hover", "Hover a chip to see the exact name ffprobe reports"),
        ("Use", "“Use for this project” saves your edits first, then re-checks every asset from stored measurements"),
    ]),
    "dashboard": ("Dashboard", "Where the delivery stands.", [
        ("Double-click", "Open an asset from Needs attention in Review"),
    ]),
    "reports": ("Reports", "Hand the result on.", [
        ("Scope", "Export everything, what the Media filter shows, or only the selected assets"),
        ("Double-click", "Reveal an exported file in Finder"),
    ]),
    "settings": ("Settings", "Project and app preferences.", []),
    "mapping": ("Mapping", "The mapped clip on an imported stage, while it plays.", [
        ("Drag", "Orbit the stage"),
        ("Right-drag  ⇧-drag", "Pan"),
        ("Scroll  pinch", "Zoom"),
        ("F", "Frame the whole stage"),
        ("Space", "Play / pause"),
        ("← →  ⇧← ⇧→", "Step one frame / one second"),
        ("Home  End", "First / last frame"),
        (f"{MOD}-click  ⇧-click", "Select several objects in the list"),
        ("Drag an arrow", "Projectors: move the projector along X, Y or Z"),
        ("Esc", "Projectors: cancel picking or a drag; leave look-through"),
        (f"{MOD}Z  ⇧{MOD}Z", "Projectors: undo / redo a projector edit"),
    ]),
}
EVERYWHERE = ("Everywhere", "", [
    ("1 – 6", "Dashboard, Media, Review, Specs, Reports, Settings (in Review too)"),
    ("?", "This panel, opened on the screen you are on"),
    ("Drop", "Drop media on the window or the Dock icon; with no project open, one is made for it"),
])
ORDER = ["review", "media", "presets", "dashboard", "reports", "settings"]


def _rows(entries) -> str:
    return "".join(
        f"<tr><td style='padding:3px 14px 3px 0; white-space:nowrap; font-family:\"IBM Plex Mono\"; color:{theme.T['ink']}'>{k}</td>"
        f"<td style='padding:3px 0; color:{theme.T['ink2']}'>{v}</td></tr>" for k, v in entries)


class ShortcutsDialog(QDialog):
    def __init__(self, parent, view: str | None):
        super().__init__(parent)
        self.setWindowTitle("Shortcuts")
        self.resize(760, 560)
        root = QHBoxLayout(self)
        self.nav = QListWidget()
        self.nav.setFixedWidth(150)
        root.addWidget(self.nav)
        self.body = QLabel()
        self.body.setTextFormat(Qt.TextFormat.RichText)
        self.body.setWordWrap(True)
        self.body.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.body.setContentsMargins(14, 8, 14, 8)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        holder = QWidget()
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addWidget(self.body)
        hl.addStretch(1)
        scroll.setWidget(holder)
        root.addWidget(scroll, 1)
        self.order = ([view] if view in SECTIONS else []) + [v for v in ORDER if v != view]
        for v in self.order:
            title = SECTIONS[v][0] + ("  · this screen" if v == view else "")
            it = QListWidgetItem(title)
            it.setData(Qt.ItemDataRole.UserRole, v)
            self.nav.addItem(it)
        self.nav.currentItemChanged.connect(lambda it, _=None: it and self.show_section(it.data(Qt.ItemDataRole.UserRole)))
        self.nav.setCurrentRow(0)

    def show_section(self, v: str):
        title, purpose, entries = SECTIONS[v]
        html = f"<div style='font-size:17px; font-weight:600'>{title}</div>"
        if purpose:
            html += f"<div style='color:{theme.T['muted']}; margin:2px 0 10px'>{purpose}</div>"
        if entries:
            html += f"<table cellspacing=0>{_rows(entries)}</table>"
        html += (f"<div style='margin-top:18px; color:{theme.T['muted']}; font-size:11px; letter-spacing:.06em'>EVERYWHERE</div>"
                 f"<table cellspacing=0>{_rows(EVERYWHERE[2])}</table>")
        self.body.setText(html)
