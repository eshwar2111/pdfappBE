"""Filesystem-backed storage for local development.

Keeps the whole app runnable without an Azure account. Instead of a SAS URL it
mints a signed, expiring token that the backend's own file endpoint validates —
so the *shape* of the flow (authorize, then hand out a short-lived credential)
matches production rather than being special-cased.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import jwt

from app.core.config import settings
from app.core.exceptions import StorageError
from app.storage.base import BlobStorage, SignedURL

_TOKEN_TYPE = "blob"


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
        expires_at = datetime.now(UTC) + timedelta(minutes=settings.sas_url_ttl_minutes)
        token = jwt.encode(
            {
                "typ": _TOKEN_TYPE,
                "key": key,
                "sub": key,
                "exp": int(expires_at.timestamp()),
            },
            settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
        url = (
            f"{settings.api_v1_prefix}/files/{quote(token)}"
            f"?filename={quote(filename)}"
        )
        return SignedURL(url=url, expires_at=expires_at)

    @staticmethod
    def verify_download_token(token: str) -> str:
        """Return the blob key a local download token authorises."""
        try:
            claims = jwt.decode(
                token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
            )
        except jwt.InvalidTokenError as exc:
            raise StorageError("This file link is invalid or has expired.") from exc
        if claims.get("typ") != _TOKEN_TYPE:
            raise StorageError("This file link is invalid.")
        return str(claims["key"])
