import unittest
from types import SimpleNamespace

from core.chat_context import (
    extract_quote_entries,
    merge_context,
    parse_onebot_history,
)


class ChatContextTests(unittest.TestCase):
    def test_extract_quote(self):
        components = [
            SimpleNamespace(
                type="reply",
                message_str="原消息内容",
                sender_nickname="群友A",
                time=0,
            )
        ]
        entries = extract_quote_entries(components)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["sender"], "群友A")
        self.assertEqual(entries[0]["text"], "原消息内容")

    def test_parse_onebot_history(self):
        payload = {
            "messages": [
                {
                    "sender": {"user_id": 1, "nickname": "A"},
                    "message": [{"type": "text", "data": {"text": "较早"}}],
                    "time": 100,
                },
                {
                    "sender": {"user_id": 2, "nickname": "B"},
                    "message": [{"type": "image", "data": {"file": "x"}}],
                    "time": 101,
                },
                {
                    "sender": {"user_id": 3, "nickname": "C"},
                    "message": [{"type": "text", "data": {"text": "较晚"}}],
                    "time": 200,
                },
                {
                    "sender": {"user_id": 999, "nickname": "bot"},
                    "message": [{"type": "text", "data": {"text": "自己"}}],
                    "time": 201,
                },
            ]
        }
        entries = parse_onebot_history(payload, self_id="999", max_messages=10)
        self.assertEqual([item["text"] for item in entries], ["较早", "较晚"])
        self.assertRegex(entries[0]["time"], r"^\d{2}:\d{2}$")
        self.assertEqual(entries[1]["sender"], "C")

    def test_merge_deduplicates(self):
        merged = merge_context(
            [{"sender": "A", "text": "同一句", "time": "10:00"}],
            [
                {"sender": "A", "text": "同一句", "time": "10:00"},
                {"sender": "B", "text": "另一句", "time": "10:01"},
            ],
            max_messages=10,
        )
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[-1]["sender"], "B")


if __name__ == "__main__":
    unittest.main()
