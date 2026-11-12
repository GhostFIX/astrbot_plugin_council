from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from .budget import estimate_tokens
from .prompts import build_safety_prompt, parse_safety_decision


@dataclass
class SafetyDecision:
    decision: str
    risk_level: str
    categories: list[str] = field(default_factory=list)
    reason: str = ""
    skipped: bool = False
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "risk_level": self.risk_level,
            "categories": self.categories,
            "reason": self.reason,
            "skipped": self.skipped,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


class SafetyReviewer:
    def __init__(self, context: Any):
        self.context = context

    async def review(
        self,
        *,
        topic: str,
        umo: str,
        settings: dict[str, Any],
        provider_id: str = "",
    ) -> SafetyDecision:
        safety = settings.get("safety", {})
        mode = str(safety.get("mode", "standard"))
        if mode == "off":
            return SafetyDecision(
                decision="allow",
                risk_level="low",
                reason="安全审核已关闭",
                skipped=True,
            )

        provider = (
            str(safety.get("provider_id", "")).strip()
            or provider_id
            or str(settings.get("moderator", {}).get("provider_id", "")).strip()
        )
        if not provider:
            try:
                provider = await self.context.get_current_chat_provider_id(umo)
            except Exception:  # noqa: BLE001
                provider = ""
        if not provider:
            return self._failure(safety, "没有可用的安全审核模型")

        system, user = build_safety_prompt(
            topic=topic,
            mode=mode,
            extra_rules=str(safety.get("extra_rules", "")),
            system_prompt=str(safety.get("system_prompt", "")),
        )
        timeout = max(
            10,
            int(settings.get("discussion", {}).get("request_timeout_sec", 90)),
        )
        try:
            response = await self._call_safety_model(
                provider=provider,
                system=system,
                user=user,
                max_tokens=int(safety.get("max_tokens", 220)),
                timeout=min(timeout, 45),
            )
        except Exception as exc:  # noqa: BLE001 - moderation must not crash the plugin
            return self._failure(safety, f"安全审核调用失败：{type(exc).__name__}")

        text = str(getattr(response, "completion_text", "") or "")
        parsed = parse_safety_decision(text)
        if parsed is None:
            return self._failure(safety, "安全审核返回格式无法解析")

        decision = str(parsed["decision"])
        risk = str(parsed["risk_level"])
        input_tokens = estimate_tokens(system) + estimate_tokens(user)
        output_tokens = estimate_tokens(text)
        if decision == "refuse":
            return SafetyDecision(
                decision="refuse",
                risk_level=risk,
                categories=list(parsed["categories"]),
                reason=str(parsed["reason"] or "议题触发安全规则"),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        if risk == "high":
            return SafetyDecision(
                decision="refuse",
                risk_level="high",
                categories=list(parsed["categories"]),
                reason=str(parsed["reason"] or "议题风险等级为高"),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        return SafetyDecision(
            decision="allow",
            risk_level=risk,
            categories=list(parsed["categories"]),
            reason=str(parsed["reason"] or ""),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    async def _call_safety_model(
        self,
        *,
        provider: str,
        system: str,
        user: str,
        max_tokens: int,
        timeout: int,
    ) -> Any:
        kwargs: dict[str, Any] = {
            "chat_provider_id": provider,
            "prompt": user,
            "system_prompt": system,
            "max_tokens": max_tokens,
            "temperature": 0,
        }
        try:
            return await asyncio.wait_for(
                self.context.llm_generate(**kwargs), timeout=timeout
            )
        except TypeError:
            kwargs.pop("temperature", None)
            try:
                return await asyncio.wait_for(
                    self.context.llm_generate(**kwargs), timeout=timeout
                )
            except TypeError:
                kwargs.pop("max_tokens", None)
                return await asyncio.wait_for(
                    self.context.llm_generate(**kwargs), timeout=timeout
                )

    @staticmethod
    def _failure(safety: dict[str, Any], reason: str) -> SafetyDecision:
        if str(safety.get("fail_mode", "closed")) == "open":
            return SafetyDecision(
                decision="allow",
                risk_level="unknown",
                categories=["review_unavailable"],
                reason=f"审核不可用，已按 fail-open 放行：{reason}",
            )
        return SafetyDecision(
            decision="refuse",
            risk_level="unknown",
            categories=["review_unavailable"],
            reason=f"审核暂不可用，已按 fail-closed 拒绝：{reason}",
        )
