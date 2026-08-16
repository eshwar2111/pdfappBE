"""Password hashing, share-token generation and JWT issue/verify.

Nothing here touches the database or HTTP — it is pure cryptographic plumbing
so it can be unit-tested and reasoned about in isolation.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError, VerificationError

from app.core.config import settings
from app.core.exceptions import InvalidTokenError
from app.domain.enums import Permission, TokenType

# Argon2id with the library's current defaults — memory-hard, side-channel
# resistant, and the present OWASP recommendation for password storage.
_password_hasher = PasswordHasher()

_SHARE_TOKEN_BYTES = 32


# --- Passwords ---------------------------------------------------------------
def hash_password(plain_password: str) -> str:
    """Return an Argon2id PHC-format hash. The plaintext is never persisted."""
    return _password_hasher.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        _password_hasher.verify(password_hash, plain_password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    return True


def password_needs_rehash(password_hash: str) -> bool:
    """True when the stored hash used weaker parameters than we now require."""
    try:
        return _password_hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


# --- Share tokens ------------------------------------------------------------
def generate_share_token() -> tuple[str, str]:
    """Return ``(raw_token, token_hash)``.

    Only the hash is stored. A leaked database therefore does not hand an
    attacker working share links — the same reasoning as password hashing,
    applied to bearer URLs.
    """
    raw = secrets.token_urlsafe(_SHARE_TOKEN_BYTES)
    return raw, hash_share_token(raw)


def hash_share_token(raw_token: str) -> str:
    """SHA-256 is correct here: the token is high-entropy, so it is not
    brute-forceable and does not need a slow KDF."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def generate_reset_token() -> tuple[str, str]:
    """Return ``(raw_token, token_hash)`` for a password reset link.

    Same construction as share tokens — high entropy, digest-only storage — so
    a database leak does not yield working reset links.
    """
    raw = secrets.token_urlsafe(_SHARE_TOKEN_BYTES)
    return raw, hash_share_token(raw)


# --- JWT ---------------------------------------------------------------------
def _encode(payload: dict[str, Any], ttl: timedelta) -> str:
    now = datetime.now(UTC)
    claims = {
        **payload,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
        "jti": secrets.token_urlsafe(16),
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(*, user_id: UUID) -> str:
    """Token for a registered user. Carries identity only — never permissions;
    a user's reach is recomputed per resource on every request."""
    return _encode(
        {"typ": TokenType.ACCESS.value, "sub": str(user_id)},
        timedelta(minutes=settings.access_token_ttl_minutes),
    )


def create_guest_token(
    *,
    guest_session_id: UUID,
    share_id: UUID,
    document_id: UUID,
    permissions: frozenset[Permission],
) -> str:
    """Token for an invited (unauthenticated) visitor.

    Scoped to one document and one share link. Permissions are embedded because
    they are fixed at share-creation time, but they are re-validated against the
    share row on every request so revocation takes effect immediately.
    """
    return _encode(
        {
            "typ": TokenType.GUEST.value,
            "sub": str(guest_session_id),
            "share": str(share_id),
            "doc": str(document_id),
            "perms": sorted(p.value for p in permissions),
        },
        timedelta(minutes=settings.guest_token_ttl_minutes),
    )


def decode_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "iat", "sub", "typ"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise InvalidTokenError("Your session has expired. Please sign in again.") from exc
    except jwt.InvalidTokenError as exc:
        raise InvalidTokenError() from exc
