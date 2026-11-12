from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

from .core.config import SettingsStore
from .core.constants import PLUGIN_NAME
from .core.engine import CouncilEngine
from .core.render import CouncilRenderer, format_summary_text, split_text
from .core.storage import CouncilStorage
from .core.web_api import CouncilWebAPI

try:
    from astrbot.api import logger
except ImportError:  # pragma: no cover
    logger = logging.getLogger(PLUGIN_NAME)

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path
except ImportError:  # pragma: no cover
    def get_astrbot_data_path() -> str:
        return str(Path.cwd() / "data")


_COMMAND_RE = re.compile(
    r"^\s*[/!！]?\s*council(?:@\S+)?(?:\s+|$)",
    flags=re.IGNORECASE,
)
_COMMAND_SEARCH_RE = re.compile(
    r"(?:^|\s)council(?:@\S+)?(?:\s+|$)",
    flags=re.IGNORECASE,
)

USAGE_TEXT = (
    "用法：/council <议题>\n"
    "停止：/council stop\n"
    "配置、历史、完整记录导出：AstrBot WebUI → 插件 → AI 圆桌"
)


class CouncilPlugin(Star):
    def __init__(self, context: Context, config: Any = None):
        super().__init__(context)
        self.config = config if config is not None else {}
        self.plugin_dir = Path(__file__).resolve().parent
        data_root = Path(get_astrbot_data_path())
        self.data_dir = data_root / "plugin_data" / PLUGIN_NAME
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.exception("council data directory could not be created")

        self.settings_store = SettingsStore(self.data_dir / "settings.json")
        self.storage = CouncilStorage(self.data_dir / "council.db")
        self.renderer = CouncilRenderer(self.plugin_dir)
        self.engine = CouncilEngine(
            context=context,
            settings_store=self.settings_store,
            storage=self.storage,
            renderer=self.renderer,
            native_config=lambda: self.config,
        )
        self.web_api = CouncilWebAPI(self)
        try:
            self.web_api.register()
        except Exception:  # noqa: BLE001
            logger.exception("council web API registration failed")

    async def initialize(self):
        engine = getattr(self, "engine", None)
        if engine is not None:
            await engine.initialize()

    async def terminate(self):
        engine = getattr(self, "engine", None)
        if engine is not None:
            await engine.terminate()

    def _debug(self, message: str, *args: Any) -> None:
        try:
            if bool(self.config.get("debug_log", False)):
                logger.info("[council] " + message, *args)
        except Exception:  # noqa: BLE001
            pass

    def _extract_topic(self, event: AstrMessageEvent, fallback: str) -> str:
        raw = str(event.get_message_str() or "").strip()
        match = _COMMAND_RE.match(raw)
        if match is None:
            match = _COMMAND_SEARCH_RE.search(raw)
        if match is not None:
            return raw[match.end() :].strip()
        # AstrBot may strip the wake prefix before the handler is called.
        if raw and not fallback:
            return raw
        return raw or str(fallback or "").strip()

    @filter.command("council")
    async def council(
        self,
        event: AstrMessageEvent,
        _arg: str = "",
    ):
        """召集 AI 圆桌讨论一个议题，或使用 /council stop 停止当前讨论。"""
        event.call_llm = False
        umo = str(event.unified_msg_origin)
        topic = self._extract_topic(event, _arg)

        if topic.casefold() == "stop":
            sender_id = ""
            try:
                sender_id = str(event.get_sender_id() or "")
            except Exception:  # noqa: BLE001
                pass
            is_admin = False
            try:
                is_admin = bool(event.is_admin())
            except Exception:  # noqa: BLE001
                pass
            ok, message = await self.engine.cancel(
                umo=umo, sender_id=sender_id, is_admin=is_admin
            )
            yield event.plain_result(message)
            return

        if not topic:
            yield event.plain_result(USAGE_TEXT)
            return

        allowed, reason = self.engine.check_access(umo)
        if not allowed:
            if reason == "disabled":
                yield event.plain_result("圆桌讨论已在插件配置中关闭。")
            elif reason == "blocked":
                yield event.plain_result("本会话已被禁止使用圆桌讨论。")
            elif reason == "not_whitelisted":
                yield event.plain_result("本会话不在圆桌讨论白名单中。")
            else:
                yield event.plain_result("当前无法使用圆桌讨论。")
            return

        cooldown = self.engine.check_cooldown(umo)
        if cooldown > 0:
            yield event.plain_result(f"请等待 {cooldown} 秒后再发起讨论。")
            return
        if await self.engine.has_active(umo):
            yield event.plain_result("本会话已有讨论正在进行。")
            return

        self._debug(
            "starting council umo=%s topic_chars=%s",
            umo,
            len(topic),
        )
        yield event.plain_result(
            "🕊️ 已收到议题，正在审核并召集圆桌成员。"
            "预计 1～3 分钟，期间可用 /council stop 停止。"
        )

        try:
            result = await self.engine.run(event=event, topic=topic)
        except Exception:  # noqa: BLE001
            logger.exception("council command failed")
            yield event.plain_result("讨论启动失败，请查看 AstrBot 日志。")
            return

        if result.refused:
            yield event.plain_result(
                result.message or "该议题不适合在本群展开讨论。"
            )
            return
        if not result.ok:
            message = result.error or "讨论未能开始"
            yield event.plain_result(f"讨论未开始：{message}")
            return

        payload = result.to_dict()
        mode = result.render_mode or "image"
        image_failed = False
        if mode in {"image", "both"}:
            try:
                image_url = await self.renderer.render_image(
                    self.html_render, payload
                )
                if not image_url:
                    raise ValueError("empty image url")
                yield event.image_result(image_url)
            except Exception:  # noqa: BLE001
                image_failed = True
                logger.exception("council image render failed")
                yield event.plain_result(
                    "图片渲染失败，已改为文字输出。"
                )

        if mode == "text" or mode == "both" or image_failed:
            text = format_summary_text(payload, result.max_text_chars)
            chunks = split_text(text, 1800)
            for chunk in chunks[:4]:
                yield event.plain_result(chunk)
            if len(chunks) > 4:
                yield event.plain_result(
                    "（总结过长，更多内容请在 WebUI 历史记录中查看。）"
                )
