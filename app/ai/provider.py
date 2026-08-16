"""The AI provider port.

Everything above this file talks to ``AIProvider``. Swapping Gemini for
OpenAI or Claude means adding one adapter and changing one factory line — no
service, controller or router changes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import StrEnum


class EmbeddingTask(StrEnum):
    """Embedding models produce better vectors when told the intended use:
    a stored passage and a search query are embedded asymmetrically."""

    DOCUMENT = "RETRIEVAL_DOCUMENT"
    QUERY = "RETRIEVAL_QUERY"


@dataclass(frozen=True, slots=True)
class ChatTurn:
    role: str  # "user" | "assistant"
    content: str


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    system_instruction: str
    turns: list[ChatTurn] = field(default_factory=list)
    temperature: float = 0.2
    max_output_tokens: int = 1_024


class AIProvider(ABC):
    """Text generation + embeddings."""

    @abstractmethod
    async def generate(self, request: GenerationRequest) -> str:
        """Return a complete response."""

    @abstractmethod
    async def stream(self, request: GenerationRequest) -> AsyncIterator[str]:
        """Yield response text incrementally."""

    @abstractmethod
    async def embed(
        self, texts: list[str], *, task: EmbeddingTask
    ) -> list[list[float]]:
        """Return one vector per input, in the same order."""

    @property
    @abstractmethod
    def embedding_dimensions(self) -> int:
        ...
