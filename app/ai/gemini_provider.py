"""Google Gemini adapter.

The only file in the codebase that imports the Gemini SDK or reads the API key.
The key is server-side configuration and is never sent to, or reachable from,
the browser.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.ai.provider import AIProvider, EmbeddingTask, GenerationRequest
from app.core.config import settings
from app.core.exceptions import AIProviderError

logger = logging.getLogger(__name__)

#: The free tier rate-limits embedding calls, so batches are kept modest and
#: sent sequentially rather than fanned out.
_EMBED_BATCH_SIZE = 32

#: Rate limiting. Retrying is the correct response; failing is not.
_RATE_LIMITED = 429


def _is_transient(exc: BaseException) -> bool:
    """Retry only what a retry can actually fix.

    A 503 ("high demand") or a 429 clears on its own. A 404 for a retired model
    name, or a 401 for a bad key, will fail identically on every attempt —
    retrying those just multiplies the latency before the user sees the error.
    """
    if isinstance(exc, genai_errors.ServerError):
        return True
    if isinstance(exc, genai_errors.ClientError):
        return getattr(exc, "code", None) == _RATE_LIMITED
    # Connection resets and timeouts surface as plain OSError subclasses.
    return isinstance(exc, (TimeoutError, ConnectionError))


_retry_policy = retry(
    retry=retry_if_exception(_is_transient),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)


def _to_domain_error(exc: Exception) -> AIProviderError:
    """Translate a provider failure into a message a user can act on."""
    if isinstance(exc, genai_errors.ServerError):
        return AIProviderError(
            "The AI model is busy right now. Please try again in a moment."
        )
    if isinstance(exc, genai_errors.ClientError):
        code = getattr(exc, "code", None)
        if code == _RATE_LIMITED:
            return AIProviderError(
                "The AI request quota has been reached. Please wait a minute and retry."
            )
        if code == 404:
            return AIProviderError(
                "The configured AI model is unavailable. Check GEMINI_CHAT_MODEL "
                "against `python scripts/list_models.py`."
            )
    return AIProviderError()


class GeminiProvider(AIProvider):
    def __init__(self) -> None:
        if not settings.gemini_api_key:
            raise AIProviderError(
                "GEMINI_API_KEY is not configured. Set it in the backend environment."
            )
        self._client = genai.Client(api_key=settings.gemini_api_key)
        self._chat_model = settings.gemini_chat_model
        self._embedding_model = settings.gemini_embedding_model
        self._dimensions = settings.gemini_embedding_dimensions

    @property
    def embedding_dimensions(self) -> int:
        return self._dimensions

    # --- internals ---------------------------------------------------------
    @staticmethod
    def _to_contents(request: GenerationRequest) -> list[genai_types.Content]:
        """Map our neutral turn list onto Gemini's content format.

        Gemini names the model role "model" rather than "assistant"; the
        translation is confined to this adapter so the rest of the app can use
        one vocabulary.
        """
        return [
            genai_types.Content(
                role="model" if turn.role == "assistant" else "user",
                parts=[genai_types.Part.from_text(text=turn.content)],
            )
            for turn in request.turns
        ]

    def _config(self, request: GenerationRequest) -> genai_types.GenerateContentConfig:
        return genai_types.GenerateContentConfig(
            system_instruction=request.system_instruction,
            temperature=request.temperature,
            max_output_tokens=request.max_output_tokens,
            # This app never exposes tools to the model, so the SDK's automatic
            # function-calling loop is dead weight — and it warns on every call.
            automatic_function_calling=genai_types.AutomaticFunctionCallingConfig(
                disable=True
            ),
        )

    # --- generation --------------------------------------------------------
    @_retry_policy
    async def generate(self, request: GenerationRequest) -> str:
        try:
            response = await self._client.aio.models.generate_content(
                model=self._chat_model,
                contents=self._to_contents(request),
                config=self._config(request),
            )
        except Exception as exc:  # noqa: BLE001 - normalised to a domain error
            logger.exception("Gemini generation failed")
            raise _to_domain_error(exc) from exc

        text = (response.text or "").strip()
        if not text:
            raise AIProviderError("The AI provider returned an empty response.")
        return text

    @_retry_policy
    async def _open_stream(self, request: GenerationRequest):
        """Establish the stream, with retries.

        Retrying is confined to *opening* the stream, which is where a 503
        lands. Once tokens are flowing a retry would be wrong: the client has
        already rendered a partial answer, and starting over would duplicate
        text rather than continue it.
        """
        return await self._client.aio.models.generate_content_stream(
            model=self._chat_model,
            contents=self._to_contents(request),
            config=self._config(request),
        )

    async def stream(self, request: GenerationRequest) -> AsyncIterator[str]:
        try:
            stream = await self._open_stream(request)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Gemini stream could not be opened")
            raise _to_domain_error(exc) from exc

        try:
            async for chunk in stream:
                if chunk.text:
                    yield chunk.text
        except Exception as exc:  # noqa: BLE001
            # Mid-stream failure. The caller has already emitted text, so this
            # is surfaced as an error event rather than retried.
            logger.exception("Gemini stream broke mid-response")
            raise _to_domain_error(exc) from exc

    # --- embeddings --------------------------------------------------------
    @_retry_policy
    async def _embed_batch(
        self, texts: list[str], task: EmbeddingTask
    ) -> list[list[float]]:
        try:
            response = await self._client.aio.models.embed_content(
                model=self._embedding_model,
                contents=texts,
                config=genai_types.EmbedContentConfig(
                    task_type=task.value,
                    output_dimensionality=self._dimensions,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Gemini embedding failed")
            raise AIProviderError() from exc

        vectors = [list(item.values or []) for item in (response.embeddings or [])]
        if len(vectors) != len(texts):
            raise AIProviderError("Embedding count did not match the input count.")
        return vectors

    async def embed(
        self, texts: list[str], *, task: EmbeddingTask
    ) -> list[list[float]]:
        if not texts:
            return []

        vectors: list[list[float]] = []
        for start in range(0, len(texts), _EMBED_BATCH_SIZE):
            batch = texts[start : start + _EMBED_BATCH_SIZE]
            vectors.extend(await self._embed_batch(batch, task))
            if start + _EMBED_BATCH_SIZE < len(texts):
                await asyncio.sleep(0.2)  # stay inside free-tier rate limits
        return vectors
