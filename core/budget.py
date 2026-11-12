from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

_CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff\u3000-\u303f]")


def estimate_tokens(text: str | None) -> int:
    """Conservative token estimate for mixed Chinese/English text.

    DeepSeek-style tokenizers usually use fewer tokens for Chinese than a
    character, but councils also contain punctuation, names and URLs.  This
    estimator deliberately rounds up so the budget guard stops early rather
    than overshooting.
    """
    if not text:
        return 0
    text = str(text)
    cjk = len(_CJK_RE.findall(text))
    other = max(0, len(text) - cjk)
    estimate = cjk * 0.70 + other / 3.5 + 12
    return max(1, int(math.ceil(estimate)))


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    @classmethod
    def from_response(cls, usage: Any) -> "TokenUsage | None":
        if usage is None:
            return None
        try:
            if hasattr(usage, "total"):
                total = int(getattr(usage, "total") or 0)
                input_tokens = int(getattr(usage, "input", 0) or 0)
                output_tokens = int(getattr(usage, "output", 0) or 0)
                if total > 0:
                    return cls(max(0, input_tokens), max(0, output_tokens))
            if isinstance(usage, dict):
                inp = int(
                    usage.get("input")
                    or usage.get("input_tokens")
                    or usage.get("prompt_tokens")
                    or 0
                )
                out = int(
                    usage.get("output")
                    or usage.get("output_tokens")
                    or usage.get("completion_tokens")
                    or 0
                )
                if inp or out:
                    return cls(max(0, inp), max(0, out))
        except (TypeError, ValueError):
            return None
        return None


@dataclass
class TokenBudget:
    max_total: int
    max_output_per_call: int
    reserve: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0
    stopped_reason: str = ""
    snapshots: list[dict[str, int]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.max_total < 1:
            self.max_total = 1
        if self.reserve <= 0:
            self.reserve = min(2000, max(800, self.max_total // 20))

    @property
    def used_total(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def remaining(self) -> int:
        return max(0, self.max_total - self.used_total)

    @property
    def available_without_reserve(self) -> int:
        return max(0, self.max_total - self.reserve - self.used_total)

    def can_afford(
        self,
        prompt: str,
        max_output: int,
        *,
        extra: int = 0,
        use_reserve: bool = False,
    ) -> bool:
        projected = estimate_tokens(prompt) + max(0, max_output) + max(0, extra)
        limit = self.max_total if use_reserve else self.max_total - self.reserve
        return self.used_total + projected <= limit

    def add(
        self,
        *,
        prompt: str = "",
        output: str = "",
        usage: Any = None,
        overhead: int = 0,
        call: bool = True,
    ) -> TokenUsage:
        parsed = TokenUsage.from_response(usage)
        if parsed is not None and parsed.total > 0:
            used = parsed
        else:
            used = TokenUsage(
                input_tokens=estimate_tokens(prompt),
                output_tokens=estimate_tokens(output),
            )
        used.input_tokens += max(0, int(overhead))
        self.input_tokens += used.input_tokens
        self.output_tokens += used.output_tokens
        if call:
            self.calls += 1
        self.snapshots.append(
            {
                "calls": self.calls,
                "input": self.input_tokens,
                "output": self.output_tokens,
                "total": self.used_total,
            }
        )
        return used

    def add_external(self, input_tokens: int, output_tokens: int) -> None:
        """Account for a call made outside the normal discussion loop."""
        self.input_tokens += max(0, int(input_tokens))
        self.output_tokens += max(0, int(output_tokens))
        self.calls += 1
        self.snapshots.append(
            {
                "calls": self.calls,
                "input": self.input_tokens,
                "output": self.output_tokens,
                "total": self.used_total,
            }
        )

    def mark_stopped(self, reason: str) -> None:
        if not self.stopped_reason:
            self.stopped_reason = reason

    def to_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.used_total,
            "calls": self.calls,
            "max_total_tokens": self.max_total,
            "reserve_tokens": self.reserve,
        }
