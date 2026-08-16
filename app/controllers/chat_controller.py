from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.domain.principal import Principal
from app.models.document import Document
from app.schemas.chat import (
    ChatMessageResponse,
    ChatResponse,
    Citation,
    ConversationResponse,
)
from app.services.chat_service import ChatService

logger = logging.getLogger(__name__)


class ChatController:
    def __init__(self, session: AsyncSession, chat_service: ChatService) -> None:
        self._session = session
        self._chat = chat_service

    async def history(
        self, *, document: Document, principal: Principal
    ) -> ConversationResponse:
        conversation = await self._chat.get_or_create_conversation(
            document=document, principal=principal
        )
        await self._session.commit()
        return ConversationResponse(
            id=conversation.id,
            document_id=document.id,
            messages=[_to_message(m) for m in conversation.messages],
        )

    async def ask(
        self, *, document: Document, principal: Principal, question: str
    ) -> ChatResponse:
        conversation = await self._chat.get_or_create_conversation(
            document=document, principal=principal
        )
        message = await self._chat.ask(
            document=document, conversation=conversation, question=question
        )
        await self._session.commit()
        return ChatResponse(
            conversation_id=conversation.id, message=_to_message(message)
        )

    async def ask_stream(
        self, *, document: Document, principal: Principal, question: str
    ) -> AsyncIterator[str]:
        """Server-sent events.

        SSE rather than WebSockets: the stream is one-directional and
        short-lived, so it needs no connection lifecycle of its own and works
        through ordinary HTTP infrastructure. The commit lands after the final
        token, so the transcript is persisted exactly once.
        """
        conversation = await self._chat.get_or_create_conversation(
            document=document, principal=principal
        )
        yield _sse("conversation", {"conversation_id": str(conversation.id)})

        # Once the response headers are sent the status code is fixed, so a
        # failure cannot become a 502 — it has to travel in-band as an event.
        # Without this the ASGI response simply dies and the browser sees a
        # truncated stream with no explanation.
        try:
            async for event, data, citations in self._chat.ask_stream(
                document=document, conversation=conversation, question=question
            ):
                if event == "citations":
                    yield _sse(
                        "citations",
                        {"citations": [c.model_dump(mode="json") for c in citations]},
                    )
                elif event == "delta":
                    yield _sse("delta", {"text": data})
                elif event == "done":
                    await self._session.commit()
                    yield _sse("done", {})
        except AppError as exc:
            logger.warning("Chat stream failed: %s", exc.message)
            await self._session.rollback()
            yield _sse("error", {"error_code": exc.error_code, "message": exc.message})
        except Exception:  # noqa: BLE001 - the stream must always terminate cleanly
            logger.exception("Unexpected failure during chat stream")
            await self._session.rollback()
            yield _sse(
                "error",
                {
                    "error_code": "internal_error",
                    "message": "Something went wrong while answering. Please try again.",
                },
            )


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


def _to_message(message) -> ChatMessageResponse:  # noqa: ANN001 - ORM Message
    return ChatMessageResponse(
        id=message.id,
        role=message.role,
        content=message.content,
        citations=[Citation.model_validate(c) for c in (message.citations or [])],
        created_at=message.created_at,
    )
