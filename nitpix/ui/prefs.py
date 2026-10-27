"""The app's own preferences (window, rail, update choices). Every QSettings use goes through
prefs(), so an experimental build reads and writes its own store and never the release's
(see nitpix/variant.py). Project data lives in the project, not here.

NITPIX_PREFS_DIR puts them in an INI file in that folder instead: for checking a packaged app
with throwaway preferences (macOS keeps the native store per user, whatever $HOME says)."""
from __future__ import annotations

import os

from PySide6.QtCore import QSettings

from .. import variant

EXPERIMENTAL_UPDATES = ("This is an experimental build of nitpix: it does not check for or install updates. "
                        "Use the released nitpix for updates.")


def prefs() -> QSettings:
    if os.environ.get("NITPIX_PREFS_DIR"):
        return QSettings(os.path.join(os.environ["NITPIX_PREFS_DIR"], f"{variant.SETTINGS[1]}.ini"), QSettings.Format.IniFormat)
    # QSettings(organisation, application) always uses the native store, whatever the default
    # format; passing the default explicitly is the same store in the app (native, user scope)
    # and lets tests that set an INI default and path keep the user's real preferences untouched.
    return QSettings(QSettings.defaultFormat(), QSettings.Scope.UserScope, *variant.SETTINGS)
