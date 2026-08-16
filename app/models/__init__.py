"""ORM models.

Every model is imported here so that ``Base.metadata`` is fully populated by the
time Alembic autogenerates a migration, and so SQLAlchemy can resolve the
string-based relationship targets.
"""

from app.models.comment import Comment
from app.models.conversation import Conversation
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.models.guest_session import GuestSession
from app.models.message import Message
from app.models.password_reset_token import PasswordResetToken
from app.models.share import Share
from app.models.user import User

__all__ = [
    "Comment",
    "Conversation",
    "Document",
    "DocumentChunk",
    "GuestSession",
    "Message",
    "PasswordResetToken",
    "Share",
    "User",
]
