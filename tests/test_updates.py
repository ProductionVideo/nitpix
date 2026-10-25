"""Update check: version comparison and the GitHub response handling, without the network."""
import io
import json
import re
import unittest
import urllib.error
from pathlib import Path

from nitpix import __version__, updates


def fake(payload=None, error=None):
    def opener(req, timeout=None):
        if error:
            raise error
        return io.BytesIO(json.dumps(payload).encode())
    return opener


RELEASE = {"tag_name": "v9.0.0", "name": "nitpix 9.0.0", "html_url": "https://github.com/x/y/releases/tag/v9.0.0",
           "body": "Fixes things.", "assets": [{"name": "nitpix-9.0.0.dmg", "browser_download_url": "https://dl/nitpix-9.0.0.dmg"}]}


class Versions(unittest.TestCase):
    def test_ordering(self):
        self.assertTrue(updates.is_newer("0.5.2", "0.5.1"))
        self.assertTrue(updates.is_newer("v0.10.0", "0.9.9"))       # numeric, not string, comparison
        self.assertTrue(updates.is_newer("1.0", "0.9.9"))
        self.assertFalse(updates.is_newer("0.5.1", "0.5.1"))
        self.assertFalse(updates.is_newer("0.5.0", "0.5.1"))
        self.assertTrue(updates.is_newer("0.6.0", "0.6.0-beta.2"))  # the release beats its beta
        self.assertFalse(updates.is_newer("0.6.0-beta.1", "0.6.0"))

    def test_one_version_everywhere(self):
        root = Path(__file__).resolve().parent.parent
        py = re.search(r'^version = "([^"]+)"', (root / "pyproject.toml").read_text(), re.M).group(1)
        self.assertEqual(py, __version__, "pyproject.toml and nitpix/__init__.py disagree")


class Check(unittest.TestCase):
    def test_newer_release(self):
        r = updates.check("0.5.1", opener=fake(RELEASE))
        self.assertTrue(r["newer"])
        self.assertEqual((r["latest"], r["notes"]), ("9.0.0", "Fixes things."))
        self.assertTrue(r["url"].endswith("v9.0.0"))

    def test_up_to_date(self):
        r = updates.check("9.0.0", opener=fake(RELEASE))
        self.assertFalse(r["newer"])

    def test_offline_and_missing(self):
        self.assertIn("Could not reach", updates.check("0.5.1", opener=fake(error=urllib.error.URLError("offline")))["error"])
        e404 = urllib.error.HTTPError(updates.API, 404, "Not Found", {}, None)
        self.assertIn("404", updates.check("0.5.1", opener=fake(error=e404))["error"])
        self.assertIn("error", updates.check("0.5.1", opener=lambda req, timeout=None: io.BytesIO(b"not json")))


if __name__ == "__main__":
    unittest.main()
