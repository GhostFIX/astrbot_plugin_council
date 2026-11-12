import unittest

from core.command_text import extract_topic, is_council_command, is_stop_command


class CommandTextTests(unittest.TestCase):
    def test_extract_topic(self):
        self.assertEqual(extract_topic("/council 是否上线"), "是否上线")
        self.assertEqual(extract_topic("council 是否上线"), "是否上线")
        self.assertEqual(extract_topic("council\n是否上线"), "是否上线")
        self.assertEqual(extract_topic("council"), "")
        self.assertEqual(extract_topic("", "fallback"), "fallback")

    def test_stop(self):
        self.assertTrue(is_stop_command("council stop"))
        self.assertTrue(is_stop_command("/council STOP"))
        self.assertTrue(is_stop_command("stop"))
        self.assertFalse(is_stop_command("council stop extra"))
        self.assertFalse(is_stop_command("council 议题"))
        self.assertFalse(is_stop_command("council"))

    def test_is_council(self):
        self.assertTrue(is_council_command("council topic"))
        self.assertTrue(is_council_command("/council"))
        self.assertFalse(is_council_command("hello council"))


if __name__ == "__main__":
    unittest.main()
