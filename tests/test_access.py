import unittest

from core.access import access_decision, normalize_umo_list


class AccessTests(unittest.TestCase):
    def test_default_allow_all(self):
        self.assertEqual(access_decision("a:group:1", [], []), (True, "allowed"))

    def test_whitelist_non_empty(self):
        self.assertEqual(
            access_decision("a:group:1", ["a:group:1"], []),
            (True, "allowed"),
        )
        self.assertEqual(
            access_decision("a:group:2", ["a:group:1"], []),
            (False, "not_whitelisted"),
        )

    def test_blacklist_wins(self):
        self.assertEqual(
            access_decision(
                "a:group:1", ["a:group:1"], ["a:group:1"]
            ),
            (False, "blocked"),
        )

    def test_normalize(self):
        self.assertEqual(
            normalize_umo_list(" a:group:1, a:group:2\n, "),
            ["a:group:1", "a:group:2"],
        )


if __name__ == "__main__":
    unittest.main()
