from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

try:  # AstrBot >= 4.28
    from astrbot.api.web import (  # type: ignore
        error_response,
        json_response,
        request,
    )
except ImportError:  # pragma: no cover - Quart compatibility for older builds
    from quart import jsonify, request  # type: ignore

    def json_response(data: Any, status_code: int = 200):
        response = jsonify(data)
        response.status_code = status_code
        return response

    def error_response(message: str, status_code: int = 400):
        response = jsonify({"success": False, "error": message})
        response.status_code = status_code
        return response


from .config import SettingsError
from .constants import PLUGIN_NAME, PLUGIN_VERSION
from .search import AnySearchClient, sources_for_storage
from .storage import session_to_markdown, sessions_to_csv, utc_now_iso


def _query_value(name: str, default: str = "") -> str:
    try:
        value = request.query.get(name, default)
    except AttributeError:
        value = request.args.get(name, default)
    if value is None:
        return default
    return str(value)


def _query_int(name: str, default: int) -> int:
    try:
        return int(_query_value(name, str(default)) or default)
    except (TypeError, ValueError):
        return default


async def _body() -> dict[str, Any]:
    try:
        data = await request.json(default={})
    except TypeError:
        data = await request.json()
    except AttributeError:
        data = await request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


class CouncilWebAPI:
    """Dashboard plugin-view API.

    The Dashboard bridge handles authentication; handlers are intentionally
    limited to council settings and history and never expose API keys.
    """

    def __init__(self, plugin: Any):
        self.plugin = plugin
        self.context = plugin.context
        self.settings_store = plugin.settings_store
        self.storage = plugin.storage
        self.engine = plugin.engine

    def register(self) -> None:
        routes = [
            ("/meta", self.meta, ["GET"], "Council providers/personas metadata"),
            ("/settings", self.settings, ["GET"], "Read council settings"),
            (
                "/settings/save",
                self.save_settings,
                ["POST"],
                "Save council settings",
            ),
            (
                "/settings/reset",
                self.reset_settings,
                ["POST"],
                "Reset council settings",
            ),
            ("/search/test", self.test_search, ["POST"], "Test AnySearch"),
            ("/sessions", self.sessions, ["GET"], "List council sessions"),
            (
                "/sessions/<session_id>",
                self.session_detail,
                ["GET"],
                "Get council session",
            ),
            (
                "/sessions/delete",
                self.delete_sessions,
                ["POST"],
                "Delete council sessions",
            ),
            ("/stats", self.stats, ["GET"], "Council statistics"),
            ("/export", self.export, ["POST"], "Export council sessions"),
            ("/active", self.active, ["GET"], "List active councils"),
            (
                "/active/cancel",
                self.cancel_active,
                ["POST"],
                "Cancel active council",
            ),
        ]
        for route, handler, methods, desc in routes:
            self.context.register_web_api(
                f"/{PLUGIN_NAME}{route}", handler, methods, desc
            )

    async def _provider_catalog(self) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        try:
            providers = self.context.get_all_providers()
        except Exception:  # noqa: BLE001
            providers = []
        for provider in providers or []:
            try:
                meta = provider.meta()
                if isinstance(meta, dict):
                    pid = str(meta.get("id") or "")
                    model = str(meta.get("model") or "")
                else:
                    pid = str(getattr(meta, "id", "") or "")
                    model = str(getattr(meta, "model", "") or "")
            except Exception:  # noqa: BLE001
                continue
            if not pid:
                continue
            label = f"{pid} · {model}" if model else pid
            items.append({"id": pid, "model": model, "label": label})
        items.sort(key=lambda item: item["label"].lower())
        return items

    async def _persona_catalog(self) -> list[dict[str, str]]:
        items: list[dict[str, str]] = [
            {
                "id": "default",
                "label": "跟随会话默认人格",
                "preview": "使用 AstrBot 当前会话的默认人格",
            }
        ]
        try:
            personas = await self.context.persona_manager.get_all_personas()
        except Exception:  # noqa: BLE001
            personas = []
        for persona in personas or []:
            if isinstance(persona, dict):
                pid = str(persona.get("persona_id") or "")
                prompt = str(persona.get("system_prompt") or "")
            else:
                pid = str(getattr(persona, "persona_id", "") or "")
                prompt = str(getattr(persona, "system_prompt", "") or "")
            if not pid or pid == "default":
                continue
            preview = " ".join(prompt.split())[:100]
            items.append(
                {"id": pid, "label": pid, "preview": preview}
            )
        items.sort(key=lambda item: (item["id"] != "default", item["label"].lower()))
        return items

    async def meta(self):
        providers = await self._provider_catalog()
        personas = await self._persona_catalog()
        return json_response(
            {
                "success": True,
                "plugin": PLUGIN_NAME,
                "version": PLUGIN_VERSION,
                "providers": providers,
                "personas": personas,
                "username": getattr(request, "username", None),
            }
        )

    async def settings(self):
        data = self.settings_store.load()
        return json_response(
            {
                "success": True,
                "settings": self.settings_store.public_settings(data),
            }
        )

    async def save_settings(self):
        body = await _body()
        incoming = body.get("settings", body)
        if not isinstance(incoming, dict):
            return error_response("settings 必须是对象", status_code=400)
        try:
            providers = {item["id"] for item in await self._provider_catalog()}
            personas = {item["id"] for item in await self._persona_catalog()}
            old = self.settings_store.load()
            incoming_search = incoming.get("search")
            if not isinstance(incoming_search, dict):
                incoming_search = {}
                incoming["search"] = incoming_search
            new_key = str(incoming_search.get("anysearch_api_key", "")).strip()
            if body.get("clear_anysearch_key"):
                new_key = ""
            elif not new_key:
                new_key = str(
                    old.get("search", {}).get("anysearch_api_key", "")
                )
            incoming_search["anysearch_api_key"] = new_key
            saved = self.settings_store.save(
                incoming,
                provider_ids=providers - {""},
                persona_ids=personas,
            )
            return json_response(
                {
                    "success": True,
                    "settings": self.settings_store.public_settings(saved),
                }
            )
        except SettingsError as exc:
            return error_response(str(exc), status_code=400)
        except Exception as exc:  # noqa: BLE001
            return error_response(f"保存失败：{type(exc).__name__}", status_code=500)

    async def reset_settings(self):
        try:
            providers = {item["id"] for item in await self._provider_catalog()}
            personas = {item["id"] for item in await self._persona_catalog()}
            saved = self.settings_store.reset(
                provider_ids=providers - {""}, persona_ids=personas
            )
            return json_response(
                {
                    "success": True,
                    "settings": self.settings_store.public_settings(saved),
                }
            )
        except SettingsError as exc:
            return error_response(str(exc), status_code=400)

    async def test_search(self):
        body = await _body()
        current = self.settings_store.load()
        key = str(body.get("anysearch_api_key", "")).strip()
        if not key:
            key = str(current.get("search", {}).get("anysearch_api_key", ""))
        query = str(body.get("query", "AstrBot")).strip() or "AstrBot"
        client = AnySearchClient(timeout_sec=20)
        bundle = await client.search(
            query, api_key=key, max_results=3, language="auto"
        )
        return json_response(
            {
                "success": True,
                "ok": bundle.ok,
                "query": bundle.query,
                "count": len(bundle.sources),
                "error": bundle.error,
                "sources": sources_for_storage(bundle),
                "anonymous": not bool(key),
            }
        )

    async def sessions(self):
        page = _query_int("page", 1)
        page_size = _query_int("page_size", 20)
        result = await self.storage.list_sessions(
            page=page,
            page_size=page_size,
            query=_query_value("q", ""),
            status=_query_value("status", ""),
            date_from=_query_value("from", ""),
            date_to=_query_value("to", ""),
        )
        return json_response({"success": True, **result})

    async def session_detail(self, session_id: str):
        data = await self.storage.get_session(session_id)
        if data is None:
            return error_response("记录不存在", status_code=404)
        return json_response({"success": True, "session": data})

    async def delete_sessions(self):
        body = await _body()
        ids = body.get("ids", [])
        if not isinstance(ids, list):
            return error_response("ids 必须是列表", status_code=400)
        ids = [str(item).strip() for item in ids if str(item).strip()][:200]
        if not ids:
            return error_response("没有选择记录", status_code=400)
        deleted = await self.storage.delete_sessions(ids)
        return json_response({"success": True, "deleted": deleted})

    async def stats(self):
        days = _query_int("days", 30)
        data = await self.storage.stats(days)
        return json_response({"success": True, **data})

    async def export(self):
        body = await _body()
        ids = body.get("ids", [])
        if not isinstance(ids, list) or not ids:
            return error_response("没有选择记录", status_code=400)
        export_format = str(body.get("format", "md")).lower()
        if export_format not in {"md", "json", "csv"}:
            return error_response("format 必须是 md/json/csv", status_code=400)
        sessions: list[dict[str, Any]] = []
        for session_id in [str(item).strip() for item in ids][:200]:
            data = await self.storage.get_session(session_id)
            if data:
                sessions.append(data)
        if not sessions:
            return error_response("记录不存在", status_code=404)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        if export_format == "json":
            content = json.dumps(
                {
                    "exported_at": utc_now_iso(),
                    "plugin": PLUGIN_NAME,
                    "sessions": sessions,
                },
                ensure_ascii=False,
                indent=2,
            )
            filename = f"council-{stamp}.json"
            content_type = "application/json; charset=utf-8"
        elif export_format == "csv":
            content = sessions_to_csv(sessions)
            filename = f"council-{stamp}.csv"
            content_type = "text/csv; charset=utf-8"
        else:
            content = "\n\n---\n\n".join(
                session_to_markdown(item) for item in sessions
            )
            filename = f"council-{stamp}.md"
            content_type = "text/markdown; charset=utf-8"
        return json_response(
            {
                "success": True,
                "filename": filename,
                "content_type": content_type,
                "content": content,
            }
        )

    async def active(self):
        runs = await self.engine.list_active()
        return json_response(
            {
                "success": True,
                "items": runs,
                "server_time": int(time.time()),
            }
        )

    async def cancel_active(self):
        body = await _body()
        session_id = str(body.get("session_id", "")).strip()
        if not session_id:
            return error_response("session_id 不能为空", status_code=400)
        ok, message = await self.engine.cancel(
            session_id=session_id, is_admin=True
        )
        if not ok:
            return error_response(message, status_code=404)
        return json_response({"success": True, "message": message})


def plugin_view_dir(plugin_dir: Path) -> Path:
    return Path(plugin_dir) / "views" / "council"
