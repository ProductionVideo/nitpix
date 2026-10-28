"""A guard against a Qt crash on macOS, until a fixed Qt is available.

Qt 6.12's macOS accessibility bridge (libqcocoa, -[QMacAccessibilityElement
accessibilitySelectedChildren]) crashes the app (EXC_BAD_ACCESS) when an accessibility client
asks a table or tree view with a selected row for its selected children. Clients do this
routinely: VoiceOver, automation and screen-reading tools, window utilities. Reproduced with Qt
alone (a QTableWidget, QTreeView or QTreeWidget with one row selected, plus a client reading the
window's hierarchy); a QListWidget is not affected. Reported upstream as QTBUG-149612 (a
regression; fix under review). Every view of that kind in nitpix is exposed: the Mapping stage
list, the media list, the QC tables.

guard() replaces that one method, for table-like elements only (roles AXTable, AXOutline, AXRow,
AXColumn, AXCell), with one that answers "no selected children". Every other element keeps Qt's
own answer, and all other accessibility (roles, names, values, children, rows, focus, actions)
is unchanged, so assistive tools still work; they just cannot ask a table for its selection this
way. Done once, after the QApplication exists, macOS only. Remove it when the bundled Qt has the
upstream fix: tests/test_macaccess.py says whether plain Qt still crashes.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import sys

TABLE_ROLES = {b"AXTable", b"AXOutline", b"AXRow", b"AXColumn", b"AXCell"}
_keep = {}                                    # the replacement and the original, alive for the process


def guard() -> bool:
    """Install the replacement. True if installed (or already); False where it does not apply."""
    if sys.platform != "darwin":
        return False
    if _keep:
        return True
    try:
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
    except OSError:
        return False
    V = ctypes.c_void_p
    objc.objc_getClass.restype = V
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = V
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    objc.class_getInstanceMethod.restype = V
    objc.class_getInstanceMethod.argtypes = [V, V]
    objc.method_setImplementation.restype = V
    objc.method_setImplementation.argtypes = [V, V]
    cls = objc.objc_getClass(b"QMacAccessibilityElement")       # loaded with the cocoa platform plugin
    method = objc.class_getInstanceMethod(cls, objc.sel_registerName(b"accessibilitySelectedChildren")) if cls else None
    if not method:
        return False
    IMP = ctypes.CFUNCTYPE(V, V, V)
    send = ctypes.cast(objc.objc_msgSend, ctypes.CFUNCTYPE(V, V, V))
    utf8 = ctypes.cast(objc.objc_msgSend, ctypes.CFUNCTYPE(ctypes.c_char_p, V, V))
    sel_role, sel_utf8 = objc.sel_registerName(b"accessibilityRole"), objc.sel_registerName(b"UTF8String")

    def selected_children(this, cmd):
        role = send(this, sel_role)
        if role and utf8(role, sel_utf8) in TABLE_ROLES:
            return None                       # nil: no selected children (the crashing path is skipped)
        return original(this, cmd)

    replacement = IMP(selected_children)
    original = IMP(objc.method_setImplementation(method, ctypes.cast(replacement, V).value))
    _keep.update(replacement=replacement, original=original)
    return True
