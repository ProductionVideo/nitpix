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
