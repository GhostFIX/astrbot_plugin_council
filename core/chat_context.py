from __future__ import annotations

from datetime import datetime
from typing import Any

MAX_MESSAGE_CHARS = 500


def _clean_text(value: object, max_chars: int = MAX_MESSAGE_CHARS) -> str:
    text = str(value or "").replace("\x00", "").strip()
    text = " ".join(text.split())
    if text.startswith("/"):
        return ""
    if text.casefold().startswith("council"):
        return ""
    return text[:max_chars]


def _time_label(value: object) -> str:
    try:
        stamp = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""
    if stamp <= 0:
        return ""
    try:
        return datetime.fromtimestamp(stamp).strftime("%H:%M")
    except (OSError, OverflowError, ValueError):
        return ""


def _component_text(component: Any) -> str:
    text = str(getattr(component, "message_str", "") or "").strip()
    if text:
        return text
    chain = getattr(component, "chain", None)
    if isinstance(chain, list):
        parts: list[str] = []
        for item in chain:
            value = getattr(item, "text", None)
            if value:
                parts.append(str(value))
        return "".join(parts)
    return ""


def extract_quote_entries(components: list[Any] | None) -> list[dict[str, str]]:
    """Extract a quoted/replied message from AstrBot message components."""
    entries: list[dict[str, str]] = []
    for component in components or []:
        type_name = str(getattr(component, "type", "") or "").lower()
        class_name = type(component).__name__.lower()
        if "reply" not in type_name and class_name != "reply":
            continue
        text = _clean_text(_component_text(component))
        if not text:
            continue
        sender = str(
            getattr(component, "sender_nickname", "")
            or getattr(component, "sender_id", "")
            or "群友"
        ).strip()
        entries.append(
            {
                "sender": sender[:40] or "群友",
                "text": text,
                "time": _time_label(getattr(component, "time", 0)),
            }
        )
    return entries


def parse_onebot_history(
    payload: Any,
    *,
    self_id: str = "",
    max_messages: int = 30,
) -> list[dict[str, str]]:
    """Parse a OneBot `get_group_msg_history` response.

    The parser intentionally only keeps plain text. Unsupported or malformed
    payloads return an empty list instead of raising.
    """
    if not isinstance(payload, dict):
        return []
    rows = payload.get("messages")
    if rows is None:
        data = payload.get("data")
        if isinstance(data, dict):
            rows = data.get("messages")
        elif isinstance(data, list):
            rows = data
    if not isinstance(rows, list):
        return []

    parsed: list[tuple[int, dict[str, str]]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        sender = row.get("sender")
        sender_id = ""
        sender_name = "群友"
        if isinstance(sender, dict):
            sender_id = str(sender.get("user_id") or sender.get("id") or "")
            sender_name = str(
                sender.get("card")
                or sender.get("nickname")
                or sender_id
                or "群友"
            )
        if self_id and sender_id and sender_id == str(self_id):
            continue

        segments = row.get("message")
        if not isinstance(segments, list):
            segments = row.get("message_chain")
        if not isinstance(segments, list):
            continue
        text_parts: list[str] = []
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            segment_type = str(segment.get("type") or "")
            if segment_type != "text":
                continue
            data = segment.get("data")
            if isinstance(data, dict) and data.get("text"):
                text_parts.append(str(data["text"]))
        text = _clean_text("".join(text_parts))
        if not text:
            continue
        stamp = 0
        try:
            stamp = int(row.get("time") or 0)
        except (TypeError, ValueError):
            stamp = 0
        parsed.append(
            (
                stamp,
                {
                    "sender": sender_name[:40] or "群友",
                    "text": text,
                    "time": _time_label(stamp),
                },
            )
        )

    parsed.sort(key=lambda item: item[0])
    return [item[1] for item in parsed[-max(1, int(max_messages)) :]]


def merge_context(
    *groups: list[dict[str, str]],
    max_messages: int = 30,
) -> list[dict[str, str]]:
    """Merge quote/history entries, keeping the newest and removing duplicates."""
    merged: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for group in groups:
        for item in group:
            key = (str(item.get("sender", "")), str(item.get("text", "")))
            if key in seen:
                continue
            seen.add(key)
            merged.append(
                {
                    "sender": str(item.get("sender", "群友"))[:40],
                    "text": str(item.get("text", ""))[:MAX_MESSAGE_CHARS],
                    "time": str(item.get("time", ""))[:8],
                }
            )
    return merged[-max(1, int(max_messages)) :]
