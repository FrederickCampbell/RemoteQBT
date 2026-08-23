import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "upstream_watch.py"
spec = importlib.util.spec_from_file_location("upstream_watch", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


class UpstreamParserTests(unittest.TestCase):
    def test_parse_actions(self):
        source = "void infoAction();\n void setForceStartAction() ;\nvoid helper();"
        self.assertEqual(mod.parse_actions(source), ["info", "setForceStart"])

    def test_parse_api_version(self):
        source = "inline const Utils::Version<3, 2> API_VERSION {2, 15, 1};"
        self.assertEqual(mod.parse_api_version(source), "2.15.1")


if __name__ == "__main__":
    unittest.main()
