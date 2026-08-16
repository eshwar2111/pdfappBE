"""Email port.

Delivery is a side effect, not a business rule: a share is still created and a
reset token is still valid whether or not the message lands. Services therefore
depend on this interface and never on a provider SDK, and failures are logged
rather than propagated.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EmailMessage:
    to: str
    subject: str
    html: str
    #: Plain-text alternative. Always sent alongside the HTML — some clients
    #: refuse HTML-only mail, and spam filters score it worse.
    text: str


class EmailSender(ABC):
    @abstractmethod
    async def send(self, message: EmailMessage) -> bool:
        """Return True when the provider accepted the message.

        Implementations must not raise: the caller's work has already been
        committed by this point, and a bounced email should not undo it.
        """
