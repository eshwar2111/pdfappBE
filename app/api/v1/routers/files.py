"""PDF delivery.

Used by both storage backends. The path token is itself the credential: it was
minted only after document authorization passed, is scoped to a single blob,
and expires in minutes — so this route needs no session of its own, which is
what lets an invited guest's browser load a PDF with no account.

Serving bytes through the API rather than from a storage SAS URL keeps
everything on one origin (no storage CORS to configure) and keeps the storage
account and its blob keys out of the client entirely. See
``app/storage/signed_links.py`` for the reasoning.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query
from fastapi.responses import Response

from app.storage.factory import get_blob_storage
from app.storage.signed_links import verify_download_token

router = APIRouter(prefix="/files", tags=["files"])


@router.get("/{token}")
async def download_file(
    token: Annotated[str, Path()],
    filename: Annotated[str, Query(max_length=255)] = "document.pdf",
) -> Response:
    key = verify_download_token(token)
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
