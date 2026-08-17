"""Short-lived, application-signed download links.

PDFs are served through this API rather than from a direct storage URL, for
both backends.

The alternative — handing the browser an Azure Blob SAS URL — requires a CORS
rule on the storage account, because the PDF then loads from a different
origin than the app. That is one more piece of configuration to get right in
every environment, and it fails in a way that looks like a broken viewer
rather than a misconfiguration.

Proxying also keeps the storage account entirely invisible to the client: the
account name, container layout and blob keys never reach the browser, and a
copied link cannot outlive its token or be replayed against storage directly.

The cost is that PDF bytes pass through the API. For 25 MB documents on a
handful of concurrent readers that is a fair trade; a high-traffic deployment
would switch back to SAS URLs (the code is still in ``azure_blob.py``) and
configure storage CORS.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import jwt

from app.core.config import settings
from app.core.exceptions import StorageError

#: Distinguishes these from access and guest tokens. A token minted for one
#: purpose must never be accepted for another.
_TOKEN_TYPE = "blob"


def mint_download_token(key: str) -> tuple[str, datetime]:
    """Return ``(token, expires_at)`` authorising a read of one blob key.

    Minted only after document authorization has already passed, so the token
    itself is the capability — short-lived, and scoped to a single object.
    """
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
    return token, expires_at


def verify_download_token(token: str) -> str:
    """Return the blob key a download token authorises."""
    try:
        claims = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
    except jwt.InvalidTokenError as exc:
        raise StorageError("This file link is invalid or has expired.") from exc

    if claims.get("typ") != _TOKEN_TYPE:
        raise StorageError("This file link is invalid.")
    return str(claims["key"])


def build_download_url(token: str, filename: str) -> str:
    """Relative URL; the client resolves it against the API base."""
    return f"{settings.api_v1_prefix}/files/{quote(token)}?filename={quote(filename)}"
