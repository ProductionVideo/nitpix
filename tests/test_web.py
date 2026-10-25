"""Contract between the web UI and the server vocabulary."""
import os
import re
import shutil
import subprocess
import unittest

from nitpix import vocab

APP_JS = os.path.join(os.path.dirname(__file__), "..", "nitpix", "web", "app.js")


class WebUI(unittest.TestCase):
    def test_preset_pickers_use_known_vocabulary(self):
        src = open(APP_JS).read()
        fields = re.findall(r'\["\w+", "[^"]+", "(?:pick|choice)", "(\w+)"\]', src)
        self.assertGreater(len(fields), 10)
        for f in fields:
            with self.subTest(field=f):
                self.assertIn(f, vocab.OPTIONS)

    def test_preset_sections_match_server_validation(self):
        """Every list field the editor offers is one the server knows how to normalise."""
        src = open(APP_JS).read()
        for sec in ("video", "audio", "sequence"):
            block = re.search(sec + r': \[(.*?)\]\],?\n', src, re.S).group(1)
            for key, vf in re.findall(r'\["(\w+)", "[^"]+", "pick", "(\w+)"\]', block):
                with self.subTest(section=sec, key=key):
                    self.assertEqual(vocab.PRESET_FIELDS[sec].get(key), vf)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_app_js_parses(self):
        r = subprocess.run(["node", "--check", APP_JS], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
