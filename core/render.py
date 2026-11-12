from __future__ import annotations

import html
from pathlib import Path
from typing import Any


def h(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def format_duration(duration_ms: int | float | None) -> str:
    ms = max(0, int(duration_ms or 0))
    seconds = ms // 1000
    if seconds < 60:
        return f"{seconds} 秒"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} 分 {seconds} 秒"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} 小时 {minutes} 分"


def format_tokens(tokens: int | float | None) -> str:
    value = max(0, int(tokens or 0))
    if value >= 1000:
        return f"{value / 1000:.1f}k"
    return str(value)


def split_text(text: str, max_chars: int = 1800) -> list[str]:
    text = str(text or "").strip()
    if not text:
        return []
    if max_chars < 200:
        max_chars = 200
    chunks: list[str] = []
    current = ""
    for paragraph in text.split("\n"):
        candidate = paragraph if not current else current + "\n" + paragraph
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        if len(paragraph) <= max_chars:
            current = paragraph
            continue
        # Split an overly long paragraph on character boundaries.
        for index in range(0, len(paragraph), max_chars):
            chunks.append(paragraph[index : index + max_chars])
    if current:
        chunks.append(current)
    return [chunk for chunk in chunks if chunk.strip()]


def format_summary_text(
    result: dict[str, Any], max_chars: int = 12000
) -> str:
    topic = str(result.get("topic") or "").strip()
    summary = str(result.get("summary") or "").strip()
    if not summary:
        summary = "本次讨论未生成有效总结。"
    lines = [
        "【AI 圆桌 · 讨论总结】",
        f"议题：{topic}",
        "",
        summary,
        "",
        "——",
        "轮数：{rounds}　发言：{speeches}　Token：约 {tokens}　用时：{duration}".format(
            rounds=result.get("rounds", 0),
            speeches=result.get("speech_count", 0),
            tokens=format_tokens(result.get("total_tokens", 0)),
            duration=format_duration(result.get("duration_ms", 0)),
        ),
    ]
    if result.get("stop_reason") and result.get("stop_reason") != "completed":
        lines.append(f"停止原因：{result.get('stop_reason')}")
    text = "\n".join(lines).strip()
    if max_chars > 0 and len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n…（总结过长，已截断）"
    return text


def build_html_fragments(result: dict[str, Any]) -> dict[str, str]:
    topic = str(result.get("topic") or "").strip() or "（无议题）"
    summary = str(result.get("summary") or "").strip() or "本次讨论未生成有效总结。"
    members = result.get("members") or []
    sources = result.get("sources") or []

    member_chips: list[str] = []
    for member in members:
        if not isinstance(member, dict):
            continue
        name = str(member.get("display_name") or member.get("persona_id") or "成员")
        model = str(member.get("provider_model") or "默认模型")
        initial = name[:1]
        member_chips.append(
            '<span class="member">'
            f'<span class="avatar">{h(initial)}</span>'
            f'<span class="member-text"><b>{h(name)}</b><small>{h(model)}</small></span>'
            "</span>"
        )
    members_html = "".join(member_chips)

    source_lines: list[str] = []
    for item in sources:
        if not isinstance(item, dict):
            continue
        idx = item.get("idx", "")
        title = str(item.get("title") or item.get("url") or "来源")
        url = str(item.get("url") or "")
        source_lines.append(
            '<li>'
            f'<span class="source-idx">S{idx}</span>'
            f'<span class="source-title">{h(title)}</span>'
            f'<span class="source-url">{h(url)}</span>'
            "</li>"
        )
    sources_html = ""
    if source_lines:
        sources_html = (
            '<section class="card sources">'
            '<div class="card-label">参考来源</div>'
            f'<ul>{"".join(source_lines)}</ul>'
            "</section>"
        )

    meta_html = (
        f"<span>轮数 {h(result.get('rounds', 0))}</span>"
        f"<span>发言 {h(result.get('speech_count', 0))}</span>"
        f"<span>约 {h(format_tokens(result.get('total_tokens', 0)))} tokens</span>"
        f"<span>用时 {h(format_duration(result.get('duration_ms', 0)))}</span>"
    )
    if result.get("stop_reason") and result.get("stop_reason") != "completed":
        meta_html += f"<span>停止于 {h(result.get('stop_reason'))}</span>"

    return {
        "topic_html": h(topic),
        "members_html": members_html,
        "summary_html": h(summary),
        "sources_html": sources_html,
        "meta_html": meta_html,
    }


class CouncilRenderer:
    def __init__(self, plugin_dir: Path):
        self.plugin_dir = Path(plugin_dir)
        self.template_path = self.plugin_dir / "templates" / "council.html"
        self._template_cache: str | None = None

    def template(self) -> str:
        if self._template_cache is None:
            self._template_cache = self.template_path.read_text(encoding="utf-8")
        return self._template_cache

    async def render_image(self, html_render: Any, result: dict[str, Any]) -> str:
        fragments = build_html_fragments(result)
        return await html_render(
            self.template(),
            fragments,
            options={
                "full_page": True,
                "type": "jpeg",
                "quality": 90,
                "timeout": 60000,
            },
        )
