"""Typed domain exceptions.

Services and repositories raise these; they know nothing about HTTP. A single
exception handler in ``app.main`` maps them to responses, so status codes live
in exactly one place instead of being scattered through business logic.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any


class AppError(Exception):
    """Base class for every expected, client-facing error."""

    status_code: int = HTTPStatus.INTERNAL_SERVER_ERROR
    error_code: str = "internal_error"
    message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.message
        self.details = details or {}
        super().__init__(self.message)


# --- 400 ---------------------------------------------------------------------
class ValidationError(AppError):
    status_code = HTTPStatus.BAD_REQUEST
    error_code = "validation_error"
    message = "The request payload is invalid."


class UnsupportedFileTypeError(ValidationError):
    error_code = "unsupported_file_type"
    message = "Only PDF files are accepted."


class FileTooLargeError(ValidationError):
    error_code = "file_too_large"
    message = "The uploaded file exceeds the maximum allowed size."


# --- 401 ---------------------------------------------------------------------
class AuthenticationError(AppError):
    status_code = HTTPStatus.UNAUTHORIZED
    error_code = "unauthenticated"
    message = "Authentication is required."


class InvalidCredentialsError(AuthenticationError):
    error_code = "invalid_credentials"
    message = "Incorrect email or password."


class InvalidTokenError(AuthenticationError):
    error_code = "invalid_token"
    message = "The provided token is invalid or has expired."


# --- 403 ---------------------------------------------------------------------
class AuthorizationError(AppError):
    status_code = HTTPStatus.FORBIDDEN
    error_code = "forbidden"
    message = "You do not have permission to perform this action."


# --- 404 ---------------------------------------------------------------------
class NotFoundError(AppError):
    status_code = HTTPStatus.NOT_FOUND
    error_code = "not_found"
    message = "The requested resource was not found."


class DocumentNotFoundError(NotFoundError):
    error_code = "document_not_found"
    message = "Document not found."


class CommentNotFoundError(NotFoundError):
    error_code = "comment_not_found"
    message = "Comment not found."


class ShareNotFoundError(NotFoundError):
    error_code = "share_not_found"
    message = "This share link is invalid, expired, or has been revoked."


# --- 409 ---------------------------------------------------------------------
class ConflictError(AppError):
    status_code = HTTPStatus.CONFLICT
    error_code = "conflict"
    message = "The request conflicts with the current state of the resource."


class EmailAlreadyRegisteredError(ConflictError):
    error_code = "email_already_registered"
    message = "An account with this email address already exists."


class DocumentNotReadyError(ConflictError):
    error_code = "document_not_ready"
    message = "This document is still being processed. Please try again shortly."


class DocumentProcessingFailedError(ConflictError):
    error_code = "document_processing_failed"
    message = "AI processing failed for this document, so it cannot be queried."


# --- 429 / 502 ---------------------------------------------------------------
class RateLimitedError(AppError):
    status_code = HTTPStatus.TOO_MANY_REQUESTS
    error_code = "rate_limited"
    message = "Too many requests. Please slow down."


class AIProviderError(AppError):
    status_code = HTTPStatus.BAD_GATEWAY
    error_code = "ai_provider_error"
    message = "The AI provider could not be reached. Please try again."


class StorageError(AppError):
    status_code = HTTPStatus.BAD_GATEWAY
    error_code = "storage_error"
    message = "The file store could not be reached. Please try again."
