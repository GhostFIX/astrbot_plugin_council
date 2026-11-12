from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

ANYSEARCH_URL = "https://api.anysearch.com/v1/search"
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")


class SearchError(RuntimeError):
    def __init__(self, message: str, *, status: int = 0, request_id: str = ""):
        super().__init__(message)
        self.status = status
        self.request_id = request_id


@dataclass
class SearchSource:
    idx: int
    title: str
    url: str
    snippet: str


@dataclass
class SearchBundle:
    query: str
    sources: list[SearchSource] = field(default_factory=list)
    error: str = ""
    raw_count: int = 0

    @property
    def ok(self) -> bool:
        return bool(self.sources)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "sources": [
                {
                    "idx": item.idx,
                    "title": item.title,
                    "url": item.url,
                    "snippet": item.snippet,
                }
                for item in self.sources
            ],
            "error": self.error,
            "raw_count": self.raw_count,
        }


def detect_language(text: str, preference: str = "auto") -> str:
    if preference in {"zh", "en"}:
        return preference
    text = str(text or "")
    cjk = len(re.findall(r"[\u3400-\u9fff]", text))
    return "zh" if cjk >= max(2, len(text) * 0.15) else "en"


def clean_snippet(value: object, max_chars: int = 280) -> str:
    text = html.unescape(str(value or ""))
    text = _TAG_RE.sub(" ", text)
    text = _SPACE_RE.sub(" ", text).strip()
    return text[:max_chars]


def _valid_url(value: object) -> bool:
    try:
        parsed = urlparse(str(value or ""))
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


class AnySearchClient:
    """Small AnySearch client used by the optional prefetch mode.

    The API supports anonymous requests.  HTTP 402 bodies can contain generated
    credentials, so this client deliberately never logs or returns response
    bodies for non-2xx responses.
    """

    def __init__(self, *, timeout_sec: int = 20):
        self.timeout_sec = max(5, min(60, int(timeout_sec)))

    async def search(
        self,
        query: str,
        *,
        api_key: str = "",
        max_results: int = 5,
        language: str = "auto",
    ) -> SearchBundle:
        query = str(query or "").strip()[:128]
        bundle = SearchBundle(query=query)
        if not query:
            bundle.error = "empty query"
            return bundle

        try:
            import aiohttp
        except ImportError as exc:  # pragma: no cover - AstrBot always ships aiohttp
            bundle.error = f"aiohttp unavailable: {exc}"
            return bundle

        lang = detect_language(query, language)
        payload = {
            "query": query,
            "max_results": max(1, min(10, int(max_results))),
            "language": "zh-CN" if lang == "zh" else "en",
            "zone": "cn" if lang == "zh" else "intl",
        }
        headers = {"Content-Type": "application/json"}
        if api_key.strip():
            headers["Authorization"] = f"Bearer {api_key.strip()}"

        timeout = aiohttp.ClientTimeout(total=self.timeout_sec)
        try:
            async with aiohttp.ClientSession(
                trust_env=True, timeout=timeout
            ) as session:
                async with session.post(
                    ANYSEARCH_URL, json=payload, headers=headers
                ) as response:
                    request_id = response.headers.get("X-Request-ID", "")
                    if response.status != 200:
                        # Never read/log the body here: 402 may contain credentials.
                        raise SearchError(
                            f"AnySearch HTTP {response.status}",
                            status=response.status,
                            request_id=request_id,
                        )
                    try:
                        data = await response.json(content_type=None)
                    except Exception as exc:  # noqa: BLE001
                        raise SearchError(
                            f"AnySearch invalid JSON: {exc}",
                            status=response.status,
                            request_id=request_id,
                        ) from exc
        except SearchError as exc:
            bundle.error = str(exc)
            return bundle
        except Exception as exc:  # noqa: BLE001 - network errors are non-fatal
            bundle.error = f"AnySearch request failed: {type(exc).__name__}"
            return bundle

        data_block = data.get("data") if isinstance(data, dict) else {}
        if not isinstance(data_block, dict):
            data_block = {}
        rows = data_block.get("results", [])
        if not isinstance(rows, list):
            rows = []
        bundle.raw_count = len(rows)
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            url = str(row.get("url") or "").strip()
            if not _valid_url(url) or url in seen:
                continue
            seen.add(url)
            title = clean_snippet(row.get("title") or url, 160)
            snippet = clean_snippet(
                row.get("snippet") or row.get("content") or "", 280
            )
            bundle.sources.append(
                SearchSource(
                    idx=len(bundle.sources) + 1,
                    title=title or url,
                    url=url,
                    snippet=snippet,
                )
            )
            if len(bundle.sources) >= max_results:
                break
        if not bundle.sources and not bundle.error:
            bundle.error = "no results"
        return bundle


def format_search_card(bundle: SearchBundle, max_chars: int = 4500) -> str:
    if not bundle.sources:
        return ""
    lines: list[str] = []
    for item in bundle.sources:
        lines.append(f"[S{item.idx}] {item.title}")
        lines.append(f"URL: {item.url}")
        if item.snippet:
            lines.append(f"摘要: {item.snippet}")
        lines.append("")
    text = "\n".join(lines).strip()
    if len(text) > max_chars:
        text = text[:max_chars] + "\n…（资料卡已截断）"
    return text


def format_compact_sources(bundle: SearchBundle) -> str:
    if not bundle.sources:
        return ""
    return "\n".join(
        f"[S{item.idx}] {item.title} — {item.url}" for item in bundle.sources
    )


def sources_for_storage(bundle: SearchBundle) -> list[dict[str, Any]]:
    return [
        {
            "idx": item.idx,
            "title": item.title,
            "url": item.url,
            "snippet": item.snippet,
        }
        for item in bundle.sources
    ]
