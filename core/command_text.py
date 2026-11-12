from __future__ import annotations

import re

_COMMAND_RE = re.compile(
    r"^\s*[/!！]?\s*council(?:@\S+)?(?:\s+|$)",
    flags=re.IGNORECASE,
)
_COMMAND_SEARCH_RE = re.compile(
    r"(?:^|\s)council(?:@\S+)?(?:\s+|$)",
    flags=re.IGNORECASE,
)


def is_council_command(raw: object) -> bool:
    text = str(raw or "").strip()
    return bool(_COMMAND_RE.match(text))


def is_stop_command(raw: object) -> bool:
    text = str(raw or "").strip()
    match = _COMMAND_RE.match(text)
    if match is None:
        match = _COMMAND_SEARCH_RE.search(text)
    if match is not None:
        return text[match.end() :].strip().casefold() == "stop"
    return text.casefold() == "stop"


def extract_topic(raw: object, fallback: str = "") -> str:
    text = str(raw or "").strip()
    match = _COMMAND_RE.match(text)
    if match is None:
        match = _COMMAND_SEARCH_RE.search(text)
    if match is not None:
        return text[match.end() :].strip()
    if text and not fallback:
        return text
    return text or str(fallback or "").strip()
