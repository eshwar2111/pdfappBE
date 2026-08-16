from __future__ import annotations

from functools import lru_cache

from app.core.config import StorageBackend, settings
from app.storage.azure_blob import AzureBlobStorage
from app.storage.base import BlobStorage
from app.storage.local_storage import LocalBlobStorage


@lru_cache(maxsize=1)
def get_blob_storage() -> BlobStorage:
    """The single place the storage backend is chosen. Everything else depends
    on the ``BlobStorage`` port."""
    if settings.storage_backend is StorageBackend.AZURE:
        return AzureBlobStorage()
    return LocalBlobStorage()
