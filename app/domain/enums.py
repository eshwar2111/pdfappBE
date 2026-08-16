"""Domain enumerations shared by models, schemas and services."""

from __future__ import annotations

from enum import StrEnum


class DocumentStatus(StrEnum):
    """Lifecycle of a document's AI processing pipeline."""

    UPLOADED = "UPLOADED"
    PROCESSING = "PROCESSING"
    READY = "READY"
    FAILED = "FAILED"


class ProcessingFailureReason(StrEnum):
    """Why processing failed — surfaced to the UI so the user gets a real message."""

    NO_EXTRACTABLE_TEXT = "NO_EXTRACTABLE_TEXT"
    CORRUPT_PDF = "CORRUPT_PDF"
    ENCRYPTED_PDF = "ENCRYPTED_PDF"
    EMBEDDING_FAILED = "EMBEDDING_FAILED"
    SUMMARY_FAILED = "SUMMARY_FAILED"
    UNKNOWN = "UNKNOWN"


class Permission(StrEnum):
    """Capabilities a principal may hold on a document."""

    VIEW = "VIEW"
    COMMENT = "COMMENT"
    CHAT = "CHAT"
    MANAGE = "MANAGE"  # owner-only: share, revoke, delete


OWNER_PERMISSIONS: frozenset[Permission] = frozenset(Permission)
DEFAULT_SHARE_PERMISSIONS: frozenset[Permission] = frozenset(
    {Permission.VIEW, Permission.COMMENT, Permission.CHAT}
)


class PrincipalKind(StrEnum):
    USER = "user"
    GUEST = "guest"


class TokenType(StrEnum):
    """``typ`` claim used to discriminate JWTs. Never trust anything else."""

    ACCESS = "access"
    GUEST = "guest"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
