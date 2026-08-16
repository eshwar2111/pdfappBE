"""Email sender implementations."""

from __future__ import annotations

import logging

import httpx

from app.core.config import settings
from app.email.base import EmailMessage, EmailSender

logger = logging.getLogger(__name__)

_RESEND_ENDPOINT = "https://api.resend.com/emails"
_TIMEOUT_SECONDS = 10.0


class ConsoleEmailSender(EmailSender):
    """Development sender: logs instead of delivering.

    Keeps the whole flow — including password reset — exercisable without an
    email provider or a verified sending domain. The reset link is written to
    the log so it can be followed locally.
    """

    async def send(self, message: EmailMessage) -> bool:
        logger.info(
            "Email (not sent — console sender)",
            extra={
                "email_to": message.to,
                "email_subject": message.subject,
                "email_text": message.text,
            },
        )
        return True


class ResendEmailSender(EmailSender):
    """Resend adapter.

    Called over plain HTTP rather than through the SDK: the API is a single
    JSON POST, and this avoids another dependency that must track the SDK's
    release cycle.
    """

    def __init__(self) -> None:
        self._api_key = settings.resend_api_key
        self._from = settings.email_from

    async def send(self, message: EmailMessage) -> bool:
        if not self._api_key:
            logger.warning("RESEND_API_KEY is not configured; email not sent")
            return False

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    _RESEND_ENDPOINT,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json={
                        "from": self._from,
                        "to": [message.to],
                        "subject": message.subject,
                        "html": message.html,
                        "text": message.text,
                    },
                )
        except httpx.HTTPError:
            # Never raised onward: the share link or reset token is already
            # committed, and undoing it because email failed would be worse.
            logger.exception("Could not reach the email provider")
            return False

        if response.status_code >= 400:
            # The provider's own explanation goes in the message, not only in
            # structured fields — "domain not verified" and "invalid key" are
            # very different problems and the distinction must survive whatever
            # log formatter happens to be attached.
            logger.error(
                "Email provider rejected the message (HTTP %s): %s",
                response.status_code,
                response.text[:500],
            )
            return False

        logger.info("Email sent", extra={"email_to": message.to})
        return True
