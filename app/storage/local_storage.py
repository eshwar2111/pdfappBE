"""Filesystem-backed storage for local development.

Keeps the whole app runnable without an Azure account, and — because both
backends hand out the same application-signed links — the download flow is
identical in development and production rather than being special-cased.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.core.config import settings
from app.core.exceptions import StorageError
from app.storage.base import BlobStorage, SignedURL
from app.storage.signed_links import build_download_url, mint_download_token


class LocalBlobStorage(BlobStorage):
    def __init__(self, root: Path | None = None) -> None:
        self._root = Path(root or settings.local_storage_dir).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        candidate = (self._root / key).resolve()
        # Refuse to resolve outside the storage root — a key is never user
        # input today, but path traversal is not a bug worth risking later.
        if not candidate.is_relative_to(self._root):
            raise StorageError("Invalid storage key.")
        return candidate

    async def upload(self, *, key: str, data: bytes, content_type: str) -> None:
        def _write() -> None:
            path = self._path(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

        await asyncio.to_thread(_write)

    async def download(self, *, key: str) -> bytes:
        def _read() -> bytes:
            path = self._path(key)
            if not path.exists():
                raise StorageError("The stored file could not be found.")
            return path.read_bytes()

        return await asyncio.to_thread(_read)

    async def delete(self, *, key: str) -> None:
        def _unlink() -> None:
            self._path(key).unlink(missing_ok=True)

        await asyncio.to_thread(_unlink)

    async def signed_url(self, *, key: str, filename: str) -> SignedURL:
        token, expires_at = mint_download_token(key)
        return SignedURL(url=build_download_url(token, filename), expires_at=expires_at)
