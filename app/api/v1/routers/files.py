"""Local-storage file delivery.

Only mounted when ``STORAGE_BACKEND=local``. In Azure the browser fetches the
blob directly from a SAS URL and this endpoint is never reached.

The path token is itself the credential: it was minted by the storage backend
only after document authorization passed, and it expires in minutes. That keeps
the local flow shaped like the production one rather than special-casing it.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query
from fastapi.responses import Response

from app.storage.factory import get_blob_storage
from app.storage.local_storage import LocalBlobStorage

router = APIRouter(prefix="/files", tags=["files"])


@router.get("/{token}")
async def download_file(
    token: Annotated[str, Path()],
    filename: Annotated[str, Query(max_length=255)] = "document.pdf",
) -> Response:
    key = LocalBlobStorage.verify_download_token(token)
    data = await get_blob_storage().download(key=key)
    return Response(
        content=data,
        media_type="application/pdf",
        headers={
            # `inline` so react-pdf renders it rather than triggering a download.
            "Content-Disposition": f'inline; filename="{filename}"',
            "Cache-Control": "private, max-age=300",
        },
    )
