"""Conversational, retrieval-grounded chat over one document."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from app.ai.prompts import CHAT_SYSTEM_INSTRUCTION, chat_user_prompt
from app.ai.provider import AIProvider, ChatTurn, GenerationRequest
from app.ai.retrieval import RetrievalService
from app.core.config import settings
from app.core.exceptions import (
    DocumentNotReadyError,
    DocumentProcessingFailedError,
)
from app.domain.enums import DocumentStatus, MessageRole
from app.domain.principal import Principal
from app.models.conversation import Conversation
from app.models.document import Document
from app.models.message import Message
from app.repositories.conversation_repository import ConversationRepository
from app.schemas.chat import Citation

logger = logging.getLogger(__name__)

_CITATION_EXCERPT_CHARS = 300


class ChatService:
    def __init__(
        self,
        *,
        provider: AIProvider,
        retrieval_service: RetrievalService,
        conversation_repository: ConversationRepository,
    ) -> None:
        self._provider = provider
        self._retrieval = retrieval_service
        self._conversations = conversation_repository

    # --- conversation state ------------------------------------------------
    async def get_or_create_conversation(
        self, *, document: Document, principal: Principal
    ) -> Conversation:
        """One conversation per (document, principal).

        Keyed by principal rather than by share link, so two guests opening the
        same link get separate histories and cannot read each other's questions.
        """
        existing = await self._conversations.get_for_principal(
            document_id=document.id,
            user_id=principal.user_id,
            guest_session_id=principal.guest_session_id,
        )
        if existing is not None:
            return existing

        conversation = Conversation(
            document_id=document.id,
            user_id=principal.user_id,
            guest_session_id=principal.guest_session_id,
        )
        # Initialise the collection explicitly. Without this the relationship is
        # unloaded on a freshly persisted row, so the first read of
        # `.messages` emits a lazy load — which async SQLAlchemy cannot service
        # outside a greenlet context and raises MissingGreenlet.
        conversation.messages = []
        return await self._conversations.add(conversation)

    @staticmethod
    def _assert_queryable(document: Document) -> None:
        if document.status is DocumentStatus.FAILED:
            raise DocumentProcessingFailedError()
        if document.status is not DocumentStatus.READY:
            raise DocumentNotReadyError()

    async def _history(self, conversation: Conversation) -> list[ChatTurn]:
        """The last N turns, where a turn is a user/assistant pair.

        Bounded so a long conversation cannot crowd the retrieved passages out
        of the context window — the grounding material matters more than the
        tenth-most-recent exchange.
        """
        messages = await self._conversations.recent_messages(
            conversation.id, limit=settings.chat_history_turns * 2
        )
        return [
            ChatTurn(
                role="assistant" if m.role is MessageRole.ASSISTANT else "user",
                content=m.content,
            )
            for m in messages
        ]

    # --- ask ---------------------------------------------------------------
    async def _build_request(
        self, *, document: Document, conversation: Conversation, question: str
    ) -> tuple[GenerationRequest, list[Citation]]:
        self._assert_queryable(document)

        retrieved = await self._retrieval.retrieve_for_document(
            document_id=document.id,
            query=question,
            top_k=settings.retrieval_top_k,
        )
        passages = self._retrieval.to_passages(retrieved)

        history = await self._history(conversation)
        turns = [
            *history,
            ChatTurn(role="user", content=chat_user_prompt(question, passages)),
        ]

        citations = [
            Citation(
                chunk_id=item.chunk.id,
                chunk_index=item.chunk.chunk_index,
                page_start=item.chunk.page_start,
                page_end=item.chunk.page_end,
                excerpt=_excerpt(item.chunk.content),
            )
            for item in sorted(retrieved, key=lambda i: i.chunk.chunk_index)
        ]

        request = GenerationRequest(
            system_instruction=CHAT_SYSTEM_INSTRUCTION,
            turns=turns,
            temperature=0.15,  # low: this is extraction, not composition
            # Headroom for the model's internal reasoning tokens on top of the
            # answer itself; too tight a cap truncates answers mid-sentence.
            max_output_tokens=3072,
        )
        return request, citations

    async def ask(
        self, *, document: Document, conversation: Conversation, question: str
    ) -> Message:
        request, citations = await self._build_request(
            document=document, conversation=conversation, question=question
        )

        # The question is persisted before the call, so a provider failure
        # leaves a coherent transcript rather than a silent gap.
        await self._conversations.add_message(
            Message(
                conversation_id=conversation.id,
                role=MessageRole.USER,
                content=question,
            )
        )

        answer = await self._provider.generate(request)

        return await self._conversations.add_message(
            Message(
                conversation_id=conversation.id,
                role=MessageRole.ASSISTANT,
                content=answer,
                citations=[c.model_dump(mode="json") for c in citations],
            )
        )

    async def ask_stream(
        self, *, document: Document, conversation: Conversation, question: str
    ) -> AsyncIterator[tuple[str, str, list[Citation]]]:
        """Yield ``(event, data, citations)`` for a server-sent event stream.

        Citations are emitted first: the UI can render its source list while
        the answer is still arriving, and the client already knows which pages
        the answer will refer to.
        """
        request, citations = await self._build_request(
            document=document, conversation=conversation, question=question
        )

        await self._conversations.add_message(
            Message(
                conversation_id=conversation.id,
                role=MessageRole.USER,
                content=question,
            )
        )

        yield "citations", "", citations

        collected: list[str] = []
        async for delta in self._provider.stream(request):
            collected.append(delta)
            yield "delta", delta, []

        answer = "".join(collected).strip()
        if answer:
            await self._conversations.add_message(
                Message(
                    conversation_id=conversation.id,
                    role=MessageRole.ASSISTANT,
                    content=answer,
                    citations=[c.model_dump(mode="json") for c in citations],
                )
            )
        yield "done", "", []


def _excerpt(content: str) -> str:
    collapsed = " ".join(content.split())
    if len(collapsed) <= _CITATION_EXCERPT_CHARS:
        return collapsed
    return collapsed[:_CITATION_EXCERPT_CHARS].rsplit(" ", 1)[0] + "..."
