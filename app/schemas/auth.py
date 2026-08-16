from __future__ import annotations

import re
from uuid import UUID

from pydantic import EmailStr, Field, field_validator

from app.schemas.common import APISchema

_HAS_LETTER = re.compile(r"[A-Za-z]")
_HAS_DIGIT = re.compile(r"\d")


class SignupRequest(APISchema):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Name cannot be blank.")
        return cleaned

    @field_validator("email")
    @classmethod
    def _normalise_email(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("password")
    @classmethod
    def _password_strength(cls, value: str) -> str:
        if not _HAS_LETTER.search(value) or not _HAS_DIGIT.search(value):
            raise ValueError("Password must contain at least one letter and one number.")
        return value


class LoginRequest(APISchema):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def _normalise_email(cls, value: str) -> str:
        return value.strip().lower()


class ForgotPasswordRequest(APISchema):
    email: EmailStr

    @field_validator("email")
    @classmethod
    def _normalise_email(cls, value: str) -> str:
        return value.strip().lower()


class ResetPasswordRequest(APISchema):
    token: str = Field(min_length=16, max_length=256)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def _password_strength(cls, value: str) -> str:
        if not _HAS_LETTER.search(value) or not _HAS_DIGIT.search(value):
            raise ValueError("Password must contain at least one letter and one number.")
        return value


class UserResponse(APISchema):
    id: UUID
    name: str
    email: EmailStr


class TokenResponse(APISchema):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Token lifetime in seconds.")
    user: UserResponse
