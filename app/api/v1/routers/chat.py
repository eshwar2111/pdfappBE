from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.api.deps import (
    CurrentPrincipal,
    DbSession,
    DocumentAccess,
    get_chat_service,
    require_document,
)
from app.controllers.chat_controller import ChatController
from app.domain.enums import Permission
from app.schemas.chat import ChatRequest, ChatResponse, ConversationResponse
from app.services.chat_service import ChatService

router = APIRouter(prefix="/documents", tags=["chat"])


def get_controller(
    session: DbSession,
    chat_service: Annotated[ChatService, Depends(get_chat_service)],
) -> ChatController:
    return ChatController(session, chat_service)


Controller = Annotated[ChatController, Depends(get_controller)]


@router.get("/{document_id}/chat", response_model=ConversationResponse)
async def get_conversation(
    principal: CurrentPrincipal,
    controller: Controller,
    access: Annotated[DocumentAccess, Depends(require_document(Permission.CHAT))],
) -> ConversationResponse:
    """This principal's conversation for this document.

    A guest's history is keyed to their guest session, so it survives a page
    refresh but is invisible to the next visitor on the same link.
    """
    return await controller.history(document=access.document, principal=principal)


@router.post("/{document_id}/chat", response_model=ChatResponse)
async def ask(
    payload: ChatRequest,
    principal: CurrentPrincipal,
    controller: Controller,
    access: Annotated[DocumentAccess, Depends(require_document(Permission.CHAT))],
) -> ChatResponse:
    """Ask a question about the document.

    Relevant passages are retrieved by vector search over that document's
    chunks only, then sent to the LLM with the recent conversation turns. The
    answer carries page citations.
    """
    return await controller.ask(
        document=access.document, principal=principal, question=payload.question
    )


@router.post("/{document_id}/chat/stream")
async def ask_streaming(
    payload: ChatRequest,
    principal: CurrentPrincipal,
    controller: Controller,
    access: Annotated[DocumentAccess, Depends(require_document(Permission.CHAT))],
) -> StreamingResponse:
    """The same answer as ``POST /chat``, streamed as server-sent events.

    ``X-Accel-Buffering: no`` stops intermediate proxies from buffering the
    stream and defeating the point of streaming it.
    """
    return StreamingResponse(
        controller.ask_stream(
            document=access.document, principal=principal, question=payload.question
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
