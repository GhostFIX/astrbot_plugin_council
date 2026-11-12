from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from .access import access_decision
from .budget import TokenBudget
from .config import SettingsStore, validate_settings
from .prompts import (
    build_speech_prompt,
    build_summary_prompt,
    build_system_prompt,
    clip_text,
    fallback_summary,
    render_transcript,
    sanitize_topic,
)
from .render import CouncilRenderer
from .safety import SafetyDecision, SafetyReviewer
from .search import (
    AnySearchClient,
    SearchBundle,
    format_compact_sources,
    format_search_card,
    sources_for_storage,
)
from .storage import CouncilStorage, utc_now_iso

logger = logging.getLogger("astrbot_plugin_council")

MAX_SPEECHES = 24


@dataclass
class ActiveRun:
    session_id: str
    umo: str
    topic: str
    starter_id: str
    started_at: float
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    round_no: int = 0
    speech_count: int = 0
    status: str = "running"
    total_tokens: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "umo": self.umo,
            "topic": self.topic,
            "started_at": self.started_at,
            "round": self.round_no,
            "speech_count": self.speech_count,
            "status": self.status,
            "total_tokens": self.total_tokens,
            "elapsed_sec": max(0, int(time.time() - self.started_at)),
        }


@dataclass
class RunResult:
    ok: bool
    refused: bool = False
    message: str = ""
    error: str = ""
    session_id: str = ""
    topic: str = ""
    summary: str = ""
    speeches: list[dict[str, Any]] = field(default_factory=list)
    members: list[dict[str, Any]] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    rounds: int = 0
    speech_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    duration_ms: int = 0
    stop_reason: str = ""
    render_mode: str = "image"
    search_mode: str = "native"
    search_error: str = ""
    max_text_chars: int = 12000
    safety: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "refused": self.refused,
            "message": self.message,
            "error": self.error,
            "session_id": self.session_id,
            "topic": self.topic,
            "summary": self.summary,
            "speeches": self.speeches,
            "members": self.members,
            "sources": self.sources,
            "rounds": self.rounds,
            "speech_count": self.speech_count,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "duration_ms": self.duration_ms,
            "stop_reason": self.stop_reason,
            "render_mode": self.render_mode,
            "search_mode": self.search_mode,
            "search_error": self.search_error,
            "max_text_chars": self.max_text_chars,
            "safety": self.safety,
            "warnings": self.warnings,
        }


class CouncilEngine:
    def __init__(
        self,
        *,
        context: Any,
        settings_store: SettingsStore,
        storage: CouncilStorage,
        renderer: CouncilRenderer,
        native_config: Callable[[], dict[str, Any]],
    ):
        self.context = context
        self.settings_store = settings_store
        self.storage = storage
        self.renderer = renderer
        self._native_config = native_config
        self._active: dict[str, ActiveRun] = {}
        self._cooldowns: dict[str, float] = {}
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(1)
        self._shutdown = False
        self._anysearch = AnySearchClient()

    async def initialize(self) -> None:
        cfg = self._config()
        concurrency = max(1, min(4, int(cfg.get("max_concurrent", 1) or 1)))
        self._semaphore = asyncio.Semaphore(concurrency)
        await self.storage.initialize()
        await self.storage.mark_interrupted()

    async def terminate(self) -> None:
        self._shutdown = True
        async with self._lock:
            runs = list(self._active.values())
        for run in runs:
            run.cancel_event.set()
            run.status = "stopping"

    def _config(self) -> dict[str, Any]:
        try:
            value = self._native_config()
            return value if isinstance(value, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    def check_access(self, umo: str) -> tuple[bool, str]:
        cfg = self._config()
        if not bool(cfg.get("enabled", True)):
            return False, "disabled"
        return access_decision(
            umo,
            cfg.get("allowed_umos", []),
            cfg.get("blocked_umos", []),
        )

    def check_cooldown(self, umo: str) -> int:
        settings = self.settings_store.load()
        cooldown = int(settings["discussion"].get("cooldown_sec", 0) or 0)
        if cooldown <= 0:
            return 0
        last = self._cooldowns.get(umo, 0)
        remaining = int(cooldown - (time.monotonic() - last))
        return max(0, remaining)

    async def list_active(self) -> list[dict[str, Any]]:
        async with self._lock:
            return [item.to_dict() for item in self._active.values()]

    async def has_active(self, umo: str) -> bool:
        async with self._lock:
            return umo in self._active

    async def cancel(
        self,
        *,
        umo: str = "",
        session_id: str = "",
        sender_id: str = "",
        is_admin: bool = False,
    ) -> tuple[bool, str]:
        async with self._lock:
            run = None
            if session_id:
                for item in self._active.values():
                    if item.session_id == session_id:
                        run = item
                        break
            elif umo:
                run = self._active.get(umo)
            if run is None:
                return False, "没有正在进行的讨论"
            if not is_admin and sender_id and run.starter_id != sender_id:
                return False, "只有发起者或管理员可以停止"
            run.cancel_event.set()
            run.status = "stopping"
            return True, "正在停止，当前模型调用返回后会结束"

    async def run(self, *, event: Any, topic: str) -> RunResult:
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        starter_id = str(
            getattr(event, "get_sender_id", lambda: "")() or ""
        )
        cfg = self._config()
        if not bool(cfg.get("enabled", True)):
            return RunResult(ok=False, error="圆桌讨论已在插件配置中关闭")

        allowed, reason = self.check_access(umo)
        if not allowed:
            if reason == "blocked":
                msg = "本会话已被禁止使用圆桌讨论。"
            elif reason == "not_whitelisted":
                msg = "本会话不在圆桌讨论白名单中。"
            else:
                msg = "当前无法使用圆桌讨论。"
            return RunResult(ok=False, error=msg)

        topic = sanitize_topic(topic)
        if not topic:
            return RunResult(ok=False, error="议题不能为空")
        if len(topic) > 2000:
            return RunResult(ok=False, error="议题过长，请控制在 2000 字以内")

        cooldown = self.check_cooldown(umo)
        if cooldown > 0:
            return RunResult(ok=False, error=f"请等待 {cooldown} 秒后再发起讨论")

        async with self._lock:
            if umo in self._active:
                return RunResult(ok=False, error="本会话已有讨论正在进行")

        try:
            await asyncio.wait_for(self._semaphore.acquire(), timeout=0.2)
        except asyncio.TimeoutError:
            return RunResult(ok=False, error="当前讨论并发已满，请稍后再试")

        session_id = uuid.uuid4().hex
        started_at = time.monotonic()
        run = ActiveRun(
            session_id=session_id,
            umo=umo,
            topic=topic,
            starter_id=starter_id,
            started_at=time.time(),
        )
        async with self._lock:
            if umo in self._active:
                self._semaphore.release()
                return RunResult(ok=False, error="本会话已有讨论正在进行")
            self._active[umo] = run
            self._cooldowns[umo] = time.monotonic()
            # Prune stale cooldown keys opportunistically.
            cutoff = time.monotonic() - 7200
            for key, value in list(self._cooldowns.items()):
                if value < cutoff:
                    self._cooldowns.pop(key, None)

        try:
            return await self._run(
                event=event,
                umo=umo,
                topic=topic,
                starter_id=starter_id,
                session_id=session_id,
                run=run,
                started_at=started_at,
            )
        except asyncio.CancelledError:
            await self._finish_cancelled(run, started_at)
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("council run failed")
            run.status = "failed"
            await self._safe_finish(
                session_id,
                status="failed",
                stop_reason="error",
                summary="",
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=int((time.monotonic() - started_at) * 1000),
                input_tokens=run.total_tokens,
                output_tokens=0,
                rounds=run.round_no,
                speech_count=run.speech_count,
            )
            return RunResult(
                ok=False,
                session_id=session_id,
                topic=topic,
                error="讨论过程中出现异常，已保存可用记录",
                stop_reason="error",
            )
        finally:
            async with self._lock:
                current = self._active.get(umo)
                if current is not None and current.session_id == session_id:
                    self._active.pop(umo, None)
            self._semaphore.release()

    async def _run(
        self,
        *,
        event: Any,
        umo: str,
        topic: str,
        starter_id: str,
        session_id: str,
        run: ActiveRun,
        started_at: float,
    ) -> RunResult:
        settings = self.settings_store.load()
        settings = copy.deepcopy(settings)
        members, warnings = await self._resolve_members(umo, settings)
        if len(members) < 2:
            return RunResult(
                ok=False,
                session_id=session_id,
                topic=topic,
                error="可用成员不足 2 人，请检查 Provider 或人格配置",
                warnings=warnings,
            )

        current_provider = await self._current_provider_id(umo)
        safety = await SafetyReviewer(self.context).review(
            topic=topic,
            umo=umo,
            settings=settings,
            provider_id=current_provider,
        )

        if not safety.allowed:
            await self._create_refused_session(
                session_id=session_id,
                umo=umo,
                topic=topic,
                settings=settings,
                safety=safety,
                started_at=started_at,
            )
            return RunResult(
                ok=False,
                refused=True,
                message=str(
                    settings["safety"].get("refuse_message")
                    or "该议题不适合在本群展开讨论。"
                ),
                session_id=session_id,
                topic=topic,
                safety=safety.to_dict(),
                stop_reason="safety_refused",
                render_mode=str(settings["output"]["render_mode"]),
                search_mode=str(settings["search"]["mode"]),
            )

        public_members = [
            {
                "display_name": item["display_name"],
                "persona_id": item["persona_id"],
                "provider_id": item["provider_id"],
                "provider_model": item["provider_model"],
                "role": item["role"],
                "stance": item["stance"],
            }
            for item in members
        ]
        models = [
            {
                "display_name": item["display_name"],
                "provider_id": item["provider_id"],
                "provider_model": item["provider_model"],
            }
            for item in members
        ]
        settings_snapshot = copy.deepcopy(settings)
        settings_snapshot["search"]["anysearch_api_key"] = ""

        created = await self._create_session(
            session_id=session_id,
            umo=umo,
            topic=topic,
            settings=settings,
            public_members=public_members,
            models=models,
            safety=safety,
            settings_snapshot=settings_snapshot,
        )
        if not created:
            return RunResult(
                ok=False,
                session_id=session_id,
                topic=topic,
                error="历史记录初始化失败，已取消本次讨论",
            )

        budget = TokenBudget(
            max_total=int(settings["discussion"]["max_total_tokens"]),
            max_output_per_call=int(
                settings["discussion"]["max_tokens_per_speech"]
            ),
        )
        budget.add_external(safety.input_tokens, safety.output_tokens)
        search_mode = str(settings["search"]["mode"])
        search_bundle = SearchBundle(query=topic)
        search_error = ""
        if search_mode == "prefetch":
            search_bundle = await self._prefetch(topic, settings)
            search_error = search_bundle.error
            if search_bundle.ok:
                await self._safe_add_sources(
                    session_id, sources_for_storage(search_bundle)
                )

        native_tools = None
        if search_mode == "native":
            native_tools = self._search_toolset()
        native_rounds = int(settings["search"].get("native_rounds", 1) or 1)

        rounds = int(settings["discussion"]["rounds"])
        max_context = int(settings["discussion"]["max_context_chars"])
        max_speeches = min(MAX_SPEECHES, rounds * len(members))
        deadline = started_at + int(
            settings["discussion"]["max_duration_sec"]
        )
        speeches: list[dict[str, Any]] = []
        stop_reason = "completed"
        rounds_completed = 0
        search_card = format_search_card(search_bundle) if search_bundle.ok else ""
        compact_sources = (
            format_compact_sources(search_bundle) if search_bundle.ok else ""
        )

        for round_no in range(1, rounds + 1):
            if run.cancel_event.is_set():
                stop_reason = "cancelled"
                break
            if time.monotonic() >= deadline:
                stop_reason = "timeout"
                break
            if len(speeches) >= max_speeches:
                stop_reason = "max_speeches"
                break

            round_success = 0
            for member in members:
                if run.cancel_event.is_set():
                    stop_reason = "cancelled"
                    break
                if time.monotonic() >= deadline:
                    stop_reason = "timeout"
                    break
                if len(speeches) >= max_speeches:
                    stop_reason = "max_speeches"
                    break

                transcript = render_transcript(
                    speeches,
                    current_round=round_no,
                    max_chars=max_context,
                )
                use_search_card = bool(search_card) and round_no == 1
                use_compact = bool(compact_sources) and round_no > 1
                prompt = build_speech_prompt(
                    topic=topic,
                    member=member,
                    round_no=round_no,
                    total_rounds=rounds,
                    transcript=transcript,
                    search_card=search_card if use_search_card else "",
                    compact_sources=compact_sources if use_compact else "",
                    max_context_chars=max_context,
                )
                system_prompt = build_system_prompt(
                    member,
                    member.get("persona_prompt", ""),
                    total_rounds=rounds,
                )
                if native_tools is not None and round_no <= native_rounds:
                    system_prompt += (
                        "\n\n你可以使用提供的网页搜索工具检索外部信息。"
                        "只在真正需要时调用；最多调用一次。"
                    )

                if not budget.can_afford(
                    system_prompt + "\n" + prompt,
                    int(settings["discussion"]["max_tokens_per_speech"]),
                ):
                    stop_reason = "budget"
                    budget.mark_stopped("token_budget")
                    break

                use_tools = (
                    native_tools is not None
                    and round_no <= native_rounds
                    and search_mode == "native"
                )
                try:
                    response, tool_overhead = await self._call_member(
                        event=event,
                        provider_id=member["provider_id"],
                        system_prompt=system_prompt,
                        prompt=prompt,
                        max_tokens=int(
                            settings["discussion"]["max_tokens_per_speech"]
                        ),
                        timeout=int(
                            settings["discussion"]["request_timeout_sec"]
                        ),
                        use_tools=use_tools,
                        tools=native_tools,
                    )
                except Exception as exc:  # noqa: BLE001
                    warnings.append(
                        f"{member['display_name']} 调用失败：{type(exc).__name__}"
                    )
                    logger.warning(
                        "council member failed: %s (%s)",
                        member["display_name"],
                        type(exc).__name__,
                    )
                    continue

                text = str(getattr(response, "completion_text", "") or "").strip()
                if not text:
                    warnings.append(f"{member['display_name']} 返回了空内容")
                    continue
                text = clip_text(text, 2400)

                used = budget.add(
                    prompt=system_prompt + "\n" + prompt,
                    output=text,
                    usage=getattr(response, "usage", None),
                    overhead=tool_overhead,
                )
                speech = {
                    "session_id": session_id,
                    "round": round_no,
                    "seq": len(speeches) + 1,
                    "speaker": member["display_name"],
                    "persona_id": member["persona_id"],
                    "persona_name": member["persona_id"],
                    "provider_id": member["provider_id"],
                    "provider_model": member["provider_model"],
                    "content": text,
                    "tokens": used.total,
                    "created_at": utc_now_iso(),
                }
                speeches.append(speech)
                round_success += 1
                run.round_no = round_no
                run.speech_count = len(speeches)
                run.total_tokens = budget.used_total
                await self._safe_add_speech(speech)

            if stop_reason not in {"completed", "max_speeches"}:
                break
            if round_success == 0:
                stop_reason = "error"
                warnings.append("本轮没有任何成员成功发言")
                break
            rounds_completed = round_no

        if not speeches:
            cancelled = run.cancel_event.is_set() or stop_reason == "cancelled"
            error = "讨论已停止" if cancelled else "没有成员成功发言，讨论未开始"
            await self._safe_finish(
                session_id,
                status="cancelled" if cancelled else "failed",
                stop_reason="cancelled"
                if cancelled
                else (stop_reason if stop_reason != "completed" else "error"),
                summary="",
                error="" if cancelled else error,
                duration_ms=int((time.monotonic() - started_at) * 1000),
                input_tokens=budget.input_tokens,
                output_tokens=budget.output_tokens,
                rounds=rounds_completed,
                speech_count=0,
            )
            await self._cleanup_history()
            return RunResult(
                ok=False,
                session_id=session_id,
                topic=topic,
                error=error,
                stop_reason="cancelled" if cancelled else "error",
                warnings=warnings,
                members=public_members,
            )

        if run.cancel_event.is_set():
            stop_reason = "cancelled"
            summary = fallback_summary(
                topic, speeches, "讨论已被手动停止，未生成 LLM 总结"
            )
        elif stop_reason == "timeout":
            summary = fallback_summary(
                topic, speeches, "已达到整场时长上限，未生成 LLM 总结"
            )
        else:
            summary, _summary_used = await self._summarize(
                settings=settings,
                topic=topic,
                speeches=speeches,
                members=public_members,
                search_bundle=search_bundle,
                budget=budget,
                current_provider=current_provider,
            )

        status = "completed"
        if stop_reason == "cancelled":
            status = "cancelled"
        elif stop_reason in {"error", "timeout", "budget", "max_speeches"}:
            status = "completed" if speeches else "failed"
            if stop_reason == "error":
                status = "completed"

        duration_ms = int((time.monotonic() - started_at) * 1000)
        await self._safe_finish(
            session_id,
            status=status,
            stop_reason=stop_reason,
            summary=summary,
            error="",
            duration_ms=duration_ms,
            input_tokens=budget.input_tokens,
            output_tokens=budget.output_tokens,
            rounds=rounds_completed,
            speech_count=len(speeches),
        )
        await self._cleanup_history()

        return RunResult(
            ok=True,
            session_id=session_id,
            topic=topic,
            summary=summary,
            speeches=speeches,
            members=public_members,
            sources=sources_for_storage(search_bundle),
            rounds=rounds_completed,
            speech_count=len(speeches),
            input_tokens=budget.input_tokens,
            output_tokens=budget.output_tokens,
            total_tokens=budget.used_total,
            duration_ms=duration_ms,
            stop_reason=stop_reason,
            render_mode=str(settings["output"]["render_mode"]),
            search_mode=search_mode,
            search_error=search_error,
            max_text_chars=int(settings["output"].get("max_text_chars", 12000)),
            safety=safety.to_dict(),
            warnings=warnings,
        )

    async def _resolve_members(
        self, umo: str, settings: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], list[str]]:
        warnings: list[str] = []
        current_provider = await self._current_provider_id(umo)
        personas: dict[str, Any] = {}
        try:
            for persona in await self.context.persona_manager.get_all_personas():
                if isinstance(persona, dict):
                    pid = str(persona.get("persona_id") or "")
                else:
                    pid = str(getattr(persona, "persona_id", "") or "")
                if pid:
                    personas[pid] = persona
        except Exception:  # noqa: BLE001
            warnings.append("人格列表读取失败，将使用默认人格")

        default_persona = None
        try:
            default_persona = await self.context.persona_manager.get_default_persona_v3(
                umo
            )
        except Exception:  # noqa: BLE001
            default_persona = None

        result: list[dict[str, Any]] = []
        used_names: set[str] = set()
        for index, raw in enumerate(settings["members"], 1):
            display_name = str(raw.get("display_name") or f"成员{index}")
            provider_id = str(raw.get("provider_id") or current_provider or "")
            provider = await self._get_provider(provider_id)
            if provider is None:
                warnings.append(
                    f"{display_name} 的 Provider 不可用，已跳过"
                )
                continue
            provider_model = self._provider_model(provider)
            persona_id = str(raw.get("persona_id") or "default")
            persona_obj = None
            if persona_id != "default":
                persona_obj = personas.get(persona_id)
                if persona_obj is None:
                    try:
                        persona_obj = await self.context.persona_manager.get_persona(
                            persona_id
                        )
                    except Exception:  # noqa: BLE001
                        persona_obj = None
                if persona_obj is None:
                    warnings.append(
                        f"人格 {persona_id} 不存在，{display_name} 使用默认人格"
                    )
            persona_prompt = self._persona_prompt(
                persona_obj or default_persona
            )
            if display_name in used_names:
                display_name = f"{display_name}-{index}"
            used_names.add(display_name)

            result.append(
                {
                    "display_name": display_name,
                    "persona_id": persona_id,
                    "provider_id": provider_id,
                    "provider_model": provider_model,
                    "persona_prompt": persona_prompt,
                    "role": str(raw.get("role") or ""),
                    "stance": str(raw.get("stance") or ""),
                }
            )
        return result, warnings

    async def _current_provider_id(self, umo: str) -> str:
        try:
            return str(
                await self.context.get_current_chat_provider_id(umo) or ""
            )
        except Exception:  # noqa: BLE001
            providers = self._all_providers()
            if providers:
                return providers[0]
            return ""

    def _all_providers(self) -> list[str]:
        try:
            providers = self.context.get_all_providers()
        except Exception:  # noqa: BLE001
            return []
        ids: list[str] = []
        for provider in providers or []:
            try:
                meta = provider.meta()
                pid = str(getattr(meta, "id", "") or "")
            except Exception:  # noqa: BLE001
                pid = ""
            if pid:
                ids.append(pid)
        return ids

    async def _get_provider(self, provider_id: str) -> Any | None:
        if not provider_id:
            return None
        try:
            provider = self.context.get_provider_by_id(provider_id)
            if inspect.isawaitable(provider):
                provider = await provider
            return provider
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _provider_model(provider: Any) -> str:
        try:
            meta = provider.meta()
            if isinstance(meta, dict):
                return str(meta.get("model") or "")
            return str(getattr(meta, "model", "") or "")
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def _persona_prompt(persona: Any) -> str:
        if persona is None:
            return ""
        if isinstance(persona, dict):
            return str(persona.get("prompt") or persona.get("system_prompt") or "")
        return str(
            getattr(persona, "system_prompt", "")
            or getattr(persona, "prompt", "")
            or ""
        )

    def _search_toolset(self) -> Any | None:
        try:
            from astrbot.core.agent.tool import ToolSet
        except Exception:  # noqa: BLE001
            return None
        manager = None
        try:
            manager = self.context.get_llm_tool_manager()
        except Exception:  # noqa: BLE001
            return None

        candidates: list[Any] = []
        try:
            candidates.extend(list(getattr(manager, "func_list", []) or []))
        except Exception:  # noqa: BLE001
            pass
        try:
            if hasattr(manager, "iter_builtin_tools"):
                candidates.extend(list(manager.iter_builtin_tools()))
        except Exception:  # noqa: BLE001
            pass
        try:
            builtin_map = getattr(manager, "builtin_func_list", {}) or {}
            if isinstance(builtin_map, dict):
                candidates.extend(list(builtin_map.values()))
        except Exception:  # noqa: BLE001
            pass

        selected: dict[str, Any] = {}
        for tool in candidates:
            name = str(getattr(tool, "name", "") or "")
            if not name:
                continue
            if not (
                name.startswith("web_search")
                or name.endswith("_extract_web_page")
            ):
                continue
            if not bool(getattr(tool, "active", True)):
                continue
            selected.setdefault(name, tool)
        if not selected:
            return None
        return ToolSet(list(selected.values()))

    async def _call_member(
        self,
        *,
        event: Any,
        provider_id: str,
        system_prompt: str,
        prompt: str,
        max_tokens: int,
        timeout: int,
        use_tools: bool,
        tools: Any,
    ) -> tuple[Any, int]:
        tool_attempted = False
        if use_tools and tools is not None:
            tool_attempted = True
            try:
                response = await asyncio.wait_for(
                    self.context.tool_loop_agent(
                        event=event,
                        chat_provider_id=provider_id,
                        prompt=prompt,
                        system_prompt=system_prompt,
                        tools=tools,
                        max_steps=2,
                        tool_call_timeout=30,
                        stream=False,
                    ),
                    timeout=timeout,
                )
                return response, 1200
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "native search tool call failed, fallback to plain: %s",
                    type(exc).__name__,
                )
        response = await self._generate(
            provider_id=provider_id,
            prompt=prompt,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            timeout=timeout,
        )
        return response, (1200 if tool_attempted else 0)

    async def _generate(
        self,
        *,
        provider_id: str,
        prompt: str,
        system_prompt: str,
        max_tokens: int,
        timeout: int,
    ) -> Any:
        kwargs: dict[str, Any] = {
            "chat_provider_id": provider_id,
            "prompt": prompt,
            "system_prompt": system_prompt,
            "max_tokens": max_tokens,
        }
        try:
            return await asyncio.wait_for(
                self.context.llm_generate(**kwargs), timeout=timeout
            )
        except TypeError:
            kwargs.pop("max_tokens", None)
            return await asyncio.wait_for(
                self.context.llm_generate(**kwargs), timeout=timeout
            )

    async def _prefetch(
        self, topic: str, settings: dict[str, Any]
    ) -> SearchBundle:
        search = settings["search"]
        try:
            self._anysearch.timeout_sec = int(search.get("timeout_sec", 20))
            return await asyncio.wait_for(
                self._anysearch.search(
                    topic,
                    api_key=str(search.get("anysearch_api_key") or ""),
                    max_results=int(search.get("max_results", 5)),
                    language=str(search.get("language", "auto")),
                ),
                timeout=int(search.get("timeout_sec", 20)) + 2,
            )
        except Exception as exc:  # noqa: BLE001
            return SearchBundle(
                query=topic,
                error=f"prefetch failed: {type(exc).__name__}",
            )

    async def _summarize(
        self,
        *,
        settings: dict[str, Any],
        topic: str,
        speeches: list[dict[str, Any]],
        members: list[dict[str, Any]],
        search_bundle: SearchBundle,
        budget: TokenBudget,
        current_provider: str,
    ) -> tuple[str, bool]:
        prompt = build_summary_prompt(
            topic=topic,
            transcript=render_transcript(
                speeches, current_round=0, max_chars=16000
            ),
            members=members,
            search_card=format_search_card(search_bundle)
            if search_bundle.ok
            else "",
            max_context_chars=14000,
        )
        provider = (
            str(settings.get("moderator", {}).get("provider_id", "")).strip()
            or current_provider
            or (members[0].get("provider_id", "") if members else "")
        )
        max_output = int(settings["discussion"].get("summary_max_tokens", 800))
        if not provider or not budget.can_afford(
            prompt, max_output, use_reserve=True
        ):
            budget.mark_stopped("summary_budget")
            return (
                fallback_summary(topic, speeches, "Token 预算不足或总结模型不可用"),
                False,
            )
        try:
            response = await asyncio.wait_for(
                self._generate(
                    provider_id=provider,
                    prompt=prompt,
                    system_prompt=(
                        "你是中立、严谨的圆桌讨论主持人。"
                        "只根据记录总结，不添加新事实，不编造来源。"
                    ),
                    max_tokens=max_output,
                    timeout=int(
                        settings["discussion"].get("request_timeout_sec", 90)
                    ),
                ),
                timeout=int(
                    settings["discussion"].get("request_timeout_sec", 90)
                )
                + 5,
            )
            text = str(getattr(response, "completion_text", "") or "").strip()
            if not text:
                raise ValueError("empty summary")
            budget.add(
                prompt=prompt,
                output=text,
                usage=getattr(response, "usage", None),
            )
            return clip_text(text, 5000), True
        except Exception as exc:  # noqa: BLE001
            logger.warning("council summary failed: %s", type(exc).__name__)
            return (
                fallback_summary(topic, speeches, "总结模型调用失败"),
                False,
            )

    async def _create_session(
        self,
        *,
        session_id: str,
        umo: str,
        topic: str,
        settings: dict[str, Any],
        public_members: list[dict[str, Any]],
        models: list[dict[str, Any]],
        safety: SafetyDecision,
        settings_snapshot: dict[str, Any],
    ) -> bool:
        try:
            await self.storage.create_session(
                {
                    "id": session_id,
                    "umo": umo,
                    "topic": topic,
                    "topic_hash": hashlib.sha256(
                        topic.encode("utf-8")
                    ).hexdigest(),
                    "status": "running",
                    "render_mode": settings["output"]["render_mode"],
                    "search_mode": settings["search"]["mode"],
                    "safety_decision": safety.decision,
                    "safety_risk": safety.risk_level,
                    "safety_categories": safety.categories,
                    "safety_reason": safety.reason,
                    "members": public_members,
                    "models": models,
                    "settings_snapshot": settings_snapshot,
                    "created_at": utc_now_iso(),
                }
            )
            return True
        except Exception:  # noqa: BLE001
            logger.exception("council storage create failed")
            return False

    async def _create_refused_session(
        self,
        *,
        session_id: str,
        umo: str,
        topic: str,
        settings: dict[str, Any],
        safety: SafetyDecision,
        started_at: float,
    ) -> None:
        store_topic = bool(settings["safety"].get("store_refused_topic", False))
        try:
            await self.storage.create_session(
                {
                    "id": session_id,
                    "umo": umo,
                    "topic": topic if store_topic else "",
                    "topic_hash": hashlib.sha256(
                        topic.encode("utf-8")
                    ).hexdigest(),
                    "status": "refused",
                    "stop_reason": "safety_refused",
                    "render_mode": settings["output"]["render_mode"],
                    "search_mode": settings["search"]["mode"],
                    "safety_decision": "refuse",
                    "safety_risk": safety.risk_level,
                    "safety_categories": safety.categories,
                    "safety_reason": safety.reason,
                    "members": [],
                    "models": [],
                    "settings_snapshot": {
                        "safety": settings["safety"],
                    },
                    "created_at": utc_now_iso(),
                }
            )
            await self.storage.finish_session(
                session_id,
                status="refused",
                stop_reason="safety_refused",
                summary="",
                error="",
                duration_ms=int((time.monotonic() - started_at) * 1000),
                input_tokens=0,
                output_tokens=0,
                rounds=0,
                speech_count=0,
            )
            await self._cleanup_history()
        except Exception:  # noqa: BLE001
            logger.exception("council storage refused session failed")

    async def _safe_add_speech(self, speech: dict[str, Any]) -> None:
        try:
            await self.storage.add_speech(speech)
        except Exception:  # noqa: BLE001
            logger.exception("council storage add_speech failed")

    async def _safe_add_sources(
        self, session_id: str, sources: list[dict[str, Any]]
    ) -> None:
        try:
            await self.storage.add_sources(session_id, sources)
        except Exception:  # noqa: BLE001
            logger.exception("council storage add_sources failed")

    async def _safe_finish(self, session_id: str, **fields: Any) -> None:
        try:
            await self.storage.finish_session(session_id, **fields)
        except Exception:  # noqa: BLE001
            logger.exception("council storage finish failed")

    async def _finish_cancelled(
        self, run: ActiveRun, started_at: float
    ) -> None:
        await self._safe_finish(
            run.session_id,
            status="cancelled",
            stop_reason="cancelled",
            summary="",
            error="",
            duration_ms=int((time.monotonic() - started_at) * 1000),
            input_tokens=run.total_tokens,
            output_tokens=0,
            rounds=run.round_no,
            speech_count=run.speech_count,
        )
        await self._cleanup_history()

    async def _cleanup_history(self) -> None:
        try:
            limit = int(self._config().get("history_limit", 500) or 500)
            await self.storage.cleanup(limit)
        except Exception:  # noqa: BLE001
            logger.exception("council storage cleanup failed")


def validate_runtime_settings(
    data: dict[str, Any],
    *,
    provider_ids: set[str] | None = None,
    persona_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Small wrapper used by tests and the Web API."""
    return validate_settings(
        data, provider_ids=provider_ids, persona_ids=persona_ids
    )
