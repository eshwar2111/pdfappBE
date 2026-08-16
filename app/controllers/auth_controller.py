from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    ResetPasswordRequest,
    SignupRequest,
    TokenResponse,
)
from app.services.auth_service import AuthService


class AuthController:
    """Orchestrates a request and owns the transaction boundary.

    Services enforce rules; repositories run queries; the controller decides
    when the unit of work commits. Keeping ``commit`` here means a request that
    touches several services still lands atomically.
    """

    def __init__(self, session: AsyncSession, auth_service: AuthService) -> None:
        self._session = session
        self._auth = auth_service

    async def signup(self, payload: SignupRequest) -> TokenResponse:
        user = await self._auth.signup(payload)
        await self._session.commit()
        return self._auth.issue_token(user)

    async def login(self, payload: LoginRequest) -> TokenResponse:
        user = await self._auth.authenticate(payload)
        # Commits any transparent password-hash upgrade performed during
        # verification. A no-op otherwise.
        await self._session.commit()
        return self._auth.issue_token(user)

    async def forgot_password(
        self, payload: ForgotPasswordRequest, *, frontend_base_url: str
    ) -> None:
        await self._auth.request_password_reset(
            email=payload.email, frontend_base_url=frontend_base_url
        )
        await self._session.commit()

    async def reset_password(self, payload: ResetPasswordRequest) -> TokenResponse:
        """Resetting signs the user straight in — they have just proved control
        of the mailbox, so a second login step adds friction and no security."""
        user = await self._auth.reset_password(
            token=payload.token, new_password=payload.password
        )
        await self._session.commit()
        return self._auth.issue_token(user)
