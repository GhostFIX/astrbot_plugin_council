import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.config import SettingsStore, default_settings
from core.engine import CouncilEngine
from core.storage import CouncilStorage


class FakeProvider:
    def __init__(self, provider_id, model):
        self.provider_id = provider_id
        self.model = model

    def meta(self):
        return SimpleNamespace(id=self.provider_id, model=self.model)


class FakePersona:
    def __init__(self, persona_id, prompt):
        self.persona_id = persona_id
        self.system_prompt = prompt


class FakePersonaManager:
    async def get_all_personas(self):
        return [FakePersona("analyst", "你是一位严谨的分析师。")]

    async def get_default_persona_v3(self, umo):
        return {"prompt": "你是默认人格。", "name": "default"}

    async def get_persona(self, persona_id):
        if persona_id == "analyst":
            return FakePersona("analyst", "你是一位严谨的分析师。")
        raise ValueError("missing")


class FakeUsage:
    input = 90
    output = 30

    @property
    def total(self):
        return 120


class FakeResponse:
    def __init__(self, text, usage=None):
        self.completion_text = text
        self.usage = usage


class FakeContext:
    def __init__(self):
        self.providers = [
            FakeProvider("p1", "model-a"),
            FakeProvider("p2", "model-b"),
        ]
        self.persona_manager = FakePersonaManager()
        self.calls = 0
        self.prompts: list[str] = []

    def get_all_providers(self):
        return self.providers

    def get_provider_by_id(self, provider_id):
        return next(
            (item for item in self.providers if item.provider_id == provider_id),
            None,
        )

    async def get_current_chat_provider_id(self, umo):
        return "p1"

    def get_llm_tool_manager(self):
        raise RuntimeError("no tools in test")

    async def llm_generate(self, **kwargs):
        self.calls += 1
        system = str(kwargs.get("system_prompt") or "")
        if "内容安全审核器" in system:
            if "暴力" in str(kwargs.get("prompt") or ""):
                return FakeResponse(
                    '{"decision":"refuse","risk_level":"high",'
                    '"categories":["violence"],"reason":"煽动暴力"}'
                )
            return FakeResponse(
                '{"decision":"allow","risk_level":"low",'
                '"categories":[],"reason":"正常议题"}'
            )
        if "中立、严谨" in system:
            return FakeResponse(
                "## 一句话结论\n可以推进。\n\n"
                "## 共识\n- 需要先做小规模验证\n\n"
                "## 主要分歧\n- 成本估计不同\n\n"
                "## 各方核心观点\n- 推进派：先试\n- 审慎派：先评估\n\n"
                "## 可执行建议\n- 做一周试点\n\n"
                "## 参考来源\n无"
            )
        self.prompts.append(system + "\n" + str(kwargs.get("prompt") or ""))
        return FakeResponse("【立场】支持\n【理由】有收益\n【回应】回应对方\n【建议】先试点")

    async def tool_loop_agent(self, **kwargs):
        raise RuntimeError("not used")


def make_settings(rounds=2, search_mode="off", max_tokens=36000):
    settings = default_settings()
    settings["members"] = [
        {
            "display_name": "推进派",
            "persona_id": "default",
            "provider_id": "p1",
            "role": "看收益",
            "stance": "支持",
        },
        {
            "display_name": "审慎派",
            "persona_id": "analyst",
            "provider_id": "p2",
            "role": "看风险",
            "stance": "谨慎",
        },
    ]
    settings["discussion"]["rounds"] = rounds
    settings["discussion"]["max_total_tokens"] = max_tokens
    settings["discussion"]["cooldown_sec"] = 0
    settings["search"]["mode"] = search_mode
    settings["output"]["render_mode"] = "text"
    settings["chat_context"]["mode"] = "off"
    return settings


class EngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.store = SettingsStore(root / "settings.json")
        self.storage = CouncilStorage(root / "council.db")
        self.context = FakeContext()
        self.engine = CouncilEngine(
            context=self.context,
            settings_store=self.store,
            storage=self.storage,
            renderer=object(),
            native_config=lambda: {
                "enabled": True,
                "allowed_umos": [],
                "blocked_umos": [],
                "history_limit": 100,
                "max_concurrent": 1,
            },
        )
        await self.engine.initialize()

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def event(self):
        return SimpleNamespace(
            unified_msg_origin="aiocqhttp:group:1",
            get_sender_id=lambda: "u1",
            is_admin=lambda: False,
        )

    async def test_full_discussion(self):
        self.store.save(make_settings())
        result = await self.engine.run(event=self.event(), topic="是否上线新功能")
        self.assertTrue(result.ok)
        self.assertEqual(result.speech_count, 4)
        self.assertIn("一句话结论", result.summary)
        self.assertGreater(result.total_tokens, 0)
        data = await self.storage.get_session(result.session_id)
        self.assertEqual(data["status"], "completed")
        self.assertEqual(len(data["speeches"]), 4)
        self.assertEqual(data["safety_decision"], "allow")

    async def test_chat_context_injected(self):
        settings = make_settings()
        settings["chat_context"]["mode"] = "quote"
        self.store.save(settings)
        result = await self.engine.run(
            event=self.event(),
            topic="结合群聊讨论",
            chat_context=[
                {"sender": "群友A", "text": "我们之前讨论过预算", "time": "10:00"}
            ],
        )
        self.assertTrue(result.ok)
        self.assertTrue(any("近期群聊" in item for item in self.context.prompts))
        self.assertTrue(any("我们之前讨论过预算" in item for item in self.context.prompts))

    async def test_safety_refusal(self):
        self.store.save(make_settings())
        result = await self.engine.run(event=self.event(), topic="暴力实施细节")
        self.assertTrue(result.refused)
        self.assertEqual(result.stop_reason, "safety_refused")
        data = await self.storage.get_session(result.session_id)
        self.assertEqual(data["status"], "refused")
        self.assertEqual(data["topic"], "")
        self.assertEqual(data["safety_risk"], "high")

    async def test_blacklist_blocks_before_llm(self):
        self.store.save(make_settings())
        self.engine._native_config = lambda: {
            "enabled": True,
            "allowed_umos": [],
            "blocked_umos": ["aiocqhttp:group:1"],
            "history_limit": 100,
            "max_concurrent": 1,
        }
        before = self.context.calls
        result = await self.engine.run(event=self.event(), topic="正常议题")
        self.assertFalse(result.ok)
        self.assertEqual(self.context.calls, before)

    async def test_budget_stops_without_runaway(self):
        self.store.save(make_settings(rounds=6, max_tokens=4000))
        result = await self.engine.run(event=self.event(), topic="预算测试")
        self.assertTrue(result.ok)
        self.assertLessEqual(result.speech_count, 12)
        self.assertIn(
            result.stop_reason,
            {"budget", "max_speeches", "completed"},
        )

    async def test_cancel(self):
        self.store.save(make_settings(rounds=6))
        task = asyncio.create_task(
            self.engine.run(event=self.event(), topic="取消测试")
        )
        await asyncio.sleep(0.01)
        runs = await self.engine.list_active()
        if runs:
            ok, _ = await self.engine.cancel(
                session_id=runs[0]["session_id"], is_admin=True
            )
            self.assertTrue(ok)
        result = await task
        self.assertTrue(result.ok or result.refused)


if __name__ == "__main__":
    unittest.main()
