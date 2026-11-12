from __future__ import annotations

import copy
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

SETTINGS_VERSION = 1

DEFAULT_REFUSE_MESSAGE = (
    "该议题可能涉及平台不允许的内容，本次不展开讨论。请换一个议题。"
)

DEFAULT_SETTINGS: dict[str, Any] = {
    "version": SETTINGS_VERSION,
    "members": [
        {
            "display_name": "推进派",
            "persona_id": "default",
            "provider_id": "",
            "role": "从收益、机会与可行性角度推进讨论",
            "stance": "倾向支持，但必须正面回应风险",
        },
        {
            "display_name": "审慎派",
            "persona_id": "default",
            "provider_id": "",
            "role": "从风险、反例与最坏情况角度审视议题",
            "stance": "倾向审慎，但不为反对而反对",
        },
        {
            "display_name": "务实派",
            "persona_id": "default",
            "provider_id": "",
            "role": "从成本、执行条件与落地路径角度给出方案",
            "stance": "不预设立场，优先可执行性",
        },
    ],
    "discussion": {
        "rounds": 3,
        "max_total_tokens": 36000,
        "max_tokens_per_speech": 500,
        "max_context_chars": 8000,
        "request_timeout_sec": 90,
        "max_duration_sec": 300,
        "cooldown_sec": 30,
        "summary_max_tokens": 800,
    },
    "output": {
        "render_mode": "image",
        "max_text_chars": 12000,
    },
    "search": {
        "mode": "native",
        "native_rounds": 1,
        "max_results": 5,
        "timeout_sec": 20,
        "language": "auto",
        "anysearch_api_key": "",
    },
    "safety": {
        "mode": "standard",
        "provider_id": "",
        "extra_rules": "",
        "fail_mode": "closed",
        "store_refused_topic": False,
        "refuse_message": DEFAULT_REFUSE_MESSAGE,
        "max_tokens": 220,
    },
    "moderator": {
        "provider_id": "",
    },
}


class SettingsError(ValueError):
    """Raised when WebUI-managed settings are invalid."""


def default_settings() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_SETTINGS)


def _deep_merge(base: dict[str, Any], value: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, item in value.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(item, dict)
        ):
            result[key] = _deep_merge(result[key], item)
        else:
            result[key] = copy.deepcopy(item)
    return result


def _need_dict(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SettingsError(f"{name} 必须是对象")
    return value


def _need_list(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise SettingsError(f"{name} 必须是列表")
    return value


def _as_int(value: object, name: str, low: int, high: int) -> int:
    if isinstance(value, bool):
        raise SettingsError(f"{name} 必须是整数")
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise SettingsError(f"{name} 必须是整数") from exc
    if number < low or number > high:
        raise SettingsError(f"{name} 必须在 {low}～{high} 之间")
    return number


def _as_bool(value: object, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lower = value.strip().lower()
        if lower in {"true", "1", "yes", "on"}:
            return True
        if lower in {"false", "0", "no", "off"}:
            return False
    raise SettingsError(f"{name} 必须是布尔值")


def _as_text(
    value: object,
    name: str,
    *,
    max_len: int,
    allow_empty: bool = True,
) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise SettingsError(f"{name} 必须是字符串")
    text = value.strip()
    if not allow_empty and not text:
        raise SettingsError(f"{name} 不能为空")
    if len(text) > max_len:
        raise SettingsError(f"{name} 不能超过 {max_len} 个字符")
    return text


def _as_choice(value: object, name: str, choices: set[str]) -> str:
    text = _as_text(value, name, max_len=40, allow_empty=False)
    if text not in choices:
        allowed = " / ".join(sorted(choices))
        raise SettingsError(f"{name} 必须是以下之一：{allowed}")
    return text


def validate_settings(
    data: object,
    *,
    provider_ids: set[str] | None = None,
    persona_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Validate and normalize WebUI-managed settings.

    The returned mapping is a fresh, JSON-serializable dict.
    """
    raw = _need_dict(data, "settings")
    merged = _deep_merge(DEFAULT_SETTINGS, raw)

    members_raw = _need_list(merged.get("members"), "members")
    if len(members_raw) < 2 or len(members_raw) > 6:
        raise SettingsError("讨论成员数量必须在 2～6 人之间")

    members: list[dict[str, str]] = []
    seen_names: set[str] = set()
    for index, item in enumerate(members_raw, 1):
        member = _need_dict(item, f"members[{index}]")
        display_name = _as_text(
            member.get("display_name"),
            f"members[{index}].display_name",
            max_len=30,
            allow_empty=False,
        )
        key = display_name.casefold()
        if key in seen_names:
            raise SettingsError(f"成员显示名重复：{display_name}")
        seen_names.add(key)

        persona_id = _as_text(
            member.get("persona_id"),
            f"members[{index}].persona_id",
            max_len=255,
        ) or "default"
        provider_id = _as_text(
            member.get("provider_id"),
            f"members[{index}].provider_id",
            max_len=255,
        )
        role = _as_text(
            member.get("role"),
            f"members[{index}].role",
            max_len=200,
        )
        stance = _as_text(
            member.get("stance"),
            f"members[{index}].stance",
            max_len=100,
        )

        if persona_ids is not None and persona_id != "default":
            if persona_id not in persona_ids:
                raise SettingsError(
                    f"成员 {display_name} 引用的人格不存在：{persona_id}"
                )
        if provider_ids is not None and provider_id:
            if provider_id not in provider_ids:
                raise SettingsError(
                    f"成员 {display_name} 引用的提供商不存在：{provider_id}"
                )

        members.append(
            {
                "display_name": display_name,
                "persona_id": persona_id,
                "provider_id": provider_id,
                "role": role,
                "stance": stance,
            }
        )

    discussion_raw = _need_dict(merged.get("discussion"), "discussion")
    rounds = _as_int(discussion_raw.get("rounds"), "discussion.rounds", 1, 6)
    discussion = {
        "rounds": rounds,
        "max_total_tokens": _as_int(
            discussion_raw.get("max_total_tokens"),
            "discussion.max_total_tokens",
            4000,
            200000,
        ),
        "max_tokens_per_speech": _as_int(
            discussion_raw.get("max_tokens_per_speech"),
            "discussion.max_tokens_per_speech",
            128,
            2048,
        ),
        "max_context_chars": _as_int(
            discussion_raw.get("max_context_chars"),
            "discussion.max_context_chars",
            2000,
            30000,
        ),
        "request_timeout_sec": _as_int(
            discussion_raw.get("request_timeout_sec"),
            "discussion.request_timeout_sec",
            10,
            300,
        ),
        "max_duration_sec": _as_int(
            discussion_raw.get("max_duration_sec"),
            "discussion.max_duration_sec",
            30,
            1800,
        ),
        "cooldown_sec": _as_int(
            discussion_raw.get("cooldown_sec"),
            "discussion.cooldown_sec",
            0,
            3600,
        ),
        "summary_max_tokens": _as_int(
            discussion_raw.get("summary_max_tokens"),
            "discussion.summary_max_tokens",
            256,
            2048,
        ),
    }

    output_raw = _need_dict(merged.get("output"), "output")
    output = {
        "render_mode": _as_choice(
            output_raw.get("render_mode"),
            "output.render_mode",
            {"image", "text", "both"},
        ),
        "max_text_chars": _as_int(
            output_raw.get("max_text_chars"),
            "output.max_text_chars",
            1000,
            50000,
        ),
    }

    search_raw = _need_dict(merged.get("search"), "search")
    search = {
        "mode": _as_choice(
            search_raw.get("mode"),
            "search.mode",
            {"native", "off", "prefetch"},
        ),
        "native_rounds": _as_int(
            search_raw.get("native_rounds"),
            "search.native_rounds",
            1,
            rounds,
        ),
        "max_results": _as_int(
            search_raw.get("max_results"),
            "search.max_results",
            1,
            10,
        ),
        "timeout_sec": _as_int(
            search_raw.get("timeout_sec"),
            "search.timeout_sec",
            5,
            60,
        ),
        "language": _as_choice(
            search_raw.get("language"),
            "search.language",
            {"auto", "zh", "en"},
        ),
        "anysearch_api_key": _as_text(
            search_raw.get("anysearch_api_key"),
            "search.anysearch_api_key",
            max_len=500,
        ),
    }

    safety_raw = _need_dict(merged.get("safety"), "safety")
    safety_provider_id = _as_text(
        safety_raw.get("provider_id"),
        "safety.provider_id",
        max_len=255,
    )
    if provider_ids is not None and safety_provider_id:
        if safety_provider_id not in provider_ids:
            raise SettingsError(
                f"safety.provider_id 引用的提供商不存在：{safety_provider_id}"
            )
    safety = {
        "mode": _as_choice(
            safety_raw.get("mode"),
            "safety.mode",
            {"off", "standard", "strict"},
        ),
        "provider_id": safety_provider_id,
        "extra_rules": _as_text(
            safety_raw.get("extra_rules"),
            "safety.extra_rules",
            max_len=2000,
        ),
        "fail_mode": _as_choice(
            safety_raw.get("fail_mode"),
            "safety.fail_mode",
            {"closed", "open"},
        ),
        "store_refused_topic": _as_bool(
            safety_raw.get("store_refused_topic"),
            "safety.store_refused_topic",
        ),
        "refuse_message": _as_text(
            safety_raw.get("refuse_message"),
            "safety.refuse_message",
            max_len=200,
            allow_empty=False,
        ),
        "max_tokens": _as_int(
            safety_raw.get("max_tokens"),
            "safety.max_tokens",
            64,
            512,
        ),
    }

    moderator_raw = _need_dict(merged.get("moderator"), "moderator")
    moderator_provider_id = _as_text(
        moderator_raw.get("provider_id"),
        "moderator.provider_id",
        max_len=255,
    )
    if provider_ids is not None and moderator_provider_id:
        if moderator_provider_id not in provider_ids:
            raise SettingsError(
                "moderator.provider_id 引用的提供商不存在："
                f"{moderator_provider_id}"
            )

    return {
        "version": SETTINGS_VERSION,
        "members": members,
        "discussion": discussion,
        "output": output,
        "search": search,
        "safety": safety,
        "moderator": {"provider_id": moderator_provider_id},
    }


class SettingsStore:
    """JSON-backed settings store with atomic writes and a best-effort backup."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self.last_error = ""

    def load(self) -> dict[str, Any]:
        with self._lock:
            if not self.path.exists():
                settings = default_settings()
                try:
                    self.save(settings)
                except OSError:
                    pass
                return settings
            try:
                with self.path.open("r", encoding="utf-8") as handle:
                    data = json.load(handle)
                settings = validate_settings(data)
                return settings
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                self.last_error = str(exc)
                self._quarantine_bad_file()
                return default_settings()

    def save(self, data: object, **validation_kwargs: Any) -> dict[str, Any]:
        settings = validate_settings(data, **validation_kwargs)
        payload = json.dumps(settings, ensure_ascii=False, indent=2) + "\n"
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(
                f".{self.path.name}.tmp-{os.getpid()}-{int(time.time() * 1000)}"
            )
            try:
                tmp.write_text(payload, encoding="utf-8")
                if self.path.exists():
                    backup = self.path.with_suffix(self.path.suffix + ".bak")
                    try:
                        backup.write_text(
                            self.path.read_text(encoding="utf-8"),
                            encoding="utf-8",
                        )
                    except OSError:
                        pass
                os.replace(tmp, self.path)
                try:
                    os.chmod(self.path, 0o600)
                except OSError:
                    pass
            finally:
                if tmp.exists():
                    try:
                        tmp.unlink()
                    except OSError:
                        pass
        return settings

    def reset(self, **validation_kwargs: Any) -> dict[str, Any]:
        return self.save(default_settings(), **validation_kwargs)

    def public_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        """Return a copy safe to send to the browser (secret masked)."""
        public = copy.deepcopy(settings)
        key = str(public.get("search", {}).get("anysearch_api_key", ""))
        public.setdefault("search", {})["anysearch_api_key"] = ""
        public["search"]["has_anysearch_api_key"] = bool(key)
        return public

    def _quarantine_bad_file(self) -> None:
        if not self.path.exists():
            return
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = self.path.with_name(f"{self.path.name}.invalid-{stamp}")
        try:
            os.replace(self.path, target)
        except OSError:
            pass
