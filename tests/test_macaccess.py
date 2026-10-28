"""The macOS accessibility guard (nitpix/ui/macaccess.py): with a table or tree row selected, an
accessibility client reading the window crashes plain Qt 6.12, and not with the guard. Uses
tests/axclient.py, a client that reads one process's accessibility tree (AXUIElementCopyHierarchy
with the selection attributes, as assistive tools do); it sends no input. Needs a display and
Accessibility permission for the terminal running it:
    NITPIX_LIVE_PLAYER=1 .venv/bin/python -m unittest tests.test_macaccess -v
If plain Qt no longer crashes, the bundled Qt has the upstream fix (QTBUG-149612) and the guard can go."""
import ctypes
import os
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TRUSTED = False
if sys.platform == "darwin" and os.environ.get("NITPIX_LIVE_PLAYER") == "1":
    ax = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
    ax.AXIsProcessTrusted.restype = ctypes.c_bool
    TRUSTED = ax.AXIsProcessTrusted()                       # never asks: only reports

APP = r'''
import os, sys
sys.path.insert(0, %r)
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem
app = QApplication([])
if sys.argv[2] == "guarded":
    from nitpix.ui.macaccess import guard
    assert guard()
if sys.argv[1] == "tree":
    w = QTreeWidget(); w.setHeaderLabels(["Object", "Media"]); top = QTreeWidgetItem(["stage.obj", ""])
    w.addTopLevelItem(top); kids = [QTreeWidgetItem([n, "no UVs"]) for n in ("Screen", "Floor")]; top.addChildren(kids)
    w.expandAll(); QTimer.singleShot(300, lambda: kids[0].setSelected(True))
else:
    w = QTableWidget(3, 2)
    [w.setItem(r, c, QTableWidgetItem(f"{r}{c}")) for r in range(3) for c in range(2)]
    QTimer.singleShot(300, lambda: w.selectRow(0))
w.resize(420, 300); w.show(); QTimer.singleShot(5000, app.quit); app.exec()
''' % os.path.dirname(HERE)


@unittest.skipUnless(TRUSTED, "set NITPIX_LIVE_PLAYER=1 on a Mac display, with Accessibility permission for this terminal")
class SelectedRowsAndAssistiveTools(unittest.TestCase):
    def read_while_open(self, kind, mode):
        with tempfile.TemporaryDirectory(prefix="nitpix ax ") as tmp:
            script = os.path.join(tmp, "app.py")
            with open(script, "w") as fh:
                fh.write(APP)
            p = subprocess.Popen([sys.executable, script, kind, mode], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(1.0)                                  # the window up and its row selected
            client = subprocess.run([sys.executable, os.path.join(HERE, "axclient.py"), str(p.pid), "20", "5"],
                                    capture_output=True, text=True, timeout=60)
            return p.wait(timeout=30), client.stdout

    def test_the_guard_keeps_the_app_alive(self):
        for kind in ("tree", "table"):
            with self.subTest(kind):
                code, said = self.read_while_open(kind, "guarded")
                self.assertEqual(code, 0, said)
                self.assertIn("hierarchy copies", said)

    def test_plain_qt_still_needs_it(self):
        code, _said = self.read_while_open("table", "plain")
        if code == 0:
            self.skipTest("plain Qt survived: the bundled Qt may have the upstream fix; nitpix/ui/macaccess.py can go")
        self.assertEqual(code, -11)                          # SIGSEGV in libqcocoa


if __name__ == "__main__":
    unittest.main()
