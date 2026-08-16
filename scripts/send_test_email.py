"""Send a test email through the configured backend.

    python scripts/send_test_email.py you@example.com

Verifies the provider credentials and the sender address in isolation, so an
email problem is not discovered in the middle of a share or reset flow.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Plain formatter rather than the app's JSON one, so the provider's rejection
# reason is readable at a terminal.
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

from app.core.config import settings  # noqa: E402
from app.email.factory import get_email_sender  # noqa: E402
from app.email.templates import share_invitation  # noqa: E402


async def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python scripts/send_test_email.py <recipient@example.com>", file=sys.stderr)
        return 1

    recipient = sys.argv[1]
    sender = get_email_sender()

    print(f"backend   : {settings.email_backend}")
    print(f"from      : {settings.email_from}")
    print(f"to        : {recipient}")
    print(f"api key   : {'set' if settings.resend_api_key else 'NOT SET'}")
    print(f"adapter   : {type(sender).__name__}")
    print()

    delivered = await sender.send(
        share_invitation(
            to=recipient,
            document_name="Test Document.pdf",
            owner_name="PDF Intelligence",
            share_url="https://example.com/s/test-token",
            can_comment=True,
        )
    )

    if delivered:
        print("Accepted by the provider.")
        if settings.email_backend.value == "console":
            print("NOTE: the console backend only logs — nothing was actually sent.")
        return 0

    print("Not delivered. Check RESEND_API_KEY and that EMAIL_FROM is a verified sender.")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
