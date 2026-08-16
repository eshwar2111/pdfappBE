from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator

from app.domain.enums import MessageRole
from app.schemas.common import APISchema

MAX_QUESTION_LENGTH = 2_000


class Citation(APISchema):
    """Which passage grounded an answer. Returned with every assistant turn so
    the user can verify the claim against the PDF rather than trust it."""

    chunk_id: UUID
    chunk_index: int
    page_start: int
    page_end: int
    excerpt: str


class ChatRequest(APISchema):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_LENGTH)

    @field_validator("question")
    @classmethod
    def _clean(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Please enter a question.")
        return cleaned


class ChatMessageResponse(APISchema):
    id: UUID
    role: MessageRole
    content: str
    citations: list[Citation] = Field(default_factory=list)
    created_at: datetime


class ChatResponse(APISchema):
    conversation_id: UUID
    message: ChatMessageResponse


class ConversationResponse(APISchema):
    id: UUID
    document_id: UUID
    messages: list[ChatMessageResponse]
