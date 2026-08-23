import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rqbt.updater import (
    consume_update_result,
    is_newer,
    parse_release_tag,
    write_update_result,
)


class ReleaseIdentityTests(unittest.TestCase):
    def test_revision_increments_within_same_qbittorrent(self):
        self.assertTrue(is_newer("qbt-5.2.3-r2", "qbt-5.2.3-r1"))

    def test_new_qbittorrent_release_is_newer(self):
        self.assertTrue(is_newer("qbt-5.2.4-r1", "qbt-5.2.3-r9"))

    def test_same_release_is_not_newer(self):
        self.assertFalse(is_newer("qbt-5.2.3-r1", "qbt-5.2.3-r1"))

    def test_older_qbittorrent_release_is_not_newer(self):
        self.assertFalse(is_newer("qbt-5.2.2-r99", "qbt-5.2.3-r1"))

    def test_retired_release_family_cannot_supersede_qbt_family(self):
        self.assertFalse(is_newer("v99.0.0", "qbt-5.2.3-r1"))

    def test_parse_release_tag(self):
        self.assertEqual(parse_release_tag("qbt-5.2.3-r7"), (5, 2, 3, 7))


class UpdateResultTests(unittest.TestCase):
    def test_result_is_persisted_and_consumed_once(self):
        with tempfile.TemporaryDirectory() as td:
            result_file = Path(td) / "update-result.json"
            with patch("rqbt.updater.UPDATE_RESULT_FILE", result_file):
                write_update_result("success", "5.2.3-r3", "Installed.")
                data = consume_update_result()
                self.assertEqual(data["status"], "success")
                self.assertEqual(data["release_id"], "5.2.3-r3")
                self.assertFalse(result_file.exists())
                self.assertIsNone(consume_update_result())


if __name__ == "__main__":
    unittest.main()
