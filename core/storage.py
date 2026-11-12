from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA_VERSION = 1
MAX_PAGE_SIZE = 100


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    for key in ("safety_categories", "members_json", "model_json"):
        if key in data:
            default = [] if key == "safety_categories" else []
            data[key] = _json_loads(data.get(key), default)
    return data


class CouncilStorage:
    """SQLite-backed history storage.

    A connection is opened per operation so the plugin can be reloaded without
    leaving a dangling connection in the event loop.
    """

    def __init__(self, path: Path, media_store: Any = None):
        self.path = Path(path)
        self.media_store = media_store
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._initialize_sync)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 10000")
        return conn

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _initialize_sync(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    umo TEXT NOT NULL,
                    topic TEXT NOT NULL DEFAULT '',
                    topic_hash TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'running',
                    stop_reason TEXT NOT NULL DEFAULT '',
                    render_mode TEXT NOT NULL DEFAULT 'image',
                    search_mode TEXT NOT NULL DEFAULT 'native',
                    safety_decision TEXT NOT NULL DEFAULT 'allow',
                    safety_risk TEXT NOT NULL DEFAULT 'low',
                    safety_categories TEXT NOT NULL DEFAULT '[]',
                    safety_reason TEXT NOT NULL DEFAULT '',
                    members_json TEXT NOT NULL DEFAULT '[]',
                    model_json TEXT NOT NULL DEFAULT '[]',
                    settings_json TEXT NOT NULL DEFAULT '{}',
                    rounds INTEGER NOT NULL DEFAULT 0,
                    speech_count INTEGER NOT NULL DEFAULT 0,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens INTEGER NOT NULL DEFAULT 0,
                    duration_ms INTEGER NOT NULL DEFAULT 0,
                    summary TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS speeches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    round INTEGER NOT NULL DEFAULT 0,
                    seq INTEGER NOT NULL DEFAULT 0,
                    speaker TEXT NOT NULL DEFAULT '',
                    persona_id TEXT NOT NULL DEFAULT '',
                    persona_name TEXT NOT NULL DEFAULT '',
                    provider_id TEXT NOT NULL DEFAULT '',
                    provider_model TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL DEFAULT '',
                    tokens INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    idx INTEGER NOT NULL DEFAULT 0,
                    title TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL DEFAULT '',
                    snippet TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    umo TEXT NOT NULL,
                    dedupe_key TEXT NOT NULL,
                    sender_id TEXT NOT NULL DEFAULT '',
                    sender_name TEXT NOT NULL DEFAULT '',
                    text TEXT NOT NULL DEFAULT '',
                    image_text TEXT NOT NULL DEFAULT '',
                    audio_text TEXT NOT NULL DEFAULT '',
                    has_image INTEGER NOT NULL DEFAULT 0,
                    has_audio INTEGER NOT NULL DEFAULT 0,
                    is_self INTEGER NOT NULL DEFAULT 0,
                    message_ts INTEGER NOT NULL DEFAULT 0,
                    time_label TEXT NOT NULL DEFAULT '',
                    media_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    UNIQUE (umo, dedupe_key)
                );
                CREATE INDEX IF NOT EXISTS idx_chat_messages_umo
                    ON chat_messages(umo, id DESC);
                CREATE INDEX IF NOT EXISTS idx_sessions_created
                    ON sessions(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_sessions_status
                    ON sessions(status);
                CREATE INDEX IF NOT EXISTS idx_sessions_umo
                    ON sessions(umo);
                CREATE INDEX IF NOT EXISTS idx_speeches_session
                    ON speeches(session_id, id);
                CREATE INDEX IF NOT EXISTS idx_sources_session
                    ON sources(session_id, idx);
                PRAGMA user_version = 1;
                """
            )
            columns = {
                str(row[1])
                for row in conn.execute(
                    "PRAGMA table_info(chat_messages)"
                ).fetchall()
            }
            if "media_json" not in columns:
                conn.execute(
                    "ALTER TABLE chat_messages "
                    "ADD COLUMN media_json TEXT NOT NULL DEFAULT '[]'"
                )
            if "is_self" not in columns:
                conn.execute(
                    "ALTER TABLE chat_messages "
                    "ADD COLUMN is_self INTEGER NOT NULL DEFAULT 0"
                )
            if "message_ts" not in columns:
                conn.execute(
                    "ALTER TABLE chat_messages "
                    "ADD COLUMN message_ts INTEGER NOT NULL DEFAULT 0"
                )
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    async def mark_interrupted(self) -> int:
        """Mark sessions left in 'running' by a restart as interrupted."""
        async with self._lock:
            return await asyncio.to_thread(self._mark_interrupted_sync)

    def _mark_interrupted_sync(self) -> int:
        with self._db() as conn:
            cursor = conn.execute(
                """
                UPDATE sessions
                SET status = 'failed',
                    stop_reason = 'interrupted',
                    error = '插件重启或进程中断',
                    finished_at = ?
                WHERE status = 'running'
                """,
                (utc_now_iso(),),
            )
            return int(cursor.rowcount or 0)

    async def create_session(self, data: dict[str, Any]) -> None:
        async with self._lock:
            await asyncio.to_thread(self._create_session_sync, data)

    def _create_session_sync(self, data: dict[str, Any]) -> None:
        fields = {
            "id": data["id"],
            "umo": data.get("umo", ""),
            "topic": data.get("topic", ""),
            "topic_hash": data.get("topic_hash", ""),
            "status": data.get("status", "running"),
            "stop_reason": data.get("stop_reason", ""),
            "render_mode": data.get("render_mode", "image"),
            "search_mode": data.get("search_mode", "native"),
            "safety_decision": data.get("safety_decision", "allow"),
            "safety_risk": data.get("safety_risk", "low"),
            "safety_categories": _json_dumps(data.get("safety_categories", [])),
            "safety_reason": data.get("safety_reason", ""),
            "members_json": _json_dumps(data.get("members", [])),
            "model_json": _json_dumps(data.get("models", [])),
            "settings_json": _json_dumps(data.get("settings_snapshot", {})),
            "rounds": int(data.get("rounds", 0) or 0),
            "speech_count": int(data.get("speech_count", 0) or 0),
            "input_tokens": int(data.get("input_tokens", 0) or 0),
            "output_tokens": int(data.get("output_tokens", 0) or 0),
            "total_tokens": int(data.get("total_tokens", 0) or 0),
            "duration_ms": int(data.get("duration_ms", 0) or 0),
            "summary": data.get("summary", ""),
            "error": data.get("error", ""),
            "created_at": data.get("created_at") or utc_now_iso(),
            "finished_at": data.get("finished_at", ""),
        }
        columns = ", ".join(fields)
        placeholders = ", ".join("?" for _ in fields)
        with self._db() as conn:
            conn.execute(
                f"INSERT INTO sessions ({columns}) VALUES ({placeholders})",
                tuple(fields.values()),
            )

    async def update_session(self, session_id: str, **fields: Any) -> None:
        allowed = {
            "status",
            "stop_reason",
            "summary",
            "error",
            "rounds",
            "speech_count",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "duration_ms",
            "finished_at",
            "safety_decision",
            "safety_risk",
            "safety_categories",
            "safety_reason",
            "topic",
            "topic_hash",
        }
        payload = {key: value for key, value in fields.items() if key in allowed}
        if not payload:
            return
        for key in ("safety_categories",):
            if key in payload and not isinstance(payload[key], str):
                payload[key] = _json_dumps(payload[key])
        async with self._lock:
            await asyncio.to_thread(self._update_session_sync, session_id, payload)

    def _update_session_sync(self, session_id: str, payload: dict[str, Any]) -> None:
        assignments = ", ".join(f"{key} = ?" for key in payload)
        with self._db() as conn:
            conn.execute(
                f"UPDATE sessions SET {assignments} WHERE id = ?",
                (*payload.values(), session_id),
            )

    async def add_speech(self, speech: dict[str, Any]) -> None:
        async with self._lock:
            await asyncio.to_thread(self._add_speech_sync, speech)

    def _add_speech_sync(self, speech: dict[str, Any]) -> None:
        with self._db() as conn:
            conn.execute(
                """
                INSERT INTO speeches
                    (session_id, round, seq, speaker, persona_id, persona_name,
                     provider_id, provider_model, content, tokens, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    speech["session_id"],
                    int(speech.get("round", 0) or 0),
                    int(speech.get("seq", 0) or 0),
                    speech.get("speaker", ""),
                    speech.get("persona_id", ""),
                    speech.get("persona_name", ""),
                    speech.get("provider_id", ""),
                    speech.get("provider_model", ""),
                    speech.get("content", ""),
                    int(speech.get("tokens", 0) or 0),
                    speech.get("created_at") or utc_now_iso(),
                ),
            )

    async def add_sources(self, session_id: str, sources: list[dict[str, Any]]) -> None:
        if not sources:
            return
        async with self._lock:
            await asyncio.to_thread(self._add_sources_sync, session_id, sources)

    def _add_sources_sync(
        self, session_id: str, sources: list[dict[str, Any]]
    ) -> None:
        with self._db() as conn:
            conn.executemany(
                """
                INSERT INTO sources (session_id, idx, title, url, snippet)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        session_id,
                        int(item.get("idx", index) or index),
                        str(item.get("title", ""))[:500],
                        str(item.get("url", ""))[:2000],
                        str(item.get("snippet", ""))[:1000],
                    )
                    for index, item in enumerate(sources, 1)
                ],
            )

    async def finish_session(
        self,
        session_id: str,
        *,
        status: str,
        stop_reason: str,
        summary: str,
        error: str,
        duration_ms: int,
        input_tokens: int,
        output_tokens: int,
        rounds: int,
        speech_count: int,
    ) -> None:
        await self.update_session(
            session_id,
            status=status,
            stop_reason=stop_reason,
            summary=summary,
            error=error,
            duration_ms=duration_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            rounds=rounds,
            speech_count=speech_count,
            finished_at=utc_now_iso(),
        )

    async def cleanup(self, limit: int) -> int:
        async with self._lock:
            return await asyncio.to_thread(self._cleanup_sync, limit)

    def _cleanup_sync(self, limit: int) -> int:
        limit = max(1, int(limit))
        with self._db() as conn:
            cursor = conn.execute(
                """
                DELETE FROM sessions
                WHERE id IN (
                    SELECT id FROM sessions
                    ORDER BY created_at DESC
                    LIMIT -1 OFFSET ?
                )
                """,
                (limit,),
            )
            return int(cursor.rowcount or 0)

    async def list_sessions(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        query: str = "",
        status: str = "",
        date_from: str = "",
        date_to: str = "",
    ) -> dict[str, Any]:
        async with self._lock:
            return await asyncio.to_thread(
                self._list_sessions_sync,
                page,
                page_size,
                query,
                status,
                date_from,
                date_to,
            )

    def _list_sessions_sync(
        self,
        page: int,
        page_size: int,
        query: str,
        status: str,
        date_from: str,
        date_to: str,
    ) -> dict[str, Any]:
        page = max(1, int(page))
        page_size = max(1, min(MAX_PAGE_SIZE, int(page_size)))
        where: list[str] = []
        params: list[Any] = []
        if query:
            like = f"%{query.strip()}%"
            where.append("(topic LIKE ? OR summary LIKE ? OR members_json LIKE ?)")
            params.extend([like, like, like])
        if status:
            where.append("status = ?")
            params.append(status)
        if date_from:
            where.append("created_at >= ?")
            params.append(date_from)
        if date_to:
            where.append("created_at <= ?")
            params.append(date_to)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        offset = (page - 1) * page_size
        with self._db() as conn:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) FROM sessions {clause}", params
                ).fetchone()[0]
            )
            rows = conn.execute(
                f"""
                SELECT id, umo, topic, topic_hash, status, stop_reason,
                       render_mode, search_mode, rounds, speech_count,
                       total_tokens, duration_ms, summary, created_at,
                       finished_at, safety_risk
                FROM sessions {clause}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                (*params, page_size, offset),
            ).fetchall()
        return {
            "items": [dict(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size),
        }

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        async with self._lock:
            return await asyncio.to_thread(self._get_session_sync, session_id)

    def _get_session_sync(self, session_id: str) -> dict[str, Any] | None:
        with self._db() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is None:
                return None
            speeches = conn.execute(
                "SELECT * FROM speeches WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()
            sources = conn.execute(
                "SELECT * FROM sources WHERE session_id = ? ORDER BY idx",
                (session_id,),
            ).fetchall()
        data = _row_to_dict(row)
        data["speeches"] = [dict(item) for item in speeches]
        data["sources"] = [dict(item) for item in sources]
        data["members"] = data.pop("members_json", [])
        data["models"] = data.pop("model_json", [])
        data["settings"] = _json_loads(data.pop("settings_json", "{}"), {})
        return data

    async def delete_sessions(self, ids: list[str]) -> int:
        clean = [str(item).strip() for item in ids if str(item).strip()]
        if not clean:
            return 0
        clean = list(dict.fromkeys(clean))[:200]
        async with self._lock:
            return await asyncio.to_thread(self._delete_sessions_sync, clean)

    def _delete_sessions_sync(self, ids: list[str]) -> int:
        placeholders = ", ".join("?" for _ in ids)
        with self._db() as conn:
            cursor = conn.execute(
                f"DELETE FROM sessions WHERE id IN ({placeholders})", tuple(ids)
            )
            return int(cursor.rowcount or 0)

    async def add_chat_message(self, data: dict[str, Any]) -> int:
        async with self._lock:
            return await asyncio.to_thread(self._add_chat_message_sync, data)

    def _add_chat_message_sync(self, data: dict[str, Any]) -> int:
        with self._db() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO chat_messages
                    (umo, dedupe_key, sender_id, sender_name, text,
                     has_image, has_audio, is_self, message_ts,
                     time_label, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["umo"],
                    data["dedupe_key"],
                    str(data.get("sender_id", ""))[:100],
                    str(data.get("sender_name", ""))[:80],
                    str(data.get("text", ""))[:1500],
                    1 if data.get("has_image") else 0,
                    1 if data.get("has_audio") else 0,
                    1 if data.get("is_self") else 0,
                    int(data.get("message_ts", 0) or 0),
                    str(data.get("time_label", ""))[:8],
                    data.get("created_at") or utc_now_iso(),
                ),
            )
            if cursor.rowcount <= 0:
                return 0
            return int(cursor.lastrowid or 0)

    async def update_chat_message_media(
        self,
        message_row_id: int,
        *,
        image_text: str = "",
        audio_text: str = "",
        media: list[dict[str, Any]] | None = None,
    ) -> bool:
        async with self._lock:
            return await asyncio.to_thread(
                self._update_chat_message_media_sync,
                message_row_id,
                image_text,
                audio_text,
                media,
            )

    def _update_chat_message_media_sync(
        self,
        message_row_id: int,
        image_text: str,
        audio_text: str,
        media: list[dict[str, Any]] | None,
    ) -> bool:
        assignments = ["image_text = ?", "audio_text = ?"]
        values: list[Any] = [
            str(image_text or "")[:1500],
            str(audio_text or "")[:2000],
        ]
        if media is not None:
            assignments.append("media_json = ?")
            values.append(_json_dumps(media))
        values.append(int(message_row_id))
        with self._db() as conn:
            cursor = conn.execute(
                f"UPDATE chat_messages SET {', '.join(assignments)} WHERE id = ?",
                tuple(values),
            )
            return bool(cursor.rowcount)

    async def trim_chat_messages(self, umo: str, limit: int) -> int:
        async with self._lock:
            return await asyncio.to_thread(
                self._trim_chat_messages_sync, umo, limit
            )

    def _trim_chat_messages_sync(self, umo: str, limit: int) -> int:
        limit = max(0, int(limit))
        with self._db() as conn:
            if limit == 0:
                rows = conn.execute(
                    "SELECT media_json FROM chat_messages WHERE umo = ?",
                    (umo,),
                ).fetchall()
                cursor = conn.execute(
                    "DELETE FROM chat_messages WHERE umo = ?", (umo,)
                )
            else:
                rows = conn.execute(
                    """
                    SELECT media_json FROM chat_messages
                    WHERE umo = ?
                      AND id NOT IN (
                          SELECT id FROM chat_messages
                          WHERE umo = ?
                          ORDER BY id DESC
                          LIMIT ?
                      )
                    """,
                    (umo, umo, limit),
                ).fetchall()
                cursor = conn.execute(
                    """
                    DELETE FROM chat_messages
                    WHERE umo = ?
                      AND id NOT IN (
                          SELECT id FROM chat_messages
                          WHERE umo = ?
                          ORDER BY id DESC
                          LIMIT ?
                      )
                    """,
                    (umo, umo, limit),
                )
            self._delete_media_for_rows(conn, rows)
            return int(cursor.rowcount or 0)

    def _delete_media_for_rows(self, conn: sqlite3.Connection, rows: Any) -> None:
        if self.media_store is None:
            return
        for row in rows:
            for item in _json_loads(row["media_json"], []):
                path = item.get("path") if isinstance(item, dict) else ""
                if path:
                    self.media_store.delete(path)

    async def get_chat_messages(
        self, umo: str, limit: int = 50
    ) -> list[dict[str, str]]:
        async with self._lock:
            return await asyncio.to_thread(
                self._get_chat_messages_sync, umo, limit
            )

    def _get_chat_messages_sync(
        self, umo: str, limit: int
    ) -> list[dict[str, str]]:
        limit = max(0, min(200, int(limit)))
        if limit == 0:
            return []
        with self._db() as conn:
            rows = conn.execute(
                """
                SELECT * FROM chat_messages
                WHERE umo = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (umo, limit),
            ).fetchall()
        result: list[dict[str, str]] = []
        for row in reversed(rows):
            parts: list[str] = []
            text = str(row["text"] or "").strip()
            if text:
                parts.append(text)
            if row["image_text"]:
                parts.append(f"[图片] {row['image_text']}")
            elif row["has_image"]:
                parts.append("[图片]")
            if row["audio_text"]:
                parts.append(f"[语音] {row['audio_text']}")
            elif row["has_audio"]:
                parts.append("[语音]")
            if not parts:
                continue
            result.append(
                {
                    "sender": str(row["sender_name"] or "群友"),
                    "text": "\n".join(parts),
                    "time": str(row["time_label"] or ""),
                }
            )
        return result

    async def get_chat_records(
        self,
        *,
        umo: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        async with self._lock:
            return await asyncio.to_thread(
                self._get_chat_records_sync, umo, page, page_size
            )

    def _get_chat_records_sync(
        self, umo: str, page: int, page_size: int
    ) -> dict[str, Any]:
        page = max(1, int(page))
        page_size = max(1, min(100, int(page_size)))
        where = "WHERE umo = ?" if umo else ""
        params: tuple[Any, ...] = (umo,) if umo else ()
        with self._db() as conn:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) FROM chat_messages {where}", params
                ).fetchone()[0]
            )
            rows = conn.execute(
                f"""
                SELECT * FROM chat_messages {where}
                ORDER BY id DESC
                LIMIT ? OFFSET ?
                """,
                (*params, page_size, (page - 1) * page_size),
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["media"] = _json_loads(item.pop("media_json", "[]"), [])
            items.append(item)
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size),
        }

    async def clear_chat_messages(self, umo: str = "") -> int:
        async with self._lock:
            return await asyncio.to_thread(
                self._clear_chat_messages_sync, umo
            )

    def _clear_chat_messages_sync(self, umo: str) -> int:
        with self._db() as conn:
            if umo:
                rows = conn.execute(
                    "SELECT media_json FROM chat_messages WHERE umo = ?",
                    (umo,),
                ).fetchall()
                cursor = conn.execute(
                    "DELETE FROM chat_messages WHERE umo = ?", (umo,)
                )
            else:
                rows = conn.execute(
                    "SELECT media_json FROM chat_messages"
                ).fetchall()
                cursor = conn.execute("DELETE FROM chat_messages")
            self._delete_media_for_rows(conn, rows)
            return int(cursor.rowcount or 0)

    async def chat_context_stats(self) -> list[dict[str, Any]]:
        async with self._lock:
            return await asyncio.to_thread(self._chat_context_stats_sync)

    def _chat_context_stats_sync(self) -> list[dict[str, Any]]:
        with self._db() as conn:
            rows = conn.execute(
                """
                SELECT umo, COUNT(*) AS count, MAX(created_at) AS last_at
                FROM chat_messages
                GROUP BY umo
                ORDER BY count DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    async def stats(self, days: int = 30) -> dict[str, Any]:
        async with self._lock:
            return await asyncio.to_thread(self._stats_sync, days)

    def _stats_sync(self, days: int) -> dict[str, Any]:
        days = max(0, min(3650, int(days)))
        params: list[Any] = []
        clause = ""
        if days:
            since = (
                datetime.now(timezone.utc) - timedelta(days=days)
            ).replace(microsecond=0).isoformat()
            clause = "WHERE s.created_at >= ?"
            params.append(since)

        with self._db() as conn:
            overview = conn.execute(
                f"""
                SELECT
                    COUNT(*) AS sessions,
                    COALESCE(SUM(total_tokens), 0) AS total_tokens,
                    COALESCE(SUM(speech_count), 0) AS speeches,
                    COALESCE(AVG(rounds), 0) AS avg_rounds,
                    COALESCE(AVG(duration_ms), 0) AS avg_duration_ms
                FROM sessions s {clause}
                """,
                params,
            ).fetchone()
            status_rows = conn.execute(
                f"""
                SELECT status, COUNT(*) AS count
                FROM sessions s {clause}
                GROUP BY status
                """,
                params,
            ).fetchall()
            daily_rows = conn.execute(
                f"""
                SELECT substr(created_at, 1, 10) AS day,
                       COUNT(*) AS count,
                       COALESCE(SUM(total_tokens), 0) AS tokens
                FROM sessions s {clause}
                GROUP BY day
                ORDER BY day ASC
                """,
                params,
            ).fetchall()
            top_members = conn.execute(
                f"""
                SELECT sp.speaker AS speaker, COUNT(*) AS count
                FROM speeches sp
                JOIN sessions s ON s.id = sp.session_id
                {clause}
                GROUP BY sp.speaker
                ORDER BY count DESC
                LIMIT 8
                """,
                params,
            ).fetchall()
            mode_rows = conn.execute(
                f"""
                SELECT render_mode, COUNT(*) AS count
                FROM sessions s {clause}
                GROUP BY render_mode
                """,
                params,
            ).fetchall()

        return {
            "overview": dict(overview) if overview else {},
            "status": [dict(row) for row in status_rows],
            "daily": [dict(row) for row in daily_rows],
            "top_members": [dict(row) for row in top_members],
            "render_modes": [dict(row) for row in mode_rows],
            "days": days,
        }


def _md_safe(value: Any) -> str:
    """Escape raw HTML so an exported Markdown file cannot execute it."""
    return (
        str(value if value is not None else "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def session_to_markdown(session: dict[str, Any]) -> str:
    """Render a full session as Markdown for export."""
    lines: list[str] = []
    lines.append(f"# {_md_safe(session.get('topic') or '[议题已隐藏]')}")
    lines.append("")
    lines.append(f"- 会话：`{session.get('umo', '')}`")
    lines.append(f"- 状态：`{session.get('status', '')}`")
    lines.append(f"- 停止原因：`{session.get('stop_reason', '')}`")
    lines.append(f"- 创建时间：{session.get('created_at', '')}")
    lines.append(f"- 轮数：{session.get('rounds', 0)}")
    lines.append(f"- 发言数：{session.get('speech_count', 0)}")
    lines.append(f"- Token：约 {session.get('total_tokens', 0)}")
    lines.append("")
    if session.get("safety_decision") == "refuse":
        lines.append("## 安全审核")
        lines.append("")
        lines.append(
            f"已拒绝：{session.get('safety_reason') or '高风险议题'}"
        )
        return "\n".join(lines) + "\n"

    members = session.get("members") or []
    if members:
        lines.append("## 成员")
        lines.append("")
        for member in members:
            name = _md_safe(
                member.get("display_name") or member.get("persona_id") or "成员"
            )
            model = _md_safe(member.get("provider_model") or "默认模型")
            lines.append(f"- **{name}**（{model}）")
        lines.append("")

    summary = str(session.get("summary") or "").strip()
    if summary:
        lines.append("## 总结")
        lines.append("")
        lines.append(_md_safe(summary))
        lines.append("")

    speeches = session.get("speeches") or []
    if speeches:
        current_round = None
        for speech in speeches:
            round_no = speech.get("round", 0)
            if round_no != current_round:
                current_round = round_no
                lines.append(f"## 第 {round_no} 轮")
                lines.append("")
            name = speech.get("speaker") or "成员"
            model = speech.get("provider_model") or ""
            suffix = f" · {_md_safe(model)}" if model else ""
            lines.append(f"### {_md_safe(name)}{suffix}")
            lines.append("")
            lines.append(_md_safe(str(speech.get("content") or "").strip()))
            lines.append("")

    sources = session.get("sources") or []
    if sources:
        lines.append("## 参考来源")
        lines.append("")
        for item in sources:
            idx = item.get("idx") or ""
            title = _md_safe(item.get("title") or item.get("url") or "来源")
            url = str(item.get("url") or "").replace(")", "%29")
            lines.append(f"- [{idx}] [{title}]({url})")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _csv_safe(value: Any) -> Any:
    text = str(value if value is not None else "")
    if text.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text


def sessions_to_csv(sessions: list[dict[str, Any]]) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "id",
            "created_at",
            "finished_at",
            "umo",
            "topic",
            "status",
            "stop_reason",
            "rounds",
            "speech_count",
            "total_tokens",
            "duration_ms",
            "render_mode",
            "search_mode",
            "safety_risk",
        ]
    )
    for item in sessions:
        writer.writerow(
            [
                _csv_safe(item.get("id", "")),
                _csv_safe(item.get("created_at", "")),
                _csv_safe(item.get("finished_at", "")),
                _csv_safe(item.get("umo", "")),
                _csv_safe(item.get("topic", "")),
                _csv_safe(item.get("status", "")),
                _csv_safe(item.get("stop_reason", "")),
                item.get("rounds", 0),
                item.get("speech_count", 0),
                item.get("total_tokens", 0),
                item.get("duration_ms", 0),
                _csv_safe(item.get("render_mode", "")),
                _csv_safe(item.get("search_mode", "")),
                _csv_safe(item.get("safety_risk", "")),
            ]
        )
    return output.getvalue()
