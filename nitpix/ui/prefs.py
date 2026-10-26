"""The app's own preferences (window, rail, update choices). Every QSettings use goes through
prefs(), so an experimental build reads and writes its own store and never the release's
(see nitpix/variant.py). Project data lives in the project, not here."""
from __future__ import annotations

from PySide6.QtCore import QSettings

from .. import variant

EXPERIMENTAL_UPDATES = ("This is an experimental build of nitpix: it does not check for or install updates. "
                        "Use the released nitpix for updates.")


def prefs() -> QSettings:
    # QSettings(organisation, application) always uses the native store, whatever the default
    # format; passing the default explicitly is the same store in the app (native, user scope)
    # and lets tests that set an INI default and path keep the user's real preferences untouched.
    return QSettings(QSettings.defaultFormat(), QSettings.Scope.UserScope, *variant.SETTINGS)
