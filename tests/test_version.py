import unittest

from core.update_check import UpdateChecker
from core.version import display_version, is_newer, normalize_version


class VersionTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_version("v1.2.3"), (1, 2, 3))
        self.assertEqual(normalize_version("1.2"), (1, 2, 0))
        self.assertIsNone(normalize_version("not-a-version"))

    def test_newer(self):
        self.assertTrue(is_newer("1.2.0", "1.1.9"))
        self.assertTrue(is_newer("v2.0.0", "1.99.99"))
        self.assertFalse(is_newer("1.1.0", "1.1.0"))
        self.assertFalse(is_newer("1.0.9", "1.1.0"))
        self.assertFalse(is_newer("bad", "1.0.0"))

    def test_display(self):
        self.assertEqual(display_version("1.1.0"), "v1.1.0")
        self.assertEqual(display_version("v1.1.0"), "v1.1.0")

    def test_apply_status(self):
        checker = UpdateChecker(current_version="1.0.0")
        result = checker._base_result()
        checker._apply(
            result, "v1.1.0", "https://github.com/x/y", "release"
        )
        self.assertEqual(result["status"], "update_available")
        self.assertEqual(result["latest_display"], "v1.1.0")

        same = checker._base_result()
        checker._apply(same, "v1.0.0", "https://github.com/x/y", "tag")
        self.assertEqual(same["status"], "up_to_date")


if __name__ == "__main__":
    unittest.main()
