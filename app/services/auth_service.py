from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from app.core.config import settings
from app.core.exceptions import (
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    InvalidTokenError,
)
from app.core.security import (
    create_access_token,
    generate_reset_token,
    hash_password,
    hash_share_token,
    password_needs_rehash,
    verify_password,
)
from app.email.base import EmailSender
from app.email.templates import password_reset
from app.models.password_reset_token import PasswordResetToken
from app.models.user import User
from app.repositories.password_reset_repository import PasswordResetRepository
from app.repositories.user_repository import UserRepository
from app.schemas.auth import LoginRequest, SignupRequest, TokenResponse, UserResponse

logger = logging.getLogger(__name__)


class AuthService:
    def __init__(
        self,
        user_repository: UserRepository,
        password_reset_repository: PasswordResetRepository | None = None,
        email_sender: EmailSender | None = None,
    ) -> None:
        self._users = user_repository
        self._resets = password_reset_repository
        self._email = email_sender

    async def signup(self, payload: SignupRequest) -> User:
        if await self._users.email_exists(payload.email):
            raise EmailAlreadyRegisteredError()

        return await self._users.add(
            User(
                name=payload.name,
                email=payload.email,  # normalised to lowercase by the schema
                password_hash=hash_password(payload.password),
            )
        )

    async def authenticate(self, payload: LoginRequest) -> User:
        user = await self._users.get_by_email(payload.email)

        if user is None:
            # Hash anyway so a missing account and a wrong password take
            # comparable time — otherwise response latency reveals which
            # email addresses are registered.
            hash_password(payload.password)
            raise InvalidCredentialsError()

        if not verify_password(payload.password, user.password_hash):
            raise InvalidCredentialsError()

        if not user.is_active:
            raise InvalidCredentialsError("This account has been deactivated.")

        # Transparently upgrade hashes written under older Argon2 parameters.
        if password_needs_rehash(user.password_hash):
            user.password_hash = hash_password(payload.password)

        return user

    # --- password reset ----------------------------------------------------
    async def request_password_reset(self, *, email: str, frontend_base_url: str) -> None:
        """Issue a reset link, if the address belongs to an account.

        This method deliberately reveals nothing. The route returns the same
        response whether or not the email is registered, because a differing
        response turns the endpoint into an account-enumeration oracle.
        """
        if self._resets is None or self._email is None:
            logger.error("Password reset requested but the flow is not wired up")
            return

        user = await self._users.get_by_email(email)
        if user is None or not user.is_active:
            logger.info("Password reset requested for an unknown address")
            return

        # A new request voids any earlier link, so old emails stop working.
        await self._resets.invalidate_outstanding(user.id)

        raw_token, token_hash = generate_reset_token()
        ttl = settings.password_reset_ttl_minutes
        await self._resets.add(
            PasswordResetToken(
                user_id=user.id,
                token_hash=token_hash,
                expires_at=datetime.now(UTC) + timedelta(minutes=ttl),
            )
        )

        await self._email.send(
            password_reset(
                to=user.email,
                name=user.name,
                reset_url=f"{frontend_base_url.rstrip('/')}/reset-password?token={raw_token}",
                ttl_minutes=ttl,
            )
        )

    async def reset_password(self, *, token: str, new_password: str) -> User:
        if self._resets is None:
            raise InvalidTokenError()

        record = await self._resets.get_by_token_hash(hash_share_token(token))
        if record is None or not record.is_usable:
            raise InvalidTokenError(
                "This reset link is invalid or has expired. Please request a new one."
            )

        user = record.user
        user.password_hash = hash_password(new_password)

        # Burned immediately, so a link cannot be replayed — including by a
        # mail client that pre-fetches URLs.
        await self._resets.mark_used(record)
        return user

    @staticmethod
    def issue_token(user: User) -> TokenResponse:
        return TokenResponse(
            access_token=create_access_token(user_id=user.id),
            expires_in=settings.access_token_ttl_minutes * 60,
            user=UserResponse.model_validate(user),
        )
