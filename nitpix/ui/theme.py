"""Visual language shared with the web UI: the same tokens, fonts and chip colours."""
from __future__ import annotations

import os

from PySide6.QtGui import QColor, QFont, QFontDatabase

FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "fonts")

DARK = {
    "bg": "#0C0C0C", "panel": "#141414", "panel2": "#181818", "ink": "#F1F1EE", "ink2": "#CFCFCB", "muted": "#95958F",
    "faint": "#66665F", "line": "#262624", "line2": "#34342F", "hover": "#1C1C1B", "sel": "#232321",
    "pass": "#4CC38A", "pass_bg": "#12291D", "warn": "#E7A33E", "warn_bg": "#2C2210", "fail": "#F2645A", "fail_bg": "#2E1512",
    "info": "#A3A39E", "info_bg": "#1F1F1E", "accent": "#F1F1EE", "accent_ink": "#0E0E0E", "focus": "#2F6BFF", "viewer": "#000000",
}
T = DARK

RESULT_COLOURS = {"pass": ("pass", "pass_bg"), "warning": ("warn", "warn_bg"), "fail": ("fail", "fail_bg"),
                  "issue": ("fail", "fail_bg"), "info": ("info", "info_bg"), "na": ("info", "info_bg"),
                  "pending": ("muted", "panel2"), "unreviewed": ("muted", "panel2"), "in_review": ("warn", "warn_bg"),
                  "approved": ("pass", "pass_bg"), "approved_notes": ("pass", "pass_bg"), "rejected": ("fail", "fail_bg")}

RESULT_LABEL = {"pass": "Pass", "warning": "Warning", "fail": "Fail", "info": "Info", "issue": "Issue", "na": "N/A",
                "pending": "Pending"}
REVIEW_LABEL = {"unreviewed": "Unreviewed", "in_review": "In review", "approved": "Approved",
                "approved_notes": "Approved w/ notes", "rejected": "Rejected"}
REVIEW_ORDER = ["unreviewed", "in_review", "approved", "approved_notes", "rejected"]


def load_fonts():
    for f in ("Inter_400Regular", "Inter_500Medium", "Inter_600SemiBold", "Inter_700Bold",
              "IBMPlexMono_400Regular", "IBMPlexMono_500Medium"):
        p = os.path.join(FONT_DIR, f + ".ttf")
        if os.path.exists(p):
            QFontDatabase.addApplicationFont(p)


def sans(size=13, weight=QFont.Weight.Normal) -> QFont:
    f = QFont("Inter", size)
    f.setWeight(weight)
    return f


def tabular(f: QFont) -> QFont:
    """Same-width digits, so a running timecode does not shift sideways."""
    f.setFeature(QFont.Tag("tnum"), 1)
    return f


def mono(size=12) -> QFont:
    f = QFont("IBM Plex Mono", size)
    f.setStyleHint(QFont.StyleHint.Monospace)
    return f


def colour(key: str) -> QColor:
    return QColor(T[key])


def chip_style(result: str) -> str:
    fg, bg = RESULT_COLOURS.get(result, ("muted", "panel2"))
    return f"QLabel {{ color: {T[fg]}; background: {T[bg]}; border-radius: 4px; padding: 1px 7px; font-weight: 600; font-size: 11px; }}"


STYLESHEET = f"""
QMainWindow, QWidget {{ background: {T['bg']}; color: {T['ink']}; font-family: "Inter"; font-size: 13px; }}
QLabel {{ background: transparent; }}
QWidget#brand {{ background: transparent; }}
QFrame#panel, QWidget#panel {{ background: {T['panel']}; border: 1px solid {T['line']}; border-radius: 6px; }}
QFrame#rail {{ background: {T['panel']}; border-right: 1px solid {T['line']}; }}
QPushButton {{ background: {T['panel2']}; color: {T['ink']}; border: 1px solid {T['line2']}; border-radius: 6px; padding: 5px 10px; }}
QPushButton:hover {{ background: {T['hover']}; }}
QPushButton:pressed {{ background: {T['sel']}; }}
QPushButton:disabled {{ color: {T['faint']}; border-color: {T['line']}; }}
QPushButton[primary="true"] {{ background: {T['accent']}; color: {T['accent_ink']}; border-color: {T['accent']}; font-weight: 600; }}
QPushButton[ghost="true"] {{ background: transparent; border-color: transparent; }}
QPushButton[ghost="true"]:hover {{ background: {T['hover']}; }}
QPushButton[nav="true"] {{ background: transparent; border: 0; border-radius: 6px; text-align: left; padding: 6px 8px; color: {T['ink2']}; font-weight: 500; }}
QPushButton[nav="true"][iconOnly="true"] {{ padding: 7px 0; text-align: center; }}
QPushButton[nav="true"]:hover {{ background: {T['hover']}; }}
QPushButton[nav="true"]:checked {{ background: {T['accent']}; color: {T['accent_ink']}; }}
QPushButton[seg="true"] {{ border-radius: 0; padding: 4px 8px; }}
QPushButton[seg="true"]:checked {{ background: {T['accent']}; color: {T['accent_ink']}; border-color: {T['accent']}; }}
QPushButton[decide="pass"]:checked {{ background: {T['pass_bg']}; color: {T['pass']}; border-color: {T['pass']}; }}
QPushButton[decide="issue"]:checked, QPushButton[decide="warning"]:checked {{ background: {T['fail_bg']}; color: {T['fail']}; border-color: {T['fail']}; }}
QPushButton[decide="warning"]:checked {{ background: {T['warn_bg']}; color: {T['warn']}; border-color: {T['warn']}; }}
QPushButton[decide="na"]:checked {{ background: {T['info_bg']}; color: {T['info']}; border-color: {T['line2']}; }}
QPushButton[set="true"] {{ color: {T['focus']}; border-color: {T['focus']}; }}
QPushButton[pick="true"] {{ border-radius: 11px; padding: 2px 9px; font-size: 12px; color: {T['ink2']}; }}
QPushButton[pick="true"]:checked {{ background: {T['accent']}; color: {T['accent_ink']}; border-color: {T['accent']}; }}
QPushButton[pick="true"][custom="true"]:checked {{ background: {T['warn_bg']}; color: {T['warn']}; border-color: {T['warn']}; }}
QLineEdit[bad="true"] {{ border-color: {T['fail']}; }}
QLineEdit[pickadd="true"] {{ background: transparent; border: 1px dashed {T['line2']}; border-radius: 11px; padding: 1px 8px; font-size: 12px; }}
QLabel[err="true"] {{ color: {T['fail']}; font-size: 11px; }}
QLabel[banner="true"] {{ background: {T['warn_bg']}; color: {T['warn']}; padding: 6px 10px; border-radius: 6px; }}
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ background: {T['panel']}; border: 1px solid {T['line2']}; border-radius: 6px; padding: 4px 8px; selection-background-color: {T['focus']}; }}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{ border-color: {T['focus']}; }}
QComboBox::drop-down {{ border: 0; width: 18px; }}
QComboBox {{ padding-right: 22px; }}
QComboBox QAbstractItemView {{ background: {T['panel']}; border: 1px solid {T['line2']}; selection-background-color: {T['sel']}; outline: 0; padding: 4px; }}
QComboBox QAbstractItemView::item {{ padding: 5px 10px; min-height: 18px; }}
QComboBox QAbstractItemView::item:selected {{ background: {T['sel']}; color: {T['ink']}; }}
QTableView, QTreeView, QListView {{ background: {T['panel']}; alternate-background-color: {T['panel2']}; border: 1px solid {T['line']}; gridline-color: {T['line']}; selection-background-color: {T['sel']}; selection-color: {T['ink']}; }}
QHeaderView::section {{ background: {T['panel2']}; color: {T['muted']}; border: 0; border-bottom: 1px solid {T['line']}; border-right: 1px solid {T['line']}; padding: 4px 8px; font-weight: 500; font-size: 11px; }}
QTabBar::tab {{ background: transparent; color: {T['muted']}; padding: 8px 14px; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {T['ink']}; border-bottom: 2px solid {T['ink']}; }}
QTabWidget::pane {{ border: 0; border-top: 1px solid {T['line']}; }}
QScrollArea {{ border: 0; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }} QScrollBar::handle:vertical {{ background: {T['line2']}; border-radius: 5px; min-height: 24px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; }} QScrollBar::handle:horizontal {{ background: {T['line2']}; border-radius: 5px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QProgressBar {{ background: {T['panel2']}; border: 1px solid {T['line']}; border-radius: 4px; height: 8px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {T['focus']}; border-radius: 3px; }}
QToolTip {{ background: {T['panel2']}; color: {T['ink']}; border: 1px solid {T['line2']}; padding: 4px 6px; }}
QSplitter::handle {{ background: {T['line']}; }}
QLabel[muted="true"] {{ color: {T['muted']}; }}
QLabel[faint="true"] {{ color: {T['faint']}; }}
QLabel[mono="true"] {{ font-family: "IBM Plex Mono"; font-size: 12px; }}
QLabel[h1="true"] {{ font-size: 18px; font-weight: 600; }}
QLabel[h2="true"] {{ font-size: 15px; font-weight: 600; }}
QLabel[tc="true"] {{ font-family: "Inter"; font-size: 22px; font-weight: 500; }}
QLabel[tc="true"][pending="true"] {{ color: {T['muted']}; }}
QMenu {{ background: {T['panel']}; border: 1px solid {T['line2']}; }} QMenu::item:selected {{ background: {T['sel']}; }}
QCheckBox, QRadioButton {{ background: transparent; }}
QCheckBox::indicator {{ width: 14px; height: 14px; }}
"""
