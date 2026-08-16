from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator

from app.domain.enums import PrincipalKind
from app.schemas.common import APISchema

MAX_COMMENT_LENGTH = 5_000


class AuthorRef(APISchema):
    """How every comment author is presented, regardless of principal kind.

    Note what is absent: the client cannot *send* this. Authorship is derived
    server-side from the caller's token, so a guest cannot post as the owner.
    """

    kind: PrincipalKind
    id: UUID
    display_name: str
    is_document_owner: bool


class CreateCommentRequest(APISchema):
    body: str = Field(min_length=1, max_length=MAX_COMMENT_LENGTH)
    parent_comment_id: UUID | None = Field(
        default=None,
        description="Set to reply to an existing comment.",
    )

    @field_validator("body")
    @classmethod
    def _clean(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("A comment cannot be empty.")
        return cleaned


class UpdateCommentRequest(APISchema):
    body: str = Field(min_length=1, max_length=MAX_COMMENT_LENGTH)

    @field_validator("body")
    @classmethod
    def _clean(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("A comment cannot be empty.")
        return cleaned


class CommentResponse(APISchema):
    id: UUID
    document_id: UUID
    parent_comment_id: UUID | None
    author: AuthorRef | None = Field(
        default=None,
        description="None for a soft-deleted comment retained as a thread anchor.",
    )
    body: str
    is_deleted: bool
    can_edit: bool = Field(description="Whether the *calling* principal may edit it.")
    created_at: datetime
    updated_at: datetime
    replies: list["CommentResponse"] = Field(default_factory=list)


CommentResponse.model_rebuild()
