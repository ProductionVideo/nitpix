"""Release and experimental builds side by side (nitpix/variant.py): an experimental build keeps
its own preferences and recent list, never migrates or touches the release's, and never checks
for or installs updates; the release keeps exactly its own stores and update behaviour.
Store tests run in child processes with a throwaway HOME and settings folder holding sentinel
values, so the user's real preferences and recent list are never read or written.
.venv/bin/python -m unittest tests.test_variant"""
import os as _os, tempfile as _tempfile; _os.environ.setdefault("NITPIX_STATE_DIR", _tempfile.mkdtemp(prefix="nitpix state "))  # keep the real recent list untouched
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["NITPIX_NO_MPV"] = "1"
ROOT = Path(__file__).resolve().parent.parent
HAS_QT = importlib.util.find_spec("PySide6") is not None

from nitpix import variant  # noqa: E402

# What one launch of the app does to its own stores: read a preference, write it, remember a project.
CHILD = r"""
import json, os, sys
from pathlib import Path
from PySide6.QtCore import QCoreApplication, QSettings
QSettings.setDefaultFormat(QSettings.Format.IniFormat)
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, os.environ["T_SETTINGS"])
app = QCoreApplication([])
from nitpix import store, variant
from nitpix.ui.prefs import prefs
seen = prefs().value("probe")
prefs().setValue("probe", "written by " + variant.VARIANT)
prefs().sync()
store.remember_project(Path(os.environ["T_PROJECT"]), "opened by " + variant.VARIANT)
native = QSettings(QSettings.Format.NativeFormat, QSettings.Scope.UserScope, *variant.SETTINGS).fileName()   # a name only: nothing is written
print(json.dumps({"variant": variant.VARIANT, "seen": seen, "recent_file": str(store._recent_file()),
                  "recent": [i["name"] for i in json.loads(store._recent_file().read_text())],
                  "settings_file": prefs().fileName(), "native": native}))
"""


class Detection(unittest.TestCase):
    def test_the_bundle_marker_decides_and_the_environment_cannot_undo_it(self):
        with tempfile.TemporaryDirectory() as d:
            marker = Path(d, "BUILD_VARIANT")
            marker.write_text("exp\n")
            self.assertEqual(variant.detect({}, marker), "exp")                           # opened from Finder
            self.assertEqual(variant.detect({"NITPIX_VARIANT": "release"}, marker), "exp")
            marker.write_text("something else")
            self.assertEqual(variant.detect({}, marker), "release")
            missing = Path(d, "none")
            self.assertEqual(variant.detect({}, missing), "release")
            self.assertEqual(variant.detect({"NITPIX_VARIANT": "exp"}, missing), "exp")      # an intentional source run
            self.assertEqual(variant.detect({"NITPIX_VARIANT": "Exp"}, missing), "release")

    def test_source_runs_are_the_release_unless_asked(self):
        self.assertFalse(variant.MARKER.exists())          # never written into the source tree

    def test_state_folders(self):
        home = Path.home()
        self.assertEqual(variant.state_dir({}, "release"), home / ".nitpix")
        self.assertEqual(variant.state_dir({}, "exp"), home / ".nitpix-exp")
        for v in ("release", "exp"):
            self.assertEqual(variant.state_dir({"NITPIX_STATE_DIR": "/tmp/x"}, v), Path("/tmp/x"))


@unittest.skipUnless(HAS_QT, "PySide6 not installed in this interpreter")
class SeparateStores(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nitpix variant "))
        self.home = self.tmp / "home"
        self.settings = self.tmp / "settings"
        (self.home / ".nitpix").mkdir(parents=True)
        (self.home / ".lhs-qc-review").mkdir()
        self.release_recent = self.home / ".nitpix" / "recent.json"
        self.release_recent.write_text(json.dumps([{"path": "/r/release.nitpix", "name": "release sentinel", "opened": 1}]))
        self.legacy = self.home / ".lhs-qc-review" / "recent.json"
        self.legacy.write_text(json.dumps([{"path": "/r/legacy.lhsqc", "name": "legacy sentinel", "opened": 0}]))
        self.release_ini = self.settings / "nitpix" / "nitpix.ini"
        self.release_ini.parent.mkdir(parents=True)
        self.release_ini.write_text("[General]\nprobe=release sentinel\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, True)

    def launch(self, variant_env=None, state_dir=None):
        env = {k: v for k, v in os.environ.items() if k not in ("NITPIX_VARIANT", "NITPIX_STATE_DIR")}
        env.update(HOME=str(self.home), T_SETTINGS=str(self.settings), T_PROJECT=str(self.tmp / "p.nitpix"), PYTHONPATH=str(ROOT),
                   PYTHONDONTWRITEBYTECODE="1")
        if variant_env:
            env["NITPIX_VARIANT"] = variant_env
        if state_dir:
            env["NITPIX_STATE_DIR"] = str(state_dir)
        r = subprocess.run([sys.executable, "-c", CHILD], env=env, cwd=str(self.tmp), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout.strip().splitlines()[-1])

    def snapshot(self):
        return {str(p.relative_to(self.tmp)): p.read_bytes() for p in self.tmp.rglob("*") if p.is_file()}

    def test_experimental_and_release_never_cross_write(self):
        release_files = {k: v for k, v in self.snapshot().items()}
        exp = self.launch("exp")
        self.assertEqual(exp["variant"], "exp")
        self.assertIsNone(exp["seen"])                                         # the release's preference is not visible
        self.assertEqual(exp["recent"], ["opened by exp"])                     # nor its recent list
        self.assertEqual(Path(exp["recent_file"]), self.home / ".nitpix-exp" / "recent.json")
        self.assertEqual(Path(exp["settings_file"]).parent.name, "nitpix-exp")
        self.assertTrue(exp["native"].endswith("com.nitpix-exp.nitpix-exp.plist"), exp["native"])
        after = self.snapshot()
        for name, data in release_files.items():                               # every release file byte for byte
            self.assertEqual(after[name], data, name)
        exp_files = {k: v for k, v in after.items() if k not in release_files}
        self.assertEqual(sorted(exp_files), ["home/.nitpix-exp/recent.json", "settings/nitpix-exp/nitpix-exp.ini"])

        rel = self.launch()                                                    # then the release, as it always was
        self.assertEqual(rel["variant"], "release")
        self.assertEqual(rel["seen"], "release sentinel")
        self.assertEqual(rel["recent"], ["opened by release", "release sentinel"])
        self.assertEqual(Path(rel["recent_file"]), self.release_recent)
        self.assertTrue(rel["native"].endswith("com.nitpix.nitpix.plist"), rel["native"])
        final = self.snapshot()
        for name, data in exp_files.items():                                   # and it left the experimental stores alone
            self.assertEqual(final[name], data, name)

        again = self.launch("exp")                                             # the experimental build remembers its own
        self.assertEqual((again["seen"], again["recent"]), ("written by exp", ["opened by exp"]))

    def test_only_the_release_takes_over_the_pre_rename_list(self):
        shutil.rmtree(self.home / ".nitpix")
        legacy = self.legacy.read_bytes()
        exp = self.launch("exp")
        self.assertEqual(exp["recent"], ["opened by exp"])
        self.assertFalse((self.home / ".nitpix").exists())                     # nothing created for the release
        rel = self.launch()
        self.assertEqual(rel["recent"], ["opened by release", "legacy sentinel"])   # the release migrates as before
        self.assertEqual(self.legacy.read_bytes(), legacy)

    def test_an_explicit_state_folder_still_wins(self):
        state = self.tmp / "state"
        for v in ("exp", None):
            r = self.launch(v, state_dir=state)
            self.assertEqual(Path(r["recent_file"]), state / "recent.json")
        self.assertFalse((self.home / ".nitpix-exp").exists())
        self.assertEqual(json.loads(self.release_recent.read_text())[0]["name"], "release sentinel")


@unittest.skipUnless(HAS_QT, "PySide6 not installed in this interpreter")
class Updates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication
        from nitpix.ui import theme
        cls.app = QApplication.instance() or QApplication([])
        cls.settings_dir = tempfile.mkdtemp(prefix="nitpix settings ")
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)  # keep tests out of the real preferences
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, cls.settings_dir)
        cls.app.setStyleSheet(theme.STYLESHEET)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.settings_dir, True)

    def window(self, experimental: bool):
        from nitpix import service
        from nitpix.ui.app import MainWindow
        if experimental:
            for name, value in (("IS_EXPERIMENTAL", True), ("VARIANT", "exp"), ("SETTINGS", ("nitpix-exp", "nitpix-exp"))):
                p = mock.patch.object(variant, name, value)
                p.start()
                self.addCleanup(p.stop)
        w = MainWindow(service.Session())
        self.addCleanup(lambda: (w.close(), w.deleteLater()))
        w.open_project(tempfile.mkdtemp(prefix="nitpix variant ") + "/v.nitpix")   # a throwaway project
        return w

    def pump(self, until, timeout=3.0):
        end = time.monotonic() + timeout
        while not until() and time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.01)

    def test_an_experimental_build_never_checks_or_installs(self):
        from nitpix import updates
        from nitpix.ui import app as appmod
        from nitpix.ui.prefs import EXPERIMENTAL_UPDATES, prefs
        w = self.window(experimental=True)
        asked = []
        with mock.patch.object(updates, "check", lambda *a, **k: asked.append(1) or {"newer": True}), \
                mock.patch.object(updates, "install", side_effect=AssertionError("must not install")), \
                mock.patch.object(appmod, "QMessageBox") as box:
            w.check_updates()                                                  # the start-up check
            self.assertEqual(w.toast_lab.text(), "")
            w.check_updates(manual=True)                                       # Help ▸ Check for Updates…
            self.assertEqual(w.toast_lab.text(), EXPERIMENTAL_UPDATES)
            self.pump(lambda: asked, 0.3)
            self.assertEqual(asked, [])
            r = {"current": "0.5.6", "latest": "9.9.9", "newer": True, "url": "u", "download": "https://x/nitpix.dmg", "notes": ""}
            w._update_ready(r, True)                                           # even a stray result offers nothing
            box.assert_not_called()
            w._install_update(r)
        self.assertFalse(updates.can_install())
        with self.assertRaises(RuntimeError):
            updates.install("https://x/nitpix.dmg", app_path="/nonexistent/nitpix.app")
        st = w.settings_screen
        st.refresh()
        self.assertFalse(st.update_btn.isEnabled() or st.auto_updates.isEnabled() or st.auto_updates.isChecked())
        self.assertIn("experimental", st.version_lab.text())
        self.assertIn("experimental", w.windowTitle())
        self.assertEqual(Path(prefs().fileName()), Path(self.settings_dir, "nitpix-exp", "nitpix-exp.ini"))

    def test_the_release_still_checks(self):
        from nitpix import updates
        w = self.window(experimental=False)
        asked = []
        with mock.patch.object(updates, "check", lambda *a, **k: asked.append(1) or {"current": "0.5.6", "latest": "0.5.6", "newer": False}):
            w.check_updates(manual=True)
            self.pump(lambda: "up to date" in w.toast_lab.text())
        self.assertEqual(asked, [1])
        self.assertEqual(w.toast_lab.text(), "nitpix 0.5.6 is up to date")
        st = w.settings_screen
        st.refresh()
        self.assertTrue(st.update_btn.isEnabled() and st.auto_updates.isEnabled())
        self.assertNotIn("experimental", w.windowTitle())
        from nitpix.ui.prefs import prefs
        self.assertEqual(Path(prefs().fileName()), Path(self.settings_dir, "nitpix", "nitpix.ini"))   # the test's INI, not the user's

    def test_every_preference_goes_through_the_variant(self):
        """No QSettings outside nitpix/ui/prefs.py, so nothing can reach the release's store from
        an experimental build by naming it directly."""
        hits = [f"{p.relative_to(ROOT)}:{i}" for p in (ROOT / "nitpix").rglob("*.py") if p.name != "prefs.py"
                for i, line in enumerate(p.read_text().splitlines(), 1) if "QSettings(" in line and not line.lstrip().startswith("#")]
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
