"""Which nitpix this is: the release, or an experimental build used beside it.

An experimental build keeps its own preferences and recent-projects list and never checks for
or installs release updates, so a development build can be tried while the release is in use.
Decided once, when this module is first imported (before the recent list or preferences are
read), and in this order:

1. A BUILD_VARIANT file next to this module, which build/nitpix.spec writes into experimental
   bundles. It wins over the environment, so an experimental app opened from Finder is
   experimental, and nothing in the environment can make it use the release's stores.
2. NITPIX_VARIANT=exp in the environment: an experimental run from source.
3. Otherwise the release, with exactly the release's preferences, state folder and updates.

NITPIX_STATE_DIR still moves the state folder for either (the tests use it).
"""
from __future__ import annotations

import os
from pathlib import Path

RELEASE, EXPERIMENTAL = "release", "exp"
MARKER = Path(__file__).resolve().parent / "BUILD_VARIANT"


def detect(environ=os.environ, marker: Path = MARKER) -> str:
    try:
        if marker.read_text().strip() == EXPERIMENTAL:
            return EXPERIMENTAL
    except OSError:
        pass
    return EXPERIMENTAL if environ.get("NITPIX_VARIANT") == EXPERIMENTAL else RELEASE


VARIANT = detect()
IS_EXPERIMENTAL = VARIANT == EXPERIMENTAL
# QSettings(organisation, application). The release's pair must never change; the experimental
# one differs in the organisation too, so Qt's organisation-wide fallback cannot read the release's.
SETTINGS = ("nitpix-exp", "nitpix-exp") if IS_EXPERIMENTAL else ("nitpix", "nitpix")


def state_dir(environ=os.environ, variant: str | None = None) -> Path:
    """The app's own state folder (the recent-projects list)."""
    if environ.get("NITPIX_STATE_DIR"):
        return Path(environ["NITPIX_STATE_DIR"])
    return Path.home() / (".nitpix-exp" if (variant or VARIANT) == EXPERIMENTAL else ".nitpix")


def describe() -> dict:
    return {"variant": VARIANT, "settings": "/".join(SETTINGS), "state_dir": str(state_dir()),
            "updates": "off (experimental build)" if IS_EXPERIMENTAL else "release channel"}
