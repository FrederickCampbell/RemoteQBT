"""Regression tests for qBittorrent 2.15.x and 2.16.x Web API requests."""
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

API_PATH = Path(__file__).resolve().parents[1] / "rqbt" / "api.py"
common = types.ModuleType("rqbt.common")
common.RELEASE_TAG = "qbt-test"
common.to_bool_text = lambda value: "true" if value else "false"
with patch.dict(sys.modules, {"rqbt.common": common}):
    spec = importlib.util.spec_from_file_location("rqbt.api_contract_tests", API_PATH)
    assert spec is not None and spec.loader is not None
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)


class StubClient(api.QbtClient):
    def __init__(self, version="2.16.2"):
        super().__init__({"server": "http://127.0.0.1:8080", "api_key": "testing"})
        self.version_string = version
        self.calls = []

    def webapi_version(self):
        return self.version_string

    def _get_json(self, endpoint):
        self.calls.append(("GET", endpoint, None))
        return {"up_limit": 100, "dl_limit": 200, "alt_up_limit": 30, "alt_dl_limit": 40}

    def _get_text(self, endpoint):
        self.calls.append(("GET-TEXT", endpoint, None))
        return "100" if "upload" in endpoint else "200"

    def preferences(self):
        self.calls.append(("PREFS", None, None))
        return {"alt_up_limit": 30, "alt_dl_limit": 40}

    def _post_form(self, endpoint, params=None):
        self.calls.append(("POST", endpoint, params or {}))
        return "ok"

    def _request(self, method, endpoint, *, data=None, headers=None, timeout=15):
        self.calls.append((method, endpoint, data))
        return b"{}"


class ApiContractTests(unittest.TestCase):
    def test_modern_speed_limits_use_combined_endpoints(self):
        client = StubClient()
        self.assertEqual(client.speed_limits(), {
            "up_limit": 100, "dl_limit": 200, "alt_up_limit": 30, "alt_dl_limit": 40,
        })
        self.assertEqual(client.calls, [("GET", "/api/v2/transfer/getSpeedLimits", None)])
        client.calls.clear()
        client.set_speed_limits(1, 2, 3, 4)
        self.assertEqual(client.calls, [("POST", "/api/v2/transfer/setSpeedLimits", {
            "up_limit": 1, "dl_limit": 2, "alt_up_limit": 3, "alt_dl_limit": 4,
        })])

    def test_legacy_speed_limits_remain_supported(self):
        client = StubClient("2.15.1")
        self.assertEqual(client.speed_limits()["alt_up_limit"], 30)
        self.assertNotIn("/api/v2/transfer/getSpeedLimits", [x[1] for x in client.calls])
        client.calls.clear()
        client.set_speed_limits(1, 2, 3, 4)
        self.assertEqual([c[1] for c in client.calls], [
            "/api/v2/transfer/setUploadLimit",
            "/api/v2/transfer/setDownloadLimit",
            "/api/v2/app/setPreferences",
        ])

    def test_share_limits_mode_required_since_2153(self):
        client = StubClient()
        client.set_share_limits(["abc"], 2.0, -2, -2, "Stop", "MatchAll")
        self.assertEqual(client.calls[-1][2]["shareLimitsMode"], "MatchAll")
        old = StubClient("2.15.1")
        old.set_share_limits(["abc"], 2.0, -2, -2, "Stop", "MatchAny")
        self.assertNotIn("shareLimitsMode", old.calls[-1][2])
        with self.assertRaises(ValueError):
            client.set_share_limits(["abc"], 2.0, -2, -2, "Stop", "invalid")

    def test_session_actions_are_version_aware(self):
        client = StubClient()
        client.pause_session()
        client.resume_session()
        self.assertEqual([call[1] for call in client.calls], [
            "/api/v2/transfer/pauseSession",
            "/api/v2/transfer/resumeSession",
        ])
        old = StubClient("2.15.1")
        old.pause_session()
        old.resume_session()
        self.assertEqual([call[1] for call in old.calls], [
            "/api/v2/torrents/stop",
            "/api/v2/torrents/start",
        ])
        self.assertEqual(old.calls[0][2]["hashes"], "all")

    def test_add_torrent_uses_only_supported_seed_mode_field(self):
        source = "magnet:?xt=urn:btih:0123456789abcdef"
        options = {"seedMode": True, "shareLimitsMode": "MatchAll"}
        modern = StubClient()
        self.assertTrue(modern.add_sources([source], options)[0]["ok"])
        payload = modern.calls[-1][2]
        self.assertIn(b'name="seedMode"', payload)
        self.assertIn(b'name="shareLimitsMode"', payload)
        self.assertNotIn(b'name="skip_checking"', payload)
        old = StubClient("2.15.1")
        self.assertTrue(old.add_sources([source], options)[0]["ok"])
        old_payload = old.calls[-1][2]
        self.assertIn(b'name="skip_checking"', old_payload)
        self.assertNotIn(b'name="seedMode"', old_payload)
        self.assertNotIn(b'name="shareLimitsMode"', old_payload)

    def test_unrecognized_version_fails_closed(self):
        client = StubClient("not-a-version")
        with self.assertRaises(api.QbtApiError):
            client.pause_session()


if __name__ == "__main__":
    unittest.main()
