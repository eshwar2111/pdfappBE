from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.conversation import Conversation
from app.models.message import Message
from app.repositories.base import BaseRepository


class ConversationRepository(BaseRepository[Conversation]):
    model = Conversation

    async def get_for_principal(
        self,
        *,
        document_id: UUID,
        user_id: UUID | None,
        guest_session_id: UUID | None,
    ) -> Conversation | None:
        """One conversation per (document, principal).

        Exactly one of the two id columns is non-null — mirrored from the
        ``CHECK`` constraint on the table — so the predicate is unambiguous.
        """
        stmt = select(Conversation).where(Conversation.document_id == document_id)
        if user_id is not None:
            stmt = stmt.where(Conversation.user_id == user_id)
        else:
            stmt = stmt.where(Conversation.guest_session_id == guest_session_id)

        result = await self.session.execute(
            stmt.options(selectinload(Conversation.messages))
        )
        return result.scalar_one_or_none()

    async def recent_messages(
        self, conversation_id: UUID, *, limit: int
    ) -> list[Message]:
        """The last ``limit`` messages in chronological order.

        Fetched newest-first so the LIMIT applies to the tail, then reversed —
        the alternative would load an entire long conversation to take its end.
        """
        result = await self.session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(limit)
        )
        return list(reversed(result.scalars().all()))

    async def add_message(self, message: Message) -> Message:
        self.session.add(message)
        await self.session.flush()
        return message
