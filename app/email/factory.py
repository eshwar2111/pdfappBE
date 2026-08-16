from __future__ import annotations

from functools import lru_cache

from app.core.config import EmailBackend, settings
from app.email.base import EmailSender
from app.email.senders import ConsoleEmailSender, ResendEmailSender


@lru_cache(maxsize=1)
def get_email_sender() -> EmailSender:
    """The single place the email backend is chosen."""
    if settings.email_backend is EmailBackend.RESEND and settings.resend_api_key:
        return ResendEmailSender()
    return ConsoleEmailSender()
