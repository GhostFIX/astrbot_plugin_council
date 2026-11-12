import unittest

from core.render import (
    build_html_fragments,
    format_duration,
    format_summary_text,
    h,
    split_text,
)


class RenderTests(unittest.TestCase):
    def test_escape(self):
        self.assertEqual(h('<script>"x"</script>'), "&lt;script&gt;&quot;x&quot;&lt;/script&gt;")

    def test_fragments_escape_model_output(self):
        result = {
            "topic": "<b>议题</b>",
            "summary": "<script>alert(1)</script>\n第二行",
            "members": [{"display_name": "A", "provider_model": "m"}],
            "sources": [{"idx": 1, "title": "<x>", "url": "https://e.com"}],
            "rounds": 2,
            "speech_count": 4,
            "total_tokens": 1234,
            "duration_ms": 61000,
        }
        fragments = build_html_fragments(result)
        self.assertNotIn("<script>", fragments["topic_html"])
        self.assertNotIn("<script>", fragments["summary_html"])
        self.assertIn("&lt;script&gt;", fragments["summary_html"])
        self.assertIn("S1", fragments["sources_html"])

    def test_text(self):
        text = format_summary_text(
            {
                "topic": "t",
                "summary": "s",
                "rounds": 1,
                "speech_count": 2,
                "total_tokens": 100,
                "duration_ms": 1000,
                "stop_reason": "completed",
            }
        )
        self.assertIn("AI 圆桌", text)
        self.assertIn("轮数", text)

    def test_split(self):
        chunks = split_text("a" * 300 + "\n" + "b" * 300, 200)
        self.assertTrue(all(len(item) <= 200 for item in chunks))
        self.assertGreaterEqual(len(chunks), 3)

    def test_duration(self):
        self.assertEqual(format_duration(1000), "1 秒")
        self.assertEqual(format_duration(61000), "1 分 1 秒")


if __name__ == "__main__":
    unittest.main()
