from __future__ import annotations

from collections.abc import Iterable


def normalize_umo_list(value: object) -> list[str]:
    """Normalize a UMO list from plugin config.

    Supports the normal list form, a comma separated string and None.
    """
    if value is None:
        return []
    if isinstance(value, str):
        raw = value.replace("\n", ",").split(",")
    elif isinstance(value, Iterable):
        raw = list(value)
    else:
        raw = [value]
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def access_decision(
    umo: str,
    allowed_umos: object,
    blocked_umos: object,
) -> tuple[bool, str]:
    """Return (allowed, reason) for a unified message origin.

    Rules:
    - blocked always wins;
    - a non-empty allow-list enables only those sessions;
    - an empty allow-list means "no restriction".
    """
    umo = str(umo or "").strip()
    allowed = normalize_umo_list(allowed_umos)
    blocked = normalize_umo_list(blocked_umos)

    if not umo:
        return False, "missing_umo"
    if umo in blocked:
        return False, "blocked"
    if allowed and umo not in allowed:
        return False, "not_whitelisted"
    return True, "allowed"
