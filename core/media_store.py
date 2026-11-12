from __future__ import annotations

import base64
import hashlib
import mimetypes
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

MAX_MEDIA_BYTES = 10 * 1024 * 1024


class MediaStoreError(RuntimeError):
    pass


class MediaStore:
    """Store optional OneBot raw images/audio under the plugin data directory."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _group_dir(self, umo: str) -> Path:
        digest = hashlib.sha1(umo.encode("utf-8")).hexdigest()[:16]
        path = self.root / digest
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _ext(source: str, fallback: str) -> str:
        try:
            suffix = Path(urlparse(source).path).suffix.lower()
        except ValueError:
            suffix = ""
        if suffix and len(suffix) <= 8:
            return suffix
        return fallback

    async def save_image(
        self,
        source: str,
        *,
        umo: str,
        row_id: int,
        index: int = 0,
    ) -> dict[str, Any] | None:
        source = str(source or "").strip()
        if not source:
            return None
        ext = self._ext(source, ".jpg")
        filename = f"{row_id}_image{index}{ext}"
        target = self._group_dir(umo) / filename
        try:
            if source.startswith("base64://"):
                raw = base64.b64decode(source[9:], validate=False)
                if len(raw) > MAX_MEDIA_BYTES:
                    return None
                target.write_bytes(raw)
            elif source.startswith(("http://", "https://")):
                await self._download(source, target)
            else:
                path = source[8:] if source.startswith("file:///") else source
                if not Path(path).is_file():
                    return None
                if Path(path).stat().st_size > MAX_MEDIA_BYTES:
                    return None
                await self._copy(Path(path), target)
            return {
                "type": "image",
                "path": target.relative_to(self.root).as_posix(),
                "name": filename,
            }
        except Exception:  # noqa: BLE001
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
            return None

    async def save_audio(
        self,
        source: str,
        *,
        umo: str,
        row_id: int,
        index: int = 0,
    ) -> dict[str, Any] | None:
        source = str(source or "").strip()
        if not source:
            return None
        ext = self._ext(source, ".silk")
        filename = f"{row_id}_audio{index}{ext}"
        target = self._group_dir(umo) / filename
        try:
            if source.startswith(("http://", "https://")):
                await self._download(source, target)
            else:
                path = source[8:] if source.startswith("file:///") else source
                if not Path(path).is_file():
                    return None
                if Path(path).stat().st_size > MAX_MEDIA_BYTES:
                    return None
                await self._copy(Path(path), target)
            return {
                "type": "audio",
                "path": target.relative_to(self.root).as_posix(),
                "name": filename,
            }
        except Exception:  # noqa: BLE001
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
            return None

    async def _download(self, url: str, target: Path) -> None:
        try:
            import aiohttp
        except ImportError as exc:  # pragma: no cover
            raise MediaStoreError("aiohttp unavailable") from exc
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(
            trust_env=True, timeout=timeout
        ) as session:
            async with session.get(url) as response:
                if response.status != 200:
                    raise MediaStoreError(f"HTTP {response.status}")
                length = int(response.headers.get("Content-Length") or 0)
                if length > MAX_MEDIA_BYTES:
                    raise MediaStoreError("media too large")
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.content.iter_chunked(64 * 1024):
                    total += len(chunk)
                    if total > MAX_MEDIA_BYTES:
                        raise MediaStoreError("media too large")
                    chunks.append(chunk)
                target.write_bytes(b"".join(chunks))

    @staticmethod
    async def _copy(source: Path, target: Path) -> None:
        import asyncio

        await asyncio.to_thread(shutil.copyfile, source, target)

    def resolve(self, relative: str) -> Path:
        root = self.root.resolve()
        target = (root / str(relative or "")).resolve()
        if target != root and root not in target.parents:
            raise MediaStoreError("invalid media path")
        if not target.is_file():
            raise MediaStoreError("media not found")
        return target

    @staticmethod
    def content_type(path: Path) -> str:
        return mimetypes.guess_type(str(path))[0] or "application/octet-stream"

    def delete(self, relative: str) -> None:
        try:
            path = self.resolve(relative)
            path.unlink(missing_ok=True)
        except (MediaStoreError, OSError):
            return
