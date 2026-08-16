from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class APISchema(BaseModel):
    """Base for every request/response DTO.

    ORM objects are never returned from a router directly — everything crosses
    the HTTP boundary as an explicit schema. That prevents a column added to a
    model (say, ``password_hash`` or ``blob_key``) from silently appearing in an
    API response.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class Page(APISchema, Generic[T]):
    items: list[T]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.items) < self.total


class ErrorResponse(APISchema):
    error_code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None


class MessageResponse(APISchema):
    message: str
