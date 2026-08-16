"""Application configuration.

All configuration is read from the environment exactly once, at import time,
and exposed as a frozen settings object. Nothing in the codebase reads
``os.environ`` directly — that keeps secrets in one auditable place and makes
misconfiguration fail loudly at boot rather than at the first request.
"""

from __future__ import annotations

import json
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, PostgresDsn, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    STAGING = "staging"
    PRODUCTION = "production"


class StorageBackend(StrEnum):
    LOCAL = "local"
    AZURE = "azure"


class EmailBackend(StrEnum):
    #: Logs messages instead of sending, so the flows work with no provider.
    CONSOLE = "console"
    RESEND = "resend"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application -------------------------------------------------------
    environment: Environment = Environment.LOCAL
    log_level: str = "INFO"
    api_v1_prefix: str = "/api/v1"
    project_name: str = "PDF Intelligence & Collaboration API"
    # NoDecode: without it, pydantic-settings tries to JSON-parse this value
    # straight from the dotenv file and never reaches the validator below.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )

    # --- Database ----------------------------------------------------------
    database_url: PostgresDsn
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout: int = 30
    db_pool_recycle: int = 1800
    db_echo: bool = False

    # --- Security ----------------------------------------------------------
    jwt_secret: str = Field(min_length=16)
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 60
    guest_token_ttl_minutes: int = 720

    # --- Storage -----------------------------------------------------------
    storage_backend: StorageBackend = StorageBackend.LOCAL
    local_storage_dir: Path = Path("./var/blobs")
    azure_storage_connection_string: str | None = None
    azure_storage_container: str = "documents"
    sas_url_ttl_minutes: int = 15

    # --- Uploads -----------------------------------------------------------
    max_upload_bytes: int = 25 * 1024 * 1024

    # --- Email -------------------------------------------------------------
    email_backend: EmailBackend = EmailBackend.CONSOLE
    resend_api_key: str = ""
    #: Must be a verified sender on the Resend account. `onboarding@resend.dev`
    #: works without a domain but only delivers to the account owner's address.
    email_from: str = "PDF Intelligence <onboarding@resend.dev>"

    # --- Password reset ----------------------------------------------------
    password_reset_ttl_minutes: int = 30

    # --- AI ----------------------------------------------------------------
    gemini_api_key: str = ""
    gemini_chat_model: str = "gemini-3.7-flash"
    gemini_embedding_model: str = "gemini-embedding-001"
    gemini_embedding_dimensions: int = 768

    # --- Retrieval ---------------------------------------------------------
    chunk_target_tokens: int = 900
    chunk_overlap_tokens: int = 150
    retrieval_top_k: int = 8
    chat_history_turns: int = 5

    #: Dashboard search demands a stronger embedding match than chat grounding.
    #: Chat wants recall — a weak passage in the right document still helps the
    #: model. Search wants precision: a document the user did not mean should
    #: not appear at all, because a listed result reads as an assertion of
    #: relevance. Embeddings rarely score unrelated documents below ~0.55, so
    #: the chat floor of 0.35 lets everything through on a dashboard query.
    search_min_similarity: float = 0.62

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept a comma-separated string or a JSON list.

        Comma-separated is the readable form for a dotenv file; the JSON form
        is what container platforms tend to inject.
        """
        if not isinstance(value, str):
            return value

        text = value.strip()
        if text.startswith("["):
            return json.loads(text)
        return [origin.strip() for origin in text.split(",") if origin.strip()]

    @field_validator("jwt_secret")
    @classmethod
    def _reject_placeholder_secret(cls, value: str) -> str:
        if value.startswith("change-me"):
            raise ValueError(
                "JWT_SECRET is still the placeholder from .env.example. "
                "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(64))\""
            )
        return value

    @property
    def is_production(self) -> bool:
        return self.environment is Environment.PRODUCTION


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
