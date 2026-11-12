from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timezone
from typing import Any

from .constants import REPO_SLUG, REPO_URL
from .version import display_version, is_newer, normalize_version

_REPO = REPO_SLUG
_REPO_URL = REPO_URL
_API_BASE = f"https://api.github.com/repos/{_REPO}"
_RAW_METADATA = (
    f"https://raw.githubusercontent.com/{_REPO}/main/metadata.yaml"
)
_VERSION_LINE = re.compile(
    r"(?m)^\s*version\s*:\s*[\"']?([^\"'\s#]+)"
)
_CACHE_SECONDS = 6 * 60 * 60


class UpdateChecker:
    """Check the public GitHub release/tag/metadata version on demand.

    Results are cached in memory. No API key or token is required, and the
    plugin never downloads or applies an update automatically.
    """

    def __init__(self, *, current_version: str):
        self.current_version = str(current_version)
        self._cached: dict[str, Any] | None = None
        self._cached_at = 0.0
        self._lock = asyncio.Lock()

    async def check(self, *, force: bool = False) -> dict[str, Any]:
        async with self._lock:
            if (
                not force
                and self._cached is not None
                and time.monotonic() - self._cached_at < _CACHE_SECONDS
            ):
                return dict(self._cached)
            result = await self._check_remote()
            self._cached = result
            self._cached_at = time.monotonic()
            return dict(result)

    def _base_result(self) -> dict[str, Any]:
        return {
            "current": self.current_version,
            "current_display": display_version(self.current_version),
            "latest": "",
            "latest_display": "",
            "status": "unavailable",
            "url": _REPO_URL,
            "source": "",
            "checked_at": datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat(),
            "error": "",
        }

    async def _check_remote(self) -> dict[str, Any]:
        result = self._base_result()
        try:
            import aiohttp
        except ImportError:
            result["error"] = "aiohttp 不可用"
            return result

        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "astrbot-plugin-council-update-check",
        }
        timeout = aiohttp.ClientTimeout(total=12)
        try:
            async with aiohttp.ClientSession(
                trust_env=True, timeout=timeout, headers=headers
            ) as session:
                release = await self._github_json(
                    session, f"{_API_BASE}/releases/latest"
                )
                if isinstance(release, dict):
                    tag = str(release.get("tag_name") or "").strip()
                    if normalize_version(tag):
                        self._apply(
                            result,
                            tag,
                            str(release.get("html_url") or ""),
                            "release",
                        )
                        return result

                tags = await self._github_json(
                    session, f"{_API_BASE}/tags?per_page=100"
                )
                if isinstance(tags, list):
                    candidates = [
                        str(item.get("name") or "")
                        for item in tags
                        if isinstance(item, dict)
                    ]
                    candidates = [
                        item for item in candidates if normalize_version(item)
                    ]
                    if candidates:
                        candidates.sort(
                            key=lambda item: normalize_version(item) or (0,),
                            reverse=True,
                        )
                        self._apply(
                            result,
                            candidates[0],
                            f"{_REPO_URL}/releases/tag/{candidates[0]}",
                            "tag",
                        )
                        return result

                raw = await self._github_text(session, _RAW_METADATA)
                match = _VERSION_LINE.search(raw or "")
                if not match:
                    raw = await self._github_text(
                        session,
                        f"https://raw.githubusercontent.com/{_REPO}/master/metadata.yaml",
                    )
                    match = _VERSION_LINE.search(raw or "")
                if match:
                    self._apply(
                        result, match.group(1).strip(), _REPO_URL, "metadata"
                    )
                    return result

            result["error"] = "GitHub 上没有找到版本信息"
            return result
        except Exception as exc:  # noqa: BLE001
            result["error"] = f"检查失败：{type(exc).__name__}"
            return result

    @staticmethod
    async def _github_json(session: Any, url: str) -> Any:
        async with session.get(url) as response:
            if response.status in {403, 429}:
                return None
            if response.status != 200:
                return None
            try:
                return await response.json(content_type=None)
            except Exception:  # noqa: BLE001
                return None

    @staticmethod
    async def _github_text(session: Any, url: str) -> str:
        async with session.get(url) as response:
            if response.status != 200:
                return ""
            try:
                return await response.text()
            except Exception:  # noqa: BLE001
                return ""

    def _apply(
        self,
        result: dict[str, Any],
        latest: str,
        url: str,
        source: str,
    ) -> None:
        result["latest"] = latest
        result["latest_display"] = display_version(latest)
        result["url"] = url or _REPO_URL
        result["source"] = source
        if is_newer(latest, self.current_version):
            result["status"] = "update_available"
        else:
            result["status"] = "up_to_date"
