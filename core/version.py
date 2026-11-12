from __future__ import annotations

import re

_VERSION_RE = re.compile(r"^[vV]?(\d+(?:\.\d+){0,3})(?:[-+].*)?$")


def normalize_version(value: object) -> tuple[int, ...] | None:
    text = str(value or "").strip()
    match = _VERSION_RE.match(text)
    if not match:
        return None
    parts = [int(item) for item in match.group(1).split(".")]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_newer(latest: object, current: object) -> bool:
    latest_key = normalize_version(latest)
    current_key = normalize_version(current)
    if latest_key is None or current_key is None:
        return False
    width = max(len(latest_key), len(current_key))
    latest_padded = latest_key + (0,) * (width - len(latest_key))
    current_padded = current_key + (0,) * (width - len(current_key))
    return latest_padded > current_padded


def display_version(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "未知"
    return text if text.lower().startswith("v") else f"v{text}"
