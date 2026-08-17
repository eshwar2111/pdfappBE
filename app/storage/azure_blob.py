"""Azure Blob Storage backend.

The container is created private and stays private. Nothing is ever served from
a public blob URL; each read is a freshly minted, read-only, minutes-long SAS.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from azure.core.exceptions import AzureError, ResourceExistsError, ResourceNotFoundError
from azure.storage.blob import (
    BlobSasPermissions,
    ContentSettings,
    generate_blob_sas,
)
from azure.storage.blob.aio import BlobServiceClient

from app.core.config import settings
from app.core.exceptions import StorageError
from app.storage.base import BlobStorage, SignedURL
from app.storage.signed_links import build_download_url, mint_download_token

logger = logging.getLogger(__name__)


class AzureBlobStorage(BlobStorage):
    def __init__(self) -> None:
        if not settings.azure_storage_connection_string:
            raise StorageError("AZURE_STORAGE_CONNECTION_STRING is not configured.")
        self._connection_string = settings.azure_storage_connection_string
        self._container = settings.azure_storage_container

    def _service(self) -> BlobServiceClient:
        return BlobServiceClient.from_connection_string(self._connection_string)

    async def ensure_container(self) -> None:
        """Idempotent; called once at startup. No public access argument is
        passed, so the container defaults to private."""
        try:
            async with self._service() as service:
                await service.create_container(self._container)
        except ResourceExistsError:
            pass
        except AzureError as exc:
            raise StorageError("Could not initialise blob storage.") from exc

    async def upload(self, *, key: str, data: bytes, content_type: str) -> None:
        try:
            async with self._service() as service:
                blob = service.get_blob_client(self._container, key)
                await blob.upload_blob(
                    data,
                    overwrite=True,
                    content_settings=ContentSettings(content_type=content_type),
                )
        except AzureError as exc:
            logger.exception("Blob upload failed for key %s", key)
            raise StorageError() from exc

    async def download(self, *, key: str) -> bytes:
        try:
            async with self._service() as service:
                blob = service.get_blob_client(self._container, key)
                stream = await blob.download_blob()
                return await stream.readall()
        except ResourceNotFoundError as exc:
            raise StorageError("The stored file could not be found.") from exc
        except AzureError as exc:
            logger.exception("Blob download failed for key %s", key)
            raise StorageError() from exc

    async def delete(self, *, key: str) -> None:
        try:
            async with self._service() as service:
                blob = service.get_blob_client(self._container, key)
                await blob.delete_blob()
        except ResourceNotFoundError:
            pass  # already gone; deletion is idempotent
        except AzureError as exc:
            logger.exception("Blob delete failed for key %s", key)
            raise StorageError() from exc

    async def signed_url(self, *, key: str, filename: str) -> SignedURL:
        """Return an application-signed link served by this API.

        Deliberately not a storage SAS URL. A SAS points the browser at
        `*.blob.core.windows.net`, a different origin, which then needs its own
        CORS rule — and when that rule is missing or subtly wrong the symptom is
        a broken PDF viewer rather than anything that names CORS. Serving the
        bytes through the API keeps everything on one origin and keeps storage
        account names and blob keys out of the client entirely.

        `generate_sas_url` below still implements the SAS approach, for a
        deployment that would rather not proxy the bytes.
        """
        token, expires_at = mint_download_token(key)
        return SignedURL(url=build_download_url(token, filename), expires_at=expires_at)

    async def generate_sas_url(self, *, key: str, filename: str) -> SignedURL:
        """Direct-to-storage read URL, valid for minutes.

        Unused by default — see `signed_url`. Requires a CORS rule on the
        storage account allowing the frontend origin with GET and HEAD.
        """
        expires_at = datetime.now(UTC) + timedelta(minutes=settings.sas_url_ttl_minutes)
        try:
            async with self._service() as service:
                account_name = service.account_name
                account_key = service.credential.account_key  # type: ignore[union-attr]
                blob_url = service.get_blob_client(self._container, key).url

            sas = generate_blob_sas(
                account_name=account_name,
                container_name=self._container,
                blob_name=key,
                account_key=account_key,
                permission=BlobSasPermissions(read=True),
                expiry=expires_at,
                # Make the browser render the PDF inline under its real name
                # rather than downloading it as an opaque blob key.
                content_disposition=f'inline; filename="{quote(filename)}"',
                content_type="application/pdf",
            )
        except (AzureError, AttributeError) as exc:
            logger.exception("SAS generation failed for key %s", key)
            raise StorageError("Could not generate a file access link.") from exc

        return SignedURL(url=f"{blob_url}?{sas}", expires_at=expires_at)
