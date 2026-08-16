from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.deps import (
    CurrentUser,
    DbSession,
    get_auth_service,
    get_frontend_base_url,
)
from app.controllers.auth_controller import AuthController
from app.repositories.user_repository import UserRepository
from app.schemas.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    ResetPasswordRequest,
    SignupRequest,
    TokenResponse,
    UserResponse,
)
from app.schemas.common import MessageResponse
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


def get_controller(
    session: DbSession,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> AuthController:
    return AuthController(session, auth_service)


Controller = Annotated[AuthController, Depends(get_controller)]


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def signup(payload: SignupRequest, controller: Controller) -> TokenResponse:
    """Create an account. The password is Argon2id-hashed before it is stored."""
    return await controller.signup(payload)


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, controller: Controller) -> TokenResponse:
    return await controller.login(payload)


@router.post("/forgot-password", response_model=MessageResponse)
async def forgot_password(
    payload: ForgotPasswordRequest,
    controller: Controller,
    frontend_base_url: Annotated[str, Depends(get_frontend_base_url)],
) -> MessageResponse:
    """Request a reset link.

    Always returns the same response, whether or not the address is
    registered — a differing response would let anyone test which emails have
    accounts.
    """
    await controller.forgot_password(payload, frontend_base_url=frontend_base_url)
    return MessageResponse(
        message="If an account exists for that address, a reset link is on its way."
    )


@router.post("/reset-password", response_model=TokenResponse)
async def reset_password(payload: ResetPasswordRequest, controller: Controller) -> TokenResponse:
    """Redeem a reset link and sign in.

    The token is single-use and expires; redeeming it also invalidates any
    other outstanding links for the account.
    """
    return await controller.reset_password(payload)


@router.get("/me", response_model=UserResponse)
async def me(
    principal: CurrentUser,
    session: DbSession,
) -> UserResponse:
    """Used by the SPA on boot to restore a session from a stored token."""
    user = await UserRepository(session).get(principal.id)
    return UserResponse.model_validate(user)
