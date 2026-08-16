"""Blob storage port.

PDFs are never served from the application process and never stored in
Postgres. The client receives a short-lived signed URL, minted only after
authorization has already passed.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class SignedURL:
    url: str
    expires_at: datetime


class BlobStorage(ABC):
    @abstractmethod
    async def upload(self, *, key: str, data: bytes, content_type: str) -> None: ...

    @abstractmethod
    async def download(self, *, key: str) -> bytes: ...

    @abstractmethod
    async def delete(self, *, key: str) -> None: ...

    @abstractmethod
    async def signed_url(self, *, key: str, filename: str) -> SignedURL:
        """A time-limited, read-only URL for the browser's PDF viewer."""
