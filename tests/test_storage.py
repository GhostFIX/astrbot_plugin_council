import json
import tempfile
import unittest
from pathlib import Path

from core.storage import (
    CouncilStorage,
    session_to_markdown,
    sessions_to_csv,
    utc_now_iso,
)


class StorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "council.db"
        self.storage = CouncilStorage(self.path)
        await self.storage.initialize()

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def _create(self, sid="s1", topic="议题", created_at=None):
        await self.storage.create_session(
            {
                "id": sid,
                "umo": "aiocqhttp:group:1",
                "topic": topic,
                "topic_hash": "hash",
                "status": "running",
                "render_mode": "image",
                "search_mode": "off",
                "safety_decision": "allow",
                "safety_risk": "low",
                "safety_categories": [],
                "safety_reason": "",
                "members": [{"display_name": "A", "provider_model": "m1"}],
                "models": [{"display_name": "A", "provider_model": "m1"}],
                "settings_snapshot": {"rounds": 1},
                "created_at": created_at or utc_now_iso(),
            }
        )

    async def test_crud_and_export(self):
        await self._create()
        await self.storage.add_speech(
            {
                "session_id": "s1",
                "round": 1,
                "seq": 1,
                "speaker": "A",
                "persona_id": "default",
                "persona_name": "default",
                "provider_id": "p1",
                "provider_model": "m1",
                "content": "观点",
                "tokens": 10,
            }
        )
        await self.storage.add_sources(
            "s1",
            [{"idx": 1, "title": "来源", "url": "https://e.com", "snippet": "摘要"}],
        )
        await self.storage.finish_session(
            "s1",
            status="completed",
            stop_reason="completed",
            summary="## 结论\n测试",
            error="",
            duration_ms=1200,
            input_tokens=100,
            output_tokens=50,
            rounds=1,
            speech_count=1,
        )
        data = await self.storage.get_session("s1")
        self.assertIsNotNone(data)
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["total_tokens"], 150)
        self.assertEqual(data["members"][0]["display_name"], "A")
        self.assertEqual(data["speeches"][0]["content"], "观点")
        self.assertEqual(data["sources"][0]["title"], "来源")
        self.assertEqual(data["settings"]["rounds"], 1)

        listing = await self.storage.list_sessions(query="议题")
        self.assertEqual(listing["total"], 1)
        stats = await self.storage.stats(30)
        self.assertEqual(stats["overview"]["sessions"], 1)
        self.assertEqual(stats["top_members"][0]["speaker"], "A")

        markdown = session_to_markdown(data)
        self.assertIn("测试", markdown)
        self.assertIn("观点", markdown)
        self.assertIn("参考来源", markdown)
        csv_text = sessions_to_csv([data])
        self.assertIn("s1", csv_text)

        deleted = await self.storage.delete_sessions(["s1"])
        self.assertEqual(deleted, 1)
        self.assertIsNone(await self.storage.get_session("s1"))

    async def test_cleanup_keeps_newest(self):
        for index in range(3):
            await self._create(
                f"s{index}",
                f"议题{index}",
                created_at=f"2026-01-0{index + 1}T00:00:00+00:00",
            )
        removed = await self.storage.cleanup(2)
        self.assertEqual(removed, 1)
        self.assertIsNotNone(await self.storage.get_session("s2"))
        self.assertIsNone(await self.storage.get_session("s0"))

    async def test_chat_context_persistence(self):
        first = await self.storage.add_chat_message(
            {
                "umo": "aiocqhttp:group:1",
                "dedupe_key": "m1",
                "sender_name": "A",
                "text": "第一句",
                "has_image": True,
                "time_label": "10:00",
            }
        )
        second = await self.storage.add_chat_message(
            {
                "umo": "aiocqhttp:group:1",
                "dedupe_key": "m2",
                "sender_name": "B",
                "text": "第二句",
                "has_audio": True,
                "time_label": "10:01",
            }
        )
        self.assertGreater(first, 0)
        self.assertGreater(second, first)
        await self.storage.update_chat_message_media(
            first, image_text="一张示意图", audio_text=""
        )
        await self.storage.update_chat_message_media(
            second, image_text="", audio_text="语音内容"
        )
        await self.storage.update_chat_message_media(
            first,
            image_text="一张示意图",
            audio_text="",
            media=[
                {
                    "type": "image",
                    "path": "group/a.jpg",
                    "name": "a.jpg",
                }
            ],
        )
        items = await self.storage.get_chat_messages(
            "aiocqhttp:group:1", 10
        )
        self.assertEqual(len(items), 2)
        self.assertIn("[图片] 一张示意图", items[0]["text"])
        self.assertIn("[语音] 语音内容", items[1]["text"])
        records = await self.storage.get_chat_records(
            umo="aiocqhttp:group:1", page=1, page_size=10
        )
        self.assertEqual(records["total"], 2)
        self.assertEqual(records["items"][1]["media"][0]["name"], "a.jpg")
        stats = await self.storage.chat_context_stats()
        self.assertEqual(stats[0]["count"], 2)
        await self.storage.trim_chat_messages("aiocqhttp:group:1", 1)
        items = await self.storage.get_chat_messages(
            "aiocqhttp:group:1", 10
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["sender"], "B")
        deleted = await self.storage.trim_chat_messages(
            "aiocqhttp:group:1", 0
        )
        self.assertEqual(deleted, 1)
        await self.storage.add_chat_message(
            {
                "umo": "aiocqhttp:group:1",
                "dedupe_key": "m3",
                "sender_name": "C",
                "text": "清空测试",
                "is_self": True,
                "message_ts": 1700000000,
                "time_label": "11:00",
            }
        )
        records = await self.storage.get_chat_records()
        self.assertTrue(records["items"][0]["is_self"])
        self.assertEqual(records["items"][0]["message_ts"], 1700000000)
        self.assertEqual(await self.storage.clear_chat_messages(), 1)
        self.assertEqual((await self.storage.get_chat_records())["total"], 0)

    async def test_refused_topic(self):
        await self.storage.create_session(
            {
                "id": "r1",
                "umo": "u",
                "topic": "",
                "topic_hash": "h",
                "status": "refused",
                "safety_decision": "refuse",
                "safety_risk": "high",
                "safety_categories": ["violence"],
                "safety_reason": "测试",
                "created_at": utc_now_iso(),
            }
        )
        data = await self.storage.get_session("r1")
        self.assertEqual(data["safety_categories"], ["violence"])
        self.assertEqual(data["safety_decision"], "refuse")


if __name__ == "__main__":
    unittest.main()
