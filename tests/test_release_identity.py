import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "release_identity.py"
spec = importlib.util.spec_from_file_location("release_identity", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


class ReleaseIdentityScriptTests(unittest.TestCase):
    def test_parse_upstream_tag(self):
        self.assertEqual(mod.parse_upstream_tag("release-5.2.3"), "5.2.3")

    def test_release_id(self):
        self.assertEqual(mod.make_release_id("5.2.3", 4), "5.2.3-r4")

    def test_release_tag(self):
        self.assertEqual(mod.make_release_tag("5.2.3", 4), "qbt-5.2.3-r4")


if __name__ == "__main__":
    unittest.main()
