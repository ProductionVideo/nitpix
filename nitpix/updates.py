"""Check GitHub for a newer release. Read-only: it never downloads or installs anything; the
app shows what is new and opens the release page.

    check() -> {"current", "latest", "newer", "url", "download", "notes"}  or  {"error": ...}
"""
from __future__ import annotations

import json
import platform
import re
import urllib.error
import urllib.request

from . import __version__

REPO = "ProductionVideo/nitpix"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES = f"https://github.com/{REPO}/releases"


def parse(v: str) -> tuple:
    """'v0.5.1' -> (0, 5, 1); pre-release tags ('0.6.0-beta.2') sort before the release."""
    m = re.match(r"^v?(\d+(?:\.\d+)*)(?:[-.]?(.*))?$", (v or "").strip())
    if not m:
        return (0,)
    nums = tuple(int(x) for x in m.group(1).split("."))
    nums += (0,) * (3 - len(nums))
    return nums + ((0, m.group(2)) if m.group(2) else (1, ""))


def is_newer(latest: str, current: str = __version__) -> bool:
    return parse(latest) > parse(current)


def _asset_for_platform(assets: list) -> str | None:
    want = {"Darwin": (".dmg",), "Windows": (".exe", ".msi", ".zip"), "Linux": (".AppImage", ".tar.gz")}.get(platform.system(), ())
    for a in assets:
        if a.get("name", "").endswith(want):
            return a.get("browser_download_url")
    return None


def check(current: str = __version__, timeout: float = 6.0, opener=urllib.request.urlopen) -> dict:
    req = urllib.request.Request(API, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": f"nitpix/{current}"})
    try:
        with opener(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"current": current, "error": f"GitHub answered {e.code}" + (" (no releases yet?)" if e.code == 404 else "")}
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {"current": current, "error": f"Could not reach GitHub: {getattr(e, 'reason', e)}"}
    latest = (d.get("tag_name") or "").lstrip("v")
    return {"current": current, "latest": latest, "newer": bool(latest) and is_newer(latest, current),
            "url": d.get("html_url") or RELEASES, "download": _asset_for_platform(d.get("assets") or []),
            "notes": (d.get("body") or "").strip(), "name": d.get("name") or f"nitpix {latest}"}


def bundle_path() -> str | None:
    """The running nitpix.app, when this is the packaged macOS app; None when run from source."""
    import os
    import sys
    if platform.system() != "Darwin" or not getattr(sys, "frozen", False):
        return None
    p = os.path.abspath(sys.executable)
    while p != "/" and not p.endswith(".app"):
        p = os.path.dirname(p)
    return p if p.endswith(".app") else None


def can_install() -> bool:
    import os
    b = bundle_path()
    return bool(b) and os.access(os.path.dirname(b), os.W_OK)


def install(dmg_url: str, app_path: str | None = None, progress=None, opener=urllib.request.urlopen) -> str:
    """Download the release DMG and replace `app_path` (default: this app) with the nitpix.app in it.
    Downloaded by Python, so the file has no quarantine flag and macOS opens the new version without
    the unidentified-developer prompt. Returns the installed path; raises on failure, leaving the
    current app untouched until the new copy is complete."""
    import os
    import shutil
    import subprocess
    import tempfile
    target = app_path or bundle_path()
    if not target:
        raise RuntimeError("In-app install works in the packaged macOS app only.")
    tmp = tempfile.mkdtemp(prefix="nitpix-update-")
    dmg, mnt = os.path.join(tmp, "nitpix.dmg"), os.path.join(tmp, "mnt")
    try:
        req = urllib.request.Request(dmg_url, headers={"User-Agent": f"nitpix/{__version__}"})
        with opener(req, timeout=60) as r, open(dmg, "wb") as f:
            total, done = int(r.headers.get("Content-Length") or 0), 0
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
        os.mkdir(mnt)
        subprocess.run(["hdiutil", "attach", "-nobrowse", "-readonly", "-quiet", "-mountpoint", mnt, dmg], check=True)
        try:
            src = os.path.join(mnt, "nitpix.app")
            if not os.path.isdir(src):
                raise RuntimeError("The downloaded disk image has no nitpix.app.")
            staged = target + ".new"
            shutil.rmtree(staged, ignore_errors=True)
            subprocess.run(["ditto", src, staged], check=True)
        finally:
            subprocess.run(["hdiutil", "detach", "-quiet", mnt], check=False)
        subprocess.run(["xattr", "-dr", "com.apple.quarantine", staged], check=False)
        old = target + ".old"
        shutil.rmtree(old, ignore_errors=True)
        if os.path.exists(target):
            os.rename(target, old)  # the running copy keeps working from its open files
        os.rename(staged, target)
        shutil.rmtree(old, ignore_errors=True)
        return target
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def relaunch(app_path: str):
    """Open `app_path` again once this process has gone."""
    import os
    import subprocess
    subprocess.Popen(["/bin/sh", "-c", f'while kill -0 {os.getpid()} 2>/dev/null; do sleep 0.3; done; open "$0"', app_path],
                     start_new_session=True)
